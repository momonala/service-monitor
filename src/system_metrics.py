"""Persist host vitals as a time series and serve windowed history for the dashboard chart.

On Linux a background sampler polls at 1Hz, then stores one row per 30s window with
both the average and the max of those ticks for each metric.
"""

from __future__ import annotations

import logging
import math
import shutil
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from src.services import (
    SystemInfo,
    _read_cpu_temperature,
    _read_memory,
    _read_proc_stat_busy_total,
    cpu_percent_from_delta,
    is_linux,
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
        return cursor.rowcount


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
            logger.info("Pruned %s expired system metric samples", deleted)
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
    logger.info(
        "System metrics sampler started (%ss poll, %ss store)",
        poll_interval_seconds,
        poll_interval_seconds * ticks_per_sample,
    )
    while stop_event is None or not stop_event.is_set():
        try:
            aggregator.add_tick(read_metrics_tick(cpu_tracker))
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


def _downsample(samples: list[SystemSample], max_points: int = MAX_RETURN_POINTS) -> list[SystemSample]:
    """Bucket samples so chart payloads stay bounded (avg of avgs, max of maxes)."""
    if len(samples) <= max_points:
        return samples
    bucket_size = math.ceil(len(samples) / max_points)
    out: list[SystemSample] = []
    for i in range(0, len(samples), bucket_size):
        bucket = samples[i : i + bucket_size]
        out.append(_aggregate_bucket(bucket, bucket[len(bucket) // 2].ts))
    return out


def _rollup_by_time(samples: list[SystemSample], bucket_seconds: int) -> list[SystemSample]:
    """Aggregate samples into fixed-width time buckets aligned to the Unix epoch."""
    if len(samples) <= 1 or bucket_seconds <= SAMPLE_INTERVAL_SECONDS:
        return samples

    out: list[SystemSample] = []
    bucket: list[SystemSample] = []
    bucket_start: float | None = None

    for sample in samples:
        start = math.floor(sample.ts / bucket_seconds) * bucket_seconds
        if bucket_start is None:
            bucket_start = start
        if start != bucket_start:
            out.append(_aggregate_bucket(bucket, bucket_start + bucket_seconds / 2))
            bucket = []
            bucket_start = start
        bucket.append(sample)

    if bucket and bucket_start is not None:
        out.append(_aggregate_bucket(bucket, bucket_start + bucket_seconds / 2))
    return out


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
