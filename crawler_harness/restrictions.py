"""C2c cooperative, offline source holds. No browser, provider or network startup.

SQLite serializes admission, activation and release. Admission is not rollback of
an already dispatched command. Synthetic verification is NOT live access proof.
"""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import threading
import uuid

from .state import StateError, OwnershipError, Claim, _identifier, _digest, _json


class RestrictionError(OwnershipError):
    pass


REASONS = {'CHALLENGE': 'CAPTCHA', 'AUTH_GATE': 'LOGIN_REQUIRED',
    'LOGIN_OR_CONSENT_GATE': 'LOGIN_REQUIRED', 'ACCESS_DENIED': 'ACCESS_DENIED',
    'RATE_LIMITED': 'RATE_LIMITED', 'UNKNOWN_403': 'UNKNOWN',
    'REQUIRED_ENDPOINT_ERROR': 'UNKNOWN', 'APPLICATION_ERROR': 'UNKNOWN',
    'ACCESS_UNKNOWN': 'UNKNOWN'}

DDL = (
    "CREATE TABLE harness_restriction_schema(version INTEGER PRIMARY KEY CHECK(version=1), environment TEXT NOT NULL CHECK(environment='OFFLINE_FIXTURE'))",
    '''CREATE TABLE harness_restrictions(
        source TEXT PRIMARY KEY, epoch INTEGER NOT NULL, session_id TEXT NOT NULL,
        active INTEGER NOT NULL CHECK(active IN (0,1)), reason TEXT NOT NULL,
        observed_at REAL NOT NULL, not_before REAL NOT NULL, evidence_ref TEXT NOT NULL,
        job_id TEXT NOT NULL REFERENCES harness_jobs(job_id), attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL)''',
    '''CREATE TABLE harness_source_sessions(
        session_id TEXT PRIMARY KEY, source TEXT NOT NULL,
        job_id TEXT NOT NULL REFERENCES harness_jobs(job_id), attempt_id TEXT NOT NULL,
        generation INTEGER NOT NULL, owner TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED')),
        opened_at REAL NOT NULL, closed_at REAL)''',
    '''CREATE TABLE harness_restriction_history(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
        session_id TEXT NOT NULL, epoch INTEGER NOT NULL, action TEXT NOT NULL,
        actor TEXT NOT NULL, evidence_ref TEXT NOT NULL, created_at REAL NOT NULL)''',
    '''CREATE TABLE harness_access_verifications(
        verification_id TEXT PRIMARY KEY, source TEXT NOT NULL, session_id TEXT NOT NULL,
        epoch INTEGER NOT NULL, operator_id TEXT NOT NULL, control_id TEXT NOT NULL,
        observed_at REAL NOT NULL, expires_at REAL NOT NULL,
        evidence_json TEXT NOT NULL, evidence_hash TEXT NOT NULL,
        consumed INTEGER NOT NULL CHECK(consumed IN (0,1)))''',
)


def installed(store):
    names = {r[0] for r in store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'harness_%'")}
    expected = {s.split('(', 1)[0].split()[-1] for s in DDL}
    found = names & expected
    if found and found != expected:
        raise StateError('Partial restriction extension; dispatch refused')
    if found and [tuple(r) for r in store.db.execute('SELECT version,environment FROM harness_restriction_schema')] != [(1,'OFFLINE_FIXTURE')]:
        raise StateError('Unsupported restriction environment/version; dispatch refused')
    return bool(found)


def assert_dispatch(store, source, session_id=None):
    """Caller holds BEGIN IMMEDIATE; legacy DB without extension is unchanged."""
    if not installed(store):
        return
    if not store.db.in_transaction:
        raise StateError('Restriction admission requires atomic transaction')
    latch = store.db.execute('SELECT * FROM harness_restrictions WHERE source=?', (source,)).fetchone()
    if latch and latch['active']:
        raise RestrictionError('Source restricted: '+latch['reason'])
    sessions = store.db.execute("SELECT session_id FROM harness_source_sessions WHERE source=? AND status='OPEN'",
                                (source,)).fetchall()
    if any(r['session_id'] != session_id for r in sessions):
        raise RestrictionError('Unresolved source session; dispatch refused')


def _session(store, token, sid):
    row = store.db.execute('SELECT * FROM harness_source_sessions WHERE session_id=?', (sid,)).fetchone()
    if row is None or (row['job_id'], row['attempt_id'], row['generation'], row['owner'], row['status']) != (
            token.job_id, token.attempt_id, token.generation, token.owner, 'OPEN'):
        raise RestrictionError('Wrong/closed session ownership')
    return dict(row)


def observation_reason(snapshot, errors):
    """Existing access classifiers plus explicit insufficient-observation hold."""
    from experiments.tiktok_comment_dom.extractor import access_failure
    from experiments.tiktok_access_benchmark.network import decision
    reason = access_failure(snapshot) or decision(errors, rendered=bool(snapshot.get('video_detail_rendered')))
    if reason is None and snapshot.get('interstitial_pending') is True:
        return None  # existing port recognized a known noninteractive wait page
    if reason is None and not (snapshot.get('challenge_present') is False and
            type(snapshot.get('dialog_kinds')) is list and
            snapshot.get('video_detail_rendered') is True):
        reason = 'ACCESS_UNKNOWN'
    return reason


class Restrictions:
    def __init__(self, store):
        self.store = store
        if not installed(store):
            raise StateError('Explicit isolated extension installation required')

    @staticmethod
    def install(store, *, offline_fixture=False):
        if offline_fixture is not True:
            raise StateError('Runtime migration disabled; explicit isolated offline fixture required')
        with store._transaction():
            if installed(store):
                return
            if store.db.execute("SELECT 1 FROM harness_jobs WHERE state IN ('RUNNING','PERSISTING')").fetchone():
                raise StateError('Quiescent ledger required for extension installation')
            for statement in DDL:
                store.db.execute(statement)
            store.db.execute("INSERT INTO harness_restriction_schema VALUES(1,'OFFLINE_FIXTURE')")

    def latch(self, source):
        row = self.store.db.execute('SELECT * FROM harness_restrictions WHERE source=?', (source,)).fetchone()
        return dict(row) if row else None

    def register(self, token, sid):
        _identifier(sid)
        s = self.store
        with s._transaction(token):
            job = s._owned(token)
            assert_dispatch(s, job['source'])
            if job['state'] != 'RUNNING':
                raise RestrictionError('Collection session requires RUNNING job')
            s.db.execute('INSERT INTO harness_source_sessions VALUES(?,?,?,?,?,?,?,?,NULL)',
                (sid, job['source'], token.job_id, token.attempt_id, token.generation,
                 token.owner, 'OPEN', s._now()))

    def admit(self, token, sid, command_id):
        _identifier(command_id)
        s = self.store
        with s._transaction(token):
            job = s._owned(token)
            _session(s, token, sid)
            assert_dispatch(s, job['source'], sid)
            if s.db.execute("SELECT 1 FROM harness_events WHERE event_type='browser.command_admitted' AND code=?",
                            (command_id,)).fetchone():
                raise RestrictionError('Command already admitted; no replay')
            s._event(job, 'browser.command_admitted', job['state'], job['state'], command_id)

    def check(self, token, sid):
        s = self.store
        with s._transaction(token):
            job = s._owned(token)
            _session(s, token, sid)
            assert_dispatch(s, job['source'], sid)

    def activate(self, token, sid, reason, *, evidence_ref, retry_after=None):
        s = self.store
        with s._transaction(token):
            return self.activate_in_transaction(s, token, sid, reason,
                evidence_ref=evidence_ref, retry_after=retry_after)

    @staticmethod
    def activate_in_transaction(s, token, sid, reason, *, evidence_ref, retry_after=None):
        if not s.db.in_transaction:
            raise StateError('Atomic restriction observation required')
        job = s._owned(token)
        _session(s, token, sid)
        _identifier(evidence_ref)
        if reason not in {'CAPTCHA','LOGIN_REQUIRED','ACCESS_DENIED','RATE_LIMITED','UNKNOWN'}:
            raise StateError('Unsupported restriction reason')
        if retry_after is not None and (type(retry_after) not in (int,float) or
                not math.isfinite(retry_after) or retry_after < 0):
            raise StateError('Invalid Retry-After; cannot establish safe release time')
        now = s._now()
        previous = s.db.execute('SELECT * FROM harness_restrictions WHERE source=?', (job['source'],)).fetchone()
        epoch = previous['epoch']+1 if previous else 1
        # Never shorten a previously observed Retry-After, even on reactivation.
        not_before = max(now+(retry_after or 0), previous['not_before'] if previous else 0)
        s.db.execute('INSERT OR REPLACE INTO harness_restrictions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (job['source'], epoch, sid, 1, reason, now, not_before, evidence_ref,
             token.job_id, token.attempt_id, token.generation))
        s.db.execute('INSERT INTO harness_restriction_history(source,session_id,epoch,action,actor,evidence_ref,created_at) VALUES(?,?,?,?,?,?,?)',
            (job['source'], sid, epoch, 'ACTIVATE', token.attempt_id, evidence_ref, now))
        s._event(job, 'access.restricted', job['state'], job['state'], reason)
        return epoch

    def observe(self, token, sid, snapshot, errors=(), *, evidence_ref):
        """Reuse DOM + network decisions; UNKNOWN is explicit, never absence of logs."""
        reason = observation_reason(snapshot, errors)
        if reason not in REASONS:
            return None
        # A 429 later in the same captured set must retain its cooldown even if
        # the existing classifier returns another preceding restriction first.
        retry = max((e.get('retry_after_seconds') or 0 for e in errors if e.get('status') == 429), default=0)
        with self.store._transaction(token):
            epoch = self.activate_in_transaction(self.store,token,sid,REASONS[reason],
                evidence_ref=evidence_ref,retry_after=retry)
            target = 'PAUSED_NEEDS_HUMAN' if REASONS[reason] == 'UNKNOWN' else 'ACCESS_RESTRICTED'
            self.store._transition_in_transaction(token,target,code=reason)
            return epoch

    def close_verified(self, token, sid, worker):
        """Called while C2a still owns native handles/source lock, after shutdown.

        Only cleanup evidence may be appended for an ended restricted job. This
        does not resurrect its Claim or permit heartbeat/source writes/release.
        """
        from .containment import ManagedWorker
        if type(worker) is not ManagedWorker or worker.token != token or worker.session_id != sid:
            raise RestrictionError('Wrong containment owner')
        proof = worker.quiescence()  # retained handle + Job Object, not dict/boolean input
        if proof['worker_handle_signaled'] is not True or proof['job_active_processes'] != 0:
            raise RestrictionError('Tree not quiescent')
        s = self.store
        with s._transaction():
            row = s.get(token.job_id)
            if ((row['owner'],row['attempt_id'],row['generation']) != (token.owner,token.attempt_id,token.generation) or
                    (row['state'] in {'RUNNING','PERSISTING'} and
                     (row['lease_until'] is None or s._now() >= row['lease_until']))):
                raise OwnershipError('Lost owner cannot resolve session')
            _session(s, token, sid)
            s.db.execute("UPDATE harness_source_sessions SET status='CLOSED',closed_at=? WHERE session_id=?",
                         (s._now(),sid))
            s._event(row,'worker.quiescent',row['state'],row['state'],sid)


class OfflineOperatorControl:
    """Reviewed synthetic verification only. No runtime/operator network CLI.

    Fresh operator ownership is independent of a terminated collection Claim.
    Holds C2a source and C1 publication locks throughout verification/release.
    Caller cannot substitute a controller Claim or a verified=True boolean.
    """
    def __init__(self, store, source, lake, *, operator_id):
        self.store,self.source,self.lake = store,source,Path(lake).resolve()
        self.operator_id = _identifier(operator_id)
        if source != 'tiktok':
            raise StateError('Only reviewed TikTok single-source operator control supported')
        self.control_id = uuid.uuid4().hex
        self.active = False

    def __enter__(self):
        from contextlib import ExitStack
        from .publication import windows_mutex, publisher_ownership, resource_id
        from .containment import assert_workers_resolved
        self.stack = ExitStack()
        try:
            if self.store.db.execute('SELECT environment FROM harness_restriction_schema').fetchone()[0] != 'OFFLINE_FIXTURE':
                raise RestrictionError('Synthetic verification only valid for isolated fixture registry')
            self.stack.enter_context(windows_mutex('Global\\IntegrationRecommendationWorkerV1_'+
                resource_id(self.store.path,'database')+'_tiktok'))
            self.unchanged = self.stack.enter_context(publisher_ownership(self.store.path,self.lake))
            with self.store._transaction():
                assert_workers_resolved(self.store,self.source)
                if self.store.db.execute("SELECT 1 FROM harness_source_sessions WHERE source=? AND status='OPEN'",
                                         (self.source,)).fetchone():
                    raise RestrictionError('Unresolved source session; operator hold')
                # Verify every sealed source receipt points to the locked lake;
                # an arbitrary other lake cannot stand in for publisher exclusion.
                for row in self.store.db.execute('SELECT state_json FROM tiktok_comment_checkpoints'):
                    manifest = json.loads(row[0]).get('managed_publication_manifest')
                    if manifest and Path(manifest['lake_root']).resolve() != self.lake:
                        raise RestrictionError('Source has a different publication lake')
            self.thread = threading.get_ident()
            self.active = True
            return self
        except BaseException:
            self.stack.close()
            raise

    def _owned(self):
        if not self.active or threading.get_ident() != self.thread:
            raise OwnershipError('Exclusive operator control not held')
        self.unchanged()

    def _fixture_bundle(self, latch):
        # Fixed reviewed fake provider, same source used by C2b worker. No caller
        # supplied verified flag/snapshots can issue an operator capability.
        import importlib.util
        fixture = Path(__file__).resolve().parents[1]/'tests'/'fixtures_harness_browser.py'
        spec = importlib.util.spec_from_file_location('restriction_offline_probe',fixture)
        provider = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(provider)
        driver = provider.FakeDriver({'_profile_path':'OFFLINE_NO_PROFILE'})
        from experiments.tiktok_comment_dom.extractor import SNAPSHOT_JS
        from experiments.tiktok_access_benchmark.session_comments import STATE_JS
        from experiments.tiktok_access_benchmark.access import dialog_kinds
        observations = []
        for _ in range(2):
            snapshot = driver.execute_script('return ('+SNAPSHOT_JS+')();')
            access = driver.execute_script('return ('+STATE_JS+')();')
            observations.append({'observed_at':self.store._now(),'snapshot':{
                'challenge_present':access.get('challenge'),
                'dialog_kinds':dialog_kinds(access.get('dialog_evidence',[])),
                'video_detail_rendered':access.get('video_detail'),
                'comment_panel_present':snapshot.get('comment_panel_present'),
                'network_gate':None}})
        return {'provider':'REVIEWED_OFFLINE_FIXTURE_V1','source':self.source,
                'session_id':latch['session_id'],'epoch':latch['epoch'],'observations':observations}

    def verify_fixture(self):
        """Run fixed synthetic observer; cannot certify actual TikTok access."""
        self._owned()
        from experiments.tiktok_comment_dom.extractor import access_failure
        s = self.store
        latch = Restrictions(s).latch(self.source)
        if not latch or not latch['active']:
            raise RestrictionError('Active latch required')
        bundle = self._fixture_bundle(latch)  # no SQLite lock across observation
        encoded = _json(bundle)
        if len(encoded) > 8192:
            raise RestrictionError('Verification evidence exceeds bounded size')
        bundle = json.loads(encoded)
        with s._transaction():
            latch = Restrictions(s).latch(self.source)
            if not latch or not latch['active']:
                raise RestrictionError('Active latch required')
            now = s._now()
            if bundle.get('provider') != 'REVIEWED_OFFLINE_FIXTURE_V1' or (
                    bundle.get('source'),bundle.get('session_id'),bundle.get('epoch')) != (
                    self.source,latch['session_id'],latch['epoch']):
                raise RestrictionError('Wrong verification provider/scope/epoch')
            observations = bundle.get('observations')
            if type(observations) is not list or len(observations) != 2:
                raise RestrictionError('Two fresh access observations required')
            for item in observations:
                timestamp = item.get('observed_at')
                if type(timestamp) not in (int,float) or not math.isfinite(timestamp) or not max(latch['observed_at'],latch['not_before']) <= timestamp <= now or now-timestamp > 60:
                    raise RestrictionError('Stale/premature verification')
                snap = item.get('snapshot',{})
                if set(snap) != {'challenge_present','dialog_kinds','video_detail_rendered','comment_panel_present','network_gate'}:
                    raise RestrictionError('Incomplete or unbounded verification evidence')
                if (snap['challenge_present'] is not False or snap['dialog_kinds'] != [] or
                        snap['video_detail_rendered'] is not True or snap['comment_panel_present'] is not True or
                        snap['network_gate'] is not None or access_failure(snap)):
                    raise RestrictionError('Access verification not clear')
            # Only bounded sanitized fields are persisted. Never browser body,
            # cookies, credentials, URLs or arbitrary caller metadata.
            bundle = {k:bundle[k] for k in ('provider','source','session_id','epoch','observations')}
            bundle['observations'] = [{'observed_at':item['observed_at'], 'snapshot':item['snapshot']}
                                      for item in observations]
            vid = uuid.uuid4().hex
            s.db.execute('INSERT INTO harness_access_verifications VALUES(?,?,?,?,?,?,?,?,?,?,0)',
                (vid,self.source,latch['session_id'],latch['epoch'],self.operator_id,self.control_id,
                 now,min(item['observed_at'] for item in observations)+60,_json(bundle),_digest(bundle)))
            return vid

    def release(self, verification_id, *, expected_epoch, session_id):
        self._owned()
        s = self.store
        with s._transaction():
            self._owned()
            latch = Restrictions(s).latch(self.source)
            row = s.db.execute('SELECT * FROM harness_access_verifications WHERE verification_id=?',
                                (verification_id,)).fetchone()
            now = s._now()
            if not latch or not latch['active'] or (latch['epoch'],latch['session_id']) != (expected_epoch,session_id):
                raise RestrictionError('Restriction epoch/session changed')
            if row is None or (row['source'],row['session_id'],row['epoch'],row['operator_id'],row['control_id'],row['consumed']) != (
                    self.source,session_id,expected_epoch,self.operator_id,self.control_id,0):
                raise RestrictionError('Missing/wrong-scope/consumed verification')
            if now < latch['not_before'] or now > row['expires_at'] or row['observed_at'] < latch['observed_at']:
                raise RestrictionError('Premature/stale verification')
            if _digest(json.loads(row['evidence_json'])) != row['evidence_hash']:
                raise RestrictionError('Verification evidence hash mismatch')
            if s.db.execute("SELECT 1 FROM harness_source_sessions WHERE source=? AND status='OPEN'",(self.source,)).fetchone():
                raise RestrictionError('Session no longer quiescent')
            s.db.execute('UPDATE harness_restrictions SET active=0 WHERE source=? AND epoch=?',
                         (self.source,expected_epoch))
            s.db.execute('UPDATE harness_access_verifications SET consumed=1 WHERE verification_id=?',(verification_id,))
            s.db.execute('INSERT INTO harness_restriction_history(source,session_id,epoch,action,actor,evidence_ref,created_at) VALUES(?,?,?,?,?,?,?)',
                (self.source,session_id,expected_epoch,'RELEASE',self.operator_id,verification_id,now))

    def __exit__(self,*_):
        self.active = False
        self.stack.close()
