"""M3-C1 isolated SQLite/Parquet, real Windows mutex/process faults, no network."""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from crawler_harness.state import JobStore, Claim, OwnershipError
from crawler_harness.tiktok_adapter import TikTokManagedAdapter
from crawler_harness.publication import JobScopedPublication, PublisherBusy, publisher_ownership
from experiments.tiktok_comment_dom.persistence import CommentPersistence
from experiments.tiktok_comment_dom.extractor import CommentSession


pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows named-mutex publication contract')
ROOT=Path(__file__).resolve().parents[1]


def fixture_store(root,now=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    repo=CommentPersistence(root/'comments.sqlite3',root/'lake');repo.close()
    clock=now if now is not None else [100]
    store=JobStore(root/'comments.sqlite3',clock=lambda:clock[0])
    adapter=TikTokManagedAdapter(store,root/'lake',root/'evidence')
    return store,clock,adapter


def seed(store,adapter,*,name='a',place='place-a',video='123',complete=False,count=2):
    job=store.create_job(source='tiktok',kind='collection',place_id=place,
        entity_id=video,input_digest='fixture-input',workflow='epoch-'+name)
    token=store.claim(job,'worker-'+name,lease_seconds=10)
    s=CommentSession(video,50)
    s.add({'url':f'https://www.tiktok.com/@fixture/video/{video}',
           'cards':[{'text':'synthetic comment '+str(i),'created_at_raw':'2d ago'} for i in range(count)],
           'end_of_comments_observed':complete},'2026-10-21T00:00:00+00:00')
    repo=adapter.repository(token)
    repo.persist(s,run_id=store.get(job)['data_run_id'],place_id=place,input_digest='fixture-input',
        next_page=1,provenance={'mode':'OFFLINE_FIXTURE'},
        final_observation={'stop_reason':'EXPLICIT_END_MARKER' if complete else 'MAX_SCROLLS'})
    repo.close()
    return job,token


@pytest.fixture
def work(tmp_path):
    store,clock,adapter=fixture_store(tmp_path)
    job,token=seed(store,adapter)
    engine=JobScopedPublication(store,adapter.lake)
    yield tmp_path,store,clock,adapter,job,token,engine
    store.close()


def recover(engine,job,token=None):
    return engine.recover(job,token,verify_quiescent=lambda _:True,evidence_ref='OFFLINE_FIXTURE_NO_SOURCE_WORKER')


def event_rows(store,job):
    ids=json.loads(store._receipt(store.get(job))['event_ids_json'])
    return [dict(store.db.execute('SELECT * FROM tiktok_comment_events WHERE event_id=?',(eid,)).fetchone()) for eid in ids]


def files(lake):
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(lake.rglob('*.parquet'))}


@pytest.mark.parametrize('place,video', [('place-b','456'),('place-b','123'),('place-a','123')])
def test_exact_job_scope_does_not_publish_ack_other_pending_events(work,place,video):
    root,store,_,adapter,job,token,engine=work
    other,_=seed(store,adapter,name='b',place=place,video=video)
    before=event_rows(store,other)
    result=recover(engine,job,token)
    assert result['status']=='PARTIAL' and result['pending']==0
    assert result['parquet_rows']==2 and len(files(adapter.lake))==2
    assert event_rows(store,other)==before
    assert store.get(other)['state']=='PERSISTING'
    assert all(e['delivered']==1 for e in event_rows(store,job))
    attempt_count=store.db.execute('SELECT attempts_used FROM harness_recovery_policy WHERE job_id=?',(job,)).fetchone()[0]
    original=files(adapter.lake)
    for _ in range(2):assert recover(engine,job)['already_finalized'] is True
    assert files(adapter.lake)==original
    assert store.db.execute('SELECT attempts_used FROM harness_recovery_policy WHERE job_id=?',(job,)).fetchone()[0]==attempt_count


def test_malformed_other_payload_is_isolated_by_atomic_scope_metadata(work):
    _,store,_,adapter,job,token,engine=work
    other,ot=seed(store,adapter,name='b',place='place-b',video='123')
    eid=event_rows(store,other)[0]['event_id']
    with store._transaction():store.db.execute('UPDATE tiktok_comment_events SET payload=? WHERE event_id=?',('{broken',eid))
    assert recover(engine,job,token)['status']=='PARTIAL'
    assert not any(e['delivered'] for e in event_rows(store,other))
    result=recover(engine,other,ot)
    assert result['status']=='MALFORMED_OUTBOX'
    assert store.get(other)['state']=='PAUSED_NEEDS_HUMAN'
    assert len(files(adapter.lake))==2


def test_unindexed_legacy_event_is_not_silently_assigned_or_skipped(work):
    _,store,_,adapter,job,token,engine=work
    repo=CommentPersistence(store.path,adapter.lake)
    s=CommentSession('456');s.add({'url':'https://www.tiktok.com/@fixture/video/456',
                                 'cards':[{'text':'legacy'}]},'2026-10-21T00:00:00+00:00')
    repo.persist(s,run_id='legacy-run',place_id='legacy',input_digest='legacy',next_page=1,provenance={})
    repo.close()
    assert recover(engine,job,token)['status']=='UNINDEXED_OUTBOX_EVENT'
    assert not files(adapter.lake)
    assert all(not e['delivered'] for e in event_rows(store,job))


@pytest.mark.parametrize('fault',['payload','schema','truncated','extra','missing'])
def test_bad_artifacts_fail_closed_without_overwrite_or_ack(work,fault):
    _,store,_,adapter,job,token,engine=work
    event=event_rows(store,job)[0];row=json.loads(event['payload'])
    target=adapter.lake/f'crawl_date={row["observed_at"][:10]}'/f'run_id={row["run_id"]}'/f'event-{event["event_id"]}.parquet'
    target.parent.mkdir(parents=True,exist_ok=True)
    if fault=='missing':
        with store._transaction():store.db.execute('UPDATE tiktok_comment_events SET delivered=1 WHERE event_id=?',(event['event_id'],))
    elif fault=='truncated':target.write_bytes(b'broken')
    else:
        schema=CommentPersistence.schema()
        if fault=='payload':row['text']='corrupted payload'
        if fault=='schema':schema=schema.remove_metadata()
        if fault=='extra':target=target.with_name('extra-copy.parquet')
        pq.write_table(pa.Table.from_pylist([row],schema=schema),target)
    before=files(adapter.lake);before_rows=event_rows(store,job)
    result=recover(engine,job,token)
    assert result['status'] not in {'COMPLETE','PARTIAL'}
    assert store.get(job)['state']=='PAUSED_NEEDS_HUMAN'
    assert files(adapter.lake)==before and event_rows(store,job)==before_rows
    assert store.db.execute('SELECT status FROM harness_recovery_attempts').fetchone()[0]=='FAILED'


def test_seal_payload_corruption_detected_before_first_publication(work):
    _,store,_,adapter,job,token,engine=work
    event=event_rows(store,job)[0];row=json.loads(event['payload']);row['text']='tampered'
    with store._transaction():store.db.execute('UPDATE tiktok_comment_events SET payload=? WHERE event_id=?',(json.dumps(row),event['event_id']))
    assert recover(engine,job,token)['status']=='SEALED_PAYLOAD_MISMATCH'
    assert not files(adapter.lake)


@pytest.mark.parametrize('stage',['before_publish','after_rename','before_ack','before_finalize'])
def test_lease_expiry_during_publication_never_commits_unauthorized_ack_or_finalize(work,stage):
    _,store,clock,adapter,job,token,engine=work
    engine.fault=lambda current:clock.__setitem__(0,111) if current==stage else None
    with pytest.raises(OwnershipError):recover(engine,job,token)
    assert store.get(job)['state']=='PERSISTING'
    delivered=sum(e['delivered'] for e in event_rows(store,job))
    assert delivered==(2 if stage=='before_finalize' else 0)
    assert not store._receipt(store.get(job))['publication_verified']
    if stage=='before_publish':assert not files(adapter.lake)
    assert store.db.execute('SELECT status FROM harness_recovery_attempts').fetchone()[0]=='STARTED'


def test_old_token_and_false_quiescence_do_not_publish(work):
    _,store,clock,adapter,job,token,engine=work
    assert engine.recover(job,token,verify_quiescent=lambda _:False,evidence_ref='NO_PROOF')['status']=='QUIESCENCE_REQUIRED'
    clock[0]=111
    new=store.takeover(job,'new-owner',expected_generation=1,verify_quiescent=lambda _:True,
                       evidence_ref='MOCK_NO_EXTERNAL_PROCESSES')
    with pytest.raises(OwnershipError):recover(engine,job,token)
    assert not files(adapter.lake)
    assert recover(engine,job,new)['status']=='PARTIAL'


def test_terminal_integrity_audit_detects_corruption_without_republication(work):
    _,store,_,adapter,job,token,engine=work
    assert recover(engine,job,token)['status']=='PARTIAL'
    target=next(adapter.lake.rglob('*.parquet'));target.write_bytes(b'broken')
    before=files(adapter.lake)
    assert recover(engine,job)['status']=='INTEGRITY_ERROR'
    assert files(adapter.lake)==before


def test_raw_lake_identity_cannot_be_redirected_to_analysis(work):
    root,store,_,adapter,job,token,_=work
    engine=JobScopedPublication(store,root/'analysis_lake')
    assert recover(engine,job,token)['status']=='SEALED_LAKE_MISMATCH'
    assert not files(root/'analysis_lake') and not files(adapter.lake)


def crash_worker(root,phase):
    root=Path(root)
    meta=json.loads((root/'meta.json').read_text())
    store=JobStore(root/'comments.sqlite3',clock=lambda:100)
    def fault(stage):
        if phase=='hold' and stage=='before_publish':
            print('ACTUAL_PUBLISHER_HELD',flush=True)
            assert sys.stdin.readline().strip()=='release'
            return
        if stage==phase:os._exit(81)
    engine=JobScopedPublication(store,root/'lake',fault=fault)
    recover(engine,meta['job'],Claim(**meta['token']))


def child_command(root,worker,arg):
    code='import runpy,sys;runpy.run_path(sys.argv[1])[sys.argv[2]](sys.argv[3],sys.argv[4])'
    return [sys.executable,'-B','-c',code,str(Path(__file__).resolve()),worker,str(root),arg]


@pytest.mark.parametrize('complete',[False,True])
@pytest.mark.parametrize('phase',['before_publish','after_rename','after_ack','before_finalize'])
def test_process_crash_restart_and_two_idempotent_recoveries(tmp_path,phase,complete):
    store,clock,adapter=fixture_store(tmp_path)
    job,token=seed(store,adapter,complete=complete)
    original=store._receipt(store.get(job))
    (tmp_path/'meta.json').write_text(json.dumps({'job':job,'token':asdict(token)}))
    store.close()
    child=subprocess.run(child_command(tmp_path,'crash_worker',phase),cwd=ROOT,capture_output=True,text=True,timeout=25)
    assert child.returncode==81,child.stderr
    store=JobStore(tmp_path/'comments.sqlite3',clock=lambda:111)
    before=event_rows(store,job);before_files=files(tmp_path/'lake')
    if phase=='before_publish':assert not before_files and not any(e['delivered'] for e in before)
    if phase=='after_rename':assert len(before_files)==1 and not any(e['delivered'] for e in before)
    if phase=='before_finalize':assert len(before_files)==2 and all(e['delivered'] for e in before)
    token=store.takeover(job,'process-b',expected_generation=1,
        verify_quiescent=lambda _:child.returncode==81,evidence_ref='PARENT_CONFIRMED_EXIT_NO_SOURCE_BROWSER')
    engine=JobScopedPublication(store,tmp_path/'lake')
    result=recover(engine,job,token)
    assert result['status']==('COMPLETE' if complete else 'PARTIAL')
    assert result['pending']==0 and result['parquet_rows']==2 and result['duplicate_events']==0
    after=store._receipt(store.get(job))
    for field in ['generation','attempt_id','created_at','event_ids_json','source_complete']:
        assert original[field]==after[field]
    final_files=files(tmp_path/'lake')
    assert len(final_files)==2
    for path,h in before_files.items():assert final_files[path]==h
    assert store.db.execute('SELECT count(*) FROM tiktok_comment_events').fetchone()[0]==2
    for _ in range(2):assert recover(engine,job)['already_finalized'] is True
    assert files(tmp_path/'lake')==final_files
    assert store.db.execute('SELECT attempts_used FROM harness_recovery_policy').fetchone()[0]==2
    assert [r[0] for r in store.db.execute('SELECT status FROM harness_recovery_attempts ORDER BY ordinal')]==['INTERRUPTED','SUCCEEDED']
    (tmp_path/'crash_evidence.json').write_text(json.dumps({
        'phase':phase,'source_complete':complete,'child_exit_code':child.returncode,
        'before_replay':{'published_files':len(before_files),'acknowledged_events':sum(e['delivered'] for e in before)},
        'after_replay':result,'source_receipt_origin_preserved':all(original[k]==after[k] for k in
            ['generation','attempt_id','created_at','event_ids_json','source_complete']),
        'files_preserved':all(final_files[p]==h for p,h in before_files.items()),
        'two_repeated_audits_unchanged':files(tmp_path/'lake')==final_files,
        'attempts_used':2},indent=2),encoding='utf-8')
    store.close()


def lock_worker(root,unused):
    root=Path(root)
    with publisher_ownership(root/'comments.sqlite3',root/'lake'):
        print('PUBLISHER_LOCK_HELD',flush=True)
        assert sys.stdin.readline().strip()=='release'


def test_windows_process_mutex_excludes_publishers_and_releases_after_exit(work):
    root,store,_,adapter,job,token,engine=work
    child=subprocess.Popen(child_command(root,'lock_worker','unused'),cwd=ROOT,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='PUBLISHER_LOCK_HELD'
        assert recover(engine,job,token)['status']=='PUBLISHER_BUSY'
        assert store.db.execute('SELECT count(*) FROM harness_recovery_attempts').fetchone()[0]==0
        assert not files(adapter.lake)
        child.communicate('release\n',timeout=15)
        assert child.returncode==0
        assert recover(engine,job,token)['status']=='PARTIAL'
    finally:
        if child.poll() is None:child.kill();child.communicate(timeout=10)


def test_same_thread_nested_publisher_is_not_allowed_by_reentrant_win_mutex(work):
    _,store,_,adapter,job,token,engine=work
    with publisher_ownership(store.path,adapter.lake):
        assert recover(engine,job,token)['status']=='PUBLISHER_BUSY'
        with pytest.raises(PublisherBusy):
            with publisher_ownership(store.path,adapter.lake):pytest.fail('Nested ownership')


def test_two_actual_publishers_contend_without_reserving_other_job_budget(work):
    root,store,_,adapter,job,token,engine=work
    other,ot=seed(store,adapter,name='b',place='place-b',video='123')
    (root/'meta.json').write_text(json.dumps({'job':job,'token':asdict(token)}))
    child=subprocess.Popen(child_command(root,'crash_worker','hold'),cwd=ROOT,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='ACTUAL_PUBLISHER_HELD'
        assert recover(engine,other,ot)['status']=='PUBLISHER_BUSY'
        assert store.db.execute('SELECT count(*) FROM harness_recovery_policy WHERE job_id=?',(other,)).fetchone()[0]==0
        # Child pauses once per event; acknowledge both without polling.
        child.stdin.write('release\n');child.stdin.flush()
        assert child.stdout.readline().strip()=='ACTUAL_PUBLISHER_HELD'
        child.communicate('release\n',timeout=20)
        assert child.returncode==0
        assert store.get(job)['state']=='PARTIAL'
        assert recover(engine,other,ot)['status']=='PARTIAL'
        assert len(files(adapter.lake))==4
    finally:
        if child.poll() is None:child.kill();child.communicate(timeout=10)


def test_guard_rejects_event_id_of_other_job_before_payload_or_ack(work):
    _,store,_,adapter,job,token,engine=work
    other,_=seed(store,adapter,name='b',place='place-b',video='123')
    eid=event_rows(store,other)[0]['event_id']
    repo=CommentPersistence(store.path,adapter.lake)
    from crawler_harness.recovery import IntegrityError
    with publisher_ownership(store.path,adapter.lake) as unchanged:
        with pytest.raises(IntegrityError):
            repo.flush(event_ids=[eid],publication_guard=lambda r,i:engine._guard(token,r,i,unchanged))
    repo.close()
    assert not files(adapter.lake)
    assert not any(e['delivered'] for e in event_rows(store,other))


@pytest.mark.parametrize('fault',['receipt','seal','mapping','extra_event'])
def test_trusted_metadata_mismatch_is_not_repaired_or_ignored(work,fault):
    _,store,_,adapter,job,token,engine=work
    if fault=='receipt':
        with store._transaction():store.db.execute("UPDATE harness_receipts SET event_ids_json='[]' WHERE job_id=?",(job,))
    elif fault=='seal':
        with store._transaction():store.db.execute("UPDATE harness_events SET code='bad' WHERE job_id=? AND event_type='job.publication_sealed'",(job,))
    elif fault=='mapping':
        with store._transaction():store.db.execute("UPDATE harness_events SET place_id='wrong' WHERE job_id=? AND event_type='source.event_committed'",(job,))
    else:
        # Simulate a rogue post-seal source insert, with its own trusted-looking
        # route: exact receipt membership still detects the extra event.
        from experiments.tiktok_comment_dom.persistence import digest
        payload=json.loads(event_rows(store,job)[0]['payload'])
        payload['storage_key']=digest(['extra-key'])
        eid=digest([payload['storage_key'],payload['revision']]);payload['event_id']=eid
        with store._transaction():
            store.db.execute("INSERT INTO tiktok_comment_events VALUES(?,?,0)",(eid,json.dumps(payload)))
            store._event(store.get(job),'source.event_committed','PERSISTING','PERSISTING',eid)
    result=recover(engine,job,token)
    assert result['status'] not in {'COMPLETE','PARTIAL'}
    assert store.get(job)['state']=='PAUSED_NEEDS_HUMAN'
    assert not files(adapter.lake)


def test_existing_running_recovery_of_b_does_not_block_a(work):
    _,store,_,adapter,job,token,engine=work
    other,ot=seed(store,adapter,name='b',place='place-b',video='123')
    assert engine._reserve(ot)[0] is not None
    assert recover(engine,job,token)['status']=='PARTIAL'
    assert not any(e['delivered'] for e in event_rows(store,other))


def test_same_database_different_lake_and_same_lake_different_database_contend(work,tmp_path):
    root,store,_,adapter,_,_,_=work
    second=root/'second.sqlite3';repo=CommentPersistence(second,adapter.lake);repo.close()
    with publisher_ownership(store.path,adapter.lake):
        with pytest.raises(PublisherBusy):
            with publisher_ownership(store.path,root/'other-lake'):pytest.fail('Same database owner')
        with pytest.raises(PublisherBusy):
            with publisher_ownership(second,adapter.lake):pytest.fail('Same lake owner')


def test_explicit_empty_job_does_not_parse_other_indexed_malformed_payload(work):
    _,store,_,adapter,job,_,engine=work
    eid=event_rows(store,job)[0]['event_id']
    with store._transaction():store.db.execute("UPDATE tiktok_comment_events SET payload='{broken' WHERE event_id=?",(eid,))
    empty,token=seed(store,adapter,name='empty',place='empty-place',count=0,complete=True)
    result=recover(engine,empty,token)
    assert result['status']=='COMPLETE' and result['event_count']==0 and result['parquet_rows']==0
    assert not files(adapter.lake)


def test_legacy_global_flush_and_unscoped_m2_cannot_bypass_managed_owner(work):
    from crawler_harness.recovery import OfflineRecovery
    from crawler_harness.state import StateError
    _,store,_,adapter,job,token,_=work
    repo=CommentPersistence(store.path,adapter.lake)
    with pytest.raises(RuntimeError,match='guarded scoped'):repo.flush()
    repo.close()
    old=OfflineRecovery(store,adapter.lake)
    with pytest.raises(StateError,match='JobScopedPublication'):recover(old,job,token)
    assert not files(adapter.lake) and not any(e['delivered'] for e in event_rows(store,job))


def test_scoped_retry_budget_and_backoff_remain_durable(work):
    _,store,_,adapter,job,token,_=work
    def failure(stage):
        if stage=='before_publish':raise OSError('fixture interruption')
    engine=JobScopedPublication(store,adapter.lake,max_attempts=2,backoff_seconds=(0,),fault=failure)
    assert recover(engine,job,token)['status']=='RETRY_LATER'
    assert recover(engine,job,token)['status']=='BUDGET_EXHAUSTED'
    assert recover(engine,job,token)['status']=='BUDGET_EXHAUSTED'
    assert store.db.execute('SELECT attempts_used FROM harness_recovery_policy WHERE job_id=?',(job,)).fetchone()[0]==2
    assert store.db.execute('SELECT count(*) FROM harness_recovery_attempts WHERE job_id=?',(job,)).fetchone()[0]==2
    assert not files(adapter.lake)


def test_physical_database_identity_handles_hard_link_alias(work):
    root,store,_,adapter,_,_,_=work
    alias=root/'database-alias.sqlite3'
    os.link(store.path,alias)
    with publisher_ownership(store.path,adapter.lake):
        with pytest.raises(PublisherBusy):
            with publisher_ownership(alias,root/'alias-lake'):pytest.fail('Same physical database')


def test_wrong_source_job_is_rejected_without_touching_frozen_google(work):
    from crawler_harness.state import StateError
    _,store,_,adapter,_,_,engine=work
    job=store.create_job(source='google',kind='collection',place_id='fixture-only',
                         entity_id='123',input_digest='fixture')
    token=store.claim(job,'fixture-worker',lease_seconds=10)
    with pytest.raises(StateError,match='TikTok collection'):recover(engine,job,token)
    assert store.get(job)['state']=='RUNNING' and not files(adapter.lake)
