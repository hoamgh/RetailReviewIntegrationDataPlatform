"""M2 real-process offline acceptance using the existing TikTok writer."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from crawler_harness import Claim, JobStore, OwnershipError, StateError
from crawler_harness.recovery import OfflineRecovery
from experiments.tiktok_comment_dom.extractor import CommentSession
from experiments.tiktok_comment_dom.persistence import CommentPersistence

ROOT = Path(__file__).resolve().parents[1]


def seed_fixture(root, *, complete=True, include_receipt=True, reason=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    repo = CommentPersistence(root / "comments.sqlite3", root / "lake")
    store = JobStore(root / "comments.sqlite3", clock=lambda: 100)
    job = store.create_job(source="tiktok", kind="collection", place_id="fixture-place",
                           entity_id="123", input_digest="fixture-input")
    token = store.claim(job, "process-a", lease_seconds=5)
    OfflineRecovery(store, root / "lake")  # Explicit isolated extension migration.
    session = CommentSession("123", limit=50)
    session.add({"url": "https://www.tiktok.com/@fixture/video/123",
                 "cards": [{"text": "fixture comment " + str(i), "created_at_raw": "2d ago"} for i in range(7)],
                 "end_of_comments_observed": complete}, "2026-10-12T00:00:00+00:00")
    if not complete or reason:
        session.pagination.update(exhausted=None, stop_reason=reason or "MAX_SCROLLS")
    repo.persist(session, run_id=store.get(job)["data_run_id"], place_id="fixture-place",
                 input_digest="fixture-input", next_page=1, provenance={"mode": "OFFLINE_FAULT_FIXTURE"})
    ids = [r[0] for r in repo.db.execute("SELECT event_id FROM tiktok_comment_events ORDER BY event_id")]
    if include_receipt:
        store.record_receipt(token, source_complete=complete and not reason,
            completion_evidence="EXPLICIT_END_MARKER" if complete and not reason else None,
            stop_reason=reason or ("EXPLICIT_END_MARKER" if complete else "MAX_SCROLLS"),
            event_ids=ids, checkpoint_ref="fixture-checkpoint")
        store.transition(token, "PERSISTING")
    metadata = {"job_id": job, "token": asdict(token), "event_ids": ids, "complete": complete}
    (root / "fixture.json").write_text(json.dumps(metadata), encoding="utf-8")
    return store, repo, token, metadata


def process_a(root, phase, complete):
    store, repo, token, metadata = seed_fixture(root, complete=complete, include_receipt=phase != "no_receipt")
    if phase in {"before_publish", "no_receipt"}:
        os._exit(81)  # Actual committed comments/events/checkpoint, no publication.
    if phase == "after_rename":
        original = Path.replace
        def renamed(path, target):
            result = original(path, target)
            if str(target).endswith(".parquet"):
                os._exit(81)  # Existing writer renamed; its acknowledgement not reached.
            return result
        Path.replace = renamed
    elif phase == "after_ack":
        original = CommentPersistence.flush
        def flushed(repository):
            original(repository)
            os._exit(81)  # All events acknowledged; receipt/finalize not reached.
        CommentPersistence.flush = flushed
    elif phase == "before_finalize_commit":
        original = store._event
        def final_event(row, event, *args, **kwargs):
            original(row, event, *args, **kwargs)
            if event in {"job.completed", "job.state_changed"} and args[1] in {"COMPLETE", "PARTIAL"}:
                os._exit(81)
        store._event = final_event
    OfflineRecovery(store, Path(root) / "lake").recover(metadata["job_id"], token,
        verify_quiescent=lambda row: True, evidence_ref="fixture-single-process-no-publisher")
    raise AssertionError("Fault boundary was not reached")


def fingerprints(lake):
    return {str(p.relative_to(lake)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(lake).rglob("*.parquet")}


def process_b(root):
    root = Path(root)
    metadata = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
    store = JobStore(root / "comments.sqlite3", clock=lambda: 106)
    # Parent waited for A's actual exit before launching B. This fixture callback
    # attests that externally established fact; it is NOT a process-death detector.
    token = store.takeover(metadata["job_id"], "process-b", expected_generation=1,
        verify_quiescent=lambda row: True, evidence_ref="parent-confirmed-process-a-exit")
    def no_collection(*args, **kwargs):
        raise AssertionError("Recovery must not recollect or persist source observations")
    CommentSession.add = no_collection
    CommentPersistence.persist = no_collection
    recovery = OfflineRecovery(store, root / "lake")
    result = recovery.recover(metadata["job_id"], token,
        verify_quiescent=lambda row: True, evidence_ref="parent-confirmed-exclusive-offline-sink")
    original_hashes = fingerprints(root / "lake")
    events_before = store.db.execute("SELECT COUNT(*) FROM tiktok_comment_events").fetchone()[0]
    used_before = store.db.execute("SELECT attempts_used FROM harness_recovery_policy WHERE job_id=?", (metadata["job_id"],)).fetchone()[0]
    repeats = []
    if result["status"] in {"COMPLETE", "PARTIAL"}:
        for _ in range(2):
            repeats.append(recovery.recover(metadata["job_id"], verify_quiescent=lambda row: True,
                evidence_ref="parent-confirmed-exclusive-offline-sink"))
        assert fingerprints(root / "lake") == original_hashes
        assert store.db.execute("SELECT COUNT(*) FROM tiktok_comment_events").fetchone()[0] == events_before
        assert store.db.execute("SELECT attempts_used FROM harness_recovery_policy WHERE job_id=?", (metadata["job_id"],)).fetchone()[0] == used_before
    result.update(repeats=repeats, event_ids=[r[0] for r in store.db.execute("SELECT event_id FROM tiktok_comment_events ORDER BY event_id")],
                  parquet_files=len(original_hashes), attempts_used=used_before,
                  job_state=store.get(metadata["job_id"])["state"],
                  pending_total=store.db.execute("SELECT COUNT(*) FROM tiktok_comment_events WHERE delivered=0").fetchone()[0])
    (root / "recovery_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    store.close()


def run_child(function, args, expected_exit=0):
    script = "import sys; sys.path.insert(0,sys.argv[1]); from test_harness_recovery import " + function + "; " + function + "(*" + repr(args) + ")"
    result = subprocess.run([sys.executable, "-B", "-c", script, str(ROOT / "tests")],
                            cwd=ROOT, timeout=30, capture_output=True)
    assert result.returncode == expected_exit, result.stderr.decode(errors="replace")


@pytest.mark.parametrize("complete", [True, False])
@pytest.mark.parametrize("phase", ["before_publish", "after_rename", "after_ack", "before_finalize_commit"])
def test_process_acceptance_and_two_idempotent_replays(tmp_path, phase, complete):
    run_child("process_a", [str(tmp_path), phase, complete], expected_exit=81)
    store = JobStore(tmp_path / "comments.sqlite3", clock=lambda: 106)
    metadata = json.loads((tmp_path / "fixture.json").read_text(encoding="utf-8"))
    assert store.db.execute("SELECT COUNT(*) FROM tiktok_comments").fetchone()[0] == 7
    assert store.db.execute("SELECT COUNT(*) FROM tiktok_comment_events").fetchone()[0] == 7
    assert store.db.execute("SELECT COUNT(*) FROM tiktok_comment_checkpoints").fetchone()[0] == 1
    before_files = fingerprints(tmp_path / "lake")
    assert len(before_files) == {"before_publish": 0, "after_rename": 1, "after_ack": 7, "before_finalize_commit": 7}[phase]
    pending = store.db.execute("SELECT COUNT(*) FROM tiktok_comment_events WHERE delivered=0").fetchone()[0]
    assert pending == (7 if phase in {"before_publish", "after_rename"} else 0)
    origin = dict(store.db.execute("SELECT * FROM harness_receipts").fetchone())
    store.close()
    run_child("process_b", [str(tmp_path)])
    result = json.loads((tmp_path / "recovery_result.json").read_text(encoding="utf-8"))
    expected = "COMPLETE" if complete else "PARTIAL"
    assert result["status"] == result["job_state"] == expected
    assert result["event_ids"] == metadata["event_ids"]
    assert result["pending_total"] == result["pending"] == result["duplicate_events"] == 0
    assert result["parquet_files"] == result["parquet_rows"] == result["sqlite_records"] == 7
    assert len(result["repeats"]) == 2 and all(r["already_finalized"] and r["status"] == expected for r in result["repeats"])
    assert result["attempts_used"] == (1 if phase == "before_publish" else 2)
    after_files = fingerprints(tmp_path / "lake")
    assert all(after_files[name] == digest for name, digest in before_files.items())
    store = JobStore(tmp_path / "comments.sqlite3", clock=lambda: 106)
    after = dict(store.db.execute("SELECT * FROM harness_receipts").fetchone())
    assert (after["generation"], after["attempt_id"], after["created_at"]) == (origin["generation"], origin["attempt_id"], origin["created_at"])
    attempts = [dict(r) for r in store.db.execute("SELECT * FROM harness_recovery_attempts ORDER BY ordinal")]
    assert attempts[-1]["status"] == "SUCCEEDED"
    if len(attempts) == 2:
        assert attempts[0]["status"] == "INTERRUPTED"
    payloads = {r[0]: json.loads(r[1]) for r in store.db.execute("SELECT event_id,payload FROM tiktok_comment_events")}
    rows = [row for p in (tmp_path / "lake").rglob("*.parquet") for row in pq.read_table(p, partitioning=None).to_pylist()]
    assert len(rows) == 7 and {row["event_id"] for row in rows} == set(payloads)
    assert all(row == payloads[row["event_id"]] for row in rows)
    assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
    store.close()


def test_process_missing_receipt_does_not_guess_completion(tmp_path):
    run_child("process_a", [str(tmp_path), "no_receipt", True], expected_exit=81)
    run_child("process_b", [str(tmp_path)])
    result = json.loads((tmp_path / "recovery_result.json").read_text(encoding="utf-8"))
    assert result["status"] == "RECEIPT_OR_STATE_INVALID"
    assert result["job_state"] == "PAUSED_NEEDS_HUMAN"
    assert result["pending_total"] == 7 and result["parquet_files"] == 0


@pytest.fixture
def seeded(tmp_path):
    store, repo, token, metadata = seed_fixture(tmp_path)
    yield store, repo, token, metadata, tmp_path
    repo.close()
    store.close()


def recover(seeded, **kwargs):
    store, repo, token, metadata, root = seeded
    return OfflineRecovery(store, root / "lake", **kwargs).recover(metadata["job_id"], token,
        verify_quiescent=lambda row: True, evidence_ref="fixture-exclusive-offline-sink")


@pytest.mark.parametrize("fault", ["missing", "payload", "duplicate", "truncated"])
def test_bad_artifacts_never_finalize_or_overwrite(seeded, fault):
    store, repo, token, metadata, root = seeded
    repo.flush()
    target = next((root / "lake").rglob("*.parquet"))
    if fault == "missing":
        target.unlink()  # Fault injection only, isolated fixture.
    elif fault == "payload":
        row = pq.read_table(target, partitioning=None).to_pylist()[0]
        row["text"] = "corrupted fixture"
        pq.write_table(pa.Table.from_pylist([row], schema=repo.schema()), target)
    elif fault == "duplicate":
        target.with_name("duplicate.parquet").write_bytes(target.read_bytes())
    else:
        target.write_bytes(b"truncated-fixture")
    before = fingerprints(root / "lake")
    result = recover(seeded)
    assert result["status"] in {"MISSING_PUBLISHED_ARTIFACT", "ARTIFACT_PAYLOAD_MISMATCH", "EXTRA_OR_DUPLICATE_ARTIFACT", "UNREADABLE_ARTIFACT"}
    assert store.get(metadata["job_id"])["state"] == "PAUSED_NEEDS_HUMAN"
    assert fingerprints(root / "lake") == before
    assert store.db.execute("SELECT COUNT(*) FROM tiktok_comment_events").fetchone()[0] == 7


def test_budget_backoff_history_persists_and_never_resets(seeded, monkeypatch):
    store, repo, token, metadata, root = seeded
    now = [100]
    store.clock = lambda: now[0]
    store.heartbeat(token, lease_seconds=3600)
    calls = []
    def interrupted(repository):
        calls.append(1)
        raise OSError("fixture-storage-interruption")
    monkeypatch.setattr(CommentPersistence, "flush", interrupted)
    engine = OfflineRecovery(store, root / "lake", max_attempts=2, backoff_seconds=(5, 30))
    arguments = dict(verify_quiescent=lambda row: True, evidence_ref="fixture-exclusive")
    assert engine.recover(metadata["job_id"], token, **arguments)["status"] == "RETRY_LATER"
    assert engine.recover(metadata["job_id"], token, **arguments)["status"] == "BACKOFF_NOT_ELAPSED"
    assert len(calls) == 1
    now[0] = 105
    assert engine.recover(metadata["job_id"], token, **arguments)["status"] == "BUDGET_EXHAUSTED"
    reopened = JobStore(store.path, clock=lambda: 1000)
    try:
        again = OfflineRecovery(reopened, root / "lake", max_attempts=2, backoff_seconds=(5, 30))
        assert again.recover(metadata["job_id"], token, **arguments)["status"] == "BUDGET_EXHAUSTED"
        assert len(calls) == 2
        assert reopened.db.execute("SELECT attempts_used FROM harness_recovery_policy").fetchone()[0] == 2
        assert [r[0] for r in reopened.db.execute("SELECT status FROM harness_recovery_attempts ORDER BY ordinal")] == ["FAILED", "FAILED"]
        assert reopened.get(metadata["job_id"])["state"] == "PERSISTING"
    finally:
        reopened.close()


@pytest.mark.parametrize("reason", ["AUTH_GATE", "CHALLENGE", "ACCESS_DENIED", "RATE_LIMITED", "UNKNOWN_403"])
def test_gates_preserved_no_storage_or_collection_retry(tmp_path, reason, monkeypatch):
    store, repo, token, metadata = seed_fixture(tmp_path, complete=False, reason=reason)
    def forbidden(*args, **kwargs):
        raise AssertionError("Restricted job must not retry source or publication")
    monkeypatch.setattr(CommentPersistence, "flush", forbidden)
    monkeypatch.setattr(CommentPersistence, "persist", forbidden)
    monkeypatch.setattr(CommentSession, "add", forbidden)
    engine = OfflineRecovery(store, tmp_path / "lake")
    result = engine.recover(metadata["job_id"], token, verify_quiescent=lambda row: True, evidence_ref="fixture-exclusive")
    expected = "PAUSED_NEEDS_HUMAN" if reason == "UNKNOWN_403" else "ACCESS_RESTRICTED"
    assert result == {"status": "HELD", "job_state": expected}
    assert engine.recover(metadata["job_id"], verify_quiescent=lambda row: True, evidence_ref="fixture-exclusive") == result
    assert store.db.execute("SELECT COUNT(*) FROM harness_recovery_attempts").fetchone()[0] == 0
    assert repo.summary()["pending_events"] == 7 and not fingerprints(tmp_path / "lake")
    repo.close()
    store.close()


def test_expiry_quiescence_stale_token_and_inflight_slot(seeded):
    store, repo, old, metadata, root = seeded
    engine = OfflineRecovery(store, root / "lake")
    assert engine.recover(metadata["job_id"], old, verify_quiescent=lambda row: False, evidence_ref="fixture-no-proof")["status"] == "QUIESCENCE_REQUIRED"
    store.clock = lambda: 106
    assert store.claim(metadata["job_id"], "worker-b") is None
    with pytest.raises(OwnershipError):
        engine.recover(metadata["job_id"], old, verify_quiescent=lambda row: True, evidence_ref="fixture-exclusive")
    token = store.takeover(metadata["job_id"], "worker-b", expected_generation=1,
        verify_quiescent=lambda row: True, evidence_ref="fixture-operator-stopped-old-process")
    with pytest.raises(OwnershipError):
        engine.recover(metadata["job_id"], old, verify_quiescent=lambda row: True, evidence_ref="fixture-exclusive")
    engine._reserve(token)
    assert engine.recover(metadata["job_id"], token, verify_quiescent=lambda row: True, evidence_ref="fixture-exclusive")["status"] == "RECOVERY_IN_PROGRESS"
    assert store.db.execute("SELECT attempts_used FROM harness_recovery_policy").fetchone()[0] == 1
    assert not fingerprints(root / "lake")


def test_policy_change_not_an_automatic_budget_increase(seeded):
    store, repo, token, metadata, root = seeded
    engine = OfflineRecovery(store, root / "lake", max_attempts=1)
    engine._reserve(token)
    changed = OfflineRecovery(store, root / "lake", max_attempts=3)
    with pytest.raises(StateError, match="immutable"):
        changed.recover(metadata["job_id"], token, verify_quiescent=lambda row: True, evidence_ref="fixture-exclusive")
    assert store.db.execute("SELECT max_attempts,attempts_used FROM harness_recovery_policy").fetchone()[:] == (1, 1)


def test_exhausted_budget_does_not_hide_still_running_recovery(seeded):
    store, repo, token, metadata, root = seeded
    engine = OfflineRecovery(store, root / "lake", max_attempts=1)
    assert engine._reserve(token)[0]
    result = engine.recover(metadata["job_id"], token,
        verify_quiescent=lambda row: True, evidence_ref="fixture-exclusive")
    assert result["status"] == "RECOVERY_IN_PROGRESS"
    policy = store.db.execute("SELECT attempts_used,hold_code FROM harness_recovery_policy").fetchone()
    assert tuple(policy) == (1, None)


def test_false_receipt_coverage_not_promoted(seeded):
    store, repo, token, metadata, root = seeded
    saved = repo.load_checkpoint(store.get(metadata["job_id"])["data_run_id"], "fixture-place", "123")
    state = json.loads(saved["state_json"])
    state["pagination"]["exhausted"] = None
    with repo.db:
        repo.db.execute("UPDATE tiktok_comment_checkpoints SET state_json=?,status='PARTIAL'", (json.dumps(state),))
    result = recover(seeded)
    assert result["status"] == "COVERAGE_MISMATCH"
    assert not fingerprints(root / "lake")


def test_unrelated_pending_events_are_not_flushed_by_job_token(seeded):
    store, repo, token, metadata, root = seeded
    session = CommentSession("456")
    session.add({"url": "https://www.tiktok.com/@fixture/video/456", "cards": [{"text": "another job"}]}, "2026-10-12T00:00:00+00:00")
    repo.persist(session, run_id="other-run", place_id="other-place", input_digest="other-input", next_page=1, provenance={"mode": "TEST"})
    result = recover(seeded)
    assert result["status"] == "UNRELATED_PENDING_EVENTS"
    assert repo.summary()["pending_events"] == 8
    assert not fingerprints(root / "lake")


def test_optional_schema_reopens_and_drift_is_rejected(seeded):
    store, repo, token, metadata, root = seeded
    reopened = JobStore(store.path)
    reopened.close()
    store.db.execute("DROP TABLE harness_recovery_attempts")  # Isolated fault fixture.
    with pytest.raises(StateError, match="Partial"):
        JobStore(store.path)
