"""Tests for backup_status.py module."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import src.backup_status as backup_status_module
from src.backup_status import (
    ManifestEntry,
    _fetch_remote_hashes,
    _local_fingerprint,
    _read_manifest,
    _stat_token,
    _status_for_db,
    backup_statuses_for_groups,
)


@pytest.fixture(autouse=True)
def reset_backup_status_cache():
    backup_status_module._cache = None
    yield


def test_read_manifest_parses_tsv(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "repo-a/data/a.db\t100:1.0\tabc123\t100\t2026-08-17T10:00:00+00:00\n"
        "\n"  # blank lines are skipped
        "repo-b/data/b.db\t200:2.0\tdef456\t200\t2026-08-17T11:00:00+00:00\n"
    )
    monkeypatch.setattr(backup_status_module, "MANIFEST_PATH", manifest)

    entries = _read_manifest()

    assert entries["repo-a/data/a.db"] == ManifestEntry("100:1.0", "abc123", "2026-08-17T10:00:00+00:00")
    assert entries["repo-b/data/b.db"] == ManifestEntry("200:2.0", "def456", "2026-08-17T11:00:00+00:00")


def test_read_manifest_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_status_module, "MANIFEST_PATH", tmp_path / "missing.tsv")
    assert _read_manifest() == {}


def test_local_fingerprint_includes_wal_file(tmp_path):
    db_path = tmp_path / "test.db"
    db_path.write_bytes(b"hello")
    wal_path = tmp_path / "test.db-wal"
    wal_path.write_bytes(b"wal-bytes")

    fingerprint = _local_fingerprint(db_path)

    assert fingerprint == f"{_stat_token(db_path.stat())}+{_stat_token(wal_path.stat())}"


def test_local_fingerprint_missing_source_returns_none(tmp_path):
    assert _local_fingerprint(tmp_path / "missing.db") is None


@patch("subprocess.run")
def test_fetch_remote_hashes_parses_lsjson(mock_run):
    """Only .zst files (not directories) contribute to the hash map, keyed without the suffix."""
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = json.dumps(
        [
            {"Path": "a.db.zst", "IsDir": False, "Hashes": {"md5": "abc123"}},
            {"Path": "sub", "IsDir": True},
            {"Path": "b.txt", "IsDir": False, "Hashes": {"md5": "ignored"}},
        ]
    )

    hashes = _fetch_remote_hashes("repo-a")

    assert hashes == {"data/a.db": "abc123"}


@patch("subprocess.run")
def test_fetch_remote_hashes_nonzero_exit_returns_empty(mock_run):
    mock_run.return_value.returncode = 1
    mock_run.return_value.stderr = "bucket not found"

    assert _fetch_remote_hashes("missing-repo") == {}


def _entry(md5="abc123", fingerprint="100:1.0", hours_ago=1.0):
    uploaded_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return ManifestEntry(fingerprint=fingerprint, uploaded_md5=md5, uploaded_at=uploaded_at)


def test_status_for_db_green_when_intact_and_unchanged(tmp_path):
    db_path = tmp_path / "repo" / "data" / "a.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"hello")
    entry = _entry(md5="abc123", fingerprint=_local_fingerprint(db_path))

    status, _, is_stale = _status_for_db(
        datetime.now(timezone.utc), tmp_path, "repo/data/a.db", entry, {"data/a.db": "abc123"}
    )

    assert status == "green"
    assert is_stale is False


def test_status_for_db_stays_green_and_stale_even_when_old(tmp_path):
    """No more age-based escalation to red — staleness is reported via the flag, not color."""
    db_path = tmp_path / "repo" / "data" / "a.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"hello")
    entry = _entry(md5="abc123", fingerprint="stale-fingerprint", hours_ago=25.0)

    status, _, is_stale = _status_for_db(
        datetime.now(timezone.utc), tmp_path, "repo/data/a.db", entry, {"data/a.db": "abc123"}
    )

    assert status == "green"
    assert is_stale is True


def test_status_for_db_red_when_remote_hash_mismatched(tmp_path):
    entry = _entry(md5="abc123", hours_ago=0.1)

    status, _, _ = _status_for_db(
        datetime.now(timezone.utc), tmp_path, "repo/data/a.db", entry, {"data/a.db": "different-hash"}
    )

    assert status == "red"


@patch("src.backup_status._fetch_remote_hashes")
@patch("src.backup_status._read_manifest")
def test_backup_statuses_for_groups_reports_worst_db_per_group(mock_read_manifest, mock_fetch_hashes):
    """A project owning multiple dbs reports the worst (reddest) status across all of them."""
    mock_read_manifest.return_value = {
        "repo-a/data/current.db": _entry(md5="ok", hours_ago=0.1),
        "repo-a/data/broken.db": _entry(md5="ok", hours_ago=25.0),
    }
    mock_fetch_hashes.return_value = {"data/current.db": "ok"}  # broken.db missing from R2

    result = backup_statuses_for_groups(["repo-a", "repo-with-no-dbs"])

    assert result["repo-a"].status == "red"
    assert result["repo-a"].db_count == 2
    assert "repo-with-no-dbs" not in result


@patch("src.backup_status._fetch_remote_hashes")
@patch("src.backup_status._read_manifest")
def test_backup_statuses_for_groups_stale_flag_is_any_db_stale(mock_read_manifest, mock_fetch_hashes):
    """The group is flagged stale if any of its dbs has changed locally since its backup."""
    mock_read_manifest.return_value = {
        "repo-a/data/current.db": _entry(md5="ok", fingerprint=None, hours_ago=0.1),
        "repo-a/data/changed.db": _entry(md5="ok", fingerprint="changed", hours_ago=0.1),
    }
    mock_fetch_hashes.return_value = {"data/current.db": "ok", "data/changed.db": "ok"}

    result = backup_statuses_for_groups(["repo-a"])

    assert result["repo-a"].status == "green"
    assert result["repo-a"].stale is True


@patch("src.backup_status._fetch_remote_hashes")
@patch("src.backup_status._read_manifest")
def test_backup_statuses_for_groups_caches_within_ttl(mock_read_manifest, mock_fetch_hashes):
    mock_read_manifest.return_value = {"repo-a/data/a.db": _entry()}
    mock_fetch_hashes.return_value = {}

    backup_statuses_for_groups(["repo-a"])
    backup_statuses_for_groups(["repo-a"])

    assert mock_read_manifest.call_count == 1
