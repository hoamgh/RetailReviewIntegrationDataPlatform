"""C2c isolated SQLite, synthetic access and contained fake driver only."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import pytest
from crawler_harness.state import JobStore,StateError,OwnershipError,_digest
from crawler_harness.restrictions import (
    Restrictions,RestrictionError,OfflineOperatorControl,assert_dispatch,installed,
)
from crawler_harness.browser_commands import ManagedBrowserSession,CommandFault
from crawler_harness.browser_profile import DedicatedProfile
from crawler_harness.publication import JobScopedPublication,PublisherBusy
from experiments.tiktok_comment_dom.persistence import CommentPersistence


@pytest.fixture
def env(tmp_path):
    path=tmp_path/'state.sqlite3';lake=tmp_path/'lake'
    CommentPersistence(path,lake).close()
    now=[time.time()]
    store=JobStore(path,clock=lambda:now[0]);Restrictions.install(store,offline_fixture=True)
    def job(source='tiktok',suffix='a'):
        return store.create_job(source=source,kind='collection',place_id='place-'+suffix,
            entity_id='123',input_digest='fixture-'+suffix,workflow='restriction-fixture')
    a=job();b=job(suffix='b')
    token=store.claim(a,'owner',lease_seconds=300)
    yield tmp_path,store,now,job,a,b,token
    store.close()


def bind(env,sid='session-a'):
    _,s,_,_,_,_,token=env
    Restrictions(s).register(token,sid)
    return Restrictions(s),sid


def close_synthetic_session(s,sid):
    # Test database seeding ONLY: no production quiescence verifier uses SQL or
    # this helper. Real session closure is tested through C2a retained handles.
    with s._transaction():
        s.db.execute("UPDATE harness_source_sessions SET status='CLOSED',closed_at=? WHERE session_id=?",(s._now(),sid))


def bundle(s,sid,epoch):
    return {'provider':'REVIEWED_OFFLINE_FIXTURE_V1','source':'tiktok','session_id':sid,'epoch':epoch,
        'observations':[{'observed_at':s._now(),'snapshot':{'challenge_present':False,
            'dialog_kinds':[],'video_detail_rendered':True,'comment_panel_present':True,
            'network_gate':None}} for _ in range(2)]}


@pytest.mark.parametrize('reason',['CAPTCHA','LOGIN_REQUIRED','ACCESS_DENIED','RATE_LIMITED','UNKNOWN'])
def test_latch_survives_restart_blocks_claim_and_adapter(env,reason):
    _,s,now,job,a,b,token=env;r,sid=bind(env)
    epoch=r.activate(token,sid,reason,evidence_ref='fixture:evidence')
    with JobStoreContext(s.path,now) as reopened:
        assert Restrictions(reopened).latch('tiktok')['reason']==reason
        assert Restrictions(reopened).latch('tiktok')['epoch']==epoch
        with pytest.raises(RestrictionError):reopened.claim(b,'worker-b')
    other=job('instagram','ig')
    assert s.claim(other,'other-source') is not None
    assert s.get(a)['state']=='RUNNING'  # latch activation alone never changes coverage/job


class JobStoreContext:
    def __init__(self,path,now):self.path,self.now=path,now
    def __enter__(self):self.store=JobStore(self.path,clock=lambda:self.now[0]);return self.store
    def __exit__(self,*_):self.store.close()


def test_unresolved_session_blocks_fresh_claim_without_latch(env):
    _,s,_,_,_,b,_=env;bind(env)
    assert Restrictions(s).latch('tiktok') is None
    with pytest.raises(RestrictionError):s.claim(b,'new')


def test_admission_rejects_stale_generation_expiry_session_and_duplicate(env):
    _,s,now,_,_,_,token=env;r,sid=bind(env)
    r.admit(token,sid,'cmd1')
    with pytest.raises(RestrictionError):r.admit(token,sid,'cmd1')
    for wrong in [replace(token,generation=2),replace(token,attempt_id='other')]:
        with pytest.raises(OwnershipError):r.admit(wrong,sid,'cmd2')
    with pytest.raises(RestrictionError):r.admit(token,'wrong-session','cmd2')
    now[0]+=301
    with pytest.raises(OwnershipError):r.admit(token,sid,'cmd2')


@pytest.mark.parametrize('errors,snapshot,expected',[
    ([{'status':403,'endpoint_kind':'related_items_api'}],{'video_detail_rendered':True,'challenge_present':False,'dialog_kinds':[]},None),
    ([{'status':403,'endpoint_kind':'document'}],{},'ACCESS_DENIED'),
    ([{'status':403,'endpoint_kind':'unknown'}],{},'UNKNOWN'),
    ([{'status':429,'endpoint_kind':'comments_api','retry_after_seconds':60}],{},'RATE_LIMITED'),
    ([],{'challenge_present':True},'CAPTCHA'),
    ([],{'dialog_kinds':['LOGIN_GATE']},'LOGIN_REQUIRED'),
    ([],{'body_text_snippet':'video unavailable'},None),
    ([],{},'UNKNOWN'),
    ([],{'interstitial_pending':True},None),
])
def test_reuses_classifier(env,errors,snapshot,expected):
    _,s,now,_,a,_,token=env;r,sid=bind(env)
    r.observe(token,sid,snapshot,errors,evidence_ref='fixture:observation')
    latch=r.latch('tiktok')
    if expected is None:
        assert latch is None and s.get(a)['state']=='RUNNING'
    else:
        assert latch['reason']==expected
        assert s.get(a)['state']==('PAUSED_NEEDS_HUMAN' if expected=='UNKNOWN' else 'ACCESS_RESTRICTED')
        if expected=='RATE_LIMITED':assert latch['not_before']==now[0]+60


@pytest.mark.parametrize('damage',['missing','wrong_scope','stale','epoch','challenge','missing_point'])
def test_bad_operator_verification_rejected(env,damage,monkeypatch):
    root,s,now,_,_,_,token=env;r,sid=bind(env)
    epoch=r.activate(token,sid,'CAPTCHA',evidence_ref='fixture:captcha')
    close_synthetic_session(s,sid)
    with OfflineOperatorControl(s,'tiktok',root/'lake',operator_id='operator') as op:
        data=bundle(s,sid,epoch)
        if damage=='missing':data={'verified':True}
        if damage=='wrong_scope':data['session_id']='other'
        if damage=='stale':data['observations'][0]['observed_at']-=61
        if damage=='epoch':data['epoch']+=1
        if damage=='challenge':data['observations'][0]['snapshot']['challenge_present']=True
        if damage=='missing_point':data['observations'].pop()
        monkeypatch.setattr(op,'_fixture_bundle',lambda _:data) # explicit faulty provider injection
        with pytest.raises(RestrictionError):op.verify_fixture()
    assert r.latch('tiktok')['active']==1


def test_retry_after_release_exact_epoch_single_use_and_terminal_preserved(env):
    root,s,now,_,a,b,token=env;r,sid=bind(env)
    epoch=r.activate(token,sid,'RATE_LIMITED',evidence_ref='fixture:429',retry_after=30)
    s.transition(token,'ACCESS_RESTRICTED',code='RATE_LIMITED')
    close_synthetic_session(s,sid)
    attempts=s.get(a)['attempt_count']
    with OfflineOperatorControl(s,'tiktok',root/'lake',operator_id='operator') as op:
        with pytest.raises(RestrictionError):op.verify_fixture()
        now[0]+=31
        assert r.latch('tiktok')['active']==1  # elapsed time never releases access
        vid=op.verify_fixture()
        with pytest.raises(RestrictionError):op.release(vid,expected_epoch=epoch+1,session_id=sid)
        op.release(vid,expected_epoch=epoch,session_id=sid)
        with pytest.raises(RestrictionError):op.release(vid,expected_epoch=epoch,session_id=sid)
    assert s.get(a)['state']=='ACCESS_RESTRICTED' and s.get(a)['attempt_count']==attempts
    assert s.claim(a,'retry') is None
    assert s.claim(b,'new-worker') is not None


def test_changed_epoch_and_expired_operator_control_refuse_release(env):
    root,s,_,_,_,_,token=env;r,sid=bind(env)
    epoch=r.activate(token,sid,'UNKNOWN',evidence_ref='fixture:unknown')
    close_synthetic_session(s,sid)
    with OfflineOperatorControl(s,'tiktok',root/'lake',operator_id='operator') as op:
        vid=op.verify_fixture()
        # Fault injection models a new observation committed after verification.
        with s._transaction():s.db.execute('UPDATE harness_restrictions SET epoch=epoch+1')
        with pytest.raises(RestrictionError):op.release(vid,expected_epoch=epoch,session_id=sid)
    with pytest.raises(OwnershipError):op.release(vid,expected_epoch=epoch,session_id=sid)


def test_db_write_failure_preserves_unresolved_hold_after_restart(env):
    _,s,now,_,_,b,token=env;r,sid=bind(env)
    s.db.execute("CREATE TRIGGER fail_restriction BEFORE INSERT ON harness_restrictions BEGIN SELECT RAISE(ABORT,'offline injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError):r.activate(token,sid,'CAPTCHA',evidence_ref='fixture:failure')
    assert r.latch('tiktok') is None
    with JobStoreContext(s.path,now) as reopened:
        with pytest.raises(RestrictionError):reopened.claim(b,'new')


def test_schema_backup_restore_exact_validation_and_no_implicit_migration(tmp_path):
    path=tmp_path/'legacy.sqlite3';CommentPersistence(path,tmp_path/'lake').close()
    s=JobStore(path);backup=sqlite3.connect(tmp_path/'before.sqlite3')
    job=s.create_job(source='tiktok',kind='discovery',place_id='p',entity_id='e',input_digest='d',workflow='backup-fixture')
    s.db.backup(backup);backup.close()
    assert not installed(s)
    Restrictions.install(s,offline_fixture=True);s.close()
    s=JobStore(path);assert installed(s);s.close()
    before=sqlite3.connect(tmp_path/'before.sqlite3');restored=sqlite3.connect(tmp_path/'restored.sqlite3')
    before.backup(restored);before.close();restored.close()
    s=JobStore(tmp_path/'restored.sqlite3');assert not installed(s)
    assert s.get(job)['state']=='PENDING'
    s.close()
    db=sqlite3.connect(path);db.execute('DROP TABLE harness_access_verifications');db.commit();db.close()
    with pytest.raises(StateError,match='Partial restriction'):JobStore(path)


def test_admission_activation_contention_separate_connections(env):
    _,s,now,_,_,_,token=env;r,sid=bind(env)
    ready=threading.Event();entered=threading.Event();go=threading.Event();errors=[]
    def contender():
        try:
            with JobStoreContext(s.path,now) as other:
                ready.set()
                assert go.wait(3)
                with other._transaction(token):
                    entered.set()
                    Restrictions.activate_in_transaction(other,token,sid,'CAPTCHA',evidence_ref='fixture:race')
        except BaseException as exc:errors.append(exc)
    t=threading.Thread(target=contender);t.start();assert ready.wait(3)
    with s._transaction(token):
        assert_dispatch(s,'tiktok',sid)
        go.set()
        assert not entered.wait(.1)
        s._event(s._owned(token),'browser.command_admitted','RUNNING','RUNNING','race-command')
    t.join(5);assert not t.is_alive() and not errors and entered.is_set()
    with pytest.raises(RestrictionError):r.admit(token,sid,'later-command')


def c2c_session(env,**kw):
    root,s,_,_,_,_,token=env
    profile=DedicatedProfile.create(root/'profile',s.path)
    return ManagedBrowserSession(s,token,profile=profile,evidence=root/'browser',**kw)


@pytest.mark.skipif(os.name!='nt',reason='Windows C2a containment')
def test_contained_worker_admission_and_restriction_cleanup(env):
    root,s,now,_,a,b,token=env
    with c2c_session(env) as browser:
        with pytest.raises(PublisherBusy):
            with OfflineOperatorControl(s,'tiktok',root/'lake',operator_id='operator'):pass
        browser.driver.get('https://fixture.invalid/video/123') # fake driver never requests network
        assert s.db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type='browser.command_admitted'").fetchone()[0]==1
        browser.observe_access({'challenge_present':True})
        assert s.get(a)['state']=='ACCESS_RESTRICTED'
        with pytest.raises(CommandFault):browser.driver.current_url
    assert browser.owner._proof['job_active_processes']==0
    assert s.db.execute('SELECT status FROM harness_source_sessions').fetchone()[0]=='CLOSED'
    assert Restrictions(s).latch('tiktok')['reason']=='CAPTCHA'
    with pytest.raises(RestrictionError):s.claim(b,'b')
    with OfflineOperatorControl(s,'tiktok',root/'lake',operator_id='operator') as op:
        latch=Restrictions(s).latch('tiktok');vid=op.verify_fixture()
        op.release(vid,expected_epoch=latch['epoch'],session_id=latch['session_id'])
    assert s.get(a)['state']=='ACCESS_RESTRICTED'


@pytest.mark.skipif(os.name!='nt',reason='Windows C2a containment')
def test_worker_crash_unknown_hold_survives_restart(env):
    _,s,now,_,_,b,_=env
    with c2c_session(env) as browser:
        with pytest.raises(CommandFault):browser.driver.execute_script('crash')
    with JobStoreContext(s.path,now) as reopened:
        assert Restrictions(reopened).latch('tiktok')['reason']=='UNKNOWN'
        with pytest.raises(RestrictionError):reopened.claim(b,'next')


@pytest.mark.skipif(os.name!='nt',reason='Windows C2a containment')
def test_latch_activated_during_command_yields_unknown_no_replay(env):
    root,s,_,_,_,_,token=env
    def activate():
        deadline=time.monotonic()+5
        while not (root/'inflight').exists():
            if time.monotonic()>deadline:return
            time.sleep(.01)
        other=JobStore(s.path)
        try:
            sid=other.db.execute('SELECT session_id FROM harness_source_sessions').fetchone()[0]
            Restrictions(other).activate(token,sid,'RATE_LIMITED',evidence_ref='fixture:inflight',retry_after=30)
        finally:other.close();(root/'release').touch()
    with c2c_session(env,fixture_config={'control':str(root)}) as browser:
        t=threading.Thread(target=activate);t.start()
        with pytest.raises(CommandFault):browser.driver.execute_script('wait')
        t.join(6);assert not t.is_alive()
        assert browser.commands==1
    assert Restrictions(s).latch('tiktok')['reason']=='RATE_LIMITED'


def test_direct_adapter_entrypoint_cannot_bypass_extension(env):
    from crawler_harness.tiktok_adapter import TikTokManagedAdapter
    from types import SimpleNamespace
    root,s,_,_,_,_,token=env
    adapter=TikTokManagedAdapter(s,root/'lake',root/'collection')
    with pytest.raises(StateError,match='guarded session'):
        adapter.execute(token,{},port=SimpleNamespace(harness_offline_fixture=True))


def test_storage_only_recovery_leaves_latch_unchanged(env):
    root,s,_,_,a,_,token=env;r,sid=bind(env)
    r.observe(token,sid,{'challenge_present':True},evidence_ref='fixture:captcha')
    before=r.latch('tiktok')
    assert JobScopedPublication(s,root/'lake').recover(a,token,
        verify_quiescent=lambda _:True,evidence_ref='fixture:storage')['status']=='HELD'
    assert r.latch('tiktok')==before


@pytest.mark.skipif(os.name!='nt',reason='Windows C2a containment')
def test_real_c1_sealed_replay_with_active_latch_no_recollection(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from crawler_harness.tiktok_adapter import TikTokManagedAdapter
    from crawl_experiment.storage import place_catalog
    p=SimpleNamespace(place_key='overture:fixture',name='Popeyes Khanh Hoi',address='172 Khanh Hoi')
    monkeypatch.setattr(place_catalog,'load_catalog',lambda _:SimpleNamespace(places=[p]))
    match={'place':{'place_id':p.place_key,'canonical_name':p.name,'address':p.address,
        'brand':'Popeyes','ward':'Khanh Hoi','search_category':'chicken'},
        'selected_video':{'video_id':'123','place_id':p.place_key,'decision':'MATCH',
            'caption':'Popeyes 172 Khanh Hoi','hashtags':[],
            'navigation_target':'https://www.tiktok.com/@fixture/video/123'}}
    path=tmp_path/'state.sqlite3';lake=tmp_path/'lake';CommentPersistence(path,lake).close()
    s=JobStore(path);Restrictions.install(s,offline_fixture=True)
    try:
        adapter=TikTokManagedAdapter(s,lake,tmp_path/'collection')
        job=adapter.prepare(match,limit=2);token=s.claim(job,'owner',lease_seconds=300)
        profile=DedicatedProfile.create(tmp_path/'profile',s.path)
        with ManagedBrowserSession(s,token,profile=profile,evidence=tmp_path/'browser') as browser:
            assert browser.run_collection(adapter,match,limit=2)['state']=='PERSISTING'
            Restrictions(s).activate(token,browser.owner.session_id,'UNKNOWN',evidence_ref='fixture:postseal')
        latch=Restrictions(s).latch('tiktok');count=browser.commands
        result=browser.recover_storage(lake)
        assert result['pending']==0 and result['parquet_rows']==1
        files={str(p):p.read_bytes() for p in lake.rglob('*.parquet')}
        for _ in range(2):assert browser.recover_storage(lake)['already_finalized']
        assert Restrictions(s).latch('tiktok')==latch and browser.commands==count
        assert {str(p):p.read_bytes() for p in lake.rglob('*.parquet')}==files
    finally:s.close()


@pytest.mark.skipif(os.name!='nt',reason='Windows C2a containment')
def test_failed_browser_latch_write_does_not_resolve_session(env):
    _,s,now,_,_,b,_=env
    with c2c_session(env) as browser:
        s.db.execute("CREATE TRIGGER fail_observation BEFORE INSERT ON harness_restrictions BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with pytest.raises(sqlite3.IntegrityError):browser.observe_access({'challenge_present':True})
        with pytest.raises(CommandFault):browser.driver.current_url
    assert s.db.execute('SELECT status FROM harness_source_sessions').fetchone()[0]=='OPEN'
    with JobStoreContext(s.path,now) as reopened:
        with pytest.raises(RestrictionError):reopened.claim(b,'new')


@pytest.mark.skipif(os.name!='nt',reason='Windows C2a containment')
def test_child_rechecks_latch_between_intent_and_ipc(env,monkeypatch):
    import crawler_harness.browser_commands as commands
    _,s,_,_,_,_,token=env
    original=commands.send
    with c2c_session(env) as browser:
        def send_after_restriction(conn,request):
            Restrictions(s).activate(token,browser.owner.session_id,'CAPTCHA',evidence_ref='fixture:preipc')
            return original(conn,request)
        monkeypatch.setattr(commands,'send',send_after_restriction)
        with pytest.raises(CommandFault):browser.driver.get('https://fixture.invalid/video/123')
    assert s.db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type='browser.command_admitted'").fetchone()[0]==0


def test_release_and_claim_serialized_across_connections(env):
    root,s,now,_,_,b,token=env;r,sid=bind(env)
    epoch=r.activate(token,sid,'CAPTCHA',evidence_ref='fixture:race')
    close_synthetic_session(s,sid)
    ready=threading.Event();go=threading.Event();released=threading.Event();errors=[]
    def operator():
        try:
            with JobStoreContext(s.path,now) as other:
                with OfflineOperatorControl(other,'tiktok',root/'lake',operator_id='operator') as op:
                    vid=op.verify_fixture();ready.set();assert go.wait(3)
                    op.release(vid,expected_epoch=epoch,session_id=sid);released.set()
        except BaseException as exc:errors.append(exc)
    t=threading.Thread(target=operator);t.start();assert ready.wait(3)
    with s._transaction():
        go.set();assert not released.wait(.1)
        with pytest.raises(RestrictionError):assert_dispatch(s,'tiktok')
    t.join(5);assert not t.is_alive() and not errors and released.is_set()
    assert s.claim(b,'new') is not None


def test_expired_verification_and_tampered_evidence_refuse_release(env):
    root,s,now,_,_,_,token=env;r,sid=bind(env)
    epoch=r.activate(token,sid,'CAPTCHA',evidence_ref='fixture:captcha');close_synthetic_session(s,sid)
    with OfflineOperatorControl(s,'tiktok',root/'lake',operator_id='operator') as op:
        vid=op.verify_fixture();now[0]+=61
        with pytest.raises(RestrictionError):op.release(vid,expected_epoch=epoch,session_id=sid)
        vid=op.verify_fixture()
        with s._transaction():s.db.execute("UPDATE harness_access_verifications SET evidence_json='{}' WHERE verification_id=?",(vid,))
        with pytest.raises(RestrictionError):op.release(vid,expected_epoch=epoch,session_id=sid)


def test_unresolved_session_denies_operator_control(env):
    root,s,_,_,_,_,token=env;r,sid=bind(env)
    r.activate(token,sid,'CAPTCHA',evidence_ref='fixture:captcha')
    with pytest.raises(RestrictionError):
        with OfflineOperatorControl(s,'tiktok',root/'lake',operator_id='operator'):pass


def test_source_install_refuses_active_claim_and_version_drift(tmp_path):
    path=tmp_path/'state.sqlite3';CommentPersistence(path,tmp_path/'lake').close();s=JobStore(path)
    job=s.create_job(source='tiktok',kind='collection',place_id='p',entity_id='v',input_digest='d',workflow='w')
    token=s.claim(job,'a')
    with pytest.raises(StateError,match='Quiescent'):Restrictions.install(s,offline_fixture=True)
    s.transition(token,'FAILED',code='fixture')
    Restrictions.install(s,offline_fixture=True)
    s.db.execute('PRAGMA ignore_check_constraints=ON')
    s.db.execute('UPDATE harness_restriction_schema SET version=2');s.close()
    with pytest.raises(StateError,match='version'):JobStore(path)


@pytest.mark.parametrize('phase',['before_commit','after_commit'])
def test_restriction_process_crash_commit_boundary(env,phase):
    root,s,_,_,_,b,token=env;r,sid=bind(env)
    script='''
import json,os,sys
from crawler_harness.state import JobStore,Claim
from crawler_harness.restrictions import Restrictions
s=JobStore(sys.argv[1]);token=Claim(**json.loads(sys.argv[2]))
with s._transaction(token):
    Restrictions.activate_in_transaction(s,token,'session-a','CAPTCHA',evidence_ref='fixture:crash')
    if sys.argv[3]=='before_commit':os._exit(82)
os._exit(83)
'''
    from dataclasses import asdict
    result=subprocess.run([sys.executable,'-B','-c',script,str(s.path),json.dumps(asdict(token)),phase],timeout=15)
    assert result.returncode==(82 if phase=='before_commit' else 83)
    for _ in range(2):
        reopened=JobStore(s.path)
        try:
            latch=Restrictions(reopened).latch('tiktok')
            count=reopened.db.execute('SELECT COUNT(*) FROM harness_restriction_history').fetchone()[0]
            if phase=='before_commit':assert latch is None and count==0
            else:assert latch['reason']=='CAPTCHA' and count==1
            with pytest.raises(RestrictionError):reopened.claim(b,'next')
        finally:reopened.close()


def test_direct_repository_hook_cannot_bypass_guard(env):
    from crawler_harness.tiktok_adapter import TikTokManagedAdapter
    root,s,_,_,_,_,token=env
    adapter=TikTokManagedAdapter(s,root/'lake',root/'collection')
    repo=adapter.repository(token)
    try:
        with pytest.raises(StateError,match='guarded session'):
            with adapter.guard(token,repo,{}):pass
        assert s.db.execute('SELECT COUNT(*) FROM tiktok_comment_events').fetchone()[0]==0
    finally:repo.close()


def test_operator_does_not_accept_caller_asserted_verification(env):
    root,s,_,_,_,_,token=env;r,sid=bind(env)
    r.activate(token,sid,'CAPTCHA',evidence_ref='fixture:captcha');close_synthetic_session(s,sid)
    with OfflineOperatorControl(s,'tiktok',root/'lake',operator_id='operator') as op:
        with pytest.raises(TypeError):op.verify_fixture({'verified':True})
        with pytest.raises(RestrictionError):op.release('missing',expected_epoch=1,session_id=sid)


def test_runtime_installation_disabled_by_default(tmp_path):
    path=tmp_path/'state.sqlite3';CommentPersistence(path,tmp_path/'lake').close();s=JobStore(path)
    try:
        with pytest.raises(StateError,match='Runtime migration disabled'):Restrictions.install(s)
        assert not installed(s)
    finally:s.close()


@pytest.mark.parametrize('kind',['raw_ui','raw_429'])
@pytest.mark.skipif(os.name!='nt',reason='Windows C2a containment')
def test_raw_ui_network_results_latch_before_next_command(env,kind):
    from experiments.tiktok_access_benchmark.session_comments import STATE_JS
    _,s,_,_,a,_,_=env
    if kind=='raw_ui':config={'access_state':{'challenge':True,'dialog_evidence':[],'video_detail':True}}
    else:
        event={'message':json.dumps({'message':{'method':'Network.responseReceived','params':{
            'type':'XHR','response':{'status':429,'url':'https://www.tiktok.com/api/comment/list/',
                'headers':{'Retry-After':'30'}}}}})}
        config={'performance_log':[event]}
    with c2c_session(env,fixture_config=config) as browser:
        if kind=='raw_ui':browser.driver.execute_script('return ('+STATE_JS+')();')
        else:browser.driver.get_log('performance')
        assert s.get(a)['state']=='ACCESS_RESTRICTED'
        assert Restrictions(s).latch('tiktok')['reason']==('CAPTCHA' if kind=='raw_ui' else 'RATE_LIMITED')
        with pytest.raises(CommandFault):browser.driver.current_url


@pytest.mark.skipif(os.name!='nt',reason='Windows C2a containment')
def test_existing_automatic_interstitial_not_misclassified_as_captcha(env):
    from experiments.tiktok_access_benchmark.session_comments import STATE_JS
    _,s,_,_,a,_,_=env
    config={'access_state':{'challenge':True,'challenge_markers':['._wafchallengeid'],
        'challenge_text_signals':['please wait'],'video_detail':False,'comments':False}}
    with c2c_session(env,fixture_config=config) as browser:
        browser.driver.execute_script('return ('+STATE_JS+')();')
        assert Restrictions(s).latch('tiktok') is None
        assert s.get(a)['state']=='RUNNING'


@pytest.mark.parametrize('context',['fresh','stale','navigated'])
@pytest.mark.skipif(os.name!='nt',reason='Windows C2a containment')
def test_optional_403_requires_fresh_rendered_context(env,context):
    from experiments.tiktok_access_benchmark.session_comments import STATE_JS
    _,s,_,_,_,_,_=env
    event={'message':json.dumps({'message':{'method':'Network.responseReceived','params':{
        'type':'XHR','response':{'status':403,'url':'https://www.tiktok.com/api/related/item_list/'}}}})}
    with c2c_session(env,fixture_config={'performance_log':[event]}) as browser:
        browser.driver.execute_script('return ('+STATE_JS+')();')
        if context=='stale':browser._access_observed_monotonic=0
        if context=='navigated':browser.driver.get('https://fixture.invalid/new')
        browser.driver.get_log('performance')
        latch=Restrictions(s).latch('tiktok')
        if context=='fresh':assert latch is None
        else:assert latch['reason']=='UNKNOWN'
