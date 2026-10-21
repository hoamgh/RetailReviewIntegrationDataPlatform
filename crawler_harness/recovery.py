"""Bounded OFFLINE outbox replay. No collection, browser, AI or new writer.

Requires explicit quiescence of all source writers/publishers for this DB/lake.
SQLite control fencing does not fence those side effects (M3 prerequisite).
Each call performs at most one reserved attempt; backoff never sleeps or loops.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sqlite3
import uuid

from .state import OwnershipError, RESTRICTED_STOP_REASONS, StateError, _identifier, _json
from experiments.tiktok_comment_dom.persistence import CommentPersistence, FIELDS, digest


class IntegrityError(StateError):
    """Safe bounded code only; never include source payloads in exceptions."""


def scoped_events(store, job):
    from .event_scope import is_managed, indexed_events
    if is_managed(store,job):
        return indexed_events(store,job)
    events = {}
    for row in store.db.execute("SELECT event_id,payload,delivered FROM tiktok_comment_events"):
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError) as exc:
            raise IntegrityError("MALFORMED_OUTBOX") from exc
        if not isinstance(payload, dict):
            raise IntegrityError("MALFORMED_OUTBOX")
        if (payload.get("place_id"), payload.get("video_id"), payload.get("run_id")) != (
                job["place_id"], job["entity_id"], job["data_run_id"]):
            continue
        if (payload.get("event_id") != row["event_id"] or
                not set(FIELDS).issubset(payload) or
                digest([payload["storage_key"], payload["revision"]]) != row["event_id"]):
            raise IntegrityError("INVALID_EVENT_IDENTITY")
        events[row["event_id"]] = {"payload": payload, "delivered": row["delivered"]}
    return events


def verify(store, lake, job, receipt, *, allow_unpublished=False):
    """Read-only exact job/run event, file, checkpoint and current-content checks."""
    import pyarrow.parquet as pq

    events = scoped_events(store, job)
    from .event_scope import is_managed, sealed_manifest
    if is_managed(store,job):
        manifest = sealed_manifest(store,job,receipt,lake)
        if set(events) != set(manifest['payload_hashes']) or any(
                digest(e['payload']) != manifest['payload_hashes'][eid] for eid,e in events.items()):
            raise IntegrityError('SEALED_PAYLOAD_MISMATCH')
    if sorted(events) != json.loads(receipt["event_ids_json"]):
        raise IntegrityError("EVENT_MEMBERSHIP_MISMATCH")
    saved = store.db.execute("""SELECT * FROM tiktok_comment_checkpoints
        WHERE run_id=? AND place_id=? AND video_id=?""",
        (job["data_run_id"], job["place_id"], job["entity_id"])).fetchone()
    if saved is None or saved["input_digest"] != job["input_digest"]:
        raise IntegrityError("CHECKPOINT_MISSING_OR_CHANGED")
    try:
        checkpoint = json.loads(saved["state_json"])
    except (ValueError, TypeError) as exc:
        raise IntegrityError("INVALID_CHECKPOINT") from exc
    if receipt["source_complete"] and (not receipt["completion_evidence"] or
            saved["status"] != "COMPLETE" or checkpoint.get("pagination", {}).get("exhausted") is not True):
        raise IntegrityError("COVERAGE_MISMATCH")
    lake = Path(lake).resolve()
    expected_paths = set()
    latest = {}
    pending = 0
    published = 0
    for event_id, event in events.items():
        payload = event["payload"]
        date = payload["observed_at"][:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise IntegrityError("INVALID_PARTITION")
        target = lake / ("crawl_date=" + date) / ("run_id=" + job["data_run_id"]) / ("event-" + event_id + ".parquet")
        if not target.resolve().is_relative_to(lake):
            raise IntegrityError("ARTIFACT_PATH_ESCAPE")
        expected_paths.add(target)
        pending += not event["delivered"]
        if target.exists():
            try:
                table = pq.read_table(target, partitioning=None)
                if not table.schema.equals(CommentPersistence.schema(), check_metadata=True) or table.to_pylist() != [payload]:
                    raise IntegrityError("ARTIFACT_PAYLOAD_MISMATCH")
            except IntegrityError:
                raise
            except (ValueError, OSError) as exc:
                # A malformed existing artifact is never overwritten automatically.
                raise IntegrityError("UNREADABLE_ARTIFACT") from exc
            published += 1
        elif event["delivered"] or receipt["publication_verified"] or not allow_unpublished:
            raise IntegrityError("MISSING_PUBLISHED_ARTIFACT")
        key = payload["storage_key"]
        if key not in latest or payload["revision"] > latest[key]["revision"]:
            latest[key] = payload
    actual_paths = {p for p in lake.rglob("*.parquet") if "run_id=" + job["data_run_id"] in p.relative_to(lake).parts}
    if actual_paths - expected_paths:
        raise IntegrityError("EXTRA_OR_DUPLICATE_ARTIFACT")
    for key, payload in latest.items():
        current = store.db.execute("SELECT * FROM tiktok_comments WHERE storage_key=?", (key,)).fetchone()
        if current is None or any(current[field] != payload[field] for field in FIELDS
                                  if field not in {"observed_at", "run_id", "provenance"}):
            raise IntegrityError("SQLITE_CONTENT_MISMATCH")
    current_keys = {r[0] for r in store.db.execute("""SELECT storage_key FROM tiktok_comments
        WHERE place_id=? AND video_id=? AND run_id=?""", (job["place_id"], job["entity_id"], job["data_run_id"]))}
    if current_keys - latest.keys():
        raise IntegrityError("SQLITE_RECORD_WITHOUT_EVENT_EVIDENCE")
    if pending and not allow_unpublished:
        raise IntegrityError("PENDING_OUTBOX")
    return {"event_count": len(events), "sqlite_records": len(latest),
            "parquet_rows": published, "pending": pending, "duplicate_events": 0}


class OfflineRecovery:
    def __init__(self, store, lake, *, max_attempts=3, backoff_seconds=(5, 30)):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise StateError("Recovery budget must be between 1 and 10")
        if not backoff_seconds or any(type(n) not in (int, float) or not math.isfinite(n) or n < 0 for n in backoff_seconds):
            raise StateError("Finite nonnegative backoff schedule required")
        self.store = store
        self.lake = Path(lake).resolve()
        if "google_maps_reviews" in self.lake.parts:
            raise StateError("Google lake protected")
        self.max_attempts = max_attempts
        self.backoff = tuple(backoff_seconds)
        store.enable_recovery_schema()

    def _running_attempts(self, job_id):
        return self.store.db.execute("SELECT * FROM harness_recovery_attempts WHERE status='STARTED'").fetchall()

    def _reserve(self, token):
        store = self.store
        with store._transaction(token):
            job = store._owned(token)
            policy = store.db.execute("SELECT * FROM harness_recovery_policy WHERE job_id=?", (token.job_id,)).fetchone()
            if policy is None:
                store.db.execute("""INSERT INTO harness_recovery_policy
                    (job_id,max_attempts,backoff_json,lake_root) VALUES(?,?,?,?)""",
                    (token.job_id, self.max_attempts, _json(self.backoff), str(self.lake)))
                policy = store.db.execute("SELECT * FROM harness_recovery_policy WHERE job_id=?", (token.job_id,)).fetchone()
            if (policy["max_attempts"], policy["backoff_json"], policy["lake_root"]) != (
                    self.max_attempts, _json(self.backoff), str(self.lake)):
                raise StateError("Recovery policy/root immutable; operator review required")
            running = self._running_attempts(token.job_id)
            for attempt in running:
                if attempt["job_id"] != token.job_id or attempt["generation"] >= token.generation:
                    return None, "RECOVERY_IN_PROGRESS"
                # Explicit takeover + quiescence preceded this call; old reserved
                # requests consume budget even if a process died before publication.
                store.db.execute("""UPDATE harness_recovery_attempts SET status='INTERRUPTED',
                    error_code='PROCESS_INTERRUPTED',ended_at=? WHERE recovery_id=?""",
                    (store._now(), attempt["recovery_id"]))
                store._event(job, "recovery.failed", job["state"], job["state"], "PROCESS_INTERRUPTED")
            if policy["hold_code"]:
                return None, policy["hold_code"]
            if policy["attempts_used"] >= policy["max_attempts"]:
                store.db.execute("UPDATE harness_recovery_policy SET hold_code='BUDGET_EXHAUSTED' WHERE job_id=?", (token.job_id,))
                store._event(job, "recovery.failed", job["state"], job["state"], "BUDGET_EXHAUSTED")
                return None, "BUDGET_EXHAUSTED"
            if policy["next_eligible_at"] > store._now():
                return None, "BACKOFF_NOT_ELAPSED"
            ordinal = policy["attempts_used"] + 1
            recovery_id = uuid.uuid4().hex
            store.db.execute("UPDATE harness_recovery_policy SET attempts_used=? WHERE job_id=?", (ordinal, token.job_id))
            store.db.execute("""INSERT INTO harness_recovery_attempts
                (recovery_id,job_id,attempt_id,generation,ordinal,status,started_at) VALUES(?,?,?,?,?,'STARTED',?)""",
                (recovery_id, token.job_id, token.attempt_id, token.generation, ordinal, store._now()))
            store._event(job, "recovery.proposed", job["state"], job["state"], "OUTBOX_ONLY")
            store._event(job, "recovery.executed", job["state"], job["state"], "OUTBOX_ONLY")
            return recovery_id, None

    def _failure(self, token, recovery_id, code, *, retryable):
        store = self.store
        with store._transaction(token):
            job = store._owned(token)
            policy = store.db.execute("SELECT * FROM harness_recovery_policy WHERE job_id=?", (token.job_id,)).fetchone()
            exhausted = policy["attempts_used"] >= policy["max_attempts"]
            hold = "BUDGET_EXHAUSTED" if retryable and exhausted else None if retryable else code
            delay = self.backoff[min(policy["attempts_used"] - 1, len(self.backoff) - 1)]
            store.db.execute("""UPDATE harness_recovery_attempts SET status='FAILED',error_code=?,ended_at=?
                WHERE recovery_id=? AND attempt_id=? AND status='STARTED'""",
                (code, store._now(), recovery_id, token.attempt_id))
            store.db.execute("UPDATE harness_recovery_policy SET next_eligible_at=?,hold_code=? WHERE job_id=?",
                            (store._now() + delay, hold, token.job_id))
            store._event(job, "recovery.failed", job["state"], job["state"], code)
            if hold and not retryable:
                store._transition_in_transaction(token, "PAUSED_NEEDS_HUMAN", code=code)
            return {"status": hold or "RETRY_LATER", "error_code": code,
                    "attempts_used": policy["attempts_used"], "next_eligible_at": store._now() + delay}

    def recover(self, job_id, token=None, *, verify_quiescent, evidence_ref):
        """One offline step. Never claim/take over/recollect or automatically retry."""
        store = self.store
        _identifier(evidence_ref)
        job = store.get(job_id)
        if job["state"] in {"ACCESS_RESTRICTED", "PAUSED_NEEDS_HUMAN", "FAILED"}:
            return {"status": "HELD", "job_state": job["state"]}
        from .event_scope import is_managed
        if job['state'] not in {'COMPLETE','PARTIAL'} and is_managed(store,job):
            raise StateError('Managed publication requires JobScopedPublication')
        if verify_quiescent(dict(job)) is not True:
            return {"status": "QUIESCENCE_REQUIRED"}
        if job["state"] in {"COMPLETE", "PARTIAL"}:
            # Repeated recovery is a read-only integrity audit, never another slot.
            try:
                facts = verify(store, self.lake, job, store._receipt(job))
                return {"status": job["state"], "already_finalized": True, **facts}
            except StateError as exc:
                return {"status": "INTEGRITY_ERROR", "error_code": str(exc)}
        if token is None or token.job_id != job_id:
            raise OwnershipError("Valid current claim required; no automatic takeover")
        with store._transaction(token):
            job = store._owned(token)
            receipt = store.db.execute("SELECT * FROM harness_receipts WHERE job_id=?", (job_id,)).fetchone()
            if receipt and receipt["stop_reason"] in RESTRICTED_STOP_REASONS:
                target = "PAUSED_NEEDS_HUMAN" if receipt["stop_reason"] in {"UNKNOWN_403", "REQUIRED_ENDPOINT_ERROR"} else "ACCESS_RESTRICTED"
                store._transition_in_transaction(token, target, code=receipt["stop_reason"])
                return {"status": "HELD", "job_state": target}
        recovery_id, blocked = self._reserve(token)
        if blocked:
            return {"status": blocked}
        try:
            with store._transaction(token):
                job = store._owned(token)
                receipt = store._receipt(job)
                store._event(job, "recovery.quiescence_attested", job["state"], job["state"], evidence_ref)
            verify(store, self.lake, job, receipt, allow_unpublished=True)
            # Existing flush is whole-DB. Refuse unrelated pending events rather
            # than publish/ack another job with this token. No replacement writer.
            ids = set(json.loads(receipt["event_ids_json"]))
            pending = {r[0] for r in store.db.execute("SELECT event_id FROM tiktok_comment_events WHERE delivered=0")}
            if pending - ids:
                raise IntegrityError("UNRELATED_PENDING_EVENTS")
            with store._transaction(token):
                store._owned(token)
            repository = CommentPersistence(store.path, self.lake)
            try:
                repository.flush()
            finally:
                repository.close()
            with store._transaction(token):
                job = store._owned(token)
                receipt = store._receipt(job)
                facts = verify(store, self.lake, job, receipt)
            store.record_receipt(token, source_complete=bool(receipt["source_complete"]),
                completion_evidence=receipt["completion_evidence"], stop_reason=receipt["stop_reason"],
                event_ids=json.loads(receipt["event_ids_json"]), checkpoint_ref=receipt["checkpoint_ref"],
                publication_verified=True, reconciliation_passed=True)
            with store._transaction(token):
                job = store._owned(token)
                if job["state"] == "RUNNING":
                    store._transition_in_transaction(token, "PERSISTING")
                final = "COMPLETE" if receipt["source_complete"] and receipt["completion_evidence"] else "PARTIAL"
                store._transition_in_transaction(token, final)
                store.db.execute("""UPDATE harness_recovery_attempts SET status='SUCCEEDED',ended_at=?
                    WHERE recovery_id=? AND attempt_id=? AND status='STARTED'""",
                    (store._now(), recovery_id, token.attempt_id))
                store._event(job, "persistence.reconciled", "PERSISTING", final, "EXACT_PAYLOAD_AND_MEMBERSHIP")
            return {"status": final, "already_finalized": False, **facts}
        except OwnershipError:
            # Lost authority cannot mutate the ledger; STARTED remains reserved
            # for a later explicitly quiescent takeover to mark interrupted.
            raise
        except IntegrityError as exc:
            return self._failure(token, recovery_id, str(exc), retryable=False)
        except StateError:
            return self._failure(token, recovery_id, "RECEIPT_OR_STATE_INVALID", retryable=False)
        except (OSError, sqlite3.OperationalError):
            return self._failure(token, recovery_id, "STORAGE_INTERRUPTION", retryable=True)
        except Exception:
            return self._failure(token, recovery_id, "UNKNOWN_RECOVERY_ERROR", retryable=False)
