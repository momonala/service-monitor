"""Cloud-backup freshness per project, for the sidebar's backup icon.

Reads the manifest and R2 object hashes written by `~/backup-db-cloudflare-r2/backup.sh` (that
script owns discovery, snapshotting, and uploading; this module only reads its state). A project
can own zero, one, or many SQLite dbs (see the manifest's `<repo>/data/...` keys); the reported
status is the worst of its dbs.

Status per db:
- green: the last backup R2 holds is intact (hash matches what backup.sh uploaded).
- red:   the last backup is missing or corrupted in R2.

Independent of that pass/fail status, a db is also flagged `stale` when its local file has
changed since that last backup was taken — i.e. a newer cycle hasn't run yet. This is rendered
as a subtle marker on the icon rather than its own color, since it doesn't mean anything failed.
"""

import json
import logging
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MANIFEST_PATH = Path.home() / ".local" / "state" / "db-backup-r2" / "manifest.tsv"
CACHE_TTL_SECONDS = 60.0
MAX_LSJSON_WORKERS = 8
_STATUS_RANK = {"green": 0, "red": 1}

_cache: tuple[float, dict[str, "BackupStatus"]] | None = None


@dataclass(frozen=True)
class ManifestEntry:
    fingerprint: str
    uploaded_md5: str
    uploaded_at: str  # ISO8601, as written by backup.sh


@dataclass
class BackupStatus:
    status: str  # "green" | "red"
    stale_seconds: float  # age of the db's last known-good backup
    stale: bool  # source file has changed locally since that backup was taken
    db_count: int


def _read_manifest() -> dict[str, ManifestEntry]:
    """Parse backup.sh's manifest.tsv: db_key -> (fingerprint, uploaded_md5, uploaded_at)."""
    if not MANIFEST_PATH.exists():
        return {}
    entries = {}
    for line in MANIFEST_PATH.read_text().splitlines():
        if not line.strip():
            continue
        key, fingerprint, md5, _bytes, uploaded_at = line.split("\t")
        entries[key] = ManifestEntry(fingerprint, md5, uploaded_at)
    return entries


def _stat_token(stat_result) -> str:
    seconds, nanos = divmod(stat_result.st_mtime_ns, 1_000_000_000)
    return f"{stat_result.st_size}:{seconds}.{nanos:09d}"


def _local_fingerprint(db_path: Path) -> str | None:
    """Reproduce backup.sh's fingerprint: size+mtime of the db, plus its -wal file if present."""
    try:
        fingerprint = _stat_token(db_path.stat())
    except OSError:
        return None
    wal_path = db_path.with_name(db_path.name + "-wal")
    try:
        fingerprint += f"+{_stat_token(wal_path.stat())}"
    except OSError:
        pass
    return fingerprint


def _fetch_remote_hashes(repo: str) -> dict[str, str]:
    """Return {key relative to bucket root, minus '.zst': md5} for a repo's R2 bucket.

    Scoped to the bucket's `data/` prefix (where db snapshots live) rather than listing the
    whole bucket recursively: some repos (e.g. incognita) also keep large, unrelated prefixes
    like `raw_data/` with thousands of objects, which made a full `-R` listing slow enough to
    hit the timeout below and get treated as "no backup found" (red) for an otherwise-healthy db.
    """
    try:
        result = subprocess.run(
            ["rclone", "lsjson", "-R", "--hash", f"r2:{repo}/data"],
            text=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("rclone lsjson failed for %s: %s", repo, exc)
        return {}
    if result.returncode != 0:
        logger.warning("rclone lsjson failed for %s: %s", repo, result.stderr.strip())
        return {}
    try:
        listed = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("rclone lsjson returned invalid JSON for %s", repo)
        return {}

    hashes = {}
    for item in listed:
        path = item.get("Path", "")
        md5 = item.get("Hashes", {}).get("md5")
        if item.get("IsDir") or not path.endswith(".zst") or not md5:
            continue
        hashes[f"data/{path[: -len('.zst')]}"] = md5
    return hashes


def _status_for_db(
    now: datetime, home: Path, key: str, entry: ManifestEntry, remote_hashes: dict[str, str]
) -> tuple[str, float, bool]:
    """Return (status, stale_seconds, is_stale) for one manifest key.

    `status` reflects only whether the upload backup.sh already made is intact in R2.
    `is_stale` is separate: whether the source has changed locally since that upload, i.e.
    a newer cycle hasn't captured it yet. A db can be green-and-stale (last backup fine,
    next one just hasn't run) or red-and-stale.
    """
    rel_key = key.split("/", 1)[1]
    age_seconds = (now - datetime.fromisoformat(entry.uploaded_at)).total_seconds()
    status = "green" if remote_hashes.get(rel_key) == entry.uploaded_md5 else "red"
    is_stale = _local_fingerprint(home / key) != entry.fingerprint
    return status, age_seconds, is_stale


def backup_statuses_for_groups(project_groups: list[str]) -> dict[str, BackupStatus]:
    """Backup freshness per project (systemd project_group).

    Only projects with at least one db in the manifest get an entry; callers should treat a
    missing key as "no backup icon for this service". Cached for CACHE_TTL_SECONDS since a fresh
    read costs one `rclone lsjson` round-trip per repo bucket (parallelized across buckets).
    """
    global _cache
    now_monotonic = time.monotonic()
    if _cache is not None:
        cached_at, cached_value = _cache
        if now_monotonic - cached_at < CACHE_TTL_SECONDS:
            return cached_value

    manifest = _read_manifest()
    keys_by_group = {
        group: keys
        for group in project_groups
        if (keys := [key for key in manifest if key.split("/", 1)[0] == group])
    }

    with ThreadPoolExecutor(max_workers=min(MAX_LSJSON_WORKERS, len(keys_by_group) or 1)) as pool:
        remote_hashes_by_group = dict(zip(keys_by_group, pool.map(_fetch_remote_hashes, keys_by_group)))

    home = Path.home()
    now = datetime.now(timezone.utc)
    result = {}
    for group, keys in keys_by_group.items():
        remote_hashes = remote_hashes_by_group[group]
        worst_status, worst_age, any_stale = "green", 0.0, False
        for key in keys:
            status, age_seconds, is_stale = _status_for_db(now, home, key, manifest[key], remote_hashes)
            any_stale = any_stale or is_stale
            is_worse = _STATUS_RANK[status] > _STATUS_RANK[worst_status]
            is_same_and_staler = status == worst_status and age_seconds > worst_age
            if is_worse or is_same_and_staler:
                worst_status, worst_age = status, age_seconds
        result[group] = BackupStatus(
            status=worst_status, stale_seconds=worst_age, stale=any_stale, db_count=len(keys)
        )

    _cache = (now_monotonic, result)
    return result
