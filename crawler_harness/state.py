"""M1 job ledger in an existing non-Google TikTok SQLite database.

Only control-plane mutations are fenced here. Source writes and filesystem/browser
side effects are NOT fenced by this module; takeover requires external quiescence.
No automatic retry, browser creation, outbox writer, AI or notification transport.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid


STATES = (
    "PENDING", "RUNNING", "PERSISTING", "COMPLETE", "PARTIAL", "FAILED",
    "PAUSED_NEEDS_HUMAN", "ACCESS_RESTRICTED",
)
ACTIVE = {"RUNNING", "PERSISTING"}
TRANSITIONS = {
    "RUNNING": {"PERSISTING", "FAILED", "PAUSED_NEEDS_HUMAN", "ACCESS_RESTRICTED"},
    "PERSISTING": {"COMPLETE", "PARTIAL", "FAILED", "PAUSED_NEEDS_HUMAN", "ACCESS_RESTRICTED"},
}
RESTRICTED_STOP_REASONS = {
    "AUTH_GATE", "LOGIN_OR_CONSENT_GATE", "CHALLENGE", "ACCESS_DENIED",
    "ACCESS_RESTRICTION", "RATE_LIMITED", "UNKNOWN_403", "REQUIRED_ENDPOINT_ERROR",
}


class StateError(ValueError):
    """Invalid transition, receipt, configuration or schema."""


class OwnershipError(StateError):
    """Wrong owner, stale generation, expired lease or inactive attempt."""


@dataclass(frozen=True)
class Claim:
    job_id: str
    owner: str
    generation: int
    attempt_id: str


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _identifier(value):
    # Identifiers and error/evidence codes only; never raw browser text or secrets.
    if not isinstance(value, str) or not value or len(value) > 200:
        raise StateError("A bounded nonempty identifier is required")
    if any(not (c.isalnum() or c in "_:.-/") for c in value):
        raise StateError("Identifier contains unsupported characters")
    return value


DDL = (
    "CREATE TABLE harness_schema(version INTEGER PRIMARY KEY CHECK(version=1))",
    """CREATE TABLE harness_jobs(
        job_id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL,
        place_id TEXT NOT NULL, entity_id TEXT NOT NULL, input_digest TEXT NOT NULL,
        data_run_id TEXT NOT NULL UNIQUE, definition_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('PENDING','RUNNING','PERSISTING','COMPLETE',
            'PARTIAL','FAILED','PAUSED_NEEDS_HUMAN','ACCESS_RESTRICTED')),
        attempt_count INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 0,
        owner TEXT, attempt_id TEXT, lease_until REAL, checkpoint_ref TEXT,
        outcome_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
    """CREATE TABLE harness_attempts(
        attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES harness_jobs(job_id),
        generation INTEGER NOT NULL, owner TEXT NOT NULL, run_id TEXT NOT NULL UNIQUE,
        started_at REAL NOT NULL, ended_at REAL, outcome_code TEXT,
        UNIQUE(job_id,generation))""",
    """CREATE TABLE harness_dependencies(
        job_id TEXT NOT NULL REFERENCES harness_jobs(job_id),
        prerequisite_id TEXT NOT NULL REFERENCES harness_jobs(job_id),
        PRIMARY KEY(job_id,prerequisite_id), CHECK(job_id<>prerequisite_id))""",
    """CREATE TABLE harness_receipts(
        job_id TEXT PRIMARY KEY REFERENCES harness_jobs(job_id),
        generation INTEGER NOT NULL, attempt_id TEXT NOT NULL,
        source_complete INTEGER NOT NULL, completion_evidence TEXT,
        stop_reason TEXT NOT NULL, event_ids_json TEXT NOT NULL,
        publication_verified INTEGER NOT NULL, reconciliation_passed INTEGER NOT NULL,
        checkpoint_ref TEXT NOT NULL, created_at REAL NOT NULL)""",
    """CREATE TABLE harness_events(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES harness_jobs(job_id),
        source TEXT NOT NULL, place_id TEXT NOT NULL, entity_id TEXT NOT NULL,
        run_id TEXT, attempt_id TEXT, generation INTEGER NOT NULL, event_type TEXT NOT NULL,
        from_state TEXT, to_state TEXT, code TEXT, created_at REAL NOT NULL)""",
)

# Optional M2 extension. M1 tables/version remain unchanged; installed explicitly
# by OfflineRecovery, never by merely opening a v1 JobStore.
RECOVERY_DDL = (
    "CREATE TABLE harness_recovery_schema(version INTEGER PRIMARY KEY CHECK(version=1))",
    """CREATE TABLE harness_recovery_policy(
        job_id TEXT PRIMARY KEY REFERENCES harness_jobs(job_id), max_attempts INTEGER NOT NULL,
        attempts_used INTEGER NOT NULL DEFAULT 0, backoff_json TEXT NOT NULL,
        next_eligible_at REAL NOT NULL DEFAULT 0, hold_code TEXT, lake_root TEXT NOT NULL,
        CHECK(max_attempts BETWEEN 1 AND 10), CHECK(attempts_used>=0 AND attempts_used<=max_attempts))""",
    """CREATE TABLE harness_recovery_attempts(
        recovery_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES harness_jobs(job_id),
        attempt_id TEXT NOT NULL REFERENCES harness_attempts(attempt_id), generation INTEGER NOT NULL,
        ordinal INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('STARTED','SUCCEEDED','FAILED','INTERRUPTED')),
        error_code TEXT, started_at REAL NOT NULL, ended_at REAL,
        UNIQUE(job_id,ordinal))""",
)


def _table_names(statements):
    return {statement.split("(", 1)[0].split()[-1] for statement in statements}


class JobStore:
    """Explicit additive v1 initialization; separate connection, same SQLite file.

    Caller establishes TikTok storage using its existing implementation first.
    The supplied clock returns UTC epoch seconds; injected clocks are for tests.
    An expired claim cannot renew, mutate state, or reclaim itself automatically.
    """

    def __init__(self, database, *, clock=time.time):
        path = Path(database).resolve()
        if path.name.lower() == "crawler_state.sqlite3" or not path.is_file():
            raise StateError("Existing non-Google TikTok database required")
        self.clock = clock
        self.path = path
        self.db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        try:
            with self._transaction():
                names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                required = {"tiktok_comments", "tiktok_comment_events", "tiktok_comment_checkpoints"}
                if names & {"review_state", "pending_review_events"} or not required <= names:
                    raise StateError("Existing TikTok schema required; Google database protected")
                harness = {name for name in names if name.startswith("harness_")}
                expected = _table_names(DDL)
                extension = harness & _table_names(RECOVERY_DDL)
                from .restrictions import DDL as restriction_ddl
                restriction_extension = harness & _table_names(restriction_ddl)
                if restriction_extension and restriction_extension != _table_names(restriction_ddl):
                    raise StateError("Partial restriction schema; no automatic repair")
                if extension and extension != _table_names(RECOVERY_DDL):
                    raise StateError("Partial recovery schema; no automatic repair")
                if harness:
                    if harness != expected | extension | restriction_extension:
                        raise StateError("Unsupported/partial harness schema; no automatic repair")
                    if [r[0] for r in self.db.execute("SELECT version FROM harness_schema")] != [1]:
                        raise StateError("Unsupported harness schema version")
                    if extension and [r[0] for r in self.db.execute("SELECT version FROM harness_recovery_schema")] != [1]:
                        raise StateError("Unsupported recovery schema version")
                    if restriction_extension and [r[0] for r in self.db.execute("SELECT version FROM harness_restriction_schema")] != [1]:
                        raise StateError("Unsupported restriction schema version")
                    for statement in DDL + (RECOVERY_DDL if extension else ()) + (restriction_ddl if restriction_extension else ()):
                        name = statement.split("(", 1)[0].split()[-1]
                        stored = self.db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
                        if " ".join(stored.split()).lower() != " ".join(statement.split()).lower():
                            raise StateError("Harness schema drift; explicit migration required")
                else:
                    for statement in DDL:
                        self.db.execute(statement)
                    self.db.execute("INSERT INTO harness_schema VALUES(1)")
        except BaseException:
            self.db.close()
            raise

    def enable_recovery_schema(self):
        """Explicit atomic additive M2 migration; no source schema alteration."""
        with self._transaction():
            present = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            extension = present & _table_names(RECOVERY_DDL)
            if extension:
                if extension != _table_names(RECOVERY_DDL):
                    raise StateError("Partial recovery schema")
                return
            for statement in RECOVERY_DDL:
                self.db.execute(statement)
            self.db.execute("INSERT INTO harness_recovery_schema VALUES(1)")

    @contextmanager
    def _transaction(self, token=None):
        joined = self.db.in_transaction
        if joined and not getattr(self, '_source_bound', False):
            raise StateError('Nested ledger transaction is not supported')
        if not joined:
            self.db.execute("BEGIN IMMEDIATE")
        try:
            authority = self._owned(token) if token is not None else None
            yield
            if getattr(self, '_source_failed', False):
                raise StateError('Source transaction has a failed nested operation')
            if authority is not None:
                # Writes remain invisible until commit. Roll back even a heartbeat
                # if its ORIGINAL lease expired while this transaction ran.
                current = self.get(token.job_id)
                if (current["owner"], current["generation"], current["attempt_id"]) != (
                        token.owner, token.generation, token.attempt_id):
                    raise OwnershipError("Ownership changed during transaction")
                if self._now() >= authority["lease_until"]:
                    raise OwnershipError("Lease expired before commit")
            if not joined:
                self.db.commit()
        except BaseException:
            if joined:
                self._source_failed = True
            if not joined:
                self.db.rollback()
            raise

    @contextmanager
    def source_transaction(self, connection, token):
        """Fence source writes and receipt hooks on the SAME database/connection.

        The borrowed view never initializes schema or takes ownership of the
        connection. Nested public ledger methods join this outer transaction;
        only the outer transaction commits and checks its original deadline.
        """
        if connection.in_transaction:
            raise StateError('Source connection must not have an open transaction')
        main = next((r[2] for r in connection.execute('PRAGMA database_list') if r[1] == 'main'), None)
        if not main or Path(main).resolve() != self.path:
            raise StateError('Source and harness must use the same SQLite database')
        if connection.row_factory is not sqlite3.Row:
            raise StateError('Source connection requires sqlite3.Row')
        view = object.__new__(JobStore)
        view.path, view.clock, view.db = self.path, self.clock, connection
        view._source_bound = True
        view._source_failed = False
        with view._transaction(token):
            yield view

    def _now(self):
        now = self.clock()
        if not isinstance(now, (float, int)) or not math.isfinite(now):
            raise StateError("Finite UTC timestamp required")
        return now

    @staticmethod
    def _duration(seconds):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
            raise StateError("Positive finite lease duration required")
        return seconds

    def get(self, job_id):
        row = self.db.execute("SELECT * FROM harness_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise StateError("Job not found")
        return dict(row)

    def _event(self, row, event, old, new, code=None):
        attempt = self.db.execute("SELECT run_id FROM harness_attempts WHERE attempt_id=?", (row["attempt_id"],)).fetchone()
        self.db.execute("""INSERT INTO harness_events
            (job_id,source,place_id,entity_id,run_id,attempt_id,generation,event_type,from_state,to_state,code,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["job_id"], row["source"], row["place_id"], row["entity_id"],
             attempt[0] if attempt else None, row["attempt_id"], row["generation"], event, old, new, code, self._now()))

    def create_job(self, *, source, kind, place_id, entity_id, input_digest,
                   workflow="v1", dependencies=()):
        if source not in {"tiktok", "google", "instagram", "facebook"} or kind not in {"discovery", "collection"}:
            raise StateError("Unsupported source/task kind")
        definition = dict(source=source, kind=kind, place_id=_identifier(place_id),
                          entity_id=_identifier(entity_id), input_digest=_identifier(input_digest),
                          workflow=_identifier(workflow), dependencies=sorted(set(dependencies)))
        job_id = _digest(definition)
        with self._transaction():
            if self.db.execute("SELECT 1 FROM harness_jobs WHERE job_id=?", (job_id,)).fetchone():
                return job_id
            now = self._now()
            self.db.execute("""INSERT INTO harness_jobs
                (job_id,source,kind,place_id,entity_id,input_digest,data_run_id,definition_json,state,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, source, kind, place_id, entity_id, input_digest, "h-" + job_id[:24],
                 _json(definition), "PENDING", now, now))
            for prerequisite in definition["dependencies"]:
                self.db.execute("INSERT INTO harness_dependencies VALUES(?,?)", (job_id, prerequisite))
            self._event(self.get(job_id), "job.created", None, "PENDING")
        return job_id

    def _new_attempt(self, row, owner, seconds, *, takeover=False):
        generation = row["generation"] + 1
        attempt_id = uuid.uuid4().hex
        now = self._now()
        state = row["state"] if takeover else "RUNNING"
        self.db.execute("""INSERT INTO harness_attempts
            (attempt_id,job_id,generation,owner,run_id,started_at) VALUES(?,?,?,?,?,?)""",
            (attempt_id, row["job_id"], generation, owner, uuid.uuid4().hex, now))
        self.db.execute("""UPDATE harness_jobs SET state=?,owner=?,generation=?,attempt_id=?,
            lease_until=?,attempt_count=attempt_count+1,updated_at=? WHERE job_id=?""",
            (state, owner, generation, attempt_id, now + seconds, now, row["job_id"]))
        self._event(self.get(row["job_id"]), "job.claimed", row["state"], state)
        return Claim(row["job_id"], owner, generation, attempt_id)

    def claim(self, job_id, owner, *, lease_seconds=60):
        _identifier(owner)
        self._duration(lease_seconds)
        with self._transaction():
            row = self.get(job_id)
            if row["state"] != "PENDING":
                return None  # In particular, never take over just because a lease expired.
            from .restrictions import assert_dispatch
            assert_dispatch(self, row['source'])
            blocked = self.db.execute("""SELECT 1 FROM harness_dependencies d
                JOIN harness_jobs j ON j.job_id=d.prerequisite_id
                WHERE d.job_id=? AND (j.state<>'COMPLETE' OR j.outcome_code='NO_MATCH') LIMIT 1""", (job_id,)).fetchone()
            if blocked:
                return None
            return self._new_attempt(row, owner, lease_seconds)

    def _owned(self, token):
        row = self.get(token.job_id)
        if (row["owner"] != token.owner or row["generation"] != token.generation
                or row["attempt_id"] != token.attempt_id or row["state"] not in ACTIVE
                or row["lease_until"] is None or row["lease_until"] <= self._now()):
            raise OwnershipError("Stale, expired or inactive lease")
        return row

    def heartbeat(self, token, *, lease_seconds=60):
        self._duration(lease_seconds)
        with self._transaction(token):
            self._owned(token)
            self.db.execute("UPDATE harness_jobs SET lease_until=?,updated_at=? WHERE job_id=?",
                            (self._now() + lease_seconds, self._now(), token.job_id))

    def takeover(self, job_id, owner, *, expected_generation, verify_quiescent,
                 evidence_ref, lease_seconds=60):
        """Explicit administrative operation, NOT automated recovery.

        Trusted operator/verifier must establish prior worker AND all unfenced
        side-effect processes stopped. A callback returning True is an attestation,
        not proof supplied by SQLite. No browser adapter calls this in M1.
        """
        _identifier(owner)
        _identifier(evidence_ref)
        self._duration(lease_seconds)
        with self._transaction():
            row = self.get(job_id)
            if row["generation"] != expected_generation or row["state"] not in ACTIVE:
                raise OwnershipError("Takeover generation/state mismatch")
            if row["lease_until"] > self._now():
                raise OwnershipError("Lease is not expired")
            if verify_quiescent(dict(row)) is not True:
                raise OwnershipError("Prior worker/publisher not confirmed quiescent")
            self.db.execute("UPDATE harness_attempts SET ended_at=?,outcome_code='QUIESCENT_TAKEOVER' WHERE attempt_id=?",
                            (self._now(), row["attempt_id"]))
            self._event(row, "ownership.released", row["state"], row["state"], evidence_ref)
            return self._new_attempt(row, owner, lease_seconds, takeover=True)

    def save_checkpoint(self, token, checkpoint_ref):
        _identifier(checkpoint_ref)
        with self._transaction(token):
            row = self._owned(token)
            self.db.execute("UPDATE harness_jobs SET checkpoint_ref=?,updated_at=? WHERE job_id=?",
                            (checkpoint_ref, self._now(), token.job_id))
            self._event(row, "job.checkpointed", row["state"], row["state"])

    def record_receipt(self, token, *, source_complete, completion_evidence,
                       stop_reason, event_ids, checkpoint_ref,
                       publication_verified=False, reconciliation_passed=False):
        """Trusted persistence observer's receipt, not an alternative outbox sink.

        M1 records attestations. Parquet payload verification remains the existing
        writer/reconciler's responsibility and is not performed by this job ledger.
        """
        for flag in (source_complete, publication_verified, reconciliation_passed):
            if type(flag) is not bool:
                raise StateError("Receipt flags must be booleans")
        _identifier(stop_reason)
        _identifier(checkpoint_ref)
        if completion_evidence is not None:
            _identifier(completion_evidence)
        if source_complete and not completion_evidence:
            raise StateError("Source completion requires explicit evidence")
        ids = sorted(event_ids)
        if len(ids) != len(set(ids)):
            raise StateError("Duplicate receipt event IDs")
        for event_id in ids:
            _identifier(event_id)
        with self._transaction(token):
            row = self._owned(token)
            for event_id in ids:
                event = self.db.execute("SELECT payload FROM tiktok_comment_events WHERE event_id=?", (event_id,)).fetchone()
                if event is None:
                    raise StateError("Receipt references an uncommitted event")
                payload = json.loads(event[0])
                if (row["source"] != "tiktok" or payload.get("place_id") != row["place_id"]
                        or payload.get("video_id") != row["entity_id"] or payload.get("run_id") != row["data_run_id"]):
                    raise StateError("Event does not belong to job scope")
            observation = (int(source_complete), completion_evidence, stop_reason, _json(ids), checkpoint_ref)
            previous = self.db.execute("SELECT * FROM harness_receipts WHERE job_id=?", (token.job_id,)).fetchone()
            same_observation = previous is not None and observation == (
                previous["source_complete"], previous["completion_evidence"], previous["stop_reason"],
                previous["event_ids_json"], previous["checkpoint_ref"])
            if previous is not None and row["state"] == "PERSISTING" and not same_observation:
                raise StateError("Persisting receipt observation is immutable")
            if same_observation:
                # Delivery revalidation by a new attempt is not a new source
                # observation. Keep the observation's originating attempt/time.
                self.db.execute("""UPDATE harness_receipts SET publication_verified=?,
                    reconciliation_passed=? WHERE job_id=?""",
                    (int(publication_verified), int(reconciliation_passed), token.job_id))
            else:
                self.db.execute("""INSERT OR REPLACE INTO harness_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (token.job_id, token.generation, token.attempt_id, int(source_complete), completion_evidence,
                     stop_reason, _json(ids), int(publication_verified), int(reconciliation_passed), checkpoint_ref, self._now()))
            self._event(row, "job.receipt_recorded", row["state"], row["state"], stop_reason)

    def _receipt(self, row):
        receipt = self.db.execute("SELECT * FROM harness_receipts WHERE job_id=?", (row["job_id"],)).fetchone()
        if receipt is None:
            raise StateError("Durable receipt required")
        origin = self.db.execute("""SELECT generation FROM harness_attempts
            WHERE attempt_id=? AND job_id=?""", (receipt["attempt_id"], row["job_id"])).fetchone()
        if origin is None or origin[0] != receipt["generation"] or receipt["generation"] > row["generation"]:
            raise StateError("Receipt provenance does not belong to job attempt")
        return dict(receipt)

    def _reconciled(self, row, receipt):
        if not receipt["publication_verified"] or not receipt["reconciliation_passed"]:
            raise StateError("Durable publication and reconciliation required")
        from .event_scope import is_managed, sealed_manifest
        if is_managed(self,row):
            from .recovery import scoped_events
            from experiments.tiktok_comment_dom.persistence import digest
            events = scoped_events(self,row)
            manifest = sealed_manifest(self,row,receipt)
            if sorted(events) != json.loads(receipt['event_ids_json']) or any(
                    not e['delivered'] or digest(e['payload']) != manifest['payload_hashes'][eid]
                    for eid,e in events.items()):
                raise StateError('Managed scope is not reconciled')
            return
        actual = []
        for event in self.db.execute("SELECT event_id,payload,delivered FROM tiktok_comment_events"):
            payload = json.loads(event["payload"])
            if (payload.get("place_id"), payload.get("video_id"), payload.get("run_id")) == (
                    row["place_id"], row["entity_id"], row["data_run_id"]):
                if not event["delivered"]:
                    raise StateError("Job has pending outbox events")
                actual.append(event["event_id"])
        if sorted(actual) != json.loads(receipt["event_ids_json"]):
            raise StateError("Receipt event membership does not match committed scope")

    def transition(self, token, target, *, code=None):
        if code is not None:
            _identifier(code)
        with self._transaction(token):
            self._transition_in_transaction(token, target, code=code)

    def _transition_in_transaction(self, token, target, *, code=None):
        """Internal reuse for atomic M2 recovery result + final job transition."""
        if not self.db.in_transaction:
            raise StateError("Transition requires a write transaction")
        row = self._owned(token)
        if target not in TRANSITIONS[row["state"]]:
            raise StateError("Invalid state transition")
        if target == "PERSISTING":
            self._receipt(row)
        if target in {"COMPLETE", "PARTIAL"}:
            receipt = self._receipt(row)
            if receipt["stop_reason"] in RESTRICTED_STOP_REASONS:
                raise StateError("Restricted/ambiguous access cannot be finalized as collection success")
            self._reconciled(row, receipt)
            if target == "COMPLETE" and not (receipt["source_complete"] and receipt["completion_evidence"]):
                raise StateError("Complete source coverage required; bounded runs remain PARTIAL")
            if target == "COMPLETE" and receipt["stop_reason"] == "NO_MATCH" and row["kind"] != "discovery":
                raise StateError("NO_MATCH is only a discovery completion")
            code = receipt["stop_reason"]
        now = self._now()
        self.db.execute("UPDATE harness_jobs SET state=?,outcome_code=?,updated_at=? WHERE job_id=?",
                        (target, code, now, token.job_id))
        if target not in ACTIVE:
            self.db.execute("UPDATE harness_attempts SET ended_at=?,outcome_code=? WHERE attempt_id=?",
                            (now, code or target, token.attempt_id))
            self.db.execute("UPDATE harness_jobs SET lease_until=NULL WHERE job_id=?", (token.job_id,))
        self._event(row, "job.completed" if target == "COMPLETE" else "job.state_changed", row["state"], target, code)

    def close(self):
        self.db.close()
