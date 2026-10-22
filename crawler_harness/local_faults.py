"""Opt-in local diagnostic faults; no new IPC opcode, retry or process adoption."""
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import time
from unittest.mock import patch
import sqlite3

from .browser_profile import atomic_json
from .containment import ContainmentError

# Fixed synthetic-only script. No dynamic duration, URL, callback or arguments.
TIMEOUT_SCRIPT = "if (!document.querySelector('#synthetic-result')) { throw Error('not synthetic'); } const deadline = performance.now() + 6000; while (performance.now() < deadline) {} return 'bounded-delay-returned';"


def terminate_owned_worker(session):
    """Reviewed fault injection via a retained, creation-verified C2a handle."""
    import ctypes
    from ctypes import wintypes
    session._validate('current_url')
    owner=session.owner
    owner._owner(owner.session_id,session.token.attempt_id)
    if owner.api.identity(owner.process)!=owner.worker_identity or owner.api.wait(owner.process):
        raise ContainmentError('Worker fault target is not the active retained identity')
    owner.api.member(owner.process,owner.job)
    terminate=owner.api.k.TerminateProcess
    terminate.argtypes=[wintypes.HANDLE,wintypes.UINT];terminate.restype=wintypes.BOOL
    owner.api.require(terminate(owner.process,87))
    if not owner.api.wait(owner.process,5000):raise ContainmentError('Fault target exit not observed')
    return asdict(owner.worker_identity)


def durable_fault_state(database, job_id):
    """Read a fresh readonly connection; never claim a job or replay an action."""
    db=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        state=db.execute('SELECT state FROM harness_jobs WHERE job_id=?',(job_id,)).fetchone()[0]
        records=db.execute('SELECT event_type,code FROM harness_events WHERE job_id=?',(job_id,)).fetchall()
        intents={code for kind,code in records if kind=='browser.command_intent'}
        successes={code for kind,code in records if kind=='browser.command_succeeded'}
        return {'job_state':state,'receipt_count':db.execute('SELECT COUNT(*) FROM harness_receipts WHERE job_id=?',(job_id,)).fetchone()[0],
            'attempt_count':db.execute('SELECT COUNT(*) FROM harness_attempts WHERE job_id=?',(job_id,)).fetchone()[0],
            'intent_count':len(intents),'succeeded_count':len(successes),
            'unresolved_intents':sorted(intents-successes),
            'unknown_count':sum(kind=='browser.command_unknown' for kind,code in records),
            'refused_count':sum(kind=='browser.command_refused' for kind,code in records),
            'outbox_count':db.execute('SELECT COUNT(*) FROM tiktok_comment_events').fetchone()[0]}
    finally:db.close()


def resolve_terminal_session(session, lake):
    """Explicit diagnostic step before owner disposal; never replay or claim."""
    from .worker_resolution import TerminalWorkerResolution
    from .containment import assert_workers_resolved
    from .state import JobStore
    if session.state != 'HALTED' or session.inflight is not None:
        raise ContainmentError('Completed-command crash scenario required')
    session.heartbeat.close()
    store=session.store;token=session.token;owner=session.owner
    before=durable_fault_state(store.path,token.job_id)
    if before['job_state']!='PAUSED_NEEDS_HUMAN':
        raise ContainmentError('Paused terminal job required')
    commands=session.commands
    args=dict(job_id=token.job_id,attempt_id=token.attempt_id,
              generation=token.generation,session_id=owner.session_id)
    with TerminalWorkerResolution(owner,lake,actor='local-resolution-operator',
                                  evidence_ref='A3_1_EXPLICIT_LOCAL_CRASH') as cleanup:
        first=cleanup.resolve(**args)
        second=cleanup.resolve(**args)
    assert first['resolved'] and not first['already_resolved']
    assert second['already_resolved']
    fresh=JobStore(store.path)
    try:
        assert_workers_resolved(fresh,'tiktok')
        params=(token.job_id,token.attempt_id,token.generation)
        events=[dict(row) for row in fresh.db.execute(
            "SELECT event_type,code FROM harness_events WHERE job_id=? AND attempt_id=? AND generation=? AND event_type IN ('worker.terminal_resolution','worker.quiescent')",params)]
        assert len(events)==2
        assert sum(e['event_type']=='worker.terminal_resolution' for e in events)==1
        assert sum(e['event_type']=='worker.quiescent' and e['code']==owner.session_id for e in events)==1
        assert fresh.get(token.job_id)['state']=='PAUSED_NEEDS_HUMAN'
        assert fresh.db.execute('SELECT COUNT(*) FROM tiktok_comments').fetchone()[0]==0
        assert fresh.db.execute('SELECT COUNT(*) FROM tiktok_comment_checkpoints').fetchone()[0]==0
    finally:fresh.close()
    assert durable_fault_state(store.path,token.job_id)==before
    assert before['receipt_count']==before['outbox_count']==0 and before['attempt_count']==1
    assert before['unknown_count']==0 and before['unresolved_intents']==[]
    assert session.commands==commands and not list(Path(lake).rglob('*.parquet'))
    return {'first':first,'repeated':second,'events':events,'fresh_connection_worker_gate':'PASS',
            'durable_state':before,'command_replay_count':0,'new_claim_count':0,
            'source_records':0,'checkpoints':0,'parquet_files':0}


@contextmanager
def observe_timeout_request(driver, evidence, command_id):
    """Record successful transport send, not an invented browser-side outcome.

The scoped request wrapper allows only the exact local execute/sync request and
refuses any resend. No bodies, endpoints or WebDriver session data are recorded.
"""
    from urllib3.connection import HTTPConnection
    original=HTTPConnection.request
    expected='/session/'+driver.session_id+'/execute/sync'
    sent=[]
    def request(connection, method, url, *args, **kwargs):
        if (sent or method!='POST' or url!=expected or connection.port!=driver.service.port or
                connection.host not in {'localhost','127.0.0.1','::1'}):
            raise ContainmentError('Unexpected or repeated diagnostic HTTP request')
        # Reserve BEFORE send: even an ambiguous transport error cannot resend.
        sent.append(True)
        result=original(connection,method,url,*args,**kwargs)
        atomic_json(Path(evidence)/('driver-call-'+command_id+'.json'),{
            'command_id':command_id,'phase':'HTTP_REQUEST_SENT','monotonic':time.monotonic(),
            'script':'FIXED_SYNTHETIC_DELAY_6000MS','browser_outcome':'UNOBSERVED'})
        return result
    with patch.object(HTTPConnection,'request',request):
        yield
