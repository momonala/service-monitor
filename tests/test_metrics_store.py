"""Tests for the buffered, time-keyed metrics store."""

import math
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from src.metrics_store import MetricsStore, ServiceSample, SystemSample, make_tick

NOW = 1_700_000_000.0


def _tick(ts: float, services: dict[str, tuple[float, float]] | None = None):
    system = SystemSample(
        ts=ts, temperature_c=50.0, cpu_percent=10.0, memory_used_pct=40.0, disk_used_pct=30.0
    )
    readings = [ServiceSample(ts, unit, memory, cpu) for unit, (memory, cpu) in (services or {}).items()]
    return make_tick(system, readings)


def _stored_rows(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as conn, conn:
        return conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "metrics.db"


def test_buffered_ticks_are_readable_before_they_are_written(db_path: Path):
    store = MetricsStore(db_path, flush_interval=math.inf, clock=lambda: NOW)
    store.append(_tick(NOW - 30, {"a.service": (1.0, 2.0)}))

    assert [s.ts for s in store.system_samples(NOW - 60, NOW)] == [NOW - 30]
    assert store.service_samples("a.service", NOW - 60, NOW) == [
        ServiceSample(NOW - 30, "a.service", 1.0, 2.0)
    ]
    assert _stored_rows(db_path) == 0
    store.close()


def test_flush_writes_the_buffer_once(db_path: Path):
    store = MetricsStore(db_path, flush_interval=math.inf, clock=lambda: NOW)
    for offset in (90, 60, 30):
        store.append(_tick(NOW - offset))

    store.flush()

    assert _stored_rows(db_path) == 3
    assert [s.ts for s in store.system_samples(NOW - 120, NOW)] == [NOW - 90, NOW - 60, NOW - 30]
    store.close()


def test_ticks_survive_a_restart(db_path: Path):
    store = MetricsStore(db_path, flush_interval=math.inf, clock=lambda: NOW)
    store.append(_tick(NOW - 30, {"a.service": (1.0, 2.0), "b.service": (3.0, None)}))
    store.close()

    reopened = MetricsStore(db_path, clock=lambda: NOW)
    latest = reopened.latest_tick()

    assert latest is not None
    assert latest.ts == NOW - 30
    assert latest.services == {"a.service": (1.0, 2.0, None, None), "b.service": (3.0, None, None, None)}
    reopened.close()


def test_latest_tick_prefers_the_buffer(db_path: Path):
    store = MetricsStore(db_path, flush_interval=math.inf, clock=lambda: NOW)
    store.append(_tick(NOW - 60))
    store.flush()
    store.append(_tick(NOW - 30))

    assert store.latest_tick().ts == NOW - 30
    store.close()


def test_first_flush_prunes_rows_past_retention(db_path: Path):
    store = MetricsStore(db_path, flush_interval=0, retention_seconds=3600, clock=lambda: NOW)
    store.append(_tick(NOW - 7200))
    store.append(_tick(NOW - 60))

    assert [s.ts for s in store.system_samples(0, NOW)] == [NOW - 60]
    store.close()


def test_opening_drops_legacy_tables(db_path: Path):
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("CREATE TABLE system_samples (ts REAL PRIMARY KEY)")
        conn.execute("CREATE TABLE service_samples (ts REAL, service TEXT)")

    store = MetricsStore(db_path, clock=lambda: NOW)
    store.flush()
    store.close()

    with closing(sqlite3.connect(db_path)) as conn, conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"samples"}


def test_service_names_with_quotes_are_rejected(db_path: Path):
    store = MetricsStore(db_path, clock=lambda: NOW)
    with pytest.raises(ValueError, match="Invalid service name"):
        store.service_samples('a".service', 0, NOW)
    store.close()


def test_written_totals_combine_stored_and_buffered_ticks(db_path: Path):
    def tick(ts: float, kb: float):
        system = SystemSample(ts, 50.0, 10.0, 40.0, 30.0, disk_written_kb=kb)
        return make_tick(system, [ServiceSample(ts, "a.service", 1.0, 2.0, kb / 2)])

    store = MetricsStore(db_path, flush_interval=math.inf, clock=lambda: NOW)
    store.append(tick(NOW - 90, 100.0))
    store.flush()
    store.append(tick(NOW - 60, 50.0))

    assert store.host_written_kb(NOW - 120, NOW) == (150.0, NOW - 90)
    assert store.service_written_kb("a.service", NOW - 120, NOW) == (75.0, NOW - 90)
    assert store.written_kb_by_service(NOW - 120, NOW) == {"a.service": 75.0}
    store.close()


def test_opening_an_older_samples_table_adds_the_written_column(db_path: Path):
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute(
            "CREATE TABLE samples (ts INTEGER PRIMARY KEY, temperature_c REAL, cpu_percent REAL, "
            "memory_used_pct REAL, disk_used_pct REAL, temperature_c_max REAL, cpu_percent_max REAL, "
            "memory_used_pct_max REAL, disk_used_pct_max REAL, services TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO samples VALUES (?, 50, 10, 40, 30, 51, 11, 41, 31, ?)",
            (int(NOW - 30), '{"a.service":[1.0,2.0]}'),
        )

    store = MetricsStore(db_path, clock=lambda: NOW)
    latest = store.latest_tick()

    assert latest.system.disk_written_kb is None
    assert latest.services == {"a.service": (1.0, 2.0, None, None)}
    store.close()
