"""A.3 real Windows handles/locks and SQLite, contained Python workers only."""
from dataclasses import replace
import json
import sqlite3
import threading
import subprocess

import pytest

from crawler_harness.worker_resolution import TerminalWorkerResolution
from crawler_harness.containment import ManagedWorker,ContainmentError,assert_workers_resolved
from crawler_harness.state import JobStore,OwnershipError
from crawler_harness.publication import publisher_ownership,PublisherBusy
from crawler_harness.restrictions import Restrictions,assert_dispatch,RestrictionError
from crawler_harness.browser_commands import CommandFault
from tests.test_harness_containment import work,argv,wait_file
from tests.test_harness_browser_commands import work as browser_work,session
from tests.test_harness_restrictions import env


def start(work):
    root,store,token,_=work
    owner=ManagedWorker(store,token);owner.__enter__()
    owner.start(argv('leaf',root,'worker'));wait_file(root/'worker.json')
    return owner


def stop_and_pause(work,owner,state='PAUSED_NEEDS_HUMAN'):
    root,store,token,_=work
    owner.inventory()
    owner.shutdown(session_id=owner.session_id,attempt_id=token.attempt_id)
    store.transition(token,state,code='SYNTHETIC_FAULT')


def request(owner):
    return dict(job_id=owner.token.job_id,attempt_id=owner.token.attempt_id,
        generation=owner.token.generation,session_id=owner.session_id)


def authority(work,owner):
    return TerminalWorkerResolution(owner,work[0]/'lake',actor='offline-cleanup-operator',evidence_ref='native-fixture')


@pytest.mark.parametrize('state',['PAUSED_NEEDS_HUMAN','ACCESS_RESTRICTED','FAILED'])
def test_exact_terminal_resolution_idempotency_no_claim_or_reopen(work,state):
    root,store,token,_=work;owner=start(work)
    try:
        stop_and_pause(work,owner,state)
        before=store.get(token.job_id)
        with authority(work,owner) as cleanup:
            assert not cleanup.resolve(**request(owner))['already_resolved']
            assert cleanup.resolve(**request(owner))['already_resolved']
        assert store.get(token.job_id)==before
        assert_workers_resolved(store,'tiktok')
        reopened=JobStore(store.path)
        try:
            assert_workers_resolved(reopened,'tiktok')
            assert reopened.get(token.job_id)['state']==state
        finally:reopened.close()
        with pytest.raises(OwnershipError):store.heartbeat(token)
        assert store.claim(token.job_id,'new-worker') is None
        events=store.db.execute("SELECT event_type FROM harness_events WHERE event_type IN ('worker.terminal_resolution','worker.quiescent')").fetchall()
        assert len(events)==2
    finally:owner.close()
    assert owner._proof['durable_resolution'] is True


@pytest.mark.parametrize('field,value',[('session_id','wrong'),('attempt_id','wrong'),('job_id','wrong'),('generation',2)])
def test_exact_resolution_scope_required(work,field,value):
    owner=start(work)
    try:
        stop_and_pause(work,owner)
        args=request(owner);args[field]=value
        with authority(work,owner) as cleanup:
            with pytest.raises(OwnershipError):cleanup.resolve(**args)
        with pytest.raises(ContainmentError):assert_workers_resolved(work[1],'tiktok')
    finally:owner.close()


@pytest.mark.parametrize('fault',['process','creation','source_lock'])
def test_wrong_retained_proof_or_missing_source_ownership(work,fault):
    owner=start(work)
    try:
        stop_and_pause(work,owner)
        old=(owner.process,owner.worker_identity,owner._source_guard)
        if fault=='process':owner.process=owner.controller
        if fault=='creation':owner.worker_identity=replace(owner.worker_identity,creation_filetime=1)
        if fault=='source_lock':owner._source_guard=None
        try:
            with pytest.raises(ContainmentError):
                with authority(work,owner):pass
        finally:owner.process,owner.worker_identity,owner._source_guard=old
    finally:owner.close()


def test_live_worker_is_not_resolved_or_killed_by_resolution(work):
    owner=start(work)
    try:
        with pytest.raises(ContainmentError):
            with authority(work,owner):pass
        assert not owner.api.wait(owner.process)
    finally:owner.close()


def test_expired_active_claim_not_cleanup_authority(work):
    owner=start(work)
    try:
        root,store,token,now=work
        owner.shutdown(session_id=owner.session_id,attempt_id=token.attempt_id)
        now[0]=store.get(token.job_id)['lease_until']+1
        with authority(work,owner) as cleanup:
            with pytest.raises(OwnershipError):cleanup.resolve(**request(owner))
    finally:owner.close()
    assert owner._proof['durable_resolution'] is False


def test_closed_controller_proof_or_serialized_boolean_cannot_resolve(work):
    owner=start(work);stop_and_pause(work,owner);owner.close()
    with pytest.raises(OwnershipError):
        with authority(work,owner):pass
    with pytest.raises(ContainmentError):TerminalWorkerResolution({'verified':True},work[0]/'lake',actor='operator',evidence_ref='untrusted')
    with pytest.raises(ContainmentError):assert_workers_resolved(work[1],'tiktok')


def test_publisher_contention_blocks_cleanup(work):
    root,store,_,_=work;owner=start(work)
    try:
        stop_and_pause(work,owner)
        with publisher_ownership(store.path,root/'lake'):
            with pytest.raises(PublisherBusy):
                with authority(work,owner):pass
        with pytest.raises(ContainmentError):assert_workers_resolved(store,'tiktok')
    finally:owner.close()


def test_scope_mutated_in_database_is_rejected_atomically(work):
    owner=start(work)
    try:
        stop_and_pause(work,owner);store=work[1]
        with store._transaction():store.db.execute('UPDATE harness_jobs SET generation=generation+1 WHERE job_id=?',(owner.token.job_id,))
        with authority(work,owner) as cleanup:
            with pytest.raises(OwnershipError):cleanup.resolve(**request(owner))
        assert store.db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type='worker.terminal_resolution'").fetchone()[0]==0
    finally:owner.close()


def test_audit_and_quiescent_event_roll_back_together(work,monkeypatch):
    owner=start(work);store=work[1]
    try:
        stop_and_pause(work,owner);original=store._event
        def fault(row,kind,*args):
            if kind=='worker.quiescent':raise RuntimeError('synthetic crash before commit')
            return original(row,kind,*args)
        monkeypatch.setattr(store,'_event',fault)
        with authority(work,owner) as cleanup:
            with pytest.raises(RuntimeError):cleanup.resolve(**request(owner))
        assert store.db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type IN ('worker.quiescent','worker.terminal_resolution')").fetchone()[0]==0
    finally:owner.close()


def test_resolution_serializes_concurrent_claim_on_separate_connection(work,monkeypatch):
    root,store,token,_=work;owner=start(work);entered=threading.Event();attempting=threading.Event();done=threading.Event();ready=threading.Event();results=[]
    try:
        stop_and_pause(work,owner);original=store._event
        def event(row,kind,*args):
            if kind=='worker.terminal_resolution':
                entered.set();assert attempting.wait(5);assert not done.wait(.1)
            return original(row,kind,*args)
        monkeypatch.setattr(store,'_event',event)
        def contender():
            db=JobStore(store.path)
            try:
                ready.set()
                assert entered.wait(5);attempting.set()
                results.append(db.claim(token.job_id,'contender'))
            finally:db.close();done.set()
        t=threading.Thread(target=contender);t.start()
        assert ready.wait(5)
        with authority(work,owner) as cleanup:cleanup.resolve(**request(owner))
        t.join(10);assert not t.is_alive() and results==[None]
    finally:owner.close()


def test_unknown_command_stays_unknown_after_worker_resolution(browser_work):
    root,store,_,_,token,_,_=browser_work
    with session(browser_work) as s:
        with pytest.raises(CommandFault):s.driver.execute_script('crash')
        count=s.commands;unknown=s.inflight
        with TerminalWorkerResolution(s.owner,root/'lake',actor='offline-operator',evidence_ref='native-proof') as cleanup:
            cleanup.resolve(**request(s.owner))
        assert_workers_resolved(store,'tiktok')
        assert s.commands==count and s.inflight==unknown
        assert store.db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type='browser.command_succeeded' AND code=?",(unknown,)).fetchone()[0]==0
        assert store.get(token.job_id)['state']=='PAUSED_NEEDS_HUMAN'
    assert s.owner._proof['durable_resolution'] is True


def test_c2c_latch_and_source_session_unchanged(env):
    root,store,now,_,_,_,token=env
    work=(root,store,token,now);owner=start(work)
    try:
        r=Restrictions(store);r.register(token,owner.session_id)
        r.activate(token,owner.session_id,'UNKNOWN',evidence_ref='synthetic-access')
        stop_and_pause(work,owner,'ACCESS_RESTRICTED')
        before=[tuple(x) for x in store.db.execute('SELECT * FROM harness_restrictions')]
        with authority(work,owner) as cleanup:cleanup.resolve(**request(owner))
        assert_workers_resolved(store,'tiktok')
        assert [tuple(x) for x in store.db.execute('SELECT * FROM harness_restrictions')]==before
        assert store.db.execute('SELECT status FROM harness_source_sessions').fetchone()[0]=='OPEN'
        with store._transaction():
            with pytest.raises(RestrictionError):assert_dispatch(store,'tiktok')
    finally:owner.close()


def test_actual_controller_death_cannot_be_replaced_by_serialized_proof(work):
    root,store,token,_=work
    child=subprocess.Popen(argv('supervisor',root,'resumed'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        wait_file(root/'supervisor-ready.json')
        old_proof=json.loads((root/'supervisor-ready.json').read_text())
        (root/'crash-supervisor').touch()
        child.communicate(timeout=12);assert child.returncode==84
        with pytest.raises(ContainmentError):TerminalWorkerResolution(old_proof,root/'lake',actor='operator',evidence_ref='old-file')
        with pytest.raises(ContainmentError,match='Unresolved'):
            with ManagedWorker(store,token):pass
    finally:
        if child.poll() is None:child.kill();child.communicate(timeout=5)


def test_missing_publisher_authority_or_wrong_thread_refuses_resolution(work):
    owner=start(work)
    try:
        stop_and_pause(work,owner);cleanup=authority(work,owner)
        with pytest.raises(OwnershipError):cleanup.resolve(**request(owner))
        errors=[]
        with cleanup:
            def wrong_thread():
                try:cleanup.resolve(**request(owner))
                except OwnershipError:errors.append('refused')
            t=threading.Thread(target=wrong_thread);t.start();t.join(5)
            assert not t.is_alive() and errors==['refused']
    finally:owner.close()


def test_released_native_source_lock_capability_cannot_be_reused(work):
    from crawler_harness.publication import windows_mutex
    with windows_mutex('Global\\A3Fixture_'+work[2].job_id) as guard:guard()
    with pytest.raises(PublisherBusy):guard()


def test_descendant_creation_proof_must_match_native_handle(work):
    owner=start(work)
    try:
        stop_and_pause(work,owner)
        pid,(handle,identity)=next(iter(owner._children.items()))
        owner._children[pid]=(handle,replace(identity,creation_filetime=1))
        try:
            with pytest.raises(ContainmentError):
                with authority(work,owner):pass
        finally:owner._children[pid]=(handle,identity)
    finally:owner.close()


def test_released_publisher_locks_cannot_authorize_cleanup(work):
    owner=start(work)
    try:
        stop_and_pause(work,owner)
        with authority(work,owner) as cleanup:
            cleanup.stack.close()
            with pytest.raises(PublisherBusy):cleanup.resolve(**request(owner))
    finally:owner.close()


def test_existing_c2c_worker_fact_reused_without_duplicate(env):
    root,store,now,_,_,_,token=env;work=(root,store,token,now);owner=start(work)
    try:
        r=Restrictions(store);r.register(token,owner.session_id)
        stop_and_pause(work,owner)
        r.close_verified(token,owner.session_id,owner)
        with authority(work,owner) as cleanup:
            assert cleanup.resolve(**request(owner))['resolved']
            assert cleanup.resolve(**request(owner))['already_resolved']
        assert store.db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type='worker.quiescent'").fetchone()[0]==1
    finally:owner.close()


def test_idempotent_resolution_refuses_changed_lake_binding(work):
    owner=start(work)
    try:
        stop_and_pause(work,owner)
        with authority(work,owner) as cleanup:cleanup.resolve(**request(owner))
        with TerminalWorkerResolution(owner,work[0]/'wrong-lake',actor='operator',evidence_ref='wrong-resource') as cleanup:
            with pytest.raises(ContainmentError):cleanup.resolve(**request(owner))
        assert work[1].db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type='worker.terminal_resolution'").fetchone()[0]==1
    finally:owner.close()
