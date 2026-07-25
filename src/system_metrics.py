"""Persist host vitals as a time series and serve windowed history for the dashboard chart.

On Linux a background sampler polls at 1Hz, then stores one row per 30s window with
both the average and the max of those ticks for each metric.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from src.services import (
    SystemInfo,
    _read_cpu_temperature,
    _read_memory,
    _read_proc_stat_busy_total,
    cpu_percent_from_delta,
    get_services,
    is_linux,
    read_service_metrics,
    read_total_memory_bytes,
)

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "system_metrics.db"
RETENTION_SECONDS = 7 * 24 * 3600
POLL_INTERVAL_SECONDS = 1
SAMPLE_INTERVAL_SECONDS = 30
TICKS_PER_SAMPLE = SAMPLE_INTERVAL_SECONDS // POLL_INTERVAL_SECONDS
MAX_RETURN_POINTS = 720
DEFAULT_WINDOW = "7d"
WINDOWS: dict[str, int] = {
    "1h": 3600,
    "6h": 6 * 3600,
    "24h": 24 * 3600,
    "7d": RETENTION_SECONDS,
}
DEFAULT_ROLLUP = "30s"
ROLLUPS: dict[str, int] = {
    "30s": 30,
    "2m": 2 * 60,
    "10m": 10 * 60,
    "30m": 30 * 60,
}

_MAX_COLUMNS = (
    "temperature_c_max",
    "cpu_percent_max",
    "memory_used_pct_max",
    "disk_used_pct_max",
)

_schema_ready: set[Path] = set()
_sampler_started = False
_sampler_lock = threading.Lock()


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


@dataclass(frozen=True)
class ServiceSample:
    """One per-service vitals point. Persisted every SAMPLE_INTERVAL_SECONDS, one row per service."""

    ts: float
    service: str
    memory_used_pct: float | None
    cpu_percent: float | None


class CpuDeltaTracker:
    """Track consecutive /proc/stat snapshots so each 1Hz tick can compute CPU % without sleeping."""

    def __init__(self) -> None:
        self._prev: tuple[int, int] | None = None

    def read_percent(self) -> float | None:
        current = _read_proc_stat_busy_total()
        if current is None:
            return None
        previous = self._prev
        self._prev = current
        if previous is None:
            return None
        return cpu_percent_from_delta(previous, current)


def window_seconds(window: str) -> int:
    """Resolve a window key to seconds. Raises ValueError for unknown windows."""
    try:
        return WINDOWS[window]
    except KeyError as exc:
        raise ValueError(f"Invalid window: {window}") from exc


def rollup_seconds(rollup: str) -> int:
    """Resolve a rollup key to seconds. Raises ValueError for unknown rollups."""
    try:
        return ROLLUPS[rollup]
    except KeyError as exc:
        raise ValueError(f"Invalid rollup: {rollup}") from exc


@contextmanager
def _connect(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_max_columns(conn: sqlite3.Connection) -> None:
    """Add *_max columns to existing databases that predate avg+max storage."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(system_samples)")}
    for column in _MAX_COLUMNS:
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE system_samples ADD COLUMN {column} REAL")


def _drop_mb_service_samples(conn: sqlite3.Connection) -> None:
    """Drop service_samples when it still stores memory in MiB; the rows can't be rescaled to %."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(service_samples)")}
    if "memory_used_mb" in existing:
        conn.execute("DROP TABLE service_samples")


def ensure_schema(db_path: Path = DB_PATH) -> None:
    """Create the samples table once per database path and migrate max columns."""
    if db_path in _schema_ready:
        return
    with _connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_samples (
                ts REAL NOT NULL PRIMARY KEY,
                temperature_c REAL,
                cpu_percent REAL,
                memory_used_pct REAL,
                disk_used_pct REAL,
                temperature_c_max REAL,
                cpu_percent_max REAL,
                memory_used_pct_max REAL,
                disk_used_pct_max REAL
            )
            """)
        _migrate_max_columns(conn)
        _drop_mb_service_samples(conn)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS service_samples (
                ts REAL NOT NULL,
                service TEXT NOT NULL,
                memory_used_pct REAL,
                cpu_percent REAL,
                PRIMARY KEY (ts, service)
            )
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_samples_service_ts ON service_samples (service, ts)"
        )
    _schema_ready.add(db_path)


def record_sample(sample: SystemSample, db_path: Path = DB_PATH) -> None:
    """Insert or replace one sample row."""
    ensure_schema(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO system_samples
                (ts, temperature_c, cpu_percent, memory_used_pct, disk_used_pct,
                 temperature_c_max, cpu_percent_max, memory_used_pct_max, disk_used_pct_max)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sample.ts,
                sample.temperature_c,
                sample.cpu_percent,
                sample.memory_used_pct,
                sample.disk_used_pct,
                sample.temperature_c_max,
                sample.cpu_percent_max,
                sample.memory_used_pct_max,
                sample.disk_used_pct_max,
            ),
        )


def record_service_samples(samples: list[ServiceSample], db_path: Path = DB_PATH) -> None:
    """Insert or replace one row per service for a single sample timestamp."""
    if not samples:
        return
    ensure_schema(db_path)
    with _connect(db_path) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO service_samples (ts, service, memory_used_pct, cpu_percent)
            VALUES (?, ?, ?, ?)
            """,
            [(s.ts, s.service, s.memory_used_pct, s.cpu_percent) for s in samples],
        )


def prune_old_samples(
    now: float | None = None,
    retention_seconds: int = RETENTION_SECONDS,
    db_path: Path = DB_PATH,
) -> int:
    """Delete samples older than the retention window. Returns rows deleted."""
    ensure_schema(db_path)
    cutoff = (time.time() if now is None else now) - retention_seconds
    with _connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM system_samples WHERE ts < ?", (cutoff,))
        deleted = cursor.rowcount
        conn.execute("DELETE FROM service_samples WHERE ts < ?", (cutoff,))
        return deleted


def sample_from_info(info: SystemInfo, ts: float | None = None) -> SystemSample:
    """Build a SystemSample from a live SystemInfo snapshot."""
    return SystemSample(
        ts=time.time() if ts is None else ts,
        temperature_c=info.temperature_c,
        cpu_percent=info.cpu_percent,
        memory_used_pct=info.memory_used_pct,
        disk_used_pct=info.disk_used_pct,
        temperature_c_max=info.temperature_c,
        cpu_percent_max=info.cpu_percent,
        memory_used_pct_max=info.memory_used_pct,
        disk_used_pct_max=info.disk_used_pct,
    )


def _avg(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 2)


def _max(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(max(present), 2)


def average_ticks(ticks: list[SystemSample], ts: float | None = None) -> SystemSample:
    """Collapse 1Hz ticks into one sample with avg and max per metric."""
    if not ticks:
        raise ValueError("ticks must be non-empty")
    return SystemSample(
        ts=ticks[-1].ts if ts is None else ts,
        temperature_c=_avg([tick.temperature_c for tick in ticks]),
        cpu_percent=_avg([tick.cpu_percent for tick in ticks]),
        memory_used_pct=_avg([tick.memory_used_pct for tick in ticks]),
        disk_used_pct=_avg([tick.disk_used_pct for tick in ticks]),
        temperature_c_max=_max([tick.temperature_c for tick in ticks]),
        cpu_percent_max=_max([tick.cpu_percent for tick in ticks]),
        memory_used_pct_max=_max([tick.memory_used_pct for tick in ticks]),
        disk_used_pct_max=_max([tick.disk_used_pct for tick in ticks]),
    )


def read_metrics_tick(cpu_tracker: CpuDeltaTracker, ts: float | None = None) -> SystemSample:
    """Cheap 1Hz host vitals snapshot (no sleep)."""
    memory = _read_memory()
    disk_used_pct = None
    try:
        usage = shutil.disk_usage("/")
        disk_used_pct = round(usage.used / usage.total * 100, 1)
    except OSError:
        pass
    temperature_c = _read_cpu_temperature()
    cpu_percent = cpu_tracker.read_percent()
    memory_used_pct = memory[2] if memory else None
    return SystemSample(
        ts=time.time() if ts is None else ts,
        temperature_c=temperature_c,
        cpu_percent=cpu_percent,
        memory_used_pct=memory_used_pct,
        disk_used_pct=disk_used_pct,
        temperature_c_max=temperature_c,
        cpu_percent_max=cpu_percent,
        memory_used_pct_max=memory_used_pct,
        disk_used_pct_max=disk_used_pct,
    )


def service_cpu_percent(
    prev_nsec: int,
    cur_nsec: int,
    prev_ts: float,
    cur_ts: float,
    cpu_count: int | None,
) -> float | None:
    """Per-service CPU percent from two cumulative CPUUsageNSec reads, normalized by core count.

    Returns None when the interval is non-positive, the core count is unknown, or the counter
    went backwards (the service restarted and CPUUsageNSec reset to zero).
    """
    wall_delta = cur_ts - prev_ts
    if wall_delta <= 0 or not cpu_count:
        return None
    cpu_delta_nsec = cur_nsec - prev_nsec
    if cpu_delta_nsec < 0:
        return None
    percent = cpu_delta_nsec / (wall_delta * 1e9 * cpu_count) * 100
    return round(max(0.0, min(100.0, percent)), 1)


def service_memory_percent(memory_bytes: int | None, total_memory_bytes: int | None) -> float | None:
    """Per-service MemoryCurrent as a percent of host RAM. None when either value is unavailable."""
    if memory_bytes is None or not total_memory_bytes:
        return None
    return round(memory_bytes / total_memory_bytes * 100, 1)


class ServiceCpuTracker:
    """Turn cumulative CPUUsageNSec readings into a per-service CPU percent across 30s samples."""

    def __init__(self) -> None:
        self._prev: dict[str, tuple[float, int]] = {}  # unit -> (ts, cpu_nsec)

    def read_percent(self, unit: str, cpu_nsec: int | None, ts: float, cpu_count: int | None) -> float | None:
        previous = self._prev.get(unit)
        if cpu_nsec is not None:
            self._prev[unit] = (ts, cpu_nsec)
        if previous is None or cpu_nsec is None:
            return None
        prev_ts, prev_nsec = previous
        return service_cpu_percent(prev_nsec, cpu_nsec, prev_ts, ts, cpu_count)

    def forget(self, live_units: set[str]) -> None:
        """Drop trackers for units that no longer exist so restarts recompute cleanly."""
        for unit in [u for u in self._prev if u not in live_units]:
            del self._prev[unit]


def sample_services(
    units: list[str],
    cpu_tracker: ServiceCpuTracker,
    cpu_count: int | None,
    total_memory_bytes: int | None,
    ts: float | None = None,
) -> list[ServiceSample]:
    """Read per-service memory and CPU for the given units and build one sample each.

    Both values are a percent of the whole host: memory of total RAM, CPU of all cores. CPU
    percent is None on a service's first sample (no prior CPUUsageNSec to diff against).
    """
    ts = time.time() if ts is None else ts
    metrics = read_service_metrics(units)
    cpu_tracker.forget(set(metrics))
    samples: list[ServiceSample] = []
    for unit, (memory_bytes, cpu_nsec) in metrics.items():
        samples.append(
            ServiceSample(
                ts=ts,
                service=unit,
                memory_used_pct=service_memory_percent(memory_bytes, total_memory_bytes),
                cpu_percent=cpu_tracker.read_percent(unit, cpu_nsec, ts, cpu_count),
            )
        )
    return samples


class MetricsAggregator:
    """Accumulate 1Hz ticks and flush one avg+max row every SAMPLE_INTERVAL_SECONDS."""

    def __init__(
        self,
        db_path: Path = DB_PATH,
        ticks_per_sample: int = TICKS_PER_SAMPLE,
    ) -> None:
        self.db_path = db_path
        self.ticks_per_sample = ticks_per_sample
        self._ticks: list[SystemSample] = []

    def add_tick(self, tick: SystemSample) -> SystemSample | None:
        """Buffer a tick; when the window is full, persist avg+max and return it."""
        self._ticks.append(tick)
        if len(self._ticks) < self.ticks_per_sample:
            return None
        aggregated = average_ticks(self._ticks)
        self._ticks.clear()
        record_sample(aggregated, db_path=self.db_path)
        deleted = prune_old_samples(db_path=self.db_path)
        if deleted:
            logger.debug("Pruned %s expired system metric samples", deleted)
        return aggregated


def metrics_sampler_loop(
    db_path: Path = DB_PATH,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    ticks_per_sample: int = TICKS_PER_SAMPLE,
    stop_event: threading.Event | None = None,
) -> None:
    """Poll host vitals at 1Hz and store a 30s avg+max sample. No-op off Linux."""
    if not is_linux():
        logger.info("System metrics sampler idle (not Linux)")
        return

    aggregator = MetricsAggregator(db_path=db_path, ticks_per_sample=ticks_per_sample)
    cpu_tracker = CpuDeltaTracker()
    service_cpu_tracker = ServiceCpuTracker()
    cpu_count = os.cpu_count()
    total_memory_bytes = read_total_memory_bytes()
    logger.info(
        "System metrics sampler started (%ss poll, %ss store)",
        poll_interval_seconds,
        poll_interval_seconds * ticks_per_sample,
    )
    while stop_event is None or not stop_event.is_set():
        try:
            # Per-service metrics ride the same 30s flush: sample them when the host row is stored.
            if aggregator.add_tick(read_metrics_tick(cpu_tracker)) is not None:
                record_service_samples(
                    sample_services(get_services(), service_cpu_tracker, cpu_count, total_memory_bytes),
                    db_path=db_path,
                )
        except Exception:
            logger.exception("Failed to sample system metrics tick")
        if stop_event is None:
            time.sleep(poll_interval_seconds)
        elif stop_event.wait(poll_interval_seconds):
            break


def start_metrics_sampler(db_path: Path = DB_PATH) -> None:
    """Start the 1Hz metrics sampler thread once."""
    global _sampler_started
    with _sampler_lock:
        if _sampler_started:
            return
        thread = threading.Thread(
            target=metrics_sampler_loop,
            kwargs={"db_path": db_path},
            name="system-metrics-sampler",
            daemon=True,
        )
        thread.start()
        _sampler_started = True
        logger.info("System metrics sampler thread started")


def _aggregate_bucket(bucket: list[SystemSample], ts: float) -> SystemSample:
    """Collapse a bucket into one sample (avg of avgs, max of maxes)."""
    return SystemSample(
        ts=ts,
        temperature_c=_avg([s.temperature_c for s in bucket]),
        cpu_percent=_avg([s.cpu_percent for s in bucket]),
        memory_used_pct=_avg([s.memory_used_pct for s in bucket]),
        disk_used_pct=_avg([s.disk_used_pct for s in bucket]),
        temperature_c_max=_max([s.temperature_c_max for s in bucket]),
        cpu_percent_max=_max([s.cpu_percent_max for s in bucket]),
        memory_used_pct_max=_max([s.memory_used_pct_max for s in bucket]),
        disk_used_pct_max=_max([s.disk_used_pct_max for s in bucket]),
    )


# Aggregators collapse a bucket of samples (of type T) into one sample stamped at a given ts.
Aggregator = Callable[[list, float], object]


def _downsample(
    samples: list, max_points: int = MAX_RETURN_POINTS, aggregate: Aggregator = _aggregate_bucket
) -> list:
    """Bucket samples by count so chart payloads stay bounded."""
    if len(samples) <= max_points:
        return samples
    bucket_size = math.ceil(len(samples) / max_points)
    out: list = []
    for i in range(0, len(samples), bucket_size):
        bucket = samples[i : i + bucket_size]
        out.append(aggregate(bucket, bucket[len(bucket) // 2].ts))
    return out


def _rollup_by_time(samples: list, bucket_seconds: int, aggregate: Aggregator = _aggregate_bucket) -> list:
    """Aggregate samples into fixed-width time buckets aligned to the Unix epoch."""
    if len(samples) <= 1 or bucket_seconds <= SAMPLE_INTERVAL_SECONDS:
        return samples

    out: list = []
    bucket: list = []
    bucket_start: float | None = None

    for sample in samples:
        start = math.floor(sample.ts / bucket_seconds) * bucket_seconds
        if bucket_start is None:
            bucket_start = start
        if start != bucket_start:
            out.append(aggregate(bucket, bucket_start + bucket_seconds / 2))
            bucket = []
            bucket_start = start
        bucket.append(sample)

    if bucket and bucket_start is not None:
        out.append(aggregate(bucket, bucket_start + bucket_seconds / 2))
    return out


def _aggregate_service_bucket(bucket: list[ServiceSample], ts: float) -> ServiceSample:
    """Collapse a bucket of one service's samples into a single averaged sample."""
    return ServiceSample(
        ts=ts,
        service=bucket[0].service,
        memory_used_pct=_avg([s.memory_used_pct for s in bucket]),
        cpu_percent=_avg([s.cpu_percent for s in bucket]),
    )


def _row_to_sample(row: tuple) -> SystemSample:
    return SystemSample(
        ts=row[0],
        temperature_c=row[1],
        cpu_percent=row[2],
        memory_used_pct=row[3],
        disk_used_pct=row[4],
        temperature_c_max=row[5],
        cpu_percent_max=row[6],
        memory_used_pct_max=row[7],
        disk_used_pct_max=row[8],
    )


def get_history(
    window: str = DEFAULT_WINDOW,
    rollup: str = DEFAULT_ROLLUP,
    now: float | None = None,
    db_path: Path = DB_PATH,
    max_points: int = MAX_RETURN_POINTS,
) -> list[SystemSample]:
    """Return samples in the requested window, rollup-averaged, then capped for charting."""
    span = window_seconds(window)
    bucket = rollup_seconds(rollup)
    ensure_schema(db_path)
    end = time.time() if now is None else now
    start = end - span
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ts, temperature_c, cpu_percent, memory_used_pct, disk_used_pct,
                   temperature_c_max, cpu_percent_max, memory_used_pct_max, disk_used_pct_max
            FROM system_samples
            WHERE ts >= ? AND ts <= ?
            ORDER BY ts ASC
            """,
            (start, end),
        ).fetchall()
    samples = [_row_to_sample(row) for row in rows]
    return _downsample(_rollup_by_time(samples, bucket), max_points=max_points)


def canned_history(
    window: str = DEFAULT_WINDOW,
    rollup: str = DEFAULT_ROLLUP,
    now: float | None = None,
) -> list[SystemSample]:
    """Synthetic history for off-Pi / dev mode so the chart has something to draw."""
    span = window_seconds(window)
    bucket = rollup_seconds(rollup)
    end = time.time() if now is None else now
    step = max(SAMPLE_INTERVAL_SECONDS, span // 240)
    samples: list[SystemSample] = []
    t = end - span
    while t <= end:
        phase = (t - (end - span)) / span * math.tau
        temp = round(48 + 6 * math.sin(phase), 1)
        cpu = round(18 + 22 * abs(math.sin(phase * 2.1)), 1)
        mem = round(45 + 8 * math.sin(phase * 0.7 + 1), 1)
        disk = round(38 + 1.5 * math.sin(phase * 0.2), 1)
        peak = abs(math.sin(phase * 3.3))
        samples.append(
            SystemSample(
                ts=t,
                temperature_c=temp,
                cpu_percent=cpu,
                memory_used_pct=mem,
                disk_used_pct=disk,
                temperature_c_max=round(temp + 1.5 + 2.5 * peak, 1),
                cpu_percent_max=round(min(100.0, cpu + 8 + 12 * peak), 1),
                memory_used_pct_max=round(min(100.0, mem + 2 + 4 * peak), 1),
                disk_used_pct_max=round(min(100.0, disk + 0.3 + 0.7 * peak), 1),
            )
        )
        t += step
    return _downsample(_rollup_by_time(samples, bucket), max_points=MAX_RETURN_POINTS)


def history_payload(
    window: str = DEFAULT_WINDOW,
    rollup: str = DEFAULT_ROLLUP,
    db_path: Path = DB_PATH,
) -> dict:
    """JSON-ready history payload for the API."""
    window_seconds(window)  # validate before I/O
    rollup_seconds(rollup)
    samples = (
        get_history(window=window, rollup=rollup, db_path=db_path)
        if is_linux()
        else canned_history(window=window, rollup=rollup)
    )
    return {"window": window, "rollup": rollup, "samples": [asdict(sample) for sample in samples]}


def get_service_history(
    service: str,
    window: str = DEFAULT_WINDOW,
    rollup: str = DEFAULT_ROLLUP,
    now: float | None = None,
    db_path: Path = DB_PATH,
    max_points: int = MAX_RETURN_POINTS,
) -> list[ServiceSample]:
    """Return one service's samples in the requested window, rollup-averaged then capped."""
    span = window_seconds(window)
    bucket = rollup_seconds(rollup)
    ensure_schema(db_path)
    end = time.time() if now is None else now
    start = end - span
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ts, memory_used_pct, cpu_percent
            FROM service_samples
            WHERE service = ? AND ts >= ? AND ts <= ?
            ORDER BY ts ASC
            """,
            (service, start, end),
        ).fetchall()
    samples = [
        ServiceSample(ts=row[0], service=service, memory_used_pct=row[1], cpu_percent=row[2]) for row in rows
    ]
    rolled = _rollup_by_time(samples, bucket, aggregate=_aggregate_service_bucket)
    return _downsample(rolled, max_points=max_points, aggregate=_aggregate_service_bucket)


def canned_service_history(
    service: str,
    window: str = DEFAULT_WINDOW,
    rollup: str = DEFAULT_ROLLUP,
    now: float | None = None,
) -> list[ServiceSample]:
    """Synthetic per-service history for off-Pi / dev mode so the chart has something to draw."""
    span = window_seconds(window)
    bucket = rollup_seconds(rollup)
    end = time.time() if now is None else now
    step = max(SAMPLE_INTERVAL_SECONDS, span // 240)
    # Vary the shape per service so different services look distinct in dev.
    seed = sum(ord(c) for c in service)
    base_mem = 2 + seed % 12
    samples: list[ServiceSample] = []
    t = end - span
    while t <= end:
        phase = (t - (end - span)) / span * math.tau
        cpu = round(6 + 14 * abs(math.sin(phase * 2.3 + seed)), 1)
        mem = round(max(0.1, base_mem + 1.5 * math.sin(phase * 0.8 + seed)), 1)
        samples.append(ServiceSample(ts=t, service=service, memory_used_pct=mem, cpu_percent=cpu))
        t += step
    rolled = _rollup_by_time(samples, bucket, aggregate=_aggregate_service_bucket)
    return _downsample(rolled, aggregate=_aggregate_service_bucket)


def service_history_payload(
    service: str,
    window: str = DEFAULT_WINDOW,
    rollup: str = DEFAULT_ROLLUP,
    db_path: Path = DB_PATH,
) -> dict:
    """JSON-ready per-service history payload for the API."""
    window_seconds(window)  # validate before I/O
    rollup_seconds(rollup)
    samples = (
        get_service_history(service, window=window, rollup=rollup, db_path=db_path)
        if is_linux()
        else canned_service_history(service, window=window, rollup=rollup)
    )
    return {
        "service": service,
        "window": window,
        "rollup": rollup,
        "samples": [asdict(sample) for sample in samples],
    }


def temperature_window_stats(
    window: str = "24h",
    now: float | None = None,
    db_path: Path = DB_PATH,
) -> tuple[float | None, float | None]:
    """Return (avg, max) temperature over the window, or (None, None) when empty."""
    span = window_seconds(window)
    end = time.time() if now is None else now
    start = end - span
    if not is_linux():
        samples = canned_history(window=window, now=end)
        avg = _avg([s.temperature_c for s in samples])
        peak = _max(
            [s.temperature_c_max if s.temperature_c_max is not None else s.temperature_c for s in samples]
        )
        return (
            None if avg is None else round(avg, 1),
            None if peak is None else round(peak, 1),
        )

    ensure_schema(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT AVG(temperature_c),
                   MAX(COALESCE(temperature_c_max, temperature_c))
            FROM system_samples
            WHERE ts >= ? AND ts <= ?
              AND COALESCE(temperature_c_max, temperature_c) IS NOT NULL
            """,
            (start, end),
        ).fetchone()
    if row is None or (row[0] is None and row[1] is None):
        return None, None
    avg = None if row[0] is None else round(float(row[0]), 1)
    peak = None if row[1] is None else round(float(row[1]), 1)
    return avg, peak


def average_temperature(
    window: str = "24h",
    now: float | None = None,
    db_path: Path = DB_PATH,
) -> float | None:
    """Mean temperature over the window, or None when no samples are available."""
    avg, _ = temperature_window_stats(window=window, now=now, db_path=db_path)
    return avg
