"""Backing the vault up to a second folder, incrementally.

Two things carry most of the weight here, and they are the two that would be
expensive to discover in production.

The first is that the fast path really is fast. The whole design rests on the
claim that a sync of an unchanged library hashes nothing at all, and the only
honest way to test that is to replace the hasher with something that raises and
watch the scan finish anyway. Several tests below do exactly that.

The second is that nothing is destroyed. A backup is somebody's second copy —
often their only other copy — so the tests care less about "did the right bytes
arrive" than about "could this ever remove or overwrite something it should
not". Hence the weight on identity mismatches, deletion opt-in, and the write
ordering that leaves the databases until last.

Audio is dummy bytes throughout. What the copy path promises is that the bytes
arrive unchanged, and that does not need a real Opus file to demonstrate.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.backup import apply as apply_mod  # noqa: E402
from app.backup import scan  # noqa: E402
from app.backup.apply import apply_plan  # noqa: E402
from app.backup.identity import ensure_vault_id, vault_id  # noqa: E402
from app.backup.locator import (  # noqa: E402
    has_backup_name,
    has_generated_name,
    is_backup,
    manifest_path,
    suggested_backup_name,
)
from app.backup.manifest import STATE_SYNCING, Manifest  # noqa: E402
from app.backup.plan import (  # noqa: E402
    BackupCancelled,
    BackupError,
    BackupOptions,
    Identity,
)
from app.backup.scan import build_plan  # noqa: E402
from app.utils.library_paths import set_library_root  # noqa: E402
from app.vault.vault_locator import is_vault  # noqa: E402


ALL = BackupOptions(tracks=True, backing=True, guis=True, models=True)


# ============================================================== fixtures


def _seed_vault(root: Path, tracks: int = 4, backing: int = 1) -> Path:
    """Build a vault on disk with real SQLite databases in WAL mode."""
    (root / ".data").mkdir(parents=True, exist_ok=True)

    for i in range(1, tracks + 1):
        path = root / "mix" / "0001-0100" / f"{i:04X}.opus"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mix-%d-" % i * 64)

    for i in range(1, backing + 1):
        path = root / "bt" / "0001-0100" / f"{i:04X}.opus"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"bt-%d-" % i * 64)

    for name in ("tracks.db", "covers.db"):
        _seed_db(root / ".data" / name)

    (root / ".data" / "session.json").write_text('{"version": 2}', encoding="utf-8")
    (root / ".data" / "settings.json").write_text('{"version": 1}', encoding="utf-8")
    ensure_vault_id(root)
    return root


def _seed_db(path: Path, rows: int = 50) -> None:
    """A WAL-mode database with committed rows still sitting in the -wal."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(rows)])
    conn.commit()
    conn.close()


@pytest.fixture
def vault(tmp_path):
    """A seeded vault, pinned as the library root for the duration."""
    root = _seed_vault(tmp_path / "venomvault")
    set_library_root(root)
    return root


@pytest.fixture
def backup(tmp_path):
    """An empty folder to back up into, on the same filesystem.

    Named with a word rather than a date on purpose. A finished sync restamps a
    *generated* name to the current date, so a fixture called
    ``venombackup_20260823`` would sit still on the day it was written and start
    renaming itself — invalidating the path every test here holds — on any other
    day. A chosen name is never restamped, so this one is stable forever.
    """
    path = tmp_path / "venombackup_fixture"
    path.mkdir()
    return path


@pytest.fixture
def new_backup(tmp_path):
    """A backup path that does **not** exist yet, inside a folder that does.

    The shape of every real "Create backup": the user picks somewhere to put it
    and the app proposes a ``venombackup_<today>`` inside. That this fixture did
    not exist is why creating a backup was broken in the shipped build — every
    test called ``mkdir()`` first, so the whole create path went unexercised.
    """
    parent = tmp_path / "external-drive"
    parent.mkdir()
    return parent / suggested_backup_name()


@pytest.fixture
def synced(vault, backup):
    """A backup that is already up to date with the vault."""
    apply_plan(build_plan(vault, backup, ALL))
    return backup


@pytest.fixture
def no_hashing(monkeypatch):
    """Make any attempt to hash a file an error.

    The point of the manifest is that an unchanged file is never opened. A test
    that installs this and still completes has proved that, rather than merely
    asserting a count that would also be right for the slow path.
    """
    def boom(path):
        raise AssertionError(f"hashed {path} when the manifest should have sufficed")

    monkeypatch.setattr(scan, "hash_file", boom)


def _touch(path: Path, offset: float) -> None:
    """Move a file's modification time by *offset* seconds."""
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + offset))


def _relpaths(actions, action=None) -> set[str]:
    return {a.relpath for a in actions if action is None or a.action is action}


# ============================================================== creating one
#
# The destination for a new backup does not exist yet — that is what makes it
# new. These are the tests that were missing, and their absence is why the
# shipped build refused every attempt to create a backup with "No such folder".


def test_a_backup_can_be_created_in_a_folder_that_does_not_exist(vault, new_backup):
    """The reported failure, end to end."""
    assert not new_backup.exists()

    plan = build_plan(vault, new_backup, ALL)
    assert plan.identity is Identity.NEW
    assert len(plan.added) == 5

    apply_plan(plan)

    assert is_backup(new_backup)
    assert (new_backup / ".data" / "tracks.db").exists()
    assert len(list((new_backup / "mix").rglob("*.opus"))) == 4


def test_scanning_a_folder_that_does_not_exist_creates_nothing(vault, new_backup):
    """The scan's promise holds even when the destination is only proposed."""
    build_plan(vault, new_backup, ALL)

    assert not new_backup.exists(), "a scan the user then cancels must leave no trace"


def test_a_missing_parent_is_refused(vault, tmp_path):
    missing = tmp_path / "no-such-drive" / "venombackup_20260823"

    with pytest.raises(BackupError, match="No such folder"):
        build_plan(vault, missing, ALL)


def test_a_destination_that_is_a_file_is_refused(vault, tmp_path):
    not_a_folder = tmp_path / "venombackup_20260823"
    not_a_folder.write_bytes(b"a file, not a folder")

    with pytest.raises(BackupError, match="not a folder"):
        build_plan(vault, not_a_folder, ALL)


def test_free_space_is_measured_from_the_drive_not_the_missing_folder(vault, new_backup):
    """``disk_usage`` raises on a path that is not there.

    Left unhandled that became 0, which is falsy, so the not-enough-room check
    was skipped entirely — for exactly the case where somebody is about to fill
    an external drive for the first time.
    """
    plan = build_plan(vault, new_backup, ALL)

    assert plan.free_bytes > 0, "fell back to the nearest folder that does exist"


# ============================================================== the diff


def test_a_first_backup_adds_everything(vault, backup):
    plan = build_plan(vault, backup, ALL)

    assert plan.identity is Identity.NEW
    assert len(plan.added) == 5          # 4 mix + 1 bt
    assert not plan.updated
    assert plan.unchanged == 0


def test_an_unchanged_library_needs_no_hashing(synced, vault, no_hashing):
    plan = build_plan(vault, synced, ALL)

    assert plan.unchanged == 5
    assert not plan.added
    assert not plan.updated


def test_only_the_new_track_is_written(synced, vault, no_hashing):
    new = vault / "mix" / "0001-0100" / "0009.opus"
    new.write_bytes(b"brand new" * 32)

    plan = build_plan(vault, synced, ALL)

    assert _relpaths(plan.added) == {"mix/0001-0100/0009.opus"}
    assert plan.unchanged == 5


def test_a_different_size_is_an_update_without_hashing(synced, vault, no_hashing):
    changed = vault / "mix" / "0001-0100" / "0001.opus"
    changed.write_bytes(b"much longer than it was before" * 40)

    plan = build_plan(vault, synced, ALL)

    assert _relpaths(plan.updated) == {"mix/0001-0100/0001.opus"}


def test_a_retagged_track_keeps_its_size_and_is_still_caught(synced, vault):
    """The case the cheap signals cannot settle, and the reason hashing exists.

    Retagging rewrites an Opus comment block in place. The file is very often
    exactly as long as it was, so only the contents give it away.
    """
    changed = vault / "mix" / "0001-0100" / "0001.opus"
    same_length = b"X" * changed.stat().st_size
    changed.write_bytes(same_length)

    plan = build_plan(vault, synced, ALL)

    assert _relpaths(plan.updated) == {"mix/0001-0100/0001.opus"}


def test_a_touched_file_is_re_read_rather_than_assumed_unchanged(synced, vault):
    """A moved timestamp costs a hash, and must never be waved through.

    This is the corrected form of a tolerance this code originally had. Allowing
    "close enough" on the source timestamp meant a track retagged within the
    window matched its recorded mtime, was skipped, and — since the recorded
    value never stopped matching — was skipped forever after. The contents are
    identical here, so the hash agrees and nothing is copied; what matters is
    that it was checked at all.
    """
    _touch(vault / "mix" / "0001-0100" / "0001.opus", 1.0)

    plan = build_plan(vault, synced, ALL)

    assert not plan.updated
    assert plan.unchanged == 5


def test_a_file_edited_immediately_after_a_backup_is_still_caught(synced, vault):
    """The failure a timestamp tolerance would have hidden permanently."""
    changed = vault / "mix" / "0001-0100" / "0001.opus"
    changed.write_bytes(b"Y" * changed.stat().st_size)   # same size, same second

    assert _relpaths(build_plan(vault, synced, ALL).updated) == {
        "mix/0001-0100/0001.opus"
    }

    apply_plan(build_plan(vault, synced, ALL))
    assert (synced / "mix" / "0001-0100" / "0001.opus").read_bytes() == changed.read_bytes()


def test_a_file_edited_on_the_backup_drive_is_noticed(synced, vault):
    """The manifest records both sides, so tampering downstream is not trusted."""
    (synced / "mix" / "0001-0100" / "0001.opus").write_bytes(b"meddled with")

    plan = build_plan(vault, synced, ALL)

    assert _relpaths(plan.updated) == {"mix/0001-0100/0001.opus"}


def test_a_deleted_track_becomes_an_extra_and_is_left_alone(synced, vault):
    (vault / "mix" / "0001-0100" / "0002.opus").unlink()

    plan = build_plan(vault, synced, ALL)
    assert _relpaths(plan.extras) == {"mix/0001-0100/0002.opus"}

    apply_plan(plan)
    assert (synced / "mix" / "0001-0100" / "0002.opus").exists()


def test_an_extra_goes_only_when_the_user_opts_in(synced, vault):
    (vault / "mix" / "0001-0100" / "0002.opus").unlink()

    opts = BackupOptions(tracks=True, backing=True, guis=True, models=True,
                         delete_extras=True)
    result = apply_plan(build_plan(vault, synced, opts))

    assert result.deleted == 1
    assert not (synced / "mix" / "0001-0100" / "0002.opus").exists()


def test_a_missing_manifest_falls_back_to_hashing_everything(synced, vault):
    manifest_path(synced).unlink()

    plan = build_plan(vault, synced, ALL)

    assert plan.full_verify
    assert plan.unchanged == 5, "the files really are identical"
    assert not plan.updated


def test_turning_an_option_off_takes_its_files_out_of_scope(vault, backup):
    plan = build_plan(vault, backup, BackupOptions(tracks=True, backing=False,
                                                   guis=False, models=False))

    assert all(a.relpath.startswith("mix/") for a in plan.added)


def test_both_persistence_documents_are_copied(vault):
    """A restored backup that came back with no preferences would be half a vault.

    ``settings.json`` carries the download agreement, the backup destination and
    the separation configuration; it was added to ``DATA_FILES`` when it was
    split out of ``session.json``, and forgetting it is silent.
    """
    from app.backup.layout import data_payload

    payload = data_payload(vault)

    assert ".data/session.json" in payload
    assert ".data/settings.json" in payload
    assert ".data/vault.json" in payload


# ============================================================== exclusions


def test_scratch_and_sidecar_files_are_never_in_a_plan(vault, backup):
    (vault / ".data" / "mpris-art").mkdir(parents=True, exist_ok=True)
    (vault / ".data" / "mpris-art" / "now.jpg").write_bytes(b"art")
    (vault / ".data" / "gui").mkdir(parents=True, exist_ok=True)
    (vault / ".data" / "gui" / "nocturne.kpi").write_bytes(b"bundle")
    (vault / ".data" / "gui" / "stray.part").write_bytes(b"junk")

    paths = _relpaths(build_plan(vault, backup, ALL).actions)

    assert ".data/gui/nocturne.kpi" in paths
    assert not any("mpris-art" in p for p in paths)
    assert not any(p.endswith(".part") for p in paths)
    assert not any(p.endswith(("-wal", "-shm")) for p in paths)


def test_the_compiled_qml_cache_is_never_backed_up(vault, backup):
    """A cache of a cache, and a large churny one.

    It is hundreds of small files keyed by a GUI's build id, regenerated on the
    first launch that needs them and invalidated outright by a Qt upgrade — so
    copying it would replicate the whole thing again after every rebuild and
    restore nothing.
    """
    cache = vault / ".data" / "gui" / ".cache" / "abc123"
    cache.mkdir(parents=True)
    (cache / "3f9c1a7e.qmlc").write_bytes(b"bytecode")
    (vault / ".data" / "gui" / "nocturne.kpi").write_bytes(b"bundle")

    paths = _relpaths(build_plan(vault, backup, ALL).actions)

    assert ".data/gui/nocturne.kpi" in paths, "the bundle itself is content"
    assert not any(".cache" in p for p in paths)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlinked_folder_is_not_descended_into(vault, backup, tmp_path):
    """Otherwise a link pointing at an ancestor makes the walk run forever."""
    outside = tmp_path / "somewhere-else"
    outside.mkdir()
    (outside / "secret.opus").write_bytes(b"not library content")
    (vault / "mix" / "linked").symlink_to(outside, target_is_directory=True)

    paths = _relpaths(build_plan(vault, backup, ALL).actions)

    assert not any("linked" in p for p in paths)


# ============================================================== identity


def test_a_vault_id_is_stable_once_assigned(vault):
    assert vault_id(vault) == ensure_vault_id(vault) != ""


def test_a_backup_of_this_vault_matches(synced, vault):
    assert build_plan(vault, synced, ALL).identity is Identity.MATCH


def test_a_backup_of_another_vault_is_a_mismatch_and_is_refused(synced, vault, tmp_path):
    """The case the whole identity mechanism exists for."""
    stranger = _seed_vault(tmp_path / "stranger")
    manifest = Manifest.load(synced)
    manifest.source_vault_id = vault_id(stranger)
    manifest.save(synced)

    plan = build_plan(vault, synced, ALL)
    assert plan.identity is Identity.MISMATCH
    assert plan.is_blocked

    with pytest.raises(BackupError):
        apply_plan(plan)


def test_a_mismatch_runs_only_with_an_explicit_override(synced, vault, tmp_path):
    stranger = _seed_vault(tmp_path / "stranger")
    manifest = Manifest.load(synced)
    manifest.source_vault_id = vault_id(stranger)
    manifest.save(synced)

    opts = BackupOptions(tracks=True, backing=True, guis=True, models=True,
                         override_mismatch=True)
    plan = build_plan(vault, synced, opts)

    assert plan.identity is Identity.MISMATCH
    assert not plan.is_blocked
    apply_plan(plan)   # does not raise


def test_a_backup_with_no_recorded_id_is_unverified_not_refused(synced, vault):
    """Refusing would strand anyone whose backup predates vault ids."""
    manifest = Manifest.load(synced)
    manifest.source_vault_id = ""
    manifest.save(synced)
    (synced / ".data" / "vault.json").unlink()

    plan = build_plan(vault, synced, ALL)

    assert plan.identity is Identity.ABSENT
    assert not plan.is_blocked


def test_confirming_an_unverified_backup_adopts_the_id(synced, vault):
    manifest = Manifest.load(synced)
    manifest.source_vault_id = ""
    manifest.save(synced)

    apply_plan(build_plan(vault, synced, ALL))

    assert Manifest.load(synced).source_vault_id == vault_id(vault)


# ============================================================== safety


def test_a_destination_inside_the_vault_is_refused(vault):
    inside = vault / "mix" / "backup-here"
    inside.mkdir(parents=True)

    with pytest.raises(BackupError, match="inside the vault"):
        build_plan(vault, inside, ALL)


def test_the_vault_itself_is_refused(vault):
    with pytest.raises(BackupError, match="vault itself"):
        build_plan(vault, vault, ALL)


def test_a_folder_containing_the_vault_is_refused(vault):
    with pytest.raises(BackupError, match="vault is inside"):
        build_plan(vault, vault.parent, ALL)


def test_an_unset_destination_never_becomes_the_working_directory(vault):
    """``Path("")`` collapses to the cwd, which exists and is writable."""
    with pytest.raises(BackupError, match="Choose a folder"):
        build_plan(vault, Path(""), ALL)


def test_a_shortage_of_space_is_reported(vault, backup, monkeypatch):
    monkeypatch.setattr(scan, "_free_bytes", lambda _p: 1)

    plan = build_plan(vault, backup, ALL)

    assert any("Not enough room" in w for w in plan.warnings)


def test_a_large_rewrite_earns_a_warning(synced, vault):
    """The guard the vault id cannot provide: right library, badly stale copy."""
    for path in (synced / "mix" / "0001-0100").glob("*.opus"):
        path.write_bytes(b"stale")

    plan = build_plan(vault, synced, ALL)

    assert plan.churn >= 0.5
    assert any("rewrite or remove" in w for w in plan.warnings)


def test_a_held_lock_stops_a_second_run(vault, backup):
    from app.backup.locator import lock_path
    lock_path(backup).parent.mkdir(parents=True, exist_ok=True)
    lock_path(backup).write_text("pid 1", encoding="utf-8")

    with pytest.raises(BackupError, match="already running"):
        build_plan(vault, backup, ALL)


# ============================================================== the write


def test_no_part_files_survive_a_successful_run(synced):
    leftovers = [p for p in synced.rglob("*.part")]
    assert leftovers == []


def test_the_lock_is_released_when_the_run_ends(synced):
    from app.backup.locator import lock_path
    assert not lock_path(synced).exists()


def test_the_audio_arrives_byte_for_byte(synced, vault):
    for source in (vault / "mix").rglob("*.opus"):
        copied = synced / source.relative_to(vault)
        assert copied.read_bytes() == source.read_bytes()


def test_the_database_snapshot_is_complete_and_self_contained(synced):
    """A plain file copy would miss whatever is still in the -wal."""
    snapshot = synced / ".data" / "tracks.db"
    rows = sqlite3.connect(snapshot).execute("SELECT count(*) FROM t").fetchone()[0]

    assert rows == 50
    for suffix in ("-wal", "-shm"):
        assert not snapshot.with_name(snapshot.name + suffix).exists()


def test_a_stale_wal_beside_the_snapshot_is_cleared(vault, backup):
    """Left there, SQLite recovers it over the fresh database on open."""
    (backup / ".data").mkdir(parents=True, exist_ok=True)
    (backup / ".data" / "tracks.db-wal").write_bytes(b"stale wal from an old run")

    apply_plan(build_plan(vault, backup, ALL))

    assert not (backup / ".data" / "tracks.db-wal").exists()


def test_a_finished_backup_is_both_a_backup_and_a_valid_vault(synced, vault):
    """Which is what makes recovery a matter of opening it."""
    assert is_backup(synced)
    assert is_vault(synced)
    assert scan.choose_backup_folder(synced.parent, vault) == synced


def test_a_covers_failure_is_a_warning_not_a_failure(vault, backup, monkeypatch):
    """It is a derived cache — it rebuilds itself from the tracks."""
    real = apply_mod.snapshot_database

    def fail_on_covers(source, destination):
        if source.name == "covers.db":
            raise sqlite3.DatabaseError("simulated")
        return real(source, destination)

    monkeypatch.setattr(apply_mod, "snapshot_database", fail_on_covers)
    result = apply_plan(build_plan(vault, backup, ALL))

    assert any("artwork cache" in w for w in result.warnings)
    assert (backup / ".data" / "tracks.db").exists()


def test_a_tracks_db_failure_fails_the_run(vault, backup, monkeypatch):
    def fail(source, destination):
        raise sqlite3.DatabaseError("simulated")

    monkeypatch.setattr(apply_mod, "snapshot_database", fail)

    with pytest.raises(BackupError, match="library database"):
        apply_plan(build_plan(vault, backup, ALL))


def test_the_audio_is_written_before_the_database(vault, backup, monkeypatch):
    """Ordering, and the reason for it.

    A database written first and then interrupted describes tracks whose files
    were never copied — broken silently, and never repaired. Audio first leaves
    files the database has not caught up with, which the next run fixes.
    """
    seen: list[str] = []
    real_copy = apply_mod._copy_one
    real_db = apply_mod.snapshot_database

    def note_copy(source_root, dest_root, action, manifest):
        seen.append("audio")
        return real_copy(source_root, dest_root, action, manifest)

    def note_db(source, destination):
        seen.append("db")
        return real_db(source, destination)

    monkeypatch.setattr(apply_mod, "_copy_one", note_copy)
    monkeypatch.setattr(apply_mod, "snapshot_database", note_db)
    apply_plan(build_plan(vault, backup, ALL))

    assert "audio" in seen and "db" in seen
    # Every audio copy happens before every database snapshot: no interleaving,
    # so there is no moment at which the backup holds a database describing
    # files that are not there yet.
    assert seen == ["audio"] * seen.count("audio") + ["db"] * seen.count("db")


def test_cancelling_stops_between_files_and_keeps_what_was_written(vault, backup):
    calls = {"n": 0}

    def stop_after_two() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(BackupCancelled):
        apply_plan(build_plan(vault, backup, ALL), should_cancel=stop_after_two)

    written = list(backup.rglob("*.opus"))
    assert written, "the files copied before the stop are still there"
    assert not list(backup.rglob("*.part")), "and none of them are half-written"


def test_an_interrupted_run_is_recorded_and_reported(vault, backup):
    def stop_immediately() -> bool:
        return True

    with pytest.raises(BackupCancelled):
        apply_plan(build_plan(vault, backup, ALL), should_cancel=stop_immediately)

    assert Manifest.load(backup).state == STATE_SYNCING
    assert build_plan(vault, backup, ALL).interrupted


def test_an_interrupted_run_keeps_what_it_learned(vault, backup):
    """So resuming does not re-hash the whole library to rediscover it."""
    calls = {"n": 0}

    with pytest.raises(BackupCancelled):
        apply_plan(
            build_plan(vault, backup, ALL),
            should_cancel=lambda: calls.__setitem__("n", calls["n"] + 1) or calls["n"] > 3,
        )

    assert Manifest.load(backup).files, "the digests of the copied files survived"


def test_repeated_failures_abort_the_run(vault, backup, monkeypatch):
    def always_fail(*_args, **_kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(apply_mod, "_copy_one", always_fail)

    with pytest.raises(BackupError, match="failures in a row"):
        apply_plan(build_plan(vault, backup, ALL))


def test_a_track_deleted_between_scan_and_write_is_not_a_failure(vault, backup):
    plan = build_plan(vault, backup, ALL)
    (vault / "mix" / "0001-0100" / "0002.opus").unlink()

    result = apply_plan(plan)

    assert result.written == 4, "the other four still went"
    assert not (backup / "mix" / "0001-0100" / "0002.opus").exists()


def test_partials_from_an_earlier_run_are_swept(vault, backup):
    (backup / "mix" / "0001-0100").mkdir(parents=True)
    stray = backup / "mix" / "0001-0100" / "0001.opus.part"
    stray.write_bytes(b"interrupted")

    apply_plan(build_plan(vault, backup, ALL))

    assert not stray.exists()


# ============================================================== locator


def test_a_backup_is_recognised_by_its_marker_not_its_name(synced, tmp_path):
    renamed = tmp_path / "some-other-name"
    synced.rename(renamed)

    assert is_backup(renamed)


def test_the_suggested_name_carries_todays_date():
    name = suggested_backup_name()

    assert name.startswith("venombackup_")
    assert len(name) == len("venombackup_YYYYMMDD")


def test_an_empty_folder_is_not_a_backup(tmp_path, vault):
    plain = tmp_path / "plain"
    plain.mkdir()

    assert not is_backup(plain)
    assert scan.choose_backup_folder(plain, vault) is None


@pytest.mark.parametrize("name, expected", [
    ("venombackup_20260823", True),
    ("venombackup_2026", True),
    ("venombackup_offsite", True),
    ("venombackup_nas", True),
    ("venombackup_", True),
    ("VenomBackup_Offsite", True),      # filesystems here are often case-blind
    ("venombackup", False),             # no separator, so not the pattern
    ("venomvault", False),
    ("music", False),
])
def test_any_suffix_counts_as_a_backup_name(tmp_path, name, expected):
    """The date is a convenience, not a format anyone has to keep."""
    assert has_backup_name(tmp_path / name) is expected


@pytest.mark.parametrize("name, expected", [
    ("venombackup_20260823", True),     # generated: restamped on sync
    ("venombackup_2026", True),
    ("venombackup_offsite", False),     # chosen: kept forever
    ("venombackup_nas", False),
    ("venombackup_20260823_2", False),  # a collision suffix, not a date
    ("venombackup_", False),
])
def test_only_generated_names_are_restamped(tmp_path, name, expected):
    assert has_generated_name(tmp_path / name) is expected


# ============================================================== choosing one


def test_a_named_folder_is_found_even_with_no_marker(vault, tmp_path):
    """Somebody made the folder by hand; nothing has been written to it yet."""
    drive = tmp_path / "drive"
    (drive / "venombackup_offsite").mkdir(parents=True)

    assert scan.choose_backup_folder(drive, vault) == drive / "venombackup_offsite"


def test_picking_the_backup_itself_is_honoured(synced, vault):
    assert scan.choose_backup_folder(synced, vault) == synced


def test_the_backup_belonging_to_this_vault_wins(vault, tmp_path):
    """A drive with several backups syncs to this library's, not the newest."""
    drive = tmp_path / "drive"
    drive.mkdir()

    stranger = _seed_vault(tmp_path / "stranger")
    theirs = drive / "venombackup_theirs"
    mine = drive / "venombackup_mine"
    for folder, owner in ((theirs, stranger), (mine, vault)):
        folder.mkdir()
        manifest = Manifest.load(folder)
        manifest.source_vault_id = vault_id(owner)
        manifest.save(folder)
    # Make the stranger's the most recently touched, so recency alone would pick it.
    os.utime(theirs, None)

    assert scan.choose_backup_folder(drive, vault) == mine


def test_a_strangers_backup_is_never_picked_for_you(vault, tmp_path):
    """A new backup is proposed beside it: two libraries, two backups, one drive."""
    drive = tmp_path / "drive"
    drive.mkdir()
    stranger = _seed_vault(tmp_path / "stranger")
    theirs = drive / "venombackup_theirs"
    theirs.mkdir()
    manifest = Manifest.load(theirs)
    manifest.source_vault_id = vault_id(stranger)
    manifest.save(theirs)

    assert scan.choose_backup_folder(drive, vault) is None


def test_a_strangers_backup_picked_directly_is_still_honoured(vault, tmp_path):
    """It has to reach the review pane so the mismatch can be seen and refused."""
    stranger = _seed_vault(tmp_path / "stranger")
    theirs = tmp_path / "drive" / "venombackup_theirs"
    theirs.mkdir(parents=True)
    manifest = Manifest.load(theirs)
    manifest.source_vault_id = vault_id(stranger)
    manifest.save(theirs)

    assert scan.choose_backup_folder(theirs, vault) == theirs
    assert build_plan(vault, theirs, ALL).identity is Identity.MISMATCH


def test_a_folder_with_nothing_backup_shaped_in_it_chooses_nothing(vault, tmp_path):
    drive = tmp_path / "drive"
    (drive / "holiday-photos").mkdir(parents=True)

    assert scan.choose_backup_folder(drive, vault) is None


# ============================================================== restamping


def test_a_dated_folder_moves_on_to_todays_date(vault, tmp_path):
    dated = tmp_path / "drive" / "venombackup_20200101"
    dated.mkdir(parents=True)

    result = apply_plan(build_plan(vault, dated, ALL))

    expected = dated.with_name(suggested_backup_name())
    assert result.destination == expected
    assert expected.is_dir() and not dated.exists()
    assert is_backup(expected)


def test_a_chosen_name_survives_every_sync(vault, tmp_path):
    named = tmp_path / "drive" / "venombackup_offsite"
    named.mkdir(parents=True)

    result = apply_plan(build_plan(vault, named, ALL))

    assert result.destination == named
    assert named.is_dir()


def test_a_taken_name_leaves_the_folder_alone_with_a_warning(vault, tmp_path):
    drive = tmp_path / "drive"
    dated = drive / "venombackup_20200101"
    dated.mkdir(parents=True)
    (drive / suggested_backup_name()).mkdir()      # the name it would want

    result = apply_plan(build_plan(vault, dated, ALL))

    assert result.destination == dated, "the run succeeded; only the rename did not"
    assert dated.is_dir()
    assert any("already taken" in w for w in result.warnings)


def test_a_restamped_backup_is_found_again_next_time(vault, tmp_path):
    """The whole point of adopting the new path: no second backup beside the first."""
    dated = tmp_path / "drive" / "venombackup_20200101"
    dated.mkdir(parents=True)
    moved = apply_plan(build_plan(vault, dated, ALL)).destination

    assert scan.choose_backup_folder(tmp_path / "drive", vault) == moved
    assert build_plan(vault, moved, ALL).identity is Identity.MATCH


# ============================================================== round trip


def test_a_second_sync_after_adding_a_track_writes_exactly_one_file(vault, backup):
    apply_plan(build_plan(vault, backup, ALL))

    (vault / "mix" / "0001-0100" / "000A.opus").write_bytes(b"the new one" * 16)
    result = apply_plan(build_plan(vault, backup, ALL))

    assert result.written == 1
    assert (backup / "mix" / "0001-0100" / "000A.opus").exists()


# ============================================================== view model
#
# The engine tests above call the functions directly. These drive the real
# worker threads, because that is where the phase machine and the reference
# ownership rule in app.viewmodels.worker_threads actually live — and a worker
# released at the wrong moment crashes the process rather than failing a test.


def _pump(app, vm, until, timeout: float = 20.0) -> None:
    """Run the event loop until *until* is true, or give up.

    Deliberately the same shape as ``_pump_until`` in ``test_worker_threads``:
    a bare ``processEvents()`` and a short sleep. The tempting
    ``processEvents(AllEvents, maxTime)`` overload loops internally, and will
    happily run a ``QThread``'s deferred delete while that same thread's
    ``finished`` signal is still being emitted — which segfaults, and looks
    exactly like a bug in the code under test.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if until():
            return
        time.sleep(0.001)
    raise AssertionError(f"timed out in phase {vm.phase!r}")


@pytest.fixture
def vm(qt_app, vault, backup):
    """A view model with real settings, backed by an in-memory session.

    Not ``BackupSettings(None)``: like its siblings in ``app/settings``, that
    serves defaults and silently drops writes, so the destination would never
    be set and every scan here would fail with "choose a folder".
    """
    from app.settings.backup import BackupSettings
    from app.settings.store import SettingsStore
    from app.viewmodels.backup_view_model import BackupViewModel

    model = BackupViewModel(BackupSettings(SettingsStore({}, {})))
    model.destination = str(backup)
    assert model.destination == str(backup)
    yield model
    model.shutdown()


@pytest.mark.parametrize("name", ["includeTracks", "includeBacking",
                                  "includeGuis", "includeModels"])
def test_every_include_toggle_actually_toggles(qt_app, vm, name):
    """Each one is written from QML as a plain property assignment.

    This exists because all four silently did nothing: their setters called a
    helper that had never been written, and PySide turns the resulting
    AttributeError into a console warning rather than an error. The switch moved
    under the cursor and the value never changed.
    """
    before = getattr(vm, name)

    setattr(vm, name, not before)
    assert getattr(vm, name) is (not before)

    setattr(vm, name, before)
    assert getattr(vm, name) is before


def test_toggling_an_option_persists_it(qt_app, vm):
    vm.includeModels = True

    assert vm.includeModels
    assert vm._settings.models, "written through to the settings store"


def test_toggling_an_option_drops_a_stale_plan(qt_app, vm):
    vm.scan()
    _pump(qt_app, vm, lambda: vm.phase == "review")
    assert vm.hasPlan

    vm.includeBacking = not vm.includeBacking

    assert not vm.hasPlan, "the numbers no longer describe what confirm would do"


def test_the_review_toggles_change_the_plan_without_rescanning(qt_app, vm, vault, backup):
    vm.scan()
    _pump(qt_app, vm, lambda: vm.phase == "review")

    vm.deleteExtras = True
    assert vm.deleteExtras
    vm.deleteExtras = False
    assert not vm.deleteExtras
    assert vm.phase == "review", "neither costs another scan"


def test_the_view_model_scans_then_waits_for_confirmation(qt_app, vm, backup):
    assert vm.phase == "idle"

    vm.scan()
    _pump(qt_app, vm, lambda: vm.phase == "review")

    assert vm.addedCount == 5
    assert vm.canConfirm
    assert not is_backup(backup), "a scan must not have written anything"


def test_the_view_model_writes_only_on_confirm(qt_app, vm, backup, vault):
    vm.scan()
    _pump(qt_app, vm, lambda: vm.phase == "review")
    vm.confirm()
    _pump(qt_app, vm, lambda: vm.phase == "done")

    assert vm.writtenCount == 5
    assert is_backup(backup)
    assert (backup / ".data" / "tracks.db").exists()


def test_the_view_model_refuses_a_mismatched_backup(qt_app, vm, vault, backup, tmp_path):
    vm.scan()
    _pump(qt_app, vm, lambda: vm.phase == "review")
    vm.confirm()
    _pump(qt_app, vm, lambda: vm.phase == "done")

    stranger = _seed_vault(tmp_path / "stranger")
    manifest = Manifest.load(backup)
    manifest.source_vault_id = vault_id(stranger)
    manifest.save(backup)

    vm.reset()
    vm.scan()
    _pump(qt_app, vm, lambda: vm.phase == "review")

    assert vm.identityMismatch
    assert not vm.canConfirm, "confirm stays dead until the override is set"

    vm.overrideMismatch = True
    assert vm.canConfirm


def test_deleting_starts_off_on_every_run(qt_app, vm, vault, backup):
    vm.scan()
    _pump(qt_app, vm, lambda: vm.phase == "review")
    vm.deleteExtras = True
    vm.confirm()
    _pump(qt_app, vm, lambda: vm.phase == "done")

    vm.reset()
    assert not vm.deleteExtras, "never carried over from the previous run"


def test_a_refused_destination_reports_an_error_and_stays_idle(qt_app, vm, vault):
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    vm.destination = str(vault / "mix")
    vm.scan()
    _pump(qt_app, vm, lambda: bool(errors))

    assert "inside the vault" in errors[0]
    assert vm.phase == "idle"
