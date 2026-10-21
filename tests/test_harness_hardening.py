"""Direct M1 audit regressions; no browser, source requests or providers."""
from dataclasses import asdict
import json
import multiprocessing
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from crawler_harness import Claim, JobStore, OwnershipError, StateError
from experiments.tiktok_comment_dom.extractor import CommentSession
from experiments.tiktok_comment_dom.persistence import CommentPersistence


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "comments.sqlite3"
    repo = CommentPersistence(path, tmp_path / "lake")
    repo.close()
    return path


def create(store):
    return store.create_job(source="tiktok", kind="collection", place_id="place-1",
                            entity_id="123", input_digest="fixture-input")


def receipt(store, token, **updates):
    values = dict(source_complete=False, completion_evidence=None, stop_reason="MAX_SCROLLS",
                  event_ids=[], checkpoint_ref="checkpoint-1", publication_verified=False,
                  reconciliation_passed=False)
    values.update(updates)
    store.record_receipt(token, **values)


def test_heartbeat_cannot_revive_lease_expiring_during_write(database):
    now = [100]
    store = JobStore(database, clock=lambda: now[0])
    job = create(store)
    token = store.claim(job, "worker-a", lease_seconds=5)
    original = store._owned
    def expire_after_check(claim):
        row = original(claim)
        now[0] = 106
        return row
    store._owned = expire_after_check
    with pytest.raises(OwnershipError):
        store.heartbeat(token, lease_seconds=60)
    assert store.get(job)["lease_until"] == 105
    store.close()


def test_receipt_status_update_after_takeover_preserves_observation_origin(database):
    now = [100]
    store = JobStore(database, clock=lambda: now[0])
    job = create(store)
    old = store.claim(job, "worker-a", lease_seconds=5)
    receipt(store, old)
    store.transition(old, "PERSISTING")
    before = dict(store.db.execute("SELECT * FROM harness_receipts").fetchone())
    now[0] = 106
    new = store.takeover(job, "worker-b", expected_generation=1,
                         verify_quiescent=lambda row: True, evidence_ref="operator-quiescent")
    receipt(store, new, publication_verified=True, reconciliation_passed=True)
    after = dict(store.db.execute("SELECT * FROM harness_receipts").fetchone())
    assert (after["generation"], after["attempt_id"], after["created_at"]) == (
        before["generation"], before["attempt_id"], before["created_at"])
    store.transition(new, "PARTIAL")
    store.close()


@pytest.mark.parametrize("reason", ["AUTH_GATE", "CHALLENGE", "ACCESS_DENIED", "RATE_LIMITED"])
def test_restriction_receipt_cannot_finalize_complete(database, reason):
    store = JobStore(database)
    job = create(store)
    token = store.claim(job, "worker-a")
    receipt(store, token, source_complete=True, completion_evidence="EARLIER_END",
            stop_reason=reason, publication_verified=True, reconciliation_passed=True)
    store.transition(token, "PERSISTING")
    with pytest.raises(StateError):
        store.transition(token, "COMPLETE")
    assert store.get(job)["state"] == "PERSISTING"
    store.close()


def test_connection_pragmas_observed_without_changing_writer(database):
    repo = CommentPersistence(database, database.parent / "lake")
    try:
        writer = {name: repo.db.execute("PRAGMA " + name).fetchone()[0]
                  for name in ("journal_mode", "synchronous", "foreign_keys")}
        store = JobStore(database)
        try:
            harness = {name: store.db.execute("PRAGMA " + name).fetchone()[0]
                       for name in writer}
            assert harness["foreign_keys"] == 1
            assert harness["journal_mode"] == writer["journal_mode"]
            assert harness["synchronous"] == 2  # Actual local default FULL.
            assert writer == {name: repo.db.execute("PRAGMA " + name).fetchone()[0] for name in writer}
            print({"writer": writer, "harness": harness})
        finally:
            store.close()
    finally:
        repo.close()


@pytest.mark.parametrize("operation", ["checkpoint", "receipt", "transition"])
def test_expiration_before_commit_rolls_back_all_control_mutations(database, operation):
    now = [100]
    store = JobStore(database, clock=lambda: now[0])
    job = create(store)
    token = store.claim(job, "worker-a", lease_seconds=5)
    before = snapshot(store)
    original = store._event
    def expire_after_event(*args, **kwargs):
        original(*args, **kwargs)
        now[0] = 106
    store._event = expire_after_event
    with pytest.raises(OwnershipError, match="before commit"):
        if operation == "checkpoint":
            store.save_checkpoint(token, "checkpoint-2")
        elif operation == "receipt":
            receipt(store, token)
        else:
            store.transition(token, "FAILED", code="TECHNICAL_TIMEOUT")
    assert snapshot(store) == before
    store.close()


def snapshot(store):
    return {table: [tuple(row) for row in store.db.execute("SELECT * FROM " + table + " ORDER BY rowid")]
            for table in ("harness_jobs", "harness_attempts", "harness_receipts", "harness_events")}


@pytest.mark.parametrize("target", ["COMPLETE", "PARTIAL"])
@pytest.mark.parametrize("reason", ["AUTH_GATE", "CHALLENGE", "ACCESS_DENIED", "RATE_LIMITED", "UNKNOWN_403"])
def test_gate_receipts_never_hide_restriction_in_terminal_success(database, target, reason):
    store = JobStore(database)
    job = create(store)
    token = store.claim(job, "worker-a")
    receipt(store, token, stop_reason=reason, source_complete=True,
            completion_evidence="EARLIER_END", publication_verified=True, reconciliation_passed=True)
    store.transition(token, "PERSISTING")
    before = snapshot(store)
    with pytest.raises(StateError, match="access"):
        store.transition(token, target)
    assert snapshot(store) == before
    store.transition(token, "ACCESS_RESTRICTED", code=reason)
    store.close()


def test_persisting_source_observation_cannot_be_rewritten(database):
    store = JobStore(database)
    token = store.claim(create(store), "worker-a")
    receipt(store, token)
    store.transition(token, "PERSISTING")
    before = snapshot(store)
    with pytest.raises(StateError, match="immutable"):
        receipt(store, token, source_complete=True, completion_evidence="OTHER_END")
    assert snapshot(store) == before
    store.close()


def test_receipt_origin_must_reference_real_attempt_of_same_job(database):
    store = JobStore(database)
    token = store.claim(create(store), "worker-a")
    receipt(store, token)
    store.db.execute("UPDATE harness_receipts SET attempt_id='nonexistent'")
    with pytest.raises(StateError, match="provenance"):
        store.transition(token, "PERSISTING")
    store.close()


def persist_comment(repo, run_id, *, text="fixture", place="place-1", video="123"):
    session = CommentSession(video)
    session.add({"url": "https://www.tiktok.com/@fixture/video/" + video, "cards": [
        {"text": text, "created_at_raw": "2d ago", "item_attributes": {"data-comment-id": "id-1"}}
    ]}, "2026-10-12T00:00:00+00:00")
    repo.persist(session, run_id=run_id, place_id=place, input_digest="fixture-input",
                 next_page=1, provenance={"mode": "TEST"})
    return [r[0] for r in repo.db.execute("SELECT event_id FROM tiktok_comment_events")]


@pytest.mark.parametrize("wrong", ["place", "video", "run"])
def test_receipt_rejects_wrong_event_scope_atomically(database, wrong):
    store = JobStore(database)
    job = create(store)
    token = store.claim(job, "worker-a")
    repo = CommentPersistence(database, database.parent / "lake")
    run_id = store.get(job)["data_run_id"]
    ids = persist_comment(repo, "another-run" if wrong == "run" else run_id,
                          place="other-place" if wrong == "place" else "place-1",
                          video="456" if wrong == "video" else "123")
    before = snapshot(store)
    with pytest.raises(StateError, match="scope"):
        receipt(store, token, event_ids=ids)
    assert snapshot(store) == before
    repo.close()
    store.close()


@pytest.mark.parametrize("target", ["COMPLETE", "PARTIAL"])
def test_finalize_rechecks_exact_membership_after_new_scoped_event(database, target):
    store = JobStore(database)
    job = create(store)
    token = store.claim(job, "worker-a")
    repo = CommentPersistence(database, database.parent / "lake")
    run_id = store.get(job)["data_run_id"]
    ids = persist_comment(repo, run_id)
    repo.flush()
    receipt(store, token, source_complete=True, completion_evidence="EXPLICIT_END",
            event_ids=ids, publication_verified=True, reconciliation_passed=True)
    store.transition(token, "PERSISTING")
    persist_comment(repo, run_id, text="edited")
    before = snapshot(store)
    with pytest.raises(StateError, match="pending"):
        store.transition(token, target)
    repo.flush()
    with pytest.raises(StateError, match="membership"):
        store.transition(token, target)
    assert snapshot(store) == before
    repo.close()
    store.close()


def _claim_process(database, job, role, start, release, messages):
    store = JobStore(database)
    messages.put((role, "ready"))
    if not start.wait(15):
        raise RuntimeError("fixture start timeout")
    try:
        if role == "holder":
            original = store._new_attempt
            def held(*args, **kwargs):
                messages.put((role, "locked"))
                if not release.wait(15):
                    raise RuntimeError("fixture release timeout")
                return original(*args, **kwargs)
            store._new_attempt = held
        else:
            store.db.execute("PRAGMA busy_timeout=0")
            try:
                store.claim(job, role)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                messages.put((role, "blocked"))
            else:
                raise AssertionError("Expected forced SQLite writer contention")
            if not release.wait(15):
                raise RuntimeError("fixture release timeout")
            store.db.execute("PRAGMA busy_timeout=10000")
        token = store.claim(job, role)
        messages.put((role, "claimed" if token else "not-claimed"))
    finally:
        store.close()


def test_process_claims_with_forced_sqlite_contention(database):
    store = JobStore(database)
    job = create(store)
    store.close()
    context = multiprocessing.get_context("spawn")
    messages = context.Queue()
    release = context.Event()
    starts = [context.Event(), context.Event()]
    workers = [context.Process(target=_claim_process, args=(str(database), job, role, start, release, messages))
               for role, start in zip(("holder", "contender"), starts)]
    try:
        for worker in workers:
            worker.start()
        assert {messages.get(timeout=20), messages.get(timeout=20)} == {("holder", "ready"), ("contender", "ready")}
        starts[0].set()
        assert messages.get(timeout=20) == ("holder", "locked")
        starts[1].set()
        assert messages.get(timeout=20) == ("contender", "blocked")
        release.set()
        assert {messages.get(timeout=20), messages.get(timeout=20)} == {("holder", "claimed"), ("contender", "not-claimed")}
        for worker in workers:
            worker.join(timeout=20)
            assert worker.exitcode == 0
    finally:
        release.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        messages.close()
    store = JobStore(database)
    assert store.get(job)["generation"] == store.get(job)["attempt_count"] == 1
    assert len(snapshot(store)["harness_attempts"]) == 1
    store.close()


@pytest.mark.parametrize("action", ["receipt", "finalize", "complete", "takeover"])
@pytest.mark.parametrize("boundary", ["before", "after"])
def test_process_crash_receipt_finalize_takeover_transactions(database, action, boundary):
    store = JobStore(database, clock=lambda: 100)
    job = create(store)
    token = store.claim(job, "worker-a", lease_seconds=5)
    if action != "receipt":
        receipt(store, token, source_complete=action == "complete",
                completion_evidence="EXPLICIT_END" if action == "complete" else None,
                publication_verified=True, reconciliation_passed=True)
        store.transition(token, "PERSISTING")
    before = snapshot(store)
    store.close()
    script = """
import json, os, sys
from crawler_harness import Claim, JobStore
action, boundary = sys.argv[3:5]
store = JobStore(sys.argv[1], clock=lambda: 106 if action=='takeover' else 100)
token = Claim(**json.loads(sys.argv[2]))
if boundary == 'before':
    original = store._event
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        if action == 'takeover' and args[1] == 'ownership.released':
            return
        os._exit(75)
    store._event = crash
if action == 'receipt':
    store.record_receipt(token,source_complete=False,completion_evidence=None,stop_reason='MAX_SCROLLS',event_ids=[],checkpoint_ref='checkpoint-1')
elif action in ('finalize', 'complete'):
    store.transition(token, 'COMPLETE' if action=='complete' else 'PARTIAL')
else:
    store.takeover(token.job_id,'worker-b',expected_generation=1,verify_quiescent=lambda row: True,evidence_ref='operator-quiescent')
os._exit(75)
"""
    result = subprocess.run([sys.executable, "-B", "-c", script, str(database), json.dumps(asdict(token)), action, boundary],
                            cwd=Path(__file__).resolve().parents[1], timeout=20, capture_output=True)
    assert result.returncode == 75, result.stderr.decode(errors="replace")
    reopened = JobStore(database, clock=lambda: 106 if action == "takeover" else 100)
    after = snapshot(reopened)
    if boundary == "before":
        assert after == before
    elif action == "receipt":
        assert after["harness_jobs"] == before["harness_jobs"]
        assert after["harness_attempts"] == before["harness_attempts"]
        assert len(after["harness_receipts"]) == 1
        assert len(after["harness_events"]) == len(before["harness_events"]) + 1
    elif action in {"finalize", "complete"}:
        assert reopened.get(job)["state"] == ("COMPLETE" if action == "complete" else "PARTIAL")
        assert reopened.get(job)["lease_until"] is None
        assert reopened.db.execute("SELECT ended_at FROM harness_attempts").fetchone()[0] == 100
        assert after["harness_receipts"] == before["harness_receipts"]
        assert len(after["harness_events"]) == len(before["harness_events"]) + 1
    else:
        assert reopened.get(job)["generation"] == reopened.get(job)["attempt_count"] == 2
        assert reopened.get(job)["owner"] == "worker-b"
        assert reopened.get(job)["state"] == "PERSISTING"
        assert after["harness_receipts"] == before["harness_receipts"]
        assert len(after["harness_attempts"]) == 2
        assert reopened.db.execute("SELECT ended_at FROM harness_attempts WHERE attempt_id=?", (token.attempt_id,)).fetchone()[0] == 106
        assert len(after["harness_events"]) == len(before["harness_events"]) + 2
    assert reopened.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert reopened.db.execute("PRAGMA foreign_key_check").fetchall() == []
    reopened.close()


def _resumed_stale_process(database, serialized_token, resume, messages):
    # Clock deliberately predates expiry: rejection must rely on fencing, not time.
    store = JobStore(database, clock=lambda: 100)
    token = Claim(**serialized_token)
    messages.put("ready")
    try:
        if not resume.wait(15):
            raise RuntimeError("fixture resume timeout")
        rejected = []
        operations = {
            "heartbeat": lambda: store.heartbeat(token),
            "checkpoint": lambda: store.save_checkpoint(token, "stale-checkpoint"),
            "receipt": lambda: receipt(store, token),
            "transition": lambda: store.transition(token, "FAILED", code="STALE_ERROR"),
        }
        for name, operation in operations.items():
            try:
                operation()
            except OwnershipError:
                rejected.append(name)
        messages.put(rejected)
    finally:
        store.close()


def test_stale_process_resumes_after_explicit_mocked_takeover(database):
    now = [100]
    store = JobStore(database, clock=lambda: now[0])
    job = create(store)
    token = store.claim(job, "worker-a", lease_seconds=5)
    context = multiprocessing.get_context("spawn")
    resume = context.Event()
    messages = context.Queue()
    worker = context.Process(target=_resumed_stale_process, args=(str(database), asdict(token), resume, messages))
    try:
        worker.start()
        assert messages.get(timeout=20) == "ready"
        now[0] = 106
        # Fault injection ONLY: zombie process has no source/publisher side effects.
        # Production must establish real quiescence, not use this mock callback.
        store.takeover(job, "worker-b", expected_generation=1,
                       verify_quiescent=lambda row: True, evidence_ref="fixture-mocked-quiescence")
        before = snapshot(store)
        resume.set()
        assert messages.get(timeout=20) == ["heartbeat", "checkpoint", "receipt", "transition"]
        worker.join(timeout=20)
        assert worker.exitcode == 0
        assert snapshot(store) == before
    finally:
        resume.set()
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
        messages.close()
        store.close()
