"""Sample host vitals and serve windowed history for the dashboard chart.

On Linux a background sampler polls at 1Hz, then stores one tick per 30s window with
both the average and the max of those ticks for each metric.
"""

import atexit
import logging
import math
import os
import shutil
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

from src.metrics_store import RETENTION_SECONDS, MetricsStore, ServiceSample, SystemSample, make_tick
from src.services import (
    CLAUDE_PROCESS_NAME,
    JOURNALD_CGROUP,
    SYSTEM_SLICE_CGROUP,
    USER_SLICE_CGROUP,
    SystemInfo,
    _read_cpu_temperature,
    _read_memory,
    _read_proc_stat_busy_total,
    cpu_percent_from_delta,
    get_services,
    is_linux,
    outermost_directories,
    parse_service_name,
    read_cgroup_written_bytes,
    read_directory_size_kb,
    read_journal_entries,
    read_process_written_bytes,
    read_root_sectors_written,
    read_service_metrics,
    read_total_memory_bytes,
    read_working_directories,
)

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "system_metrics.db"
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
WRITES_WINDOW = "24h"
PROJECT_SIZES_TTL_SECONDS = 30 * 60
WINDOW_ROLLUP_SECONDS: dict[str, int] = {
    "1h": 30,
    "6h": 2 * 60,
    "24h": 30 * 60,
    "7d": 60 * 60,
}

_sampler_started = False
_sampler_lock = threading.Lock()
_default_store: MetricsStore | None = None
_default_store_lock = threading.Lock()
_project_sizes_cache: tuple[float, dict[str, float]] | None = None
_project_sizes_lock = threading.Lock()


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


def default_store() -> MetricsStore:
    """The process-wide store over DB_PATH, flushed at interpreter exit."""
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = MetricsStore(DB_PATH)
            atexit.register(_default_store.flush)
        return _default_store


def window_seconds(window: str) -> int:
    """Resolve a window key to seconds. Raises ValueError for unknown windows."""
    try:
        return WINDOWS[window]
    except KeyError as exc:
        raise ValueError(f"Invalid window: {window}") from exc


def rollup_seconds(window: str) -> int:
    """Bucket width a window's samples are averaged into. Raises ValueError for unknown windows."""
    window_seconds(window)
    return WINDOW_ROLLUP_SECONDS[window]


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


class CounterDeltaTracker:
    """Turn cumulative counter readings into per-interval deltas.

    A key's first reading has nothing to diff against, and a counter that went backwards was reset
    (a service restarted, or the host rebooted); both give None rather than a misleading delta.
    """

    def __init__(self) -> None:
        self._prev: dict[str, int] = {}

    def delta(self, key: str, value: int | None) -> int | None:
        previous = self._prev.get(key)
        if value is None:
            self._prev.pop(key, None)
            return None
        self._prev[key] = value
        if previous is None or value < previous:
            return None
        return value - previous

    def forget(self, live_keys: set[str]) -> None:
        for key in [k for k in self._prev if k not in live_keys]:
            del self._prev[key]


class ProcessWriteTracker:
    """Per-interval writes summed over a changing set of processes.

    A process first seen after the first reading counts its whole counter, since it started within
    the interval. Writes a process makes between the last reading and its exit are lost.
    """

    def __init__(self) -> None:
        self._prev: dict[int, int] | None = None

    def delta(self, written_by_pid: dict[int, int]) -> int | None:
        previous, self._prev = self._prev, written_by_pid
        if previous is None:
            return None
        return sum(
            value - previous.get(pid, 0)
            for pid, value in written_by_pid.items()
            if value >= previous.get(pid, 0)
        )


def _kb(byte_count: int | None) -> float | None:
    return None if byte_count is None else round(byte_count / 1024, 1)


def sample_services(
    units: list[str],
    cpu_tracker: ServiceCpuTracker,
    write_tracker: CounterDeltaTracker,
    cpu_count: int | None,
    total_memory_bytes: int | None,
    ts: float | None = None,
) -> list[ServiceSample]:
    """Read per-service memory, CPU and disk writes for the given units and build one sample each.

    Memory and CPU are a percent of the whole host: memory of total RAM, CPU of all cores. CPU
    percent and written KB are None on a service's first sample (no prior counter to diff against).
    """
    ts = time.time() if ts is None else ts
    metrics = read_service_metrics(units)
    cpu_tracker.forget(set(metrics))
    write_tracker.forget(set(metrics))
    samples: list[ServiceSample] = []
    for unit, counters in metrics.items():
        samples.append(
            ServiceSample(
                ts=ts,
                service=unit,
                memory_used_pct=service_memory_percent(counters.memory_bytes, total_memory_bytes),
                cpu_percent=cpu_tracker.read_percent(unit, counters.cpu_nsec, ts, cpu_count),
                written_kb=_kb(write_tracker.delta(unit, counters.written_bytes)),
            )
        )
    return samples


class JournalEntryTracker:
    """Count journal entries per unit between consecutive samples, resuming from the last cursor."""

    def __init__(self) -> None:
        self._cursor: str | None = None

    def read(self, ts: float) -> dict[str, int]:
        """Entries per unit since the last read. A failed read drops the cursor so the next one starts fresh."""
        result = read_journal_entries(self._cursor, since_ts=ts - SAMPLE_INTERVAL_SECONDS)
        if result is None:
            self._cursor = None
            return {}
        counts, cursor = result
        if cursor is not None:
            self._cursor = cursor
        return counts


def attribute_log_writes(
    services: list[ServiceSample], entries: dict[str, int], journald_kb: float | None, ts: float
) -> list[ServiceSample]:
    """Split journald's written KB across project units in proportion to the entries each logged.

    Project units that logged but aren't in ``services`` (e.g. a oneshot that already exited) get a
    sample of their own with only the log share set. Non-project entries keep the rest unattributed.
    """
    total_entries = sum(entries.values())
    if journald_kb is None or not total_entries:
        return services
    shares = {
        unit: round(journald_kb * count / total_entries, 1)
        for unit, count in entries.items()
        if unit.startswith("projects_")
    }
    attributed = [replace(s, log_written_kb=shares.pop(s.service, 0.0)) for s in services]
    return attributed + [
        ServiceSample(ts, unit, None, None, log_written_kb=kb) for unit, kb in sorted(shares.items())
    ]


class MetricsAggregator:
    """Accumulate 1Hz ticks and emit one avg+max sample every SAMPLE_INTERVAL_SECONDS."""

    def __init__(self, ticks_per_sample: int = TICKS_PER_SAMPLE) -> None:
        self.ticks_per_sample = ticks_per_sample
        self._ticks: list[SystemSample] = []

    def add_tick(self, tick: SystemSample) -> SystemSample | None:
        """Buffer a tick; when the window is full, return its avg+max sample."""
        self._ticks.append(tick)
        if len(self._ticks) < self.ticks_per_sample:
            return None
        aggregated = average_ticks(self._ticks)
        self._ticks.clear()
        return aggregated


def metrics_sampler_loop(
    store: MetricsStore,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    ticks_per_sample: int = TICKS_PER_SAMPLE,
    stop_event: threading.Event | None = None,
) -> None:
    """Poll host vitals at 1Hz and store a 30s avg+max sample. No-op off Linux."""
    if not is_linux():
        logger.info("System metrics sampler idle (not Linux)")
        return

    aggregator = MetricsAggregator(ticks_per_sample=ticks_per_sample)
    cpu_tracker = CpuDeltaTracker()
    service_cpu_tracker = ServiceCpuTracker()
    service_write_tracker = CounterDeltaTracker()
    host_write_tracker = CounterDeltaTracker()
    claude_write_tracker = ProcessWriteTracker()
    journal_tracker = JournalEntryTracker()
    cpu_count = os.cpu_count()
    total_memory_bytes = read_total_memory_bytes()
    logger.info(
        "System metrics sampler started (%ss poll, %ss store)",
        poll_interval_seconds,
        poll_interval_seconds * ticks_per_sample,
    )
    while stop_event is None or not stop_event.is_set():
        try:
            sample = aggregator.add_tick(read_metrics_tick(cpu_tracker))
            if sample is not None:
                sectors = host_write_tracker.delta("root", read_root_sectors_written())
                journald_kb = _kb(
                    host_write_tracker.delta("journald", read_cgroup_written_bytes(JOURNALD_CGROUP))
                )
                sample = replace(
                    sample,
                    disk_written_kb=_kb(None if sectors is None else sectors * 512),
                    journald_written_kb=journald_kb,
                    system_slice_written_kb=_kb(
                        host_write_tracker.delta("system", read_cgroup_written_bytes(SYSTEM_SLICE_CGROUP))
                    ),
                    user_slice_written_kb=_kb(
                        host_write_tracker.delta("user", read_cgroup_written_bytes(USER_SLICE_CGROUP))
                    ),
                    claude_written_kb=_kb(
                        claude_write_tracker.delta(read_process_written_bytes(CLAUDE_PROCESS_NAME))
                    ),
                )
                services = sample_services(
                    get_services(),
                    service_cpu_tracker,
                    service_write_tracker,
                    cpu_count,
                    total_memory_bytes,
                    ts=sample.ts,
                )
                services = attribute_log_writes(
                    services, journal_tracker.read(sample.ts), journald_kb, sample.ts
                )
                store.append(make_tick(sample, services))
        except Exception:
            logger.exception("Failed to sample system metrics tick")
        if stop_event is None:
            time.sleep(poll_interval_seconds)
        elif stop_event.wait(poll_interval_seconds):
            break


def start_metrics_sampler(store: MetricsStore | None = None) -> None:
    """Start the 1Hz metrics sampler thread once."""
    global _sampler_started
    with _sampler_lock:
        if _sampler_started:
            return
        thread = threading.Thread(
            target=metrics_sampler_loop,
            kwargs={"store": store or default_store()},
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
        disk_written_kb=_avg([s.disk_written_kb for s in bucket]),
    )


def _downsample[T](
    samples: list[T],
    max_points: int = MAX_RETURN_POINTS,
    aggregate: Callable[[list[T], float], T] = _aggregate_bucket,
) -> list[T]:
    """Bucket samples by count so chart payloads stay bounded."""
    if len(samples) <= max_points:
        return samples
    bucket_size = math.ceil(len(samples) / max_points)
    out: list[T] = []
    for i in range(0, len(samples), bucket_size):
        bucket = samples[i : i + bucket_size]
        out.append(aggregate(bucket, bucket[len(bucket) // 2].ts))
    return out


def _rollup_by_time[T](
    samples: list[T],
    bucket_seconds: int,
    aggregate: Callable[[list[T], float], T] = _aggregate_bucket,
) -> list[T]:
    """Aggregate samples into fixed-width time buckets aligned to the Unix epoch."""
    if len(samples) <= 1 or bucket_seconds <= SAMPLE_INTERVAL_SECONDS:
        return samples

    out: list[T] = []
    bucket: list[T] = []
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


def get_history(
    window: str = DEFAULT_WINDOW,
    now: float | None = None,
    store: MetricsStore | None = None,
    max_points: int = MAX_RETURN_POINTS,
) -> list[SystemSample]:
    """Return samples in the requested window, rollup-averaged, then capped for charting."""
    span = window_seconds(window)
    bucket = rollup_seconds(window)
    end = time.time() if now is None else now
    samples = (store or default_store()).system_samples(end - span, end)
    return _downsample(_rollup_by_time(samples, bucket), max_points=max_points)


def canned_history(
    window: str = DEFAULT_WINDOW,
    now: float | None = None,
) -> list[SystemSample]:
    """Synthetic history for off-Pi / dev mode so the chart has something to draw."""
    span = window_seconds(window)
    bucket = rollup_seconds(window)
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
                disk_written_kb=round(600 + 1400 * peak, 1),
            )
        )
        t += step
    return _downsample(_rollup_by_time(samples, bucket), max_points=MAX_RETURN_POINTS)


def history_payload(
    window: str = DEFAULT_WINDOW,
    store: MetricsStore | None = None,
) -> dict:
    """JSON-ready history payload for the API."""
    window_seconds(window)  # validate before I/O
    samples = get_history(window=window, store=store) if is_linux() else canned_history(window=window)
    return {
        "window": window,
        "samples": [
            {**asdict(sample), "disk_write_mb_min": _write_rate_mb_min(sample)} for sample in samples
        ],
    }


def _write_rate_mb_min(sample: SystemSample) -> float | None:
    if sample.disk_written_kb is None:
        return None
    return round(sample.disk_written_kb / 1024 * 60 / SAMPLE_INTERVAL_SECONDS, 2)


def get_service_history(
    service: str,
    window: str = DEFAULT_WINDOW,
    now: float | None = None,
    store: MetricsStore | None = None,
    max_points: int = MAX_RETURN_POINTS,
) -> list[ServiceSample]:
    """Return one service's samples in the requested window, rollup-averaged then capped."""
    span = window_seconds(window)
    bucket = rollup_seconds(window)
    end = time.time() if now is None else now
    samples = (store or default_store()).service_samples(service, end - span, end)
    rolled = _rollup_by_time(samples, bucket, aggregate=_aggregate_service_bucket)
    return _downsample(rolled, max_points=max_points, aggregate=_aggregate_service_bucket)


def get_latest_service_samples(
    services: list[str], store: MetricsStore | None = None
) -> dict[str, ServiceSample]:
    """Each requested service's reading from the most recent tick (for the sidebar rows)."""
    if not services:
        return {}
    tick = (store or default_store()).latest_tick()
    if tick is None:
        return {}
    return {
        service: ServiceSample(tick.ts, service, *tick.services[service])
        for service in services
        if service in tick.services
    }


def _total_memory_mb() -> float:
    """Host RAM in MB, or 0.0 when /proc/meminfo is unreadable."""
    return (read_total_memory_bytes() or 0) / (1024 * 1024)


def _memory_mb(memory_used_pct: float | None, total_mb: float) -> int | None:
    """Convert a percent-of-host-RAM reading to MB. None when either input is unavailable."""
    if memory_used_pct is None or not total_mb:
        return None
    return round(memory_used_pct / 100 * total_mb)


def latest_service_samples_payload(services: list[str], store: MetricsStore | None = None) -> dict[str, dict]:
    """JSON-ready mapping of service name -> latest CPU/memory reading, for the sidebar rows."""
    samples = get_latest_service_samples(services, store=store)
    total_mb = _total_memory_mb()
    return {
        service: {
            "cpu_percent": sample.cpu_percent,
            "memory_used_pct": sample.memory_used_pct,
            "memory_used_mb": _memory_mb(sample.memory_used_pct, total_mb),
        }
        for service, sample in samples.items()
    }


def canned_service_history(
    service: str,
    window: str = DEFAULT_WINDOW,
    now: float | None = None,
) -> list[ServiceSample]:
    """Synthetic per-service history for off-Pi / dev mode so the chart has something to draw."""
    span = window_seconds(window)
    bucket = rollup_seconds(window)
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
    store: MetricsStore | None = None,
) -> dict:
    """JSON-ready per-service history payload for the API."""
    window_seconds(window)  # validate before I/O
    samples = (
        get_service_history(service, window=window, store=store)
        if is_linux()
        else canned_service_history(service, window=window)
    )
    total_mb = _total_memory_mb()
    return {
        "service": service,
        "window": window,
        "samples": [
            {**asdict(sample), "memory_used_mb": _memory_mb(sample.memory_used_pct, total_mb)}
            for sample in samples
        ],
    }


def _writes_summary(total_kb: float, first_ts: float | None, end: float) -> dict:
    """JSON-ready written-MB total plus how many hours of the window it actually covers."""
    if first_ts is None:
        return {"disk_written_mb": None, "disk_written_hours": None}
    covered = min(window_seconds(WRITES_WINDOW), end - first_ts + SAMPLE_INTERVAL_SECONDS)
    return {"disk_written_mb": round(total_kb / 1024, 1), "disk_written_hours": round(covered / 3600, 2)}


def host_writes_payload(now: float | None = None, store: MetricsStore | None = None) -> dict:
    """Root-disk writes over the last 24h, for the dashboard tile."""
    end = time.time() if now is None else now
    if not is_linux():
        return {"disk_written_mb": 3480.0, "disk_written_hours": 24.0}
    total_kb, first_ts = (store or default_store()).host_written_kb(end - window_seconds(WRITES_WINDOW), end)
    return _writes_summary(total_kb, first_ts, end)


def _canned_written_mb(service: str) -> float:
    """Stable fake 24h write total per unit for off-Pi dev mode."""
    return float(sum(ord(c) for c in service) % 400)


def _canned_size_kb(service: str) -> float:
    """Stable fake directory size per unit for off-Pi dev mode."""
    return float(sum(ord(c) for c in service) % 900 * 4096)


def service_writes_payload(service: str, now: float | None = None, store: MetricsStore | None = None) -> dict:
    """One service's disk writes over the last 24h, for its detail-view tile."""
    end = time.time() if now is None else now
    if not is_linux():
        return {"disk_written_mb": _canned_written_mb(service), "disk_written_hours": 24.0}
    total_kb, first_ts = (store or default_store()).service_written_kb(
        service, end - window_seconds(WRITES_WINDOW), end
    )
    return _writes_summary(total_kb, first_ts, end)


def _sum_by_project(by_service: dict[str, float | None]) -> dict[str, float]:
    by_project: defaultdict[str, float] = defaultdict(float)
    for service, value in by_service.items():
        if value is not None:
            by_project[parse_service_name(service)[0]] += value
    return by_project


def _latest_service_samples_for_breakdown(store: MetricsStore | None) -> list[ServiceSample]:
    services = get_services()
    if is_linux():
        return list(get_latest_service_samples(services, store=store).values())
    return [canned_service_history(service, window="1h")[-1] for service in services]


def _cpu_percent_by_project(now: float, store: MetricsStore | None) -> dict[str, float]:
    samples = _latest_service_samples_for_breakdown(store)
    return _sum_by_project({sample.service: sample.cpu_percent for sample in samples})


def _memory_mb_by_project(now: float, store: MetricsStore | None) -> dict[str, float]:
    samples = _latest_service_samples_for_breakdown(store)
    memory_pct = _sum_by_project({sample.service: sample.memory_used_pct for sample in samples})
    total_mb = _total_memory_mb()
    return {project: pct / 100 * total_mb for project, pct in memory_pct.items()}


def _written_mb_split_by_project(
    now: float, store: MetricsStore | None
) -> tuple[dict[str, float], dict[str, float]]:
    """MB each project wrote over the window: (its own writes, journald writes for its logs)."""
    if is_linux():
        start = now - window_seconds(WRITES_WINDOW)
        store = store or default_store()
        direct_kb = store.written_kb_by_service(start, now)
        log_kb = store.log_written_kb_by_service(start, now)
    else:
        direct_kb = {unit: _canned_written_mb(unit) * 1024 for unit in get_services()}
        log_kb = {unit: _canned_written_mb(unit) * 512 for unit in get_services()}

    def to_mb(by_unit: dict[str, float]) -> dict[str, float]:
        return {project: kb / 1024 for project, kb in _sum_by_project(by_unit).items()}

    return to_mb(direct_kb), to_mb(log_kb)


def _written_mb_outside_projects(now: float, store: MetricsStore | None) -> dict[str, float]:
    """Writes no project accounts for, by source. The subtracted rows are clamped at zero.

    The root-disk counter, the cgroup counters and the per-process Claude Code counter measure at
    different layers, so the subtractions are estimates rather than exact remainders.
    """
    if is_linux():
        totals_kb = (store or default_store()).written_kb_by_source(now - window_seconds(WRITES_WINDOW), now)
        disk, journald, system, user, claude, projects, project_logs = (
            totals_kb[key] / 1024
            for key in (
                "disk_written_kb",
                "journald_written_kb",
                "system_slice_written_kb",
                "user_slice_written_kb",
                "claude_written_kb",
                "services_written_kb",
                "services_log_written_kb",
            )
        )
    else:
        disk, journald, system, user, claude, projects, project_logs = (
            3480.0,
            900.0,
            2400.0,
            80.0,
            60.0,
            900.0,
            450.0,
        )
    return {
        "System logs (non-project)": max(0.0, journald - project_logs),
        "Other system services": max(0.0, system - journald - projects),
        "Claude Code": min(claude, user),
        "User sessions (excl. Claude Code)": max(0.0, user - claude),
        "Kernel / filesystem": max(0.0, disk - system - user),
    }


def writes_breakdown_payload(now: float | None = None, store: MetricsStore | None = None) -> dict:
    """Project writes split into direct and log writes, plus the writes outside any project."""
    end = time.time() if now is None else now
    direct, logs = _written_mb_split_by_project(end, store)
    projects = {
        project: direct.get(project, 0.0) + logs.get(project, 0.0) for project in direct.keys() | logs.keys()
    }
    ranked = sorted(projects.items(), key=lambda item: (-item[1], item[0]))
    other = sorted(_written_mb_outside_projects(end, store).items(), key=lambda item: -item[1])
    return {
        "metric": "writes",
        "total": round(sum(projects.values()), 1),
        "projects": [
            {
                "project": project,
                "value": round(value, 1),
                "direct": round(direct.get(project, 0.0), 1),
                "logs": round(logs.get(project, 0.0), 1),
            }
            for project, value in ranked
        ],
        "other_total": round(sum(value for _, value in other), 1),
        "other": [{"name": name, "value": round(value, 1)} for name, value in other],
    }


def _measure_project_sizes_kb() -> dict[str, float]:
    if not is_linux():
        return _sum_by_project({unit: _canned_size_kb(unit) for unit in get_services()})
    directories: defaultdict[str, set[Path]] = defaultdict(set)
    for unit, directory in read_working_directories(get_services()).items():
        directories[parse_service_name(unit)[0]].add(directory)
    sizes: dict[str, float] = {}
    for project, project_directories in directories.items():
        measured_kb = [read_directory_size_kb(d) for d in outermost_directories(project_directories)]
        sizes[project] = float(sum(kb for kb in measured_kb if kb is not None))
    return sizes


def project_sizes_kb() -> dict[str, float]:
    """On-disk size of each project's working directories, re-measured at most every PROJECT_SIZES_TTL_SECONDS."""
    global _project_sizes_cache
    with _project_sizes_lock:
        measured_at = time.monotonic()
        if _project_sizes_cache is None or measured_at - _project_sizes_cache[0] > PROJECT_SIZES_TTL_SECONDS:
            _project_sizes_cache = (measured_at, _measure_project_sizes_kb())
        return _project_sizes_cache[1]


def _disk_mb_by_project(now: float, store: MetricsStore | None) -> dict[str, float]:
    return {project: kb / 1024 for project, kb in project_sizes_kb().items()}


BREAKDOWNS: dict[str, Callable[[float, MetricsStore | None], dict[str, float]]] = {
    "cpu": _cpu_percent_by_project,
    "memory": _memory_mb_by_project,
    "disk": _disk_mb_by_project,
}
BREAKDOWN_METRICS = (*BREAKDOWNS, "writes")


def project_breakdown_payload(
    metric: str, now: float | None = None, store: MetricsStore | None = None
) -> dict:
    """One metric summed per project, largest first, plus the total across projects.

    cpu is percent of the host and memory is MB, both from the latest tick; disk is the MB the
    project's working directories take up; writes is MB written over the last 24h, shaped by
    writes_breakdown_payload.

    Raises:
        KeyError: metric is not one of BREAKDOWN_METRICS.
    """
    if metric == "writes":
        return writes_breakdown_payload(now, store)
    by_project = BREAKDOWNS[metric](time.time() if now is None else now, store)
    ranked = sorted(by_project.items(), key=lambda item: (-item[1], item[0]))
    return {
        "metric": metric,
        "total": round(sum(by_project.values()), 1),
        "projects": [{"project": project, "value": round(value, 1)} for project, value in ranked],
    }


def latest_service_sample_payload(service: str, store: MetricsStore | None = None) -> dict:
    """JSON-ready payload of one service's most recent CPU/memory reading."""
    if is_linux():
        sample = get_latest_service_samples([service], store=store).get(service)
    else:
        history = canned_service_history(service, window="1h")
        sample = history[-1] if history else None
    total_mb = _total_memory_mb()
    memory_used_pct = sample.memory_used_pct if sample else None
    return {
        "service": service,
        "cpu_percent": sample.cpu_percent if sample else None,
        "memory_used_pct": memory_used_pct,
        "memory_used_mb": _memory_mb(memory_used_pct, total_mb),
        "memory_total_mb": round(total_mb) if total_mb else None,
        **service_writes_payload(service, store=store),
    }


def temperature_window_stats(
    window: str = "24h",
    now: float | None = None,
    store: MetricsStore | None = None,
) -> tuple[float | None, float | None]:
    """Return (avg, max) temperature over the window, or (None, None) when empty."""
    span = window_seconds(window)
    end = time.time() if now is None else now
    start = end - span
    samples = (
        (store or default_store()).system_samples(start, end)
        if is_linux()
        else canned_history(window=window, now=end)
    )
    avg = _avg([s.temperature_c for s in samples])
    peak = _max(
        [s.temperature_c_max if s.temperature_c_max is not None else s.temperature_c for s in samples]
    )
    return (
        None if avg is None else round(avg, 1),
        None if peak is None else round(peak, 1),
    )
