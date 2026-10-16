"""Tests for system metrics persistence and history API helpers."""

import time
from pathlib import Path

import pytest

from src.metrics_store import MetricsStore, make_tick
from src.services import ServiceCounters, SystemInfo
from src.system_metrics import (
    MAX_RETURN_POINTS,
    CounterDeltaTracker,
    MetricsAggregator,
    ServiceCpuTracker,
    ServiceSample,
    SystemSample,
    average_ticks,
    canned_history,
    get_history,
    get_latest_service_samples,
    get_service_history,
    history_payload,
    host_writes_payload,
    latest_service_samples_payload,
    sample_from_info,
    sample_services,
    service_cpu_percent,
    service_history_payload,
    service_writes_payload,
    temperature_window_stats,
    writes_by_project_payload,
)


@pytest.fixture
def metrics_store(tmp_path: Path) -> MetricsStore:
    store = MetricsStore(tmp_path / "system_metrics.db", flush_interval=0, clock=lambda: 1_700_000_000.0)
    yield store
    store.close()


def _sample(
    ts: float,
    temp: float = 50.0,
    cpu: float = 10.0,
    mem: float = 40.0,
    disk: float = 30.0,
    temp_max: float | None = None,
    cpu_max: float | None = None,
    mem_max: float | None = None,
    disk_max: float | None = None,
) -> SystemSample:
    return SystemSample(
        ts=ts,
        temperature_c=temp,
        cpu_percent=cpu,
        memory_used_pct=mem,
        disk_used_pct=disk,
        temperature_c_max=temp_max,
        cpu_percent_max=cpu_max,
        memory_used_pct_max=mem_max,
        disk_used_pct_max=disk_max,
    )


def _record(store: MetricsStore, sample: SystemSample) -> None:
    store.append(make_tick(sample, []))


def _record_services(store: MetricsStore, ts: float, readings: dict[str, tuple[float, float]]) -> None:
    services = [ServiceSample(ts, unit, memory, cpu) for unit, (memory, cpu) in readings.items()]
    store.append(make_tick(_sample(ts), services))


def test_record_and_get_history(metrics_store: MetricsStore):
    """Samples in-window are returned oldest-first with avg and max."""
    now = 1_700_000_000.0
    _record(metrics_store, _sample(now - 100, temp=41, temp_max=44))
    _record(metrics_store, _sample(now - 50, temp=42, temp_max=45))
    _record(metrics_store, _sample(now - 10, temp=43, temp_max=46))

    samples = get_history(window="1h", now=now, store=metrics_store)
    assert [s.temperature_c for s in samples] == [41, 42, 43]
    assert [s.temperature_c_max for s in samples] == [44, 45, 46]
    assert samples[0].ts < samples[-1].ts


def test_get_history_rejects_unknown_window(metrics_store: MetricsStore):
    with pytest.raises(ValueError, match="Invalid window"):
        get_history(window="2h", store=metrics_store)


def test_window_seconds_rejects_unknown():
    from src.system_metrics import window_seconds

    with pytest.raises(ValueError, match="Invalid window"):
        window_seconds("2h")


def test_prune_old_samples(metrics_store: MetricsStore):
    now = 1_700_000_000.0
    _record(metrics_store, _sample(now - 8 * 24 * 3600))
    _record(metrics_store, _sample(now - 60))

    metrics_store.prune(now=now)

    samples = get_history(window="7d", now=now, store=metrics_store)
    assert len(samples) == 1


def test_downsample_caps_points(metrics_store: MetricsStore):
    """Large histories are bucket-averaged down to MAX_RETURN_POINTS."""
    now = 1_700_000_000.0
    for i in range(MAX_RETURN_POINTS * 3):
        _record(
            metrics_store,
            _sample(now - (MAX_RETURN_POINTS * 3 - i) * 30, cpu=float(i % 50), cpu_max=float(i % 50) + 5),
        )

    samples = get_history(window="7d", now=now, store=metrics_store, max_points=MAX_RETURN_POINTS)
    assert len(samples) <= MAX_RETURN_POINTS
    assert len(samples) > 0


def test_downsample_uses_max_of_maxes_not_max_of_avgs(metrics_store: MetricsStore):
    """Downsample averages avgs and takes the max of stored maxes."""
    now = 1_700_000_000.0
    _record(metrics_store, _sample(now - 90, temp=40, temp_max=45, cpu=10, cpu_max=20))
    _record(metrics_store, _sample(now - 60, temp=50, temp_max=55, cpu=20, cpu_max=30))
    _record(metrics_store, _sample(now - 30, temp=60, temp_max=70, cpu=30, cpu_max=40))
    _record(metrics_store, _sample(now - 10, temp=70, temp_max=75, cpu=40, cpu_max=50))

    out = get_history(window="1h", now=now, store=metrics_store, max_points=2)
    assert len(out) == 2
    assert out[0].temperature_c == 45.0
    assert out[0].temperature_c_max == 55.0
    assert out[1].temperature_c == 65.0
    assert out[1].temperature_c_max == 75.0
    assert out[0].cpu_percent_max == 30.0
    assert out[1].cpu_percent_max == 50.0


def test_sample_from_info():
    info = SystemInfo(
        hostname="pi",
        local_ip="192.168.1.42",
        uptime="1d",
        boot_time="2026-08-24T11:52:00",
        temperature_c=55.5,
        cpu_percent=12.0,
        cpu_count=4,
        memory_used_pct=44.0,
        memory_used_mb=1000,
        memory_total_mb=2000,
        disk_used_pct=33.0,
        disk_used_gb=10.0,
        disk_total_gb=30.0,
    )
    sample = sample_from_info(info, ts=123.0)
    assert sample == SystemSample(
        ts=123.0,
        temperature_c=55.5,
        cpu_percent=12.0,
        memory_used_pct=44.0,
        disk_used_pct=33.0,
        temperature_c_max=55.5,
        cpu_percent_max=12.0,
        memory_used_pct_max=44.0,
        disk_used_pct_max=33.0,
    )


def test_canned_history_has_points_with_max():
    samples = canned_history(window="1h", now=1_700_000_000.0)
    assert len(samples) > 10
    assert samples[0].ts < samples[-1].ts
    assert samples[0].temperature_c_max is not None
    assert samples[0].temperature_c_max >= samples[0].temperature_c


@pytest.mark.parametrize("window", ["1h", "6h", "24h", "7d"])
def test_history_payload_shape_off_linux(window, monkeypatch):
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: False)
    payload = history_payload(window=window, rollup="2m")
    assert payload["window"] == window
    assert payload["rollup"] == "2m"
    assert isinstance(payload["samples"], list)
    assert payload["samples"]
    assert {
        "ts",
        "temperature_c",
        "cpu_percent",
        "memory_used_pct",
        "disk_used_pct",
        "temperature_c_max",
        "cpu_percent_max",
        "memory_used_pct_max",
        "disk_used_pct_max",
    } <= payload["samples"][0].keys()


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ((0, 100), (40, 200), 40.0),
        ((10, 100), (10, 100), None),  # no progress
        ((0, 100), (100, 200), 100.0),
    ],
)
def test_cpu_percent_from_delta(first, second, expected):
    from src.services import cpu_percent_from_delta

    assert cpu_percent_from_delta(first, second) == expected


def test_average_ticks_computes_avg_and_max():
    ticks = [
        _sample(1.0, temp=40, cpu=10, mem=40, disk=30),
        _sample(2.0, temp=50, cpu=20, mem=50, disk=30),
        _sample(3.0, temp=60, cpu=None, mem=60, disk=30),
    ]
    aggregated = average_ticks(ticks)
    assert aggregated.ts == 3.0
    assert aggregated.temperature_c == 50.0
    assert aggregated.cpu_percent == 15.0
    assert aggregated.memory_used_pct == 50.0
    assert aggregated.disk_used_pct == 30.0
    assert aggregated.temperature_c_max == 60.0
    assert aggregated.cpu_percent_max == 20.0
    assert aggregated.memory_used_pct_max == 60.0
    assert aggregated.disk_used_pct_max == 30.0


def test_metrics_aggregator_emits_avg_and_max():
    now = time.time()
    aggregator = MetricsAggregator(ticks_per_sample=3)
    assert aggregator.add_tick(_sample(now - 2, temp=40)) is None
    assert aggregator.add_tick(_sample(now - 1, temp=50)) is None
    stored = aggregator.add_tick(_sample(now, temp=60))
    assert stored is not None
    assert stored.temperature_c == 50.0
    assert stored.temperature_c_max == 60.0


def test_temperature_window_stats_average_from_db(metrics_store: MetricsStore, monkeypatch):
    """24h average is the mean of in-window samples, ignoring nulls."""
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: True)
    now = 1_700_000_000.0
    _record(metrics_store, _sample(now - 100, temp=40))
    _record(metrics_store, _sample(now - 50, temp=50))
    _record(metrics_store, _sample(now - 10, temp=60))
    _record(
        metrics_store,
        SystemSample(
            ts=now - 5,
            temperature_c=None,
            cpu_percent=10.0,
            memory_used_pct=40.0,
            disk_used_pct=30.0,
        ),
    )

    avg, _ = temperature_window_stats(window="1h", now=now, store=metrics_store)
    assert avg == 50.0


def test_temperature_window_stats_uses_stored_max(metrics_store: MetricsStore, monkeypatch):
    """Window stats return avg of means and max of stored peaks."""
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: True)
    now = 1_700_000_000.0
    _record(metrics_store, _sample(now - 100, temp=40, temp_max=48))
    _record(metrics_store, _sample(now - 50, temp=50, temp_max=55))
    _record(metrics_store, _sample(now - 10, temp=60, temp_max=72))

    avg, peak = temperature_window_stats(window="1h", now=now, store=metrics_store)
    assert avg == 50.0
    assert peak == 72.0


def test_temperature_window_stats_empty_window(metrics_store: MetricsStore, monkeypatch):
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: True)
    assert temperature_window_stats(window="1h", now=1_700_000_000.0, store=metrics_store) == (None, None)


def test_temperature_window_stats_canned_off_linux(monkeypatch):
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: False)
    avg, peak = temperature_window_stats(window="24h", now=1_700_000_000.0)
    assert avg is not None and peak is not None
    assert 40.0 <= avg <= 60.0


def test_rollup_by_time_averages_and_keeps_max():
    from src.system_metrics import _rollup_by_time

    # Four 30s samples spanning two 2-minute epoch buckets.
    base = 1_700_000_000.0
    samples = [
        _sample(base + 0, temp=40, temp_max=42),
        _sample(base + 30, temp=50, temp_max=55),
        _sample(base + 120, temp=60, temp_max=70),
        _sample(base + 150, temp=70, temp_max=75),
    ]
    rolled = _rollup_by_time(samples, bucket_seconds=120)
    assert len(rolled) == 2
    assert rolled[0].temperature_c == 45.0
    assert rolled[0].temperature_c_max == 55.0
    assert rolled[1].temperature_c == 65.0
    assert rolled[1].temperature_c_max == 75.0


def test_rollup_averages_disk_writes_into_a_per_sample_rate():
    from dataclasses import replace

    from src.system_metrics import _rollup_by_time, _write_rate_mb_min

    base = 1_700_000_000.0
    samples = [
        replace(_sample(base + 0, temp=40, temp_max=42), disk_written_kb=1024.0),
        replace(_sample(base + 30, temp=40, temp_max=42), disk_written_kb=3072.0),
    ]
    [rolled] = _rollup_by_time(samples, bucket_seconds=120)
    assert rolled.disk_written_kb == 2048.0
    assert _write_rate_mb_min(rolled) == 4.0


def test_get_history_applies_rollup(metrics_store: MetricsStore, monkeypatch):
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: True)
    now = 1_700_000_120.0
    for offset, temp in ((0, 40), (30, 50), (60, 60), (90, 70)):
        _record(metrics_store, _sample(now - 120 + offset, temp=temp, temp_max=temp + 5))

    samples = get_history(window="1h", rollup="2m", now=now, store=metrics_store)
    assert len(samples) == 2
    assert samples[0].temperature_c == 45.0
    assert samples[0].temperature_c_max == 55.0
    assert samples[1].temperature_c == 65.0
    assert samples[1].temperature_c_max == 75.0


# ---- Per-service metrics ----------------------------------------------------


@pytest.mark.parametrize(
    ("prev_nsec", "cur_nsec", "prev_ts", "cur_ts", "cpu_count", "expected"),
    [
        (0, 15 * 10**9, 100.0, 130.0, 4, 12.5),  # 15s CPU over 30s wall on 4 cores
        (0, 30 * 10**9, 100.0, 130.0, 1, 100.0),  # fully saturating a single core
        (0, 0, 100.0, 130.0, 4, 0.0),  # idle
        (10**9, 0, 100.0, 130.0, 4, None),  # counter reset (service restarted)
        (0, 10**9, 100.0, 100.0, 4, None),  # non-positive interval
        (0, 10**9, 100.0, 130.0, None, None),  # unknown core count
    ],
)
def test_service_cpu_percent(prev_nsec, cur_nsec, prev_ts, cur_ts, cpu_count, expected):
    assert service_cpu_percent(prev_nsec, cur_nsec, prev_ts, cur_ts, cpu_count) == expected


def test_service_cpu_tracker_first_sample_none_then_percent():
    tracker = ServiceCpuTracker()
    assert tracker.read_percent("svc", 10**9, 100.0, 4) is None  # no prior read
    # +30s of CPU over 30s wall on 4 cores -> 25%
    assert tracker.read_percent("svc", 10**9 + 30 * 10**9, 130.0, 4) == 25.0


def test_service_cpu_tracker_forget_drops_dead_units():
    tracker = ServiceCpuTracker()
    tracker.read_percent("a", 10**9, 100.0, 4)
    tracker.read_percent("b", 10**9, 100.0, 4)
    tracker.forget({"a"})
    assert "b" not in tracker._prev
    assert "a" in tracker._prev


def test_record_and_get_service_history(metrics_store: MetricsStore):
    now = 1_700_000_000.0
    _record_services(
        metrics_store, now - 100, {"projects_foo.service": (12.0, 5.0), "projects_bar.service": (6.0, 1.0)}
    )
    _record_services(metrics_store, now - 50, {"projects_foo.service": (13.0, 7.0)})
    foo = get_service_history("projects_foo.service", window="1h", now=now, store=metrics_store)
    assert [s.ts for s in foo] == [now - 100, now - 50]
    assert [s.memory_used_pct for s in foo] == [12.0, 13.0]
    # Other services are not mixed in.
    bar = get_service_history("projects_bar.service", window="1h", now=now, store=metrics_store)
    assert len(bar) == 1


def test_get_latest_service_samples_reads_the_most_recent_tick(metrics_store: MetricsStore):
    now = 1_700_000_000.0
    _record_services(metrics_store, now - 100, {"projects_foo.service": (12.0, 5.0)})
    _record_services(
        metrics_store, now - 50, {"projects_foo.service": (13.0, 7.0), "projects_bar.service": (6.0, 1.0)}
    )
    latest = get_latest_service_samples(
        ["projects_foo.service", "projects_bar.service", "projects_missing.service"], store=metrics_store
    )
    assert latest["projects_foo.service"].ts == now - 50
    assert latest["projects_foo.service"].memory_used_pct == 13.0
    assert latest["projects_bar.service"].cpu_percent == 1.0
    assert "projects_missing.service" not in latest


def test_get_latest_service_samples_empty_list(metrics_store: MetricsStore):
    assert get_latest_service_samples([], store=metrics_store) == {}


def test_latest_service_samples_payload_shape(metrics_store: MetricsStore, monkeypatch):
    from src import system_metrics

    monkeypatch.setattr(system_metrics, "read_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    _record_services(metrics_store, 1_700_000_000.0, {"projects_foo.service": (12.0, 5.0)})
    payload = latest_service_samples_payload(
        ["projects_foo.service", "projects_bar.service"], store=metrics_store
    )
    assert payload == {
        "projects_foo.service": {
            "cpu_percent": 5.0,
            "memory_used_pct": 12.0,
            "memory_used_mb": round(12.0 / 100 * 8 * 1024),
        }
    }


def test_get_service_history_applies_rollup(metrics_store: MetricsStore):
    now = 1_700_000_120.0
    for offset, cpu, mem in ((0, 10, 10), (30, 20, 11), (60, 30, 12), (90, 40, 13)):
        _record_services(
            metrics_store, now - 120 + offset, {"projects_foo.service": (float(mem), float(cpu))}
        )
    samples = get_service_history(
        "projects_foo.service", window="1h", rollup="2m", now=now, store=metrics_store
    )
    assert len(samples) == 2
    assert samples[0].cpu_percent == 15.0
    assert samples[0].memory_used_pct == 10.5
    assert samples[1].cpu_percent == 35.0


def test_prune_removes_service_samples(metrics_store: MetricsStore):
    now = 1_700_000_000.0
    _record_services(metrics_store, now - 8 * 24 * 3600, {"projects_foo.service": (100.0, 5.0)})
    _record_services(metrics_store, now - 60, {"projects_foo.service": (110.0, 6.0)})
    metrics_store.prune(now=now)
    remaining = get_service_history("projects_foo.service", window="7d", now=now, store=metrics_store)
    assert len(remaining) == 1
    assert remaining[0].ts == now - 60


def test_sample_services_reads_memory_and_cpu(monkeypatch):
    metrics = {
        "projects_foo.service": ServiceCounters(200 * 1024 * 1024, 10**9, 5 * 1024),  # 200 MiB, some CPU
        "projects_bar.service": ServiceCounters(),  # unset
    }
    monkeypatch.setattr("src.system_metrics.read_service_metrics", lambda units: metrics)
    tracker = ServiceCpuTracker()
    samples = sample_services(
        list(metrics),
        tracker,
        CounterDeltaTracker(),
        cpu_count=4,
        total_memory_bytes=2000 * 1024 * 1024,
        ts=100.0,
    )
    by_name = {s.service: s for s in samples}
    assert by_name["projects_foo.service"].memory_used_pct == 10.0  # 200 MiB of 2000 MiB
    assert by_name["projects_foo.service"].cpu_percent is None  # first read has no delta
    assert by_name["projects_foo.service"].written_kb is None
    assert by_name["projects_bar.service"].memory_used_pct is None


def test_service_history_payload_off_linux(monkeypatch):
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: False)
    payload = service_history_payload("projects_foo.service", window="6h", rollup="2m")
    assert payload["service"] == "projects_foo.service"
    assert payload["window"] == "6h"
    assert payload["samples"]
    assert {"ts", "service", "memory_used_pct", "cpu_percent"} <= payload["samples"][0].keys()


def test_start_metrics_sampler_with_default_store_does_not_deadlock(tmp_path: Path, monkeypatch):
    import threading

    from src import system_metrics

    monkeypatch.setattr(system_metrics, "DB_PATH", tmp_path / "system_metrics.db")
    monkeypatch.setattr(system_metrics, "_default_store", None)
    monkeypatch.setattr(system_metrics, "_sampler_started", False)
    monkeypatch.setattr(system_metrics, "is_linux", lambda: False)

    starter = threading.Thread(target=system_metrics.start_metrics_sampler, daemon=True)
    starter.start()
    starter.join(timeout=5)

    assert not starter.is_alive()


def test_sample_services_reports_written_kb_between_samples(monkeypatch):
    readings = iter([ServiceCounters(written_bytes=10 * 1024), ServiceCounters(written_bytes=30 * 1024)])
    monkeypatch.setattr(
        "src.system_metrics.read_service_metrics", lambda units: {"projects_foo.service": next(readings)}
    )
    cpu_tracker, write_tracker = ServiceCpuTracker(), CounterDeltaTracker()
    args = (["projects_foo.service"], cpu_tracker, write_tracker, 4, 1024)

    sample_services(*args, ts=100.0)
    (second,) = sample_services(*args, ts=130.0)

    assert second.written_kb == 20.0


@pytest.mark.parametrize(
    "readings, expected",
    [
        ([100, 150], [None, 50]),  # first read has nothing to diff
        ([100, 40, 70], [None, None, 30]),  # counter reset (restart/reboot) -> None, then resumes
        ([100, None, 120], [None, None, None]),  # unreadable forgets the baseline
    ],
)
def test_counter_delta_tracker(readings, expected):
    tracker = CounterDeltaTracker()
    assert [tracker.delta("k", value) for value in readings] == expected


def _record_writes(store: MetricsStore, ts: float, host_kb: float | None, foo_kb: float | None) -> None:
    sample = SystemSample(
        ts=ts,
        temperature_c=50.0,
        cpu_percent=10.0,
        memory_used_pct=40.0,
        disk_used_pct=30.0,
        disk_written_kb=host_kb,
    )
    store.append(make_tick(sample, [ServiceSample(ts, "projects_foo.service", 1.0, 1.0, foo_kb)]))


def test_writes_payloads_sum_the_last_24h(metrics_store: MetricsStore, monkeypatch):
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: True)
    now = 1_700_000_000.0
    _record_writes(metrics_store, now - 25 * 3600, 999_999.0, 999_999.0)  # outside the window
    _record_writes(metrics_store, now - 2 * 3600, None, None)  # first tick after start: no delta
    _record_writes(metrics_store, now - 3600, 1024.0, 512.0)
    _record_writes(metrics_store, now - 60, 2048.0, 512.0)

    host = host_writes_payload(now=now, store=metrics_store)
    foo = service_writes_payload("projects_foo.service", now=now, store=metrics_store)

    assert host == {"disk_written_mb": 3.0, "disk_written_hours": 1.01}  # 1h plus the first tick
    assert foo == {"disk_written_mb": 1.0, "disk_written_hours": 1.01}


def test_writes_payload_with_no_readings_is_null(metrics_store: MetricsStore, monkeypatch):
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: True)
    assert host_writes_payload(now=1_700_000_000.0, store=metrics_store) == {
        "disk_written_mb": None,
        "disk_written_hours": None,
    }


def test_writes_by_project_groups_units_and_sorts_largest_first(metrics_store: MetricsStore, monkeypatch):
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: True)
    now = 1_700_000_000.0
    services = [
        ServiceSample(now - 60, "projects_small.service", 1.0, 1.0, 1024.0),
        ServiceSample(now - 60, "projects_big.service", 1.0, 1.0, 2048.0),
        ServiceSample(now - 60, "projects_big_worker.service", 1.0, 1.0, 1024.0),
    ]
    metrics_store.append(make_tick(_sample(now - 60), services))

    assert writes_by_project_payload(now=now, store=metrics_store) == {
        "projects": [{"project": "big", "written_mb": 3.0}, {"project": "small", "written_mb": 1.0}]
    }
