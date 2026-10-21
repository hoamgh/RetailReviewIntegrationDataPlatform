"""C2b: contained fake driver IPC, actual SQLite/profile locks, no websites."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import subprocess
import sys
from types import SimpleNamespace

import pytest
from crawler_harness.browser_commands import ManagedBrowserSession,CommandFault
from crawler_harness.browser_profile import DedicatedProfile,atomic_json
from crawler_harness.containment import ContainmentError
from crawler_harness.state import JobStore,OwnershipError,StateError
from crawler_harness.tiktok_adapter import TikTokManagedAdapter
from crawler_harness.publication import JobScopedPublication,PublisherBusy
from crawler_harness.containment import ManagedWorker
from crawler_harness.heartbeat import LeaseHeartbeat
from experiments.tiktok_comment_dom.persistence import CommentPersistence

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows contained named-pipe fixtures')
URL='https://www.tiktok.com/@fixture/video/123'


@pytest.fixture
def work(tmp_path,monkeypatch):
    from crawl_experiment.storage import place_catalog
    p=SimpleNamespace(place_key='overture:fixture',name='Popeyes Khanh Hoi',address='172 Khanh Hoi')
    monkeypatch.setattr(place_catalog,'load_catalog',lambda _:SimpleNamespace(places=[p]))
    match={'place':{'place_id':p.place_key,'canonical_name':p.name,'address':p.address,
        'brand':'Popeyes','ward':'Khanh Hoi','search_category':'chicken'},
        'selected_video':{'video_id':'123','place_id':p.place_key,'decision':'MATCH',
            'caption':'Popeyes 172 Khanh Hoi','hashtags':[],'navigation_target':URL}}
    repo=CommentPersistence(tmp_path/'comments.sqlite3',tmp_path/'lake');repo.close()
    now=[time.time()]
    store=JobStore(tmp_path/'comments.sqlite3',clock=lambda:now[0])
    adapter=TikTokManagedAdapter(store,tmp_path/'lake',tmp_path/'collection')
    job=adapter.prepare(match,limit=2)
    token=store.claim(job,'controller',lease_seconds=300)
    profile=DedicatedProfile.create(tmp_path/'dedicated-profile',store.path)
    yield tmp_path,store,now,adapter,token,profile,match
    store.close()


def session(work,**kw):
    root,store,_,_,token,profile,_=work
    return ManagedBrowserSession(store,token,profile=profile,evidence=root/'browser',**kw)


def test_valid_owner_dispatch_and_remote_elements(work):
    with session(work) as s:
        s.driver.get(URL)
        assert s.driver.current_url==URL
        element=s.driver.execute_script('nested')['items'][0]
        assert element.is_displayed() and element.text=='fixture text'
        element.find_element('xpath','..').click()
        s.driver.save_screenshot(str(s.evidence/'fixture.png'))
        assert s.driver.execute_script('data-not-element')=={'type':'element','value':'forged'}
        assert s.owner.api.active(s.owner.job)>=1
    evidence=[json.loads(p.read_text()) for p in (work[0]/'browser'/'commands').glob('*.json')]
    assert evidence and all(e['outcome']=='SUCCEEDED' for e in evidence)
    assert all('args' not in e and 'result' not in e for e in evidence)
    assert s.owner._proof['job_active_processes']==0


def test_existing_dom_runner_and_c1_storage_only_replay(work):
    root,store,_,adapter,token,_,match=work
    with session(work) as s:
        result=s.run_collection(adapter,match,limit=2)
        assert result['state']=='PERSISTING'
        assert store.db.execute('SELECT COUNT(*) FROM tiktok_comments').fetchone()[0]==1
        assert (adapter.evidence_root/token.job_id/token.attempt_id/'final.png').exists()
    command_count=s.commands
    result=s.recover_storage(root/'lake')
    assert result['pending']==0 and result['parquet_rows']==1
    paths={str(p):p.read_bytes() for p in (root/'lake').rglob('*.parquet')}
    for _ in range(2):assert s.recover_storage(root/'lake')['already_finalized']
    assert {str(p):p.read_bytes() for p in (root/'lake').rglob('*.parquet')}==paths
    assert s.commands==command_count


def wait_for(predicate,timeout=5):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>deadline:raise AssertionError('Bounded fixture wait expired')
        time.sleep(.01)


def test_expired_lease_refuses_new_commands(work):
    root,store,now,_,token,_,_=work
    with session(work) as s:
        now[0]=store.get(token.job_id)['lease_until']+1
        with pytest.raises(CommandFault):s.driver.get(URL)
        assert s.commands==0 and s.owner.api.active(s.owner.job)==0
    assert s.owner._proof['durable_resolution'] is False


def test_old_generation_after_fault_injected_takeover_cannot_dispatch(work):
    _,store,now,_,token,_,_=work
    with session(work) as s:
        now[0]=store.get(token.job_id)['lease_until']+1
        # This deliberately false attestation injects a fencing failure scenario;
        # it is NOT a valid production quiescence verifier.
        newer=store.takeover(token.job_id,'new-owner',expected_generation=1,
            verify_quiescent=lambda _:True,evidence_ref='FAULT_INJECTION_ONLY')
        assert newer.generation==2
        with pytest.raises(CommandFault):s.driver.get(URL)
        assert s.commands==0 and s.owner.api.active(s.owner.job)==0


@pytest.mark.parametrize('script',['crash','crash-with-child','change-session','change-profile','unapproved-object'])
def test_unknown_worker_outcome_halts_and_never_replays(work,script):
    _,store,_,_,token,_,_=work
    with session(work) as s:
        with pytest.raises(CommandFault):s.driver.execute_script(script)
        count=s.commands
        with pytest.raises(CommandFault):s.driver.execute_script(script)
        assert count==s.commands==1
        assert s.owner.api.active(s.owner.job)==0
        assert store.get(token.job_id)['state']=='PAUSED_NEEDS_HUMAN'
    records=[json.loads(p.read_text()) for p in (s.evidence/'commands').glob('*.json')]
    assert any(r['outcome']=='UNKNOWN' for r in records)


def test_authority_expiry_during_inflight_action_is_unknown_not_rollback(work):
    root,store,now,_,token,_,_=work
    with session(work,fixture_config={'control':str(root)}) as s:
        expired_at=store.get(token.job_id)['lease_until']+1
        def expire():
            wait_for(lambda:(root/'inflight').exists())
            now[0]=expired_at
            (root/'release').touch()
        # No main-thread sqlite connection is used from this fixture thread.
        t=threading.Thread(target=expire);t.start()
        with pytest.raises(CommandFault):s.driver.execute_script('wait')
        t.join(5)
        assert not t.is_alive() and (root/'inflight').exists()
        assert s.owner.api.active(s.owner.job)==0
    assert any(json.loads(p.read_text())['outcome']=='UNKNOWN' for p in (s.evidence/'commands').glob('*.json'))


def test_no_sqlite_write_lock_is_held_during_browser_wait(work):
    root,store,_,_,_,_,_=work
    errors=[]
    with session(work,fixture_config={'control':str(root)}) as s:
        def writer():
            try:
                wait_for(lambda:(root/'inflight').exists())
                db=sqlite3.connect(store.path,timeout=.5)
                db.execute('BEGIN IMMEDIATE');db.execute('UPDATE harness_jobs SET updated_at=updated_at');db.commit();db.close()
                (root/'release').touch()
            except Exception as exc:errors.append(type(exc).__name__)
        t=threading.Thread(target=writer);t.start()
        assert s.driver.execute_script('wait') is True
        t.join(5);assert errors==[] and not t.is_alive()


@pytest.mark.parametrize('surface',['command_executor','service','browser_process','execute','_execute','__dict__','quit','close','switch_to'])
def test_raw_driver_surface_attempts_fail_closed(work,surface):
    with session(work) as s:
        with pytest.raises(CommandFault):getattr(s.driver,surface)
        assert s.commands==0 and s.owner.api.active(s.owner.job)==0


@pytest.mark.parametrize('surface',['_parent','parent','_execute','__dict__','driver'])
def test_remote_element_never_exposes_driver_or_executor(work,surface):
    with session(work) as s:
        e=s.driver.execute_script('element')
        count=s.commands
        with pytest.raises(CommandFault):getattr(e,surface)
        assert s.commands==count and s.owner.api.active(s.owner.job)==0


def test_facade_attribute_assignment_cannot_replace_guard(work):
    with session(work) as s:
        with pytest.raises(CommandFault):s.driver._boundary='replacement'
        assert s.commands==0


def test_finally_screenshot_and_cdp_bypass_after_authority_loss_are_refused(work):
    root,store,now,_,token,_,_=work
    with session(work) as s:
        now[0]=store.get(token.job_id)['lease_until']+1
        with pytest.raises(CommandFault):s.driver.save_screenshot(str(s.evidence/'late.png'))
        with pytest.raises(CommandFault):s.driver.execute_cdp_cmd('Network.enable',{})
        assert not (s.evidence/'late.png').exists() and s.commands==0


def test_unapproved_cdp_and_outside_screenshot_refused(work):
    root=work[0]
    with session(work) as s:
        with pytest.raises(CommandFault):s.driver.execute_cdp_cmd('Browser.close',{})
        with pytest.raises(CommandFault):s.driver.save_screenshot(str(root/'outside.png'))
        assert s.commands==0 and not (root/'outside.png').exists()


def test_profile_is_exclusive_and_cannot_release_before_tree_quiescence(work):
    with session(work) as s:
        with pytest.raises(PublisherBusy):
            with DedicatedProfile(s.profile_path,s.owner):pass
        with pytest.raises(ContainmentError):s.profile.release_after_quiescence()
        code="from pathlib import Path; from crawler_harness.publication import resource_id,windows_mutex,PublisherBusy\ntry:\n with windows_mutex('Global\\\\IntegrationRecommendationProfileV1_'+resource_id(Path(__import__('sys').argv[1]),'profile')):raise AssertionError('unexpected owner')\nexcept PublisherBusy:print('BUSY')"
        child=subprocess.run([sys.executable,'-B','-c',code,str(s.profile_path)],capture_output=True,text=True,timeout=5)
        assert child.returncode==0 and child.stdout.strip()=='BUSY',child.stderr
    manifest=json.loads((s.profile_path/'harness-profile.json').read_text())
    assert manifest['active_session'] is None


def test_existing_user_profile_cannot_be_stamped_or_adopted(work):
    path=work[0]/'user-chrome';path.mkdir();(path/'Preferences').write_text('private fixture')
    with pytest.raises(FileExistsError):DedicatedProfile.create(path,work[1].path)
    assert not (path/'harness-profile.json').exists()


def test_profile_marker_mismatch_refuses_before_browser_action(work):
    with pytest.raises(ContainmentError):
        with session(work) as s:
            manifest=json.loads(s.profile.file.read_text());manifest['profile_id']='other'
            atomic_json(s.profile.file,manifest)
            with pytest.raises(CommandFault):s.driver.get(URL)
            assert s.commands==0 and s.owner.api.active(s.owner.job)==0


def test_wrong_session_or_attempt_port_cannot_run_collector(work):
    with session(work) as s:
        with pytest.raises(CommandFault):s.validate_port(SimpleNamespace(driver=s.driver,lifecycle=None),replace(s.token,generation=2))
        assert s.commands==0


def test_heartbeat_owns_connection_and_cannot_resurrect_expired_token(work):
    _,store,now,_,token,_,_=work
    hb=LeaseHeartbeat(store,token,interval=.03,lease_seconds=.3)
    try:
        hb.start();wait_for(lambda:hb.renewals>=1)
        assert hb.connection_thread!=threading.get_ident()
        now[0]=store.get(token.job_id)['lease_until']+1
        wait_for(hb.lost.is_set)
        assert store.get(token.job_id)['lease_until']<now[0]
    finally:hb.close()
    assert not hb.thread.is_alive()


def test_worker_failure_preserves_committed_intermediate_source_data(work):
    _,store,_,adapter,token,_,_=work
    from experiments.tiktok_comment_dom.extractor import CommentSession
    with session(work) as s:
        data=CommentSession('123',2)
        data.add({'url':URL,'cards':[{'text':'committed synthetic'}]},'2026-10-21T00:00:00+00:00')
        job=store.get(token.job_id)
        repo=adapter.repository(token,source_gate=s.source_gate)
        repo.persist(data,run_id=job['data_run_id'],place_id=job['place_id'],input_digest=job['input_digest'],
            next_page=1,provenance={'mode':'OFFLINE_FIXTURE'})
        repo.close()
        with pytest.raises(CommandFault):s.driver.execute_script('crash')
        assert store.db.execute('SELECT COUNT(*) FROM tiktok_comments').fetchone()[0]==1
        assert store.db.execute('SELECT COUNT(*) FROM tiktok_comment_events WHERE delivered=0').fetchone()[0]==1
        assert store.db.execute('SELECT COUNT(*) FROM harness_receipts').fetchone()[0]==0


def test_heartbeat_loss_interrupts_pending_wait_and_quiesces_owned_tree(work):
    root,_,now,_,_,_,_=work
    with session(work,fixture_config={'control':str(root)},heartbeat_interval=.03,lease_seconds=.3) as s:
        def expire():
            wait_for(lambda:(root/'inflight').exists());now[0]+=301
        t=threading.Thread(target=expire);t.start();started=time.monotonic()
        with pytest.raises(CommandFault):s.driver.execute_script('wait-descendant')
        t.join(5)
        assert time.monotonic()-started<3
        assert s.heartbeat.lost.is_set() and s.owner.api.active(s.owner.job)==0
        assert not (root/'release').exists()


def test_unknown_final_screenshot_keeps_sealed_receipt_for_storage_replay(work):
    root,store,_,adapter,token,_,match=work
    with session(work,fixture_config={'crash_screenshot':True}) as s:
        with pytest.raises(CommandFault):s.run_collection(adapter,match,limit=2)
        assert store.get(token.job_id)['state']=='PERSISTING'
        assert store.db.execute('SELECT COUNT(*) FROM harness_receipts').fetchone()[0]==1
        assert s.owner.api.active(s.owner.job)==0
    count=s.commands
    result=s.recover_storage(root/'lake')
    assert result['pending']==0 and result['sqlite_records']==result['parquet_rows']==1
    assert s.commands==count


def test_unresolved_browser_intent_blocks_new_session_without_native_launch(work):
    _,store,_,_,token,_,_=work
    with store._transaction(token):
        row=store._owned(token)
        store._event(row,'browser.command_intent',row['state'],row['state'],'interrupted-fixture-command')
    with pytest.raises(CommandFault,match='Unresolved'):
        with session(work):pass
    assert store.db.execute("SELECT COUNT(*) FROM harness_events WHERE event_type='worker.launch_intent'").fetchone()[0]==0


def test_storage_replay_refuses_before_worker_is_closed(work):
    with session(work) as s:
        with pytest.raises(CommandFault,match='closed'):s.recover_storage(work[0]/'lake')
        assert s.owner.api.active(s.owner.job)>0


@pytest.mark.parametrize('field',['session_id','attempt_id'])
def test_worker_rejects_wrong_ipc_identity_before_screenshot(work,monkeypatch,field):
    from crawler_harness import browser_commands
    original=browser_commands.send
    with session(work) as s:
        def corrupt(conn,request):
            request=dict(request);request[field]='wrong-fixture-identity'
            original(conn,request)
        monkeypatch.setattr(browser_commands,'send',corrupt)
        target=s.evidence/'never-written.png'
        with pytest.raises(CommandFault):s.driver.save_screenshot(str(target))
        assert not target.exists() and s.owner.api.active(s.owner.job)==0


def test_heartbeat_old_generation_cannot_renew_new_owner(work):
    _,store,now,_,token,_,_=work
    hb=LeaseHeartbeat(store,token,interval=.03,lease_seconds=.3)
    try:
        hb.start();wait_for(lambda:hb.renewals>=1)
        now[0]=store.get(token.job_id)['lease_until']+1
        newer=store.takeover(token.job_id,'new-fixture-owner',expected_generation=1,
            verify_quiescent=lambda _:True,evidence_ref='OFFLINE_NO_BROWSER_FIXTURE')
        deadline=store.get(token.job_id)['lease_until']
        wait_for(hb.lost.is_set)
        assert newer.generation==2 and store.get(token.job_id)['lease_until']==deadline
    finally:hb.close()


def test_storage_replay_cannot_bypass_another_source_owner(work):
    root,store,_,adapter,token,_,match=work
    with session(work) as s:s.run_collection(adapter,match,limit=2)
    other=store.create_job(source='tiktok',kind='collection',place_id='fixture-b',
        entity_id='456',input_digest='fixture-b')
    other_token=store.claim(other,'other-owner',lease_seconds=300)
    with ManagedWorker(store,other_token):
        with pytest.raises(PublisherBusy):s.recover_storage(root/'lake')
        assert store.db.execute('SELECT delivered FROM tiktok_comment_events').fetchone()[0]==0
    assert s.recover_storage(root/'lake')['pending']==0
