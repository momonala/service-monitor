"""Time-keyed storage for host and per-service vitals.

One row per sample tick, keyed by the tick's epoch second: host vitals as columns plus every service's
reading in a JSON column. Ticks are buffered in memory and written in one transaction every
``flush_interval`` seconds on a single long-lived connection. Reads merge the buffer with the database.
"""

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_SECONDS = 5 * 60
PRUNE_INTERVAL_SECONDS = 24 * 3600
RETENTION_SECONDS = 7 * 24 * 3600

_LEGACY_TABLES = ("system_samples", "service_samples")
_HOST_COLUMNS = (
    "temperature_c",
    "cpu_percent",
    "memory_used_pct",
    "disk_used_pct",
    "temperature_c_max",
    "cpu_percent_max",
    "memory_used_pct_max",
    "disk_used_pct_max",
    "disk_written_kb",
    "journald_written_kb",
    "system_slice_written_kb",
    "user_slice_written_kb",
    "claude_written_kb",
)
_WRITE_SOURCE_COLUMNS = (
    "disk_written_kb",
    "journald_written_kb",
    "system_slice_written_kb",
    "user_slice_written_kb",
)


@dataclass(frozen=True)
class SystemSample:
    ts: float
    temperature_c: float | None
    cpu_percent: float | None
    memory_used_pct: float | None
    disk_used_pct: float | None
    temperature_c_max: float | None = None
    cpu_percent_max: float | None = None
    memory_used_pct_max: float | None = None
    disk_used_pct_max: float | None = None
    disk_written_kb: float | None = None
    journald_written_kb: float | None = None
    system_slice_written_kb: float | None = None
    user_slice_written_kb: float | None = None
    claude_written_kb: float | None = None


@dataclass(frozen=True)
class ServiceSample:
    """One per-service vitals point, stored alongside the host sample of the same tick."""

    ts: float
    service: str
    memory_used_pct: float | None
    cpu_percent: float | None
    written_kb: float | None = None
    log_written_kb: float | None = None


class ServiceReading(NamedTuple):
    """One unit's values within a tick, stored as a JSON array in this field order."""

    memory_used_pct: float | None
    cpu_percent: float | None
    written_kb: float | None = None
    log_written_kb: float | None = None


@dataclass(frozen=True)
class Tick:
    """Everything recorded at one sample time."""

    system: SystemSample
    services: dict[str, ServiceReading]

    @property
    def ts(self) -> int:
        return round(self.system.ts)


def make_tick(system: SystemSample, services: list[ServiceSample]) -> Tick:
    return Tick(
        system=system,
        services={
            s.service: ServiceReading(s.memory_used_pct, s.cpu_percent, s.written_kb, s.log_written_kb)
            for s in services
        },
    )


class MetricsStore:
    """Buffered, time-keyed metrics storage over one SQLite file. Safe to share across threads."""

    def __init__(
        self,
        db_path: Path,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
        retention_seconds: int = RETENTION_SECONDS,
        prune_interval: float = PRUNE_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = db_path
        self._clock = clock
        self._flush_interval = flush_interval
        self._retention_seconds = retention_seconds
        self._prune_interval = prune_interval
        self._lock = threading.Lock()
        self._buffer: list[Tick] = []
        self._last_flush = time.monotonic()
        self._last_prune: float | None = None
        self._conn: sqlite3.Connection | None = None

    def append(self, tick: Tick) -> None:
        """Buffer one tick, writing the buffer out once ``flush_interval`` has passed."""
        with self._lock:
            self._buffer.append(tick)
            if time.monotonic() - self._last_flush >= self._flush_interval:
                self._flush_locked()

    def flush(self) -> None:
        """Write all buffered ticks now, and prune if a prune is due."""
        with self._lock:
            self._flush_locked()

    def prune(self, now: float | None = None) -> int:
        """Delete rows older than the retention window. Returns rows deleted."""
        with self._lock:
            return self._prune_locked(self._clock() if now is None else now)

    def system_samples(self, start: float, end: float) -> list[SystemSample]:
        """Host samples with ``start <= ts <= end``, oldest first."""
        columns = ", ".join(_HOST_COLUMNS)
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    f"SELECT ts, {columns} FROM samples WHERE ts >= ? AND ts <= ? ORDER BY ts", (start, end)
                )
                .fetchall()
            )
            buffered = [replace(tick.system, ts=tick.ts) for tick in self._buffer if start <= tick.ts <= end]
        return [SystemSample(*row) for row in rows] + buffered

    def service_samples(self, service: str, start: float, end: float) -> list[ServiceSample]:
        """One service's samples with ``start <= ts <= end``, oldest first."""
        path = _json_path(service)
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    """
                    SELECT ts, json_extract(services, ?), json_extract(services, ?)
                    FROM samples
                    WHERE ts >= ? AND ts <= ? AND json_type(services, ?) IS NOT NULL
                    ORDER BY ts
                    """,
                    (f"{path}[0]", f"{path}[1]", start, end, path),
                )
                .fetchall()
            )
            buffered = [tick for tick in self._buffer if start <= tick.ts <= end and service in tick.services]
        return [ServiceSample(ts, service, memory, cpu) for ts, memory, cpu in rows] + [
            ServiceSample(tick.ts, service, *tick.services[service]) for tick in buffered
        ]

    def latest_tick(self) -> Tick | None:
        """The most recent tick, buffered or stored."""
        with self._lock:
            if self._buffer:
                return self._buffer[-1]
            columns = ", ".join(_HOST_COLUMNS)
            row = (
                self._connection()
                .execute(f"SELECT ts, {columns}, services FROM samples ORDER BY ts DESC LIMIT 1")
                .fetchone()
            )
        if row is None:
            return None
        services = {unit: _service_reading(values) for unit, values in json.loads(row[-1]).items()}
        return Tick(system=SystemSample(*row[:-1]), services=services)

    def host_written_kb(self, start: float, end: float) -> tuple[float, float | None]:
        """Total KB written to the root disk in the window, and the first tick that has a reading."""
        with self._lock:
            total, first = (
                self._connection()
                .execute(
                    "SELECT SUM(disk_written_kb), MIN(ts) FROM samples "
                    "WHERE ts >= ? AND ts <= ? AND disk_written_kb IS NOT NULL",
                    (start, end),
                )
                .fetchone()
            )
            buffered = [
                (tick.ts, tick.system.disk_written_kb)
                for tick in self._buffer
                if start <= tick.ts <= end and tick.system.disk_written_kb is not None
            ]
        return _combine_totals(total, first, buffered)

    def written_kb_by_source(self, start: float, end: float) -> dict[str, float]:
        """Total KB per write source, summed only over ticks where every host counter has a reading.

        Keys are the host write columns, ``claude_written_kb`` (zero where unread), plus
        ``services_written_kb`` and ``services_log_written_kb``
        (all services' direct and journald writes in those same ticks). Summing each over its own
        ticks would let one with longer history, or a missed read, skew the differences between them.
        """
        not_null = " AND ".join(f"{column} IS NOT NULL" for column in _WRITE_SOURCE_COLUMNS)
        sums = ", ".join(f"COALESCE(SUM({column}), 0)" for column in _WRITE_SOURCE_COLUMNS)
        services_sum = "COALESCE(SUM((SELECT SUM(json_extract(value, '$[{}]')) FROM json_each(services))), 0)"
        keys = (*_WRITE_SOURCE_COLUMNS, "claude_written_kb", "services_written_kb", "services_log_written_kb")
        with self._lock:
            stored = (
                self._connection()
                .execute(
                    f"SELECT {sums}, COALESCE(SUM(claude_written_kb), 0), "
                    f"{services_sum.format(2)}, {services_sum.format(3)} "
                    f"FROM samples WHERE ts >= ? AND ts <= ? AND {not_null}",
                    (start, end),
                )
                .fetchone()
            )
            totals = dict(zip(keys, stored, strict=True))
            for tick in self._buffer:
                host = [getattr(tick.system, column) for column in _WRITE_SOURCE_COLUMNS]
                if not start <= tick.ts <= end or None in host:
                    continue
                readings = tick.services.values()
                values = (
                    *host,
                    tick.system.claude_written_kb or 0.0,
                    sum(r.written_kb or 0.0 for r in readings),
                    sum(r.log_written_kb or 0.0 for r in readings),
                )
                for key, value in zip(keys, values, strict=True):
                    totals[key] += value
        return totals

    def service_written_kb(self, service: str, start: float, end: float) -> tuple[float, float | None]:
        """Total KB one service wrote in the window, and the first tick that has a reading."""
        path = f"{_json_path(service)}[2]"
        with self._lock:
            total, first = (
                self._connection()
                .execute(
                    "SELECT SUM(json_extract(services, ?)), MIN(ts) FROM samples "
                    "WHERE ts >= ? AND ts <= ? AND json_extract(services, ?) IS NOT NULL",
                    (path, start, end, path),
                )
                .fetchone()
            )
            buffered = [
                (tick.ts, tick.services[service].written_kb)
                for tick in self._buffer
                if start <= tick.ts <= end
                and service in tick.services
                and tick.services[service].written_kb is not None
            ]
        return _combine_totals(total, first, buffered)

    def written_kb_by_service(self, start: float, end: float) -> dict[str, float]:
        """Total KB each service wrote in the window."""
        return self._sum_by_service("written_kb", start, end)

    def log_written_kb_by_service(self, start: float, end: float) -> dict[str, float]:
        """Total KB of journald writes attributed to each service in the window."""
        return self._sum_by_service("log_written_kb", start, end)

    def _sum_by_service(self, field: str, start: float, end: float) -> dict[str, float]:
        path = f"$[{ServiceReading._fields.index(field)}]"
        with self._lock:
            rows = (
                self._connection()
                .execute(
                    "SELECT entry.key, SUM(json_extract(entry.value, ?)) "
                    "FROM samples, json_each(samples.services) AS entry "
                    "WHERE ts >= ? AND ts <= ? AND json_extract(entry.value, ?) IS NOT NULL "
                    "GROUP BY entry.key",
                    (path, start, end, path),
                )
                .fetchall()
            )
            totals = dict(rows)
            for tick in self._buffer:
                if not start <= tick.ts <= end:
                    continue
                for service, reading in tick.services.items():
                    value = getattr(reading, field)
                    if value is not None:
                        totals[service] = totals.get(service, 0.0) + value
        return totals

    def close(self) -> None:
        with self._lock:
            self._flush_locked()
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _flush_locked(self) -> None:
        self._last_flush = time.monotonic()
        if self._buffer:
            conn = self._connection()
            with conn:
                conn.executemany(
                    f"INSERT OR REPLACE INTO samples (ts, {', '.join(_HOST_COLUMNS)}, services) "
                    f"VALUES (?, {', '.join('?' * len(_HOST_COLUMNS))}, ?)",
                    [_row(tick) for tick in self._buffer],
                )
            self._buffer.clear()
        if self._last_prune is None or time.monotonic() - self._last_prune >= self._prune_interval:
            deleted = self._prune_locked(self._clock())
            if deleted:
                logger.info("Pruned %s metric samples", deleted)

    def _prune_locked(self, now: float) -> int:
        conn = self._connection()
        with conn:
            deleted = conn.execute(
                "DELETE FROM samples WHERE ts < ?", (now - self._retention_seconds,)
            ).rowcount
        self._last_prune = time.monotonic()
        return deleted

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = _open(self.db_path)
        return self._conn


def _open(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    with conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS samples (
                ts INTEGER PRIMARY KEY,
                {", ".join(f"{column} REAL" for column in _HOST_COLUMNS)},
                services TEXT NOT NULL
            )
            """)
    _add_missing_host_columns(conn)
    _drop_legacy_tables(conn)
    return conn


def _add_missing_host_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(samples)")}
    with conn:
        for column in _HOST_COLUMNS:
            if column not in existing:
                conn.execute(f"ALTER TABLE samples ADD COLUMN {column} REAL")


def _drop_legacy_tables(conn: sqlite3.Connection) -> None:
    existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    legacy = [table for table in _LEGACY_TABLES if table in existing]
    if not legacy:
        return
    with conn:
        for table in legacy:
            conn.execute(f"DROP TABLE {table}")
    conn.execute("VACUUM")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    logger.info("Dropped legacy metric tables %s", ", ".join(legacy))


def _row(tick: Tick) -> tuple:
    system = tick.system
    host = tuple(getattr(system, column) for column in _HOST_COLUMNS)
    return (tick.ts, *host, json.dumps(tick.services, separators=(",", ":")))


def _service_reading(values: list) -> ServiceReading:
    """Older rows hold only [memory, cpu] or [memory, cpu, written]."""
    return ServiceReading(*values)


def _combine_totals(
    stored_total: float | None, stored_first: float | None, buffered: list[tuple[int, float]]
) -> tuple[float, float | None]:
    total = (stored_total or 0.0) + sum(kb for _, kb in buffered)
    firsts = [ts for ts in (stored_first, *(ts for ts, _ in buffered)) if ts is not None]
    return total, min(firsts) if firsts else None


def _json_path(service: str) -> str:
    """JSON path to one service's entry. Unit names never contain quotes or backslashes."""
    if '"' in service or "\\" in service:
        raise ValueError(f"Invalid service name: {service!r}")
    return f'$."{service}"'
