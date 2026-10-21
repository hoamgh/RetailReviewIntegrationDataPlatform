"""Real Windows Job Object faults; Python fixtures only, no browser/network."""
from dataclasses import asdict,replace
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pytest
from crawler_harness.containment import ManagedWorker,WindowsAPI,ContainmentError
from crawler_harness.state import JobStore,Claim,OwnershipError
from crawler_harness.publication import PublisherBusy,JobScopedPublication
from experiments.tiktok_comment_dom.persistence import CommentPersistence

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows atomic Job Object contract')


def wait_file(path,timeout=8):
    deadline=time.monotonic()+timeout
    while not path.exists():
        if time.monotonic()>deadline:raise AssertionError('Fixture handshake timeout: '+path.name)
        time.sleep(.01)


def argv(*args):
    return [sys.executable,'-B',str(Path(__file__).resolve()),*map(str,args)]


def seed(root):
    root.mkdir(parents=True,exist_ok=True)
    db=root/'comments.sqlite3'
    repo=CommentPersistence(db,root/'lake');repo.close()
    now=[time.time()]
    store=JobStore(db,clock=lambda:now[0])
    job=store.create_job(source='tiktok',kind='collection',place_id='fixture-place',
        entity_id='123',input_digest='fixture-input')
    token=store.claim(job,'fixture-owner',lease_seconds=300)
    (root/'context.json').write_text(json.dumps({'claim':asdict(token),'now':now[0]}))
    return store,token,now


@pytest.fixture
def work(tmp_path):
    store,token,now=seed(tmp_path)
    yield tmp_path,store,token,now
    store.close()


def opened(api,identity):
    handle=api.k.OpenProcess(0x100000|0x1000,False,identity['pid'])
    assert handle
    assert asdict(api.identity(handle))==identity
    return handle


def test_atomic_membership_before_first_instruction_and_descendants(work):
    root,store,token,_=work
    stages=[]
    def inspect(phase,owner):
        if phase=='created_suspended':
            owner.api.member(owner.process,owner.job)
            assert owner.api.active(owner.job)==1
            assert not (root/'worker-started').exists()
            stages.append(phase)
    with ManagedWorker(store,token,fault=inspect) as owner:
        binding=owner.start(argv('tree',root))
        wait_file(root/'grandchild.json')
        inventory=owner.inventory()
        # CREATE_NO_WINDOW may add Windows-owned conhost descendants. Verify
        # every kernel member; do not silently discard helpers to fit a count.
        assert len(inventory)>=3
        grandchild=json.loads((root/'grandchild.json').read_text())
        assert grandchild['pid'] in {p['pid'] for p in inventory}
        assert binding['worker']['pid'] in {p['pid'] for p in inventory}
        assert all(p['creation_filetime']>0 for p in inventory)
        assert binding['attempt_id']==token.attempt_id
        assert owner.api.identity(owner.process).windows_session_id==binding['worker']['windows_session_id']
        with pytest.raises(ContainmentError,match='not proven'):owner.quiescence()
        proof=owner.shutdown(session_id=owner.session_id,attempt_id=token.attempt_id)
        assert proof['job_active_processes']==0 and proof['worker_handle_signaled']
    assert owner._proof['durable_resolution'] is True
    assert stages==['created_suspended']
    with pytest.raises(ContainmentError,match='already launched'):
        with ManagedWorker(store,token):pass


def test_worker_crash_does_not_prove_descendant_quiescence(work):
    root,store,token,_=work
    with ManagedWorker(store,token) as owner:
        owner.start(argv('tree',root));wait_file(root/'grandchild.json')
        (root/'crash-worker').touch()
        assert owner.api.wait(owner.process,5000)
        assert owner.api.active(owner.job)>=1
        with pytest.raises(ContainmentError,match='not proven'):owner.quiescence()
        proof=owner.shutdown(session_id=owner.session_id,attempt_id=token.attempt_id)
        assert proof['job_active_processes']==0


@pytest.mark.parametrize('phase',['intent_committed','created_suspended','resumed'])
def test_supervisor_crash_kills_tree_and_restart_remains_held(work,phase):
    root,store,token,_=work
    child=subprocess.Popen(argv('supervisor',root,phase),cwd=ROOT,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    handles=[];api=WindowsAPI()
    try:
        wait_file(root/'supervisor-ready.json')
        data=json.loads((root/'supervisor-ready.json').read_text())
        identities=data['processes']
        handles=[opened(api,i) for i in identities]
        (root/'crash-supervisor').touch()
        _,err=child.communicate(timeout=10)
        assert child.returncode==84,err
        assert all(api.wait(h,5000) for h in handles)
        assert bool(identities)==(phase!='intent_committed')
        if phase=='created_suspended':assert not (root/'worker-started').exists()
        if phase=='resumed':assert len(identities)>=3
        for _ in range(2):
            with pytest.raises(ContainmentError,match='Unresolved'):
                with ManagedWorker(store,token):pass
        other=store.create_job(source='tiktok',kind='collection',place_id='other-place',
            entity_id='456',input_digest='fixture-input')
        other_token=store.claim(other,'other-owner',lease_seconds=300)
        with pytest.raises(ContainmentError,match='Unresolved'):
            with ManagedWorker(store,other_token):pass
        assert store.db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type='worker.launch_intent'").fetchone()[0]==1
        assert store.db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type='worker.quiescent'").fetchone()[0]==0
        (root/'containment_evidence.json').write_text(json.dumps({'phase':phase,
            'supervisor_exit':84,'retained_handles_signaled':len(handles),
            'repeated_restart_held':True,'atomic_creation':True},indent=2))
    finally:
        if child.poll() is None:child.kill();child.communicate(timeout=5)
        for h in handles:api.k.CloseHandle(h)


def test_lease_expiry_never_authorizes_takeover_and_cleanup_is_kernel_owned(work):
    root,store,token,now=work
    with ManagedWorker(store,token) as owner:
        owner.start(argv('leaf',root,'worker'))
        wait_file(root/'worker.json')
        now[0]+=301
        assert store.claim(token.job_id,'other') is None
        with pytest.raises(OwnershipError):
            store.takeover(token.job_id,'other',expected_generation=1,
                verify_quiescent=lambda _:False,evidence_ref='NOT_QUIESCENT')
        assert owner.api.active(owner.job)>=1 and not owner.api.wait(owner.process)
        owner.shutdown(session_id=owner.session_id,attempt_id=token.attempt_id)
    assert owner._proof['durable_resolution'] is False
    # Even an explicit M1 attestation/new generation cannot silently erase C2a intent.
    new=store.takeover(token.job_id,'other',expected_generation=1,
        verify_quiescent=lambda _:True,evidence_ref='OFFLINE_VERIFIED_HANDLES')
    with pytest.raises(ContainmentError,match='Unresolved'):
        with ManagedWorker(store,new):pass


def test_session_attempt_and_supervisor_thread_mismatch_never_kill_owned_tree(work):
    root,store,token,_=work
    with ManagedWorker(store,token) as owner:
        owner.start(argv('leaf',root,'worker'));wait_file(root/'worker.json')
        for session,attempt in [('wrong',token.attempt_id),(owner.session_id,'wrong')]:
            with pytest.raises(OwnershipError):owner.shutdown(session_id=session,attempt_id=attempt)
        errors=[]
        def wrong_thread():
            try:owner.shutdown(session_id=owner.session_id,attempt_id=token.attempt_id)
            except OwnershipError:errors.append(True)
        t=threading.Thread(target=wrong_thread);t.start();t.join(3)
        assert errors==[True] and owner.api.active(owner.job)>=1 and not owner.api.wait(owner.process)


def test_two_supervisors_cannot_own_same_physical_database(work):
    root,store,token,_=work
    with ManagedWorker(store,token) as owner:
        owner.start(argv('leaf',root,'worker'));wait_file(root/'worker.json')
        child=subprocess.run(argv('contender',root),cwd=ROOT,capture_output=True,text=True,timeout=8)
        assert child.returncode==0,child.stderr
        assert child.stdout.strip()=='OWNERSHIP_BUSY'
        assert owner.api.active(owner.job)>=1 and not owner.api.wait(owner.process)


def test_invalid_argv_and_adoption_not_supported(work):
    root,store,token,_=work
    with ManagedWorker(store,token) as owner:
        for command in ['python -c pass',[],['C:/Windows/System32/notepad.exe']]:
            with pytest.raises(ContainmentError):owner.start(command)
        assert owner.api.active(owner.job)==0
        assert not owner._intent
        assert not hasattr(owner,'adopt')


def test_unsupported_job_attribute_fails_closed_without_assign_fallback(work,monkeypatch):
    root,store,token,_=work
    with ManagedWorker(store,token) as owner:
        def unavailable(*_):raise ContainmentError('Job-list unavailable')
        monkeypatch.setattr(owner.api,'create',unavailable)
        with pytest.raises(ContainmentError,match='unavailable'):
            owner.start(argv('leaf',root,'worker'))
        assert not owner._created and owner.api.active(owner.job)==0
    assert not (root/'worker.json').exists()
    assert store.db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type='worker.launch_aborted'").fetchone()[0]==1


def test_controller_exception_after_creation_joins_suspended_worker(work):
    root,store,token,_=work
    def fault(phase,_):
        if phase=='created_suspended':raise RuntimeError('offline fault')
    with ManagedWorker(store,token,fault=fault) as owner:
        with pytest.raises(RuntimeError):owner.start(argv('leaf',root,'worker'))
        assert owner.api.active(owner.job)==0 and owner.api.wait(owner.process)
    assert not (root/'worker.json').exists()


@pytest.mark.parametrize('function',['UpdateProcThreadAttribute','CreateProcessW'])
def test_native_attribute_or_creation_failure_has_no_uncontained_fallback(work,monkeypatch,function):
    root,store,token,_=work
    with ManagedWorker(store,token) as owner:
        def rejected(*_):c.set_last_error(50);return False
        with monkeypatch.context() as patch:
            patch.setattr(owner.api.k,function,rejected)
            with pytest.raises(ContainmentError,match='API failure'):
                owner.start(argv('leaf',root,'worker'))
        assert not owner._created and owner.api.active(owner.job)==0
    assert not (root/'worker.json').exists()


def test_expiry_before_resume_stops_suspended_worker_without_executing_fixture(work):
    root,store,token,now=work
    def fault(phase,_):
        if phase=='created_suspended':now[0]+=301
    with ManagedWorker(store,token,fault=fault) as owner:
        with pytest.raises(OwnershipError):owner.start(argv('leaf',root,'worker'))
        assert owner.api.wait(owner.process) and owner.api.active(owner.job)==0
    assert owner._proof['durable_resolution'] is False
    assert not (root/'worker.json').exists()


def test_stale_claim_is_rejected_before_intent_or_process_creation(work):
    root,store,token,_=work
    with pytest.raises(OwnershipError):
        with ManagedWorker(store,replace(token,generation=2)):pass
    assert store.db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type='worker.launch_intent'").fetchone()[0]==0


def test_database_hardlink_alias_cannot_acquire_second_owner(work):
    root,store,token,now=work
    os.link(store.path,root/'alias.sqlite3')
    alias=JobStore(root/'alias.sqlite3',clock=lambda:now[0])
    try:
        with ManagedWorker(store,token):
            with pytest.raises(PublisherBusy):
                with ManagedWorker(alias,token):pass
    finally:alias.close()


def test_shutdown_timeout_does_not_issue_quiescence_proof(work,monkeypatch):
    root,store,token,_=work
    with ManagedWorker(store,token) as owner:
        owner.start(argv('leaf',root,'worker'));wait_file(root/'worker.json')
        with monkeypatch.context() as patch:
            patch.setattr(owner.api,'active',lambda _:1)
            with pytest.raises(ContainmentError,match='unverified'):
                owner.shutdown(session_id=owner.session_id,attempt_id=token.attempt_id,timeout=.02)
        assert owner._proof is None


def test_clean_session_allows_other_job_without_reusing_same_attempt(work):
    root,store,token,_=work
    with ManagedWorker(store,token) as owner:
        owner.start(argv('leaf',root,'worker'));wait_file(root/'worker.json')
    other=store.create_job(source='tiktok',kind='collection',place_id='fixture-b',
        entity_id='456',input_digest='fixture-input')
    second=store.claim(other,'second',lease_seconds=300)
    with ManagedWorker(store,second) as owner2:
        owner2.start(argv('leaf',root,'second'));wait_file(root/'second.json')
    assert owner2._proof['durable_resolution'] is True


def test_source_sealed_between_owner_entry_and_start_refuses_launch(work):
    root,store,token,_=work
    with ManagedWorker(store,token) as owner:
        store.record_receipt(token,source_complete=False,completion_evidence=None,
            stop_reason='MAX_SCROLLS',event_ids=[],checkpoint_ref='fixture-checkpoint')
        store.transition(token,'PERSISTING')
        with pytest.raises(OwnershipError):owner.start(argv('leaf',root,'worker'))
        assert not owner._intent and owner.api.active(owner.job)==0


def test_unverified_job_configuration_never_creates_process(work,monkeypatch):
    root,store,token,_=work
    with ManagedWorker(store,token) as owner:
        original=owner.api.k.QueryInformationJobObject
        def bad_limits(job,kind,buf,size,returned):
            ok=original(job,kind,buf,size,returned)
            if kind==9:
                from crawler_harness.containment import ExtendedLimits
                c.cast(buf,c.POINTER(ExtendedLimits)).contents.basic.flags=0
            return ok
        with monkeypatch.context() as patch:
            patch.setattr(owner.api.k,'QueryInformationJobObject',bad_limits)
            with pytest.raises(ContainmentError,match='Unsafe'):
                owner.start(argv('leaf',root,'worker'))
        assert not owner._created and owner.api.active(owner.job)==0
    assert not (root/'worker.json').exists()


def test_inventory_never_adopts_process_outside_owned_job(work,monkeypatch):
    root,store,token,_=work
    external=subprocess.Popen(argv('leaf',root,'outside'),cwd=ROOT)
    try:
        wait_file(root/'outside.json')
        with ManagedWorker(store,token) as owner:
            with monkeypatch.context() as patch:
                patch.setattr(owner.api,'pids',lambda _:[external.pid])
                with pytest.raises(ContainmentError,match='not in owned'):owner.inventory()
            assert not owner._children
        assert external.poll() is None
    finally:
        if external.poll() is None:external.terminate()
        external.wait(timeout=5)


def test_detached_descendant_remains_in_job_and_breakaway_is_denied(work):
    root,store,token,_=work
    with ManagedWorker(store,token) as owner:
        owner.start(argv('detached',root))
        wait_file(root/'detached-result.json')
        result=json.loads((root/'detached-result.json').read_text())
        wait_file(root/'detached-child.json')
        assert result['breakaway_rejected'] is True
        inventory=owner.inventory()
        assert len(inventory)>=2
        assert json.loads((root/'detached-child.json').read_text())['pid'] in {p['pid'] for p in inventory}
    assert not (root/'breakaway-child.json').exists()


def test_unrelated_process_is_not_closed_or_adopted(work):
    root,store,token,_=work
    external=subprocess.Popen(argv('leaf',root,'unrelated'),cwd=ROOT)
    try:
        wait_file(root/'unrelated.json')
        with ManagedWorker(store,token) as owner:
            owner.start(argv('leaf',root,'owned'));wait_file(root/'owned.json')
            assert external.pid not in {p['pid'] for p in owner.inventory()}
        assert external.poll() is None
    finally:
        if external.poll() is None:external.terminate()
        external.wait(timeout=5)


def test_committed_data_survives_worker_crash_c1_replays_without_collector(work):
    root,store,token,_=work
    with ManagedWorker(store,token) as owner:
        owner.start(argv('commit-crash',root))
        assert owner.api.wait(owner.process,8000)
        proof=owner.quiescence()
        assert store.get(token.job_id)['state']=='PERSISTING'
        assert store.db.execute('SELECT COUNT(*) FROM tiktok_comments').fetchone()[0]==1
        assert store.db.execute('SELECT delivered FROM tiktok_comment_events').fetchone()[0]==0
    engine=JobScopedPublication(store,root/'lake')
    result=engine.recover(token.job_id,token,verify_quiescent=lambda _:proof['job_active_processes']==0,
        evidence_ref='OFFLINE_RETAINED_HANDLE_AND_JOB_ZERO')
    assert result['status']=='PARTIAL' and result['pending']==0 and result['parquet_rows']==1
    initial={str(p):p.read_bytes() for p in (root/'lake').rglob('*.parquet')}
    for _ in range(2):assert engine.recover(token.job_id,verify_quiescent=lambda _:True,
        evidence_ref='OFFLINE_ALREADY_QUIESCENT')['already_finalized']
    assert {str(p):p.read_bytes() for p in (root/'lake').rglob('*.parquet')}==initial
    assert store.db.execute('SELECT COUNT(*) FROM tiktok_comment_events').fetchone()[0]==1
    assert not (root/'collector-called').exists()


def helper(mode,root,*extra):
    root=Path(root)
    if mode=='leaf':
        api=WindowsAPI();h=api.k.OpenProcess(0x100000|0x1000,False,os.getpid())
        try:(root/(extra[0]+'.json')).write_text(json.dumps(asdict(api.identity(h))))
        finally:api.k.CloseHandle(h)
        threading.Event().wait(20)
    elif mode in ('tree','branch','detached'):
        if mode=='tree':
            (root/'worker-started').touch()
            subprocess.Popen(argv('branch',root),creationflags=subprocess.CREATE_NO_WINDOW)
        elif mode=='branch':subprocess.Popen(argv('leaf',root,'grandchild'),creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            subprocess.Popen(argv('leaf',root,'detached-child'),creationflags=8|0x200)
            try:
                child=subprocess.Popen(argv('leaf',root,'breakaway-child'),creationflags=0x01000000|subprocess.CREATE_NO_WINDOW)
            except OSError:rejected=True
            else:
                rejected=False;child.terminate();child.wait(timeout=5)
            (root/'detached-result.json').write_text(json.dumps({'breakaway_rejected':rejected}))
        end=time.monotonic()+20
        while time.monotonic()<end:
            if (root/'crash-worker').exists() and mode=='tree':os._exit(81)
            time.sleep(.02)
    elif mode in ('supervisor','contender','commit-crash'):
        ctx=json.loads((root/'context.json').read_text());token=Claim(**ctx['claim'])
        store=JobStore(root/'comments.sqlite3',clock=lambda:ctx['now'])
        if mode=='commit-crash':
            from crawler_harness.tiktok_adapter import TikTokManagedAdapter
            from experiments.tiktok_comment_dom.extractor import CommentSession
            adapter=TikTokManagedAdapter(store,root/'lake',root/'evidence')
            s=CommentSession('123')
            s.add({'url':'https://www.tiktok.com/@fixture/video/123',
                'cards':[{'text':'synthetic committed comment'}]},'2026-10-21T00:00:00+00:00')
            repo=adapter.repository(token)
            repo.persist(s,run_id=store.get(token.job_id)['data_run_id'],place_id='fixture-place',
                input_digest='fixture-input',next_page=1,provenance={'mode':'OFFLINE_FIXTURE'},
                final_observation={'stop_reason':'MAX_SCROLLS'})
            os._exit(81)
        elif mode=='contender':
            try:
                with ManagedWorker(store,token):pass
            except PublisherBusy:print('OWNERSHIP_BUSY',flush=True)
            else:raise AssertionError('Conflicting supervisor acquired ownership')
        else:
            phase=extra[0]
            def fault(current,owner):
                if current!=phase:return
                if current=='resumed':wait_file(root/'grandchild.json')
                processes=owner.inventory() if current=='resumed' else (
                    [asdict(owner.worker_identity)] if owner.worker_identity else [])
                (root/'supervisor-ready.json').write_text(json.dumps({'processes':processes}))
                wait_file(root/'crash-supervisor')
                os._exit(84)
            with ManagedWorker(store,token,fault=fault) as owner:owner.start(argv('tree',root))
        store.close()
    else:raise AssertionError('Unknown fixture')


if __name__=='__main__':helper(*sys.argv[1:])
