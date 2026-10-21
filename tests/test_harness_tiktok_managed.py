"""M3-B: actual SQLite transactions and subprocess faults, fake browser only."""
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from crawler_harness.state import JobStore, OwnershipError, StateError
from crawler_harness.tiktok_adapter import TikTokManagedAdapter
from experiments.tiktok_comment_dom.extractor import CommentSession
from experiments.tiktok_comment_dom.persistence import CommentPersistence, SourceRunConflict


URL='https://www.tiktok.com/@fixture/video/123'
ROOT=Path(__file__).resolve().parents[1]


def card(text='synthetic comment', stable=False):
    return {'text':text,'created_at_raw':'2d ago',
            **({'item_attributes':{'data-comment-id':'source-1'}} if stable else {})}


def session(stable=False):
    s=CommentSession('123',2)
    s.add({'url':URL,'cards':[card(stable=stable)]},'2026-10-21T00:00:00+00:00')
    return s


def fixture_store(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    repo=CommentPersistence(root/'comments.sqlite3',root/'lake');repo.close()
    now=[100]
    store=JobStore(root/'comments.sqlite3',clock=lambda:now[0])
    return store,now,TikTokManagedAdapter(store,root/'lake',root/'evidence')


@pytest.fixture
def managed(tmp_path,monkeypatch):
    from crawl_experiment.storage import place_catalog
    p=SimpleNamespace(place_key='overture:fixture',name='Popeyes Khanh Hoi',address='172 Khanh Hoi')
    monkeypatch.setattr(place_catalog,'load_catalog',lambda _:SimpleNamespace(places=[p]))
    match={'place':{'place_id':p.place_key,'canonical_name':p.name,'address':p.address,
                    'brand':'Popeyes','ward':'Khanh Hoi','search_category':'chicken'},
           'selected_video':{'video_id':'123','place_id':p.place_key,'decision':'MATCH',
                             'caption':'Popeyes 172 Khanh Hoi','hashtags':[],
                             'navigation_target':URL}}
    store,now,adapter=fixture_store(tmp_path)
    job=adapter.prepare(match,limit=2)
    token=store.claim(job,'worker-a',lease_seconds=10)
    yield store,now,adapter,job,token,match
    store.close()


def persist(adapter,token,s=None,final=None,**overrides):
    job=adapter.store.get(token.job_id)
    repo=adapter.repository(token)
    try:
        return repo.persist(s or session(),run_id=job['data_run_id'],place_id=job['place_id'],
            input_digest=job['input_digest'],next_page=1,provenance={'mode':'OFFLINE_FIXTURE'},
            **({'final_observation':{'stop_reason':final}} if final else {}),**overrides)
    finally:repo.close()


def source_counts(store):
    return {t:store.db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in
            ['tiktok_comments','tiktok_comment_events','tiktok_comment_checkpoints','harness_receipts']}


class Port:
    harness_offline_fixture=True
    def __init__(self,**extra):
        self.actions=[]
        self.driver=SimpleNamespace(current_url=URL,window_handles=['one'],save_screenshot=lambda _:None)
        self.state={'url':URL,'cards':[card()],'controls':[{}],'comment_panel_present':True,**extra}
    def navigate(self,url):self.actions.append('navigate')
    def wait_for(self,*args):return dict(self.state)
    def has_comments_tab(self):return False
    def scroll_comments(self):pytest.fail('No scroll requested')


def test_adapter_calls_existing_collector_seals_partial_and_defers_publication(managed):
    store,now,adapter,job,token,match=managed
    result=adapter.execute(token,match,port=Port(),limit=2)
    assert result['state']=='PERSISTING' and result['collection_complete'] is False
    assert result['data_run_id']==store.get(job)['data_run_id']
    assert token.attempt_id in result['evidence']
    assert set(source_counts(store).values())=={1}
    receipt=store._receipt(store.get(job))
    assert receipt['stop_reason']=='MAX_SCROLLS' and not receipt['publication_verified']
    assert store.db.execute('SELECT sum(delivered) FROM tiktok_comment_events').fetchone()[0]==0
    assert not list(adapter.lake.rglob('*.parquet'))
    repo=adapter.repository(token)
    with pytest.raises(RuntimeError,match='M3-C'):repo.flush()
    repo.close()
    with pytest.raises(StateError):store.transition(token,'COMPLETE')
    with pytest.raises(StateError,match='sealed'):
        adapter.execute(token,match,port=Port(),limit=2)


@pytest.mark.parametrize('reason,exhausted,expected',[
    ('MAX_SCROLLS',True,False),('COMMENT_LIMIT',True,False),
    ('EXPLICIT_END_MARKER',False,False),('EXPLICIT_END_MARKER',True,True),
])
def test_completion_requires_explicit_end_and_still_never_finalizes_without_publication(managed,reason,exhausted,expected):
    store,_,adapter,job,token,_=managed;s=session()
    s.pagination['exhausted']=exhausted
    persist(adapter,token,s,reason)
    receipt=store._receipt(store.get(job))
    assert bool(receipt['source_complete']) is expected
    assert store.get(job)['state']=='PERSISTING'
    with pytest.raises(StateError,match='publication'):store.transition(token,'COMPLETE')


@pytest.mark.parametrize('reason,target',[
    ('AUTH_GATE','ACCESS_RESTRICTED'),('CHALLENGE','ACCESS_RESTRICTED'),
    ('ACCESS_DENIED','ACCESS_RESTRICTED'),('RATE_LIMITED','ACCESS_RESTRICTED'),
    ('UNKNOWN_403','PAUSED_NEEDS_HUMAN'),('UNRECOGNIZED_LAYOUT','PAUSED_NEEDS_HUMAN'),
])
def test_restrictions_and_unknown_observations_never_become_success(managed,reason,target):
    store,_,adapter,job,token,_=managed
    persist(adapter,token,final=reason)
    assert store.get(job)['state']==target
    assert not store._receipt(store.get(job))['source_complete']
    assert store.db.execute('SELECT delivered FROM tiktok_comment_events').fetchone()[0]==0


@pytest.mark.parametrize('field,value',[('owner','other'),('generation',7),('attempt_id','wrong')])
def test_wrong_claim_is_rejected_on_source_connection(managed,field,value):
    store,_,adapter,_,token,_=managed
    with pytest.raises(OwnershipError):persist(adapter,replace(token,**{field:value}))
    assert set(source_counts(store).values())=={0}


def test_expired_source_claim_rolls_back_everything(managed):
    store,now,adapter,_,token,_=managed;now[0]=111
    with pytest.raises(OwnershipError):persist(adapter,token,final='MAX_SCROLLS')
    assert set(source_counts(store).values())=={0}


def test_expiry_inside_final_seal_rolls_back_source_receipt_state_and_history(managed):
    store,now,adapter,job,token,_=managed
    before=store.db.execute('SELECT count(*) FROM harness_events').fetchone()[0]
    adapter.fault=lambda phase:now.__setitem__(0,111) if phase=='before_commit' else None
    with pytest.raises(OwnershipError):persist(adapter,token,final='MAX_SCROLLS')
    assert set(source_counts(store).values())=={0}
    assert store.get(job)['state']=='RUNNING'
    assert store.db.execute('SELECT count(*) FROM harness_events').fetchone()[0]==before


def test_stale_worker_after_takeover_cannot_commit_even_with_old_clock(managed):
    store,now,adapter,job,token,_=managed;now[0]=111
    store.takeover(job,'worker-b',expected_generation=1,verify_quiescent=lambda _:True,
                   evidence_ref='MOCK_NO_SOURCE_PROCESS')
    now[0]=100  # Rejection must come from generation, not just expiry.
    with pytest.raises(OwnershipError):persist(adapter,token,final='MAX_SCROLLS')
    assert set(source_counts(store).values())=={0}


@pytest.mark.parametrize('changed',[False,True])
def test_cross_run_source_id_conflict_is_preflighted_without_dml(managed,changed):
    store,_,adapter,job,token,match=managed
    persist(adapter,token,session(True),final='MAX_SCROLLS')
    before=[tuple(r) for r in store.db.execute('SELECT * FROM harness_receipts')]
    second=adapter.prepare(match,workflow='refresh-2',limit=2)
    other=store.claim(second,'worker-b',lease_seconds=10)
    s=session(True)
    if changed:s.records['source-1']['text']='changed synthetic content'
    # New fallback record appears first; the entire batch must be preflighted.
    extra=session();s.records={**extra.records,**s.records}
    repo=adapter.repository(other);sql=[];repo.db.set_trace_callback(sql.append)
    with pytest.raises(SourceRunConflict):
        repo.persist(s,run_id=store.get(second)['data_run_id'],place_id=store.get(second)['place_id'],
                     input_digest=store.get(second)['input_digest'],next_page=1,provenance={})
    repo.close()
    assert not any(q.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in sql)
    assert set(source_counts(store).values())=={1}
    assert [tuple(r) for r in store.db.execute('SELECT * FROM harness_receipts')]==before


def test_same_job_retry_keeps_data_run_checkpoint_and_event_identity(managed):
    store,now,adapter,job,token,match=managed
    persist(adapter,token)
    old_run=store.get(job)['data_run_id']
    old_events=[r[0] for r in store.db.execute('SELECT event_id FROM tiktok_comment_events')]
    now[0]=111
    new=store.takeover(job,'worker-b',expected_generation=1,verify_quiescent=lambda _:True,
                       evidence_ref='MOCK_FIXTURE_PORT_QUIESCENT')
    assert new.attempt_id!=token.attempt_id
    result=adapter.execute(new,match,port=Port(),resume=True,limit=2)
    assert result['data_run_id']==old_run
    assert [r[0] for r in store.db.execute('SELECT event_id FROM tiktok_comment_events')]==old_events
    assert store._receipt(store.get(job))['attempt_id']==new.attempt_id


def test_adapter_conflict_holds_new_job_and_preserves_old_receipt(managed):
    store,_,adapter,job,token,match=managed
    persist(adapter,token,session(True),final='MAX_SCROLLS')
    before=store._receipt(store.get(job))
    other=adapter.prepare(match,workflow='refresh-2',limit=2)
    newer=store.claim(other,'worker-b',lease_seconds=10)
    port=Port();port.state['cards']=[card(stable=True)]
    with pytest.raises(SourceRunConflict):adapter.execute(newer,match,port=port,limit=2)
    assert store.get(other)['state']=='PAUSED_NEEDS_HUMAN'
    assert store.get(other)['outcome_code']=='CROSS_RUN_SOURCE_ID_CONFLICT'
    assert store._receipt(store.get(job))==before
    assert source_counts(store)['tiktok_comments']==1


def test_same_database_required_and_failed_nested_hook_cannot_commit(managed,tmp_path):
    store,_,adapter,_,token,_=managed
    other=sqlite3.connect(tmp_path/'other.sqlite3');other.row_factory=sqlite3.Row
    with pytest.raises(StateError,match='same SQLite'):
        with store.source_transaction(other,token):pytest.fail('Must reject')
    other.close()
    original=adapter.observe
    def swallowed(token,repo,ctx,bound):
        try:bound.transition(token,'COMPLETE')
        except StateError:pass
    adapter.observe=swallowed
    with pytest.raises(StateError,match='failed nested'):persist(adapter,token,final='MAX_SCROLLS')
    assert set(source_counts(store).values())=={0}
    adapter.observe=original


def test_pinned_input_and_nonfixture_port_rejected_before_ui_or_new_job(managed):
    store,_,adapter,_,token,match=managed
    p=Port()
    with pytest.raises(StateError,match='changed'):adapter.execute(token,match,port=p,limit=3)
    assert not p.actions
    assert store.db.execute('SELECT count(*) FROM harness_jobs').fetchone()[0]==1
    p.harness_offline_fixture=False
    with pytest.raises(StateError,match='fixture'):adapter.execute(token,match,port=p,limit=2)


@pytest.mark.parametrize('field,value',[
    ('run_id','other-run'),('place_id','other-place'),
    ('input_digest','other-input'),('next_page',-1),
])
def test_scope_and_checkpoint_contract_checked_inside_source_transaction(managed,field,value):
    store,_,adapter,job,token,_=managed;row=store.get(job)
    args=dict(run_id=row['data_run_id'],place_id=row['place_id'],input_digest=row['input_digest'],
              next_page=1,provenance={});args[field]=value
    repo=adapter.repository(token)
    with pytest.raises(StateError):repo.persist(session(),**args)
    repo.close()
    assert set(source_counts(store).values())=={0}


def test_explicit_end_through_runner_has_m2_readable_receipt_but_no_job_complete(managed):
    from crawler_harness.recovery import verify
    store,_,adapter,job,token,match=managed
    result=adapter.execute(token,match,port=Port(end_of_comments_observed=True),limit=2)
    assert result['collection_complete'] is True and result['state']=='PERSISTING'
    row=store.get(job);receipt=store._receipt(row)
    facts=verify(store,adapter.lake,row,receipt,allow_unpublished=True)
    assert facts['pending']==1 and facts['parquet_rows']==0 and facts['event_count']==1
    cp=json.loads(store.db.execute('SELECT state_json FROM tiktok_comment_checkpoints').fetchone()[0])
    assert len(CommentSession.from_checkpoint(cp).records)==1
    reopened=JobStore(store.path,clock=lambda:100)
    assert reopened._receipt(reopened.get(job))==receipt
    reopened.close()


def test_unknown_exception_holds_without_inventing_final_receipt(managed):
    store,_,adapter,job,token,match=managed
    class Broken(Port):
        def navigate(self,url):raise ValueError('Synthetic mismatch')
    with pytest.raises(ValueError):adapter.execute(token,match,port=Broken(),limit=2)
    assert store.get(job)['state']=='PAUSED_NEEDS_HUMAN'
    assert store.get(job)['outcome_code']=='COLLECTION_EXCEPTION'
    assert source_counts(store)['harness_receipts']==0


def test_lost_authority_inside_runner_never_writes_unknown_hold(managed):
    store,now,adapter,job,token,match=managed
    class Expiring(Port):
        def navigate(self,url):now[0]=111
    with pytest.raises(OwnershipError):adapter.execute(token,match,port=Expiring(),limit=2)
    assert store.get(job)['state']=='RUNNING'
    assert set(source_counts(store).values())=={0}


@pytest.mark.parametrize('terminal,target',[
    ('CHALLENGE','ACCESS_RESTRICTED'),('RATE_LIMITED','ACCESS_RESTRICTED'),
    ('COMPLETE','PAUSED_NEEDS_HUMAN'),
])
def test_unsealed_terminal_checkpoint_never_resumes_ui_or_invents_receipt(managed,terminal,target):
    store,_,adapter,job,token,match=managed;s=session()
    if terminal=='COMPLETE':s.pagination['exhausted']=True
    else:s.pagination['stop_reason']=terminal
    persist(adapter,token,s)
    assert store.get(job)['state']=='RUNNING'
    assert source_counts(store)['harness_receipts']==0
    port=Port()
    with pytest.raises(StateError,match='operator review'):
        adapter.execute(token,match,port=port,resume=True,limit=2)
    assert not port.actions and store.get(job)['state']==target
    assert source_counts(store)['harness_receipts']==0


def process_worker(root,phase):
    """Real process exit at atomic boundary; no fake browser invocation needed."""
    import os
    store,now,adapter=fixture_store(root)
    job=store.create_job(source='tiktok',kind='collection',place_id='fixture-place',
                         entity_id='123',input_digest='fixture-input')
    token=store.claim(job,'process-a',lease_seconds=10)
    def fault(stage):
        if phase=='race' and stage=='before_commit':
            print('SOURCE_LOCK_HELD',flush=True)
            assert sys.stdin.readline().strip()=='expire'
            now[0]=111
        elif stage==phase:os._exit(81)
    adapter.fault=fault
    try:persist(adapter,token,final='MAX_SCROLLS')
    except OwnershipError:
        print('SOURCE_ROLLED_BACK',flush=True)
    finally:store.close()


def child_command(root,phase):
    script='import runpy,sys;runpy.run_path(sys.argv[1])["process_worker"](sys.argv[2],sys.argv[3])'
    return [sys.executable,'-B','-c',script,str(Path(__file__).resolve()),str(root),phase]


@pytest.mark.parametrize('phase',['before_commit','after_commit'])
def test_process_crash_atomic_source_checkpoint_receipt_and_history(tmp_path,phase):
    result=subprocess.run(child_command(tmp_path,phase),cwd=ROOT,capture_output=True,text=True,timeout=25)
    assert result.returncode==81,result.stderr
    store=JobStore(tmp_path/'comments.sqlite3',clock=lambda:100)
    expected=0 if phase=='before_commit' else 1
    assert set(source_counts(store).values())=={expected}
    job=dict(store.db.execute('SELECT * FROM harness_jobs').fetchone())
    assert job['state']==('RUNNING' if not expected else 'PERSISTING')
    assert store.db.execute('SELECT count(*) FROM harness_attempts').fetchone()[0]==1
    assert store.db.execute('SELECT count(*) FROM harness_events').fetchone()[0]==(2 if not expected else 6)
    if expected:
        receipt=store._receipt(job)
        ids=[r[0] for r in store.db.execute('SELECT event_id FROM tiktok_comment_events')]
        assert json.loads(receipt['event_ids_json'])==ids
        cp=json.loads(store.db.execute('SELECT state_json FROM tiktok_comment_checkpoints').fetchone()[0])
        assert cp['managed_collection_observation']['attempt_id']==receipt['attempt_id']
        assert cp['managed_collection_observation']['source_complete'] is False
    store.close()


def test_process_source_commit_lock_contends_with_takeover(tmp_path):
    contender,_,_=fixture_store(tmp_path)
    job_id=contender.create_job(source='tiktok',kind='collection',place_id='fixture-place',
                                entity_id='123',input_digest='fixture-input')
    child=subprocess.Popen(child_command(tmp_path,'race'),cwd=ROOT,stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='SOURCE_LOCK_HELD'
        contender.clock=lambda:111  # Takeover sees an expired lease, not a live lease.
        contender.db.execute('PRAGMA busy_timeout=0')
        with pytest.raises(sqlite3.OperationalError,match='locked'):
            contender.takeover(job_id,'process-b',expected_generation=1,
                verify_quiescent=lambda _:False,evidence_ref='NO_QUIESCENCE_YET')
        tail,err=child.communicate('expire\n',timeout=20)
        assert child.returncode==0,err
        assert 'SOURCE_ROLLED_BACK' in tail
        contender.clock=lambda:111
        new=contender.takeover(job_id,'process-b',expected_generation=1,
            verify_quiescent=lambda _:child.poll() is not None,
            evidence_ref='PARENT_CONFIRMED_CHILD_EXIT_NO_PUBLISHER')
        assert new.generation==2 and set(source_counts(contender).values())=={0}
    finally:
        if child.poll() is None:child.kill();child.communicate(timeout=10)
        contender.close()
