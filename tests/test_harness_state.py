"""M1 only: real SQLite concurrency/crashes, no browser or provider requests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier

import pytest

from crawler_harness import JobStore, OwnershipError, StateError
from experiments.tiktok_comment_dom.extractor import CommentSession
from experiments.tiktok_comment_dom.persistence import CommentPersistence

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "comments.sqlite3"
    repo = CommentPersistence(path, tmp_path / "lake")
    repo.close()
    return path


def create(store, **kwargs):
    values = dict(source="tiktok", kind="collection", place_id="fixture-place",
                  entity_id="123", input_digest="fixture-input")
    values.update(kwargs)
    return store.create_job(**values)


def receipt(store, token, **kwargs):
    values = dict(source_complete=False, completion_evidence=None,
                  stop_reason="MAX_SCROLLS", event_ids=[], checkpoint_ref="fixture-checkpoint",
                  publication_verified=True, reconciliation_passed=True)
    values.update(kwargs)
    store.record_receipt(token, **values)


def test_duplicate_creation_and_reopen(database):
    store = JobStore(database)
    job = create(store)
    assert create(store) == job
    assert create(store, workflow="revisit-2") != job
    assert store.db.execute("SELECT COUNT(*) FROM harness_events").fetchone()[0] == 2
    store.close()
    again = JobStore(database)
    assert again.get(job)["state"] == "PENDING"
    assert create(again) == job
    again.close()


def test_two_workers_atomic_claim(database):
    store = JobStore(database)
    job = create(store)
    store.close()
    barrier = Barrier(2)

    def worker(owner):
        ledger = JobStore(database)
        try:
            barrier.wait(timeout=10)
            return ledger.claim(job, owner)
        finally:
            ledger.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(worker, ["worker-a", "worker-b"]))
    assert sum(c is not None for c in claims) == 1
    store = JobStore(database)
    assert store.get(job)["attempt_count"] == store.get(job)["generation"] == 1
    assert store.db.execute("SELECT COUNT(*) FROM harness_attempts").fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type='job.claimed'").fetchone()[0] == 1
    store.close()


def test_expired_lease_requires_quiescence_and_rejects_stale_worker(database):
    now = [100.0]
    store = JobStore(database, clock=lambda: now[0])
    job = create(store)
    old = store.claim(job, "worker-a", lease_seconds=5)
    store.heartbeat(old, lease_seconds=10)
    now[0] = 110
    assert store.claim(job, "worker-b") is None
    for operation in (lambda: store.heartbeat(old), lambda: store.save_checkpoint(old, "cp"),
                      lambda: store.transition(old, "FAILED", code="TECHNICAL_TIMEOUT"),
                      lambda: receipt(store, old)):
        with pytest.raises(OwnershipError):
            operation()
    with pytest.raises(OwnershipError):
        store.takeover(job, "worker-b", expected_generation=1,
                       verify_quiescent=lambda row: False, evidence_ref="not-stopped")
    new = store.takeover(job, "worker-b", expected_generation=1,
                         verify_quiescent=lambda row: True, evidence_ref="operator-processes-stopped")
    assert new.generation == 2 and new.attempt_id != old.attempt_id
    assert store.get(job)["attempt_count"] == 2
    store.save_checkpoint(new, "cp-2")
    for token in (old, replace(new, generation=1), replace(new, owner="worker-c"), replace(new, attempt_id=old.attempt_id)):
        with pytest.raises(OwnershipError):
            store.transition(token, "FAILED")
    assert store.get(job)["checkpoint_ref"] == "cp-2"
    assert store.db.execute("SELECT outcome_code FROM harness_attempts WHERE attempt_id=?", (old.attempt_id,)).fetchone()[0] == "QUIESCENT_TAKEOVER"
    store.close()


def test_takeover_generation_and_unexpired_checks(database):
    store = JobStore(database, clock=lambda: 100)
    job = create(store)
    store.claim(job, "worker-a")
    for generation in (0, 1):
        with pytest.raises(OwnershipError):
            store.takeover(job, "worker-b", expected_generation=generation,
                           verify_quiescent=lambda row: True, evidence_ref="operator-stopped")
    assert store.get(job)["generation"] == 1
    store.close()


@pytest.mark.parametrize("target", ["PENDING", "RUNNING", "COMPLETE", "PARTIAL", "BOGUS"])
def test_invalid_running_transitions(database, target):
    store = JobStore(database)
    job = create(store)
    token = store.claim(job, "worker-a")
    with pytest.raises(StateError):
        store.transition(token, target)
    assert store.get(job)["state"] == "RUNNING"
    store.close()


@pytest.mark.parametrize("target", ["FAILED", "PAUSED_NEEDS_HUMAN", "ACCESS_RESTRICTED"])
def test_stop_states_are_terminal_without_automatic_retry(database, target):
    store = JobStore(database)
    job = create(store)
    token = store.claim(job, "worker-a")
    store.transition(token, target, code="TEST_STOP")
    assert store.claim(job, "worker-b") is None
    with pytest.raises(OwnershipError):
        store.save_checkpoint(token, "late-write")
    assert store.db.execute("SELECT ended_at FROM harness_attempts").fetchone()[0] is not None
    store.close()


def test_complete_invariants_and_partial_preserved(database):
    store = JobStore(database)
    job = create(store)
    token = store.claim(job, "worker-a")
    with pytest.raises(StateError):
        store.transition(token, "PERSISTING")
    receipt(store, token)
    store.transition(token, "PERSISTING")
    with pytest.raises(StateError, match="coverage"):
        store.transition(token, "COMPLETE")
    store.transition(token, "PARTIAL")
    assert store.get(job)["state"] == "PARTIAL"
    assert store.get(job)["outcome_code"] == "MAX_SCROLLS"
    assert store.claim(job, "worker-b") is None
    store.close()
    reopened = JobStore(database)
    assert reopened.get(job)["state"] == "PARTIAL"
    reopened.close()


@pytest.mark.parametrize("flags", [dict(publication_verified=False), dict(reconciliation_passed=False)])
def test_complete_rejected_without_storage_attestations(database, flags):
    store = JobStore(database)
    job = create(store)
    token = store.claim(job, "worker-a")
    receipt(store, token, source_complete=True, completion_evidence="EXPLICIT_END", **flags)
    store.transition(token, "PERSISTING")
    with pytest.raises(StateError, match="reconciliation"):
        store.transition(token, "COMPLETE")
    assert store.get(job)["state"] == "PERSISTING"
    store.close()


def test_existing_outbox_integration_and_pending_membership(database):
    store = JobStore(database)
    job = create(store)
    token = store.claim(job, "worker-a")
    data_run = store.get(job)["data_run_id"]
    repo = CommentPersistence(database, database.parent / "lake")
    session = CommentSession("123")
    session.add({"url": "https://www.tiktok.com/@fixture/video/123", "cards": [
        {"text": "fixture", "created_at_raw": "2d ago", "item_attributes": {"data-comment-id": "id-1"}}
    ]}, "2026-10-12T00:00:00+00:00")
    repo.persist(session, run_id=data_run, place_id="fixture-place", input_digest="fixture-input",
                 next_page=1, provenance={"mode": "TEST"})
    ids = [r[0] for r in repo.db.execute("SELECT event_id FROM tiktok_comment_events")]
    receipt(store, token, source_complete=True, completion_evidence="EXPLICIT_END", event_ids=ids)
    store.transition(token, "PERSISTING")
    with pytest.raises(StateError, match="pending"):
        store.transition(token, "COMPLETE")
    repo.flush()  # Existing writer, no competing publication implementation.
    with pytest.raises(StateError, match="immutable"):
        receipt(store, token, source_complete=True, completion_evidence="EXPLICIT_END", event_ids=[])
    receipt(store, token, source_complete=True, completion_evidence="EXPLICIT_END", event_ids=ids)
    store.transition(token, "COMPLETE")
    assert repo.summary() == {"records": 1, "events": 1, "pending_events": 0}
    assert len(list((database.parent / "lake").rglob("*.parquet"))) == 1
    assert store.claim(job, "worker-b") is None
    repo.close()
    store.close()


def test_receipt_validation_and_no_match_dependencies(database):
    store = JobStore(database)
    discovery = create(store, kind="discovery", entity_id="search")
    collection = create(store, dependencies=[discovery])
    assert store.claim(collection, "worker-b") is None
    token = store.claim(discovery, "worker-a")
    with pytest.raises(StateError, match="evidence"):
        receipt(store, token, source_complete=True)
    with pytest.raises(StateError, match="uncommitted"):
        receipt(store, token, event_ids=["missing-event"])
    receipt(store, token, source_complete=True, completion_evidence="BOUNDED_SEARCH_FINISHED", stop_reason="NO_MATCH")
    store.transition(token, "PERSISTING")
    store.transition(token, "COMPLETE")
    assert store.claim(collection, "worker-b") is None
    store.close()


def test_successful_dependency_allows_independent_job(database):
    store = JobStore(database)
    discovery = create(store, kind="discovery", entity_id="search")
    dependent = create(store, dependencies=[discovery])
    token = store.claim(discovery, "worker-a")
    receipt(store, token, source_complete=True, completion_evidence="CANDIDATES_FOUND", stop_reason="MATCHES_FOUND")
    store.transition(token, "PERSISTING")
    store.transition(token, "COMPLETE")
    assert store.claim(dependent, "worker-b") is not None
    store.close()


@pytest.mark.parametrize("boundary,expected", [("before", 0), ("after", 1)])
def test_process_crash_before_after_job_commit(database, boundary, expected):
    store = JobStore(database)
    store.close()
    script = """
import os, sys
from crawler_harness import JobStore
store = JobStore(sys.argv[1])
if sys.argv[2] == 'before':
    store._event = lambda *args, **kwargs: os._exit(73)
store.create_job(source='tiktok',kind='collection',place_id='fixture-place',entity_id='123',input_digest='fixture-input')
os._exit(73)
"""
    result = subprocess.run([sys.executable, "-B", "-c", script, str(database), boundary],
                            cwd=ROOT, timeout=20, capture_output=True)
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    reopened = JobStore(database)
    assert reopened.db.execute("SELECT COUNT(*) FROM harness_jobs").fetchone()[0] == expected
    assert reopened.db.execute("SELECT COUNT(*) FROM harness_events").fetchone()[0] == expected
    assert reopened.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    job = create(reopened)
    assert create(reopened) == job
    reopened.close()


def test_claim_transaction_rolls_back_on_history_failure(database, monkeypatch):
    store = JobStore(database)
    job = create(store)
    def fail(*args):
        raise RuntimeError("fixture-history-failure")
    monkeypatch.setattr(store, "_event", fail)
    with pytest.raises(RuntimeError):
        store.claim(job, "worker-a")
    assert store.get(job)["state"] == "PENDING"
    assert store.get(job)["generation"] == 0
    assert store.db.execute("SELECT COUNT(*) FROM harness_attempts").fetchone()[0] == 0
    store.close()


def test_google_and_unknown_databases_rejected_without_mutation(tmp_path):
    for name, schema in [("crawler_state.sqlite3", "CREATE TABLE marker(x)"),
                         ("renamed.sqlite3", "CREATE TABLE review_state(x)"),
                         ("unknown.sqlite3", "CREATE TABLE marker(x)")]:
        path = tmp_path / name
        db = sqlite3.connect(path)
        db.execute(schema)
        db.close()
        before = path.read_bytes()
        with pytest.raises(StateError):
            JobStore(path)
        assert path.read_bytes() == before
    with pytest.raises(StateError):
        JobStore(tmp_path / "missing.sqlite3")
    assert not (tmp_path / "missing.sqlite3").exists()


def test_additive_schema_preserves_tiktok_and_rejects_drift(database):
    db = sqlite3.connect(database)
    before = db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'tiktok_%' ORDER BY name").fetchall()
    db.close()
    store = JobStore(database)
    assert [tuple(r) for r in store.db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'tiktok_%' ORDER BY name")] == before
    store.db.execute("ALTER TABLE harness_jobs ADD COLUMN unsupported TEXT")
    store.close()
    with pytest.raises(StateError, match="drift"):
        JobStore(database)


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan"), True])
def test_invalid_lease_duration(database, duration):
    store = JobStore(database)
    job = create(store)
    with pytest.raises(StateError):
        store.claim(job, "worker-a", lease_seconds=duration)
    assert store.get(job)["attempt_count"] == 0
    store.close()


def test_persisting_takeover_keeps_receipt_and_data_run(database):
    now = [100]
    store = JobStore(database, clock=lambda: now[0])
    job = create(store)
    old = store.claim(job, "worker-a", lease_seconds=5)
    data_run = store.get(job)["data_run_id"]
    receipt(store, old)
    store.transition(old, "PERSISTING")
    now[0] = 106
    new = store.takeover(job, "worker-b", expected_generation=1,
                         verify_quiescent=lambda row: True, evidence_ref="operator-quiescent")
    assert store.get(job)["state"] == "PERSISTING"
    assert store.get(job)["data_run_id"] == data_run
    store.transition(new, "PARTIAL")
    assert store.get(job)["state"] == "PARTIAL"
    store.close()


def test_no_match_cannot_complete_collection(database):
    store = JobStore(database)
    job = create(store)
    token = store.claim(job, "worker-a")
    receipt(store, token, source_complete=True, completion_evidence="TEST", stop_reason="NO_MATCH")
    store.transition(token, "PERSISTING")
    with pytest.raises(StateError, match="discovery"):
        store.transition(token, "COMPLETE")
    store.close()


@pytest.mark.parametrize("boundary,expected_attempts", [("before", 0), ("after", 1)])
def test_process_crash_during_claim_commit(database, boundary, expected_attempts):
    store = JobStore(database)
    job = create(store)
    store.close()
    script = """
import os, sys
from crawler_harness import JobStore
store = JobStore(sys.argv[1])
if sys.argv[3] == 'before':
    store._event = lambda *args, **kwargs: os._exit(74)
store.claim(sys.argv[2], 'worker-a')
os._exit(74)
"""
    result = subprocess.run([sys.executable, "-B", "-c", script, str(database), job, boundary],
                            cwd=ROOT, timeout=20, capture_output=True)
    assert result.returncode == 74, result.stderr.decode(errors="replace")
    reopened = JobStore(database)
    assert reopened.get(job)["attempt_count"] == reopened.get(job)["generation"] == expected_attempts
    assert reopened.db.execute("SELECT COUNT(*) FROM harness_attempts").fetchone()[0] == expected_attempts
    assert reopened.db.execute("SELECT COUNT(*) FROM harness_events").fetchone()[0] == 1 + expected_attempts
    assert reopened.get(job)["state"] == ("RUNNING" if expected_attempts else "PENDING")
    reopened.close()


@pytest.mark.parametrize("target", ["FAILED", "PAUSED_NEEDS_HUMAN", "ACCESS_RESTRICTED"])
def test_persisting_stop_transitions(database, target):
    store = JobStore(database)
    job = create(store)
    token = store.claim(job, "worker-a")
    receipt(store, token)
    store.transition(token, "PERSISTING")
    store.transition(token, target, code="TEST_STOP")
    assert store.get(job)["state"] == target
    assert store.db.execute("SELECT COUNT(*) FROM harness_receipts").fetchone()[0] == 1
    store.close()
