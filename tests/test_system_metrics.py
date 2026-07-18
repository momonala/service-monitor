"""Tests for system metrics persistence and history API helpers."""

import sqlite3
import time
from pathlib import Path

import pytest

from src.services import SystemInfo
from src.system_metrics import (
    MAX_RETURN_POINTS,
    MetricsAggregator,
    SystemSample,
    average_temperature,
    average_ticks,
    canned_history,
    ensure_schema,
    get_history,
    history_payload,
    prune_old_samples,
    record_sample,
    sample_from_info,
)


@pytest.fixture
def metrics_db(tmp_path: Path) -> Path:
    return tmp_path / "system_metrics.db"


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


def test_record_and_get_history(metrics_db: Path):
    """Samples in-window are returned oldest-first with avg and max."""
    now = 1_700_000_000.0
    record_sample(_sample(now - 100, temp=41, temp_max=44), db_path=metrics_db)
    record_sample(_sample(now - 50, temp=42, temp_max=45), db_path=metrics_db)
    record_sample(_sample(now - 10, temp=43, temp_max=46), db_path=metrics_db)

    samples = get_history(window="1h", now=now, db_path=metrics_db)
    assert [s.temperature_c for s in samples] == [41, 42, 43]
    assert [s.temperature_c_max for s in samples] == [44, 45, 46]
    assert samples[0].ts < samples[-1].ts


def test_get_history_rejects_unknown_window(metrics_db: Path):
    with pytest.raises(ValueError, match="Invalid window"):
        get_history(window="2h", db_path=metrics_db)


def test_window_seconds_rejects_unknown():
    from src.system_metrics import window_seconds

    with pytest.raises(ValueError, match="Invalid window"):
        window_seconds("2h")


def test_prune_old_samples(metrics_db: Path):
    """Samples older than retention are deleted."""
    now = 1_700_000_000.0
    record_sample(_sample(now - 8 * 24 * 3600), db_path=metrics_db)
    record_sample(_sample(now - 60), db_path=metrics_db)

    deleted = prune_old_samples(now=now, retention_seconds=7 * 24 * 3600, db_path=metrics_db)
    assert deleted == 1

    samples = get_history(window="7d", now=now, db_path=metrics_db)
    assert len(samples) == 1
    assert samples[0].ts == now - 60


def test_downsample_caps_points(metrics_db: Path):
    """Large histories are bucket-averaged down to MAX_RETURN_POINTS."""
    now = 1_700_000_000.0
    for i in range(MAX_RETURN_POINTS * 3):
        record_sample(
            _sample(now - (MAX_RETURN_POINTS * 3 - i) * 30, cpu=float(i % 50), cpu_max=float(i % 50) + 5),
            db_path=metrics_db,
        )

    samples = get_history(window="7d", now=now, db_path=metrics_db, max_points=MAX_RETURN_POINTS)
    assert len(samples) <= MAX_RETURN_POINTS
    assert len(samples) > 0


def test_downsample_uses_max_of_maxes_not_max_of_avgs(metrics_db: Path):
    """Downsample averages avgs and takes the max of stored maxes."""
    now = 1_700_000_000.0
    record_sample(_sample(now - 90, temp=40, temp_max=45, cpu=10, cpu_max=20), db_path=metrics_db)
    record_sample(_sample(now - 60, temp=50, temp_max=55, cpu=20, cpu_max=30), db_path=metrics_db)
    record_sample(_sample(now - 30, temp=60, temp_max=70, cpu=30, cpu_max=40), db_path=metrics_db)
    record_sample(_sample(now - 10, temp=70, temp_max=75, cpu=40, cpu_max=50), db_path=metrics_db)

    out = get_history(window="1h", now=now, db_path=metrics_db, max_points=2)
    assert len(out) == 2
    assert out[0].temperature_c == 45.0
    assert out[0].temperature_c_max == 55.0
    assert out[1].temperature_c == 65.0
    assert out[1].temperature_c_max == 75.0
    assert out[0].cpu_percent_max == 30.0
    assert out[1].cpu_percent_max == 50.0


def test_ensure_schema_migrates_max_columns(metrics_db: Path):
    """Existing DBs without *_max columns gain them without wiping rows."""
    with sqlite3.connect(metrics_db) as conn:
        conn.execute("""
            CREATE TABLE system_samples (
                ts REAL NOT NULL PRIMARY KEY,
                temperature_c REAL,
                cpu_percent REAL,
                memory_used_pct REAL,
                disk_used_pct REAL
            )
            """)
        conn.execute(
            "INSERT INTO system_samples VALUES (?, ?, ?, ?, ?)",
            (1_700_000_000.0, 41.0, 10.0, 40.0, 30.0),
        )
        conn.commit()

    ensure_schema(metrics_db)

    with sqlite3.connect(metrics_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(system_samples)")}
        row = conn.execute(
            "SELECT temperature_c, temperature_c_max FROM system_samples WHERE ts = ?",
            (1_700_000_000.0,),
        ).fetchone()

    assert {
        "temperature_c_max",
        "cpu_percent_max",
        "memory_used_pct_max",
        "disk_used_pct_max",
    } <= columns
    assert row == (41.0, None)


def test_sample_from_info():
    info = SystemInfo(
        hostname="pi",
        uptime="1d",
        temperature_c=55.5,
        cpu_percent=12.0,
        load_avg=0.5,
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


def test_metrics_aggregator_flushes_avg_and_max(metrics_db: Path):
    now = time.time()
    aggregator = MetricsAggregator(db_path=metrics_db, ticks_per_sample=3)
    assert aggregator.add_tick(_sample(now - 2, temp=40)) is None
    assert aggregator.add_tick(_sample(now - 1, temp=50)) is None
    stored = aggregator.add_tick(_sample(now, temp=60))
    assert stored is not None
    assert stored.temperature_c == 50.0
    assert stored.temperature_c_max == 60.0

    history = get_history(window="1h", now=now + 1, db_path=metrics_db)
    assert len(history) == 1
    assert history[0].temperature_c == 50.0
    assert history[0].temperature_c_max == 60.0


def test_average_temperature_from_db(metrics_db: Path, monkeypatch):
    """24h average is the mean of in-window samples, ignoring nulls."""
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: True)
    now = 1_700_000_000.0
    record_sample(_sample(now - 100, temp=40), db_path=metrics_db)
    record_sample(_sample(now - 50, temp=50), db_path=metrics_db)
    record_sample(_sample(now - 10, temp=60), db_path=metrics_db)
    record_sample(
        SystemSample(
            ts=now - 5,
            temperature_c=None,
            cpu_percent=10.0,
            memory_used_pct=40.0,
            disk_used_pct=30.0,
        ),
        db_path=metrics_db,
    )

    assert average_temperature(window="1h", now=now, db_path=metrics_db) == 50.0


def test_temperature_window_stats_uses_stored_max(metrics_db: Path, monkeypatch):
    """Window stats return avg of means and max of stored peaks."""
    from src.system_metrics import temperature_window_stats

    monkeypatch.setattr("src.system_metrics.is_linux", lambda: True)
    now = 1_700_000_000.0
    record_sample(_sample(now - 100, temp=40, temp_max=48), db_path=metrics_db)
    record_sample(_sample(now - 50, temp=50, temp_max=55), db_path=metrics_db)
    record_sample(_sample(now - 10, temp=60, temp_max=72), db_path=metrics_db)

    avg, peak = temperature_window_stats(window="1h", now=now, db_path=metrics_db)
    assert avg == 50.0
    assert peak == 72.0


def test_average_temperature_empty_window(metrics_db: Path, monkeypatch):
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: True)
    assert average_temperature(window="1h", now=1_700_000_000.0, db_path=metrics_db) is None


def test_average_temperature_canned_off_linux(monkeypatch):
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: False)
    avg = average_temperature(window="24h", now=1_700_000_000.0)
    assert avg is not None
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


def test_get_history_applies_rollup(metrics_db: Path, monkeypatch):
    monkeypatch.setattr("src.system_metrics.is_linux", lambda: True)
    now = 1_700_000_120.0
    for offset, temp in ((0, 40), (30, 50), (60, 60), (90, 70)):
        record_sample(_sample(now - 120 + offset, temp=temp, temp_max=temp + 5), db_path=metrics_db)

    samples = get_history(window="1h", rollup="2m", now=now, db_path=metrics_db)
    assert len(samples) == 2
    assert samples[0].temperature_c == 45.0
    assert samples[0].temperature_c_max == 55.0
    assert samples[1].temperature_c == 65.0
    assert samples[1].temperature_c_max == 75.0
