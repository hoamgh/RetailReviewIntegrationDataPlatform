"""Offline SQLite/outbox/Parquet integration using existing DOM collector and Storage."""
import json
from pathlib import Path
import pytest
import pyarrow.parquet as pq
from experiments.tiktok_comment_dom.extractor import CommentSession
from experiments.tiktok_comment_dom.persistence import CommentPersistence, replay_comments, outcome, FIELDS
from experiments.tiktok_access_benchmark.crawler import Storage

FIXTURE=Path(__file__).with_name('fixtures')/'replay.json'
def data():return json.loads(FIXTURE.read_text(encoding='utf-8'))
def replay(tmp_path,**kw):
    storage=Storage(tmp_path/'output','OFFLINE',{'max_comments_per_video':50})
    return replay_comments(data(),storage=storage,database=tmp_path/'comments.sqlite3',parquet_root=tmp_path/'lake',run_id='run-1',place_id='fixture-place',**kw)
def parquet_rows(tmp_path):
    return [r for p in (tmp_path/'lake').rglob('*.parquet') for r in pq.read_table(p,partitioning=None).to_pylist()]
def session(text='hello',cid='observed-1'):
    s=CommentSession('123')
    s.add({'url':'https://www.tiktok.com/@fixture/video/123','cards':[{'text':text,'created_at_raw':'2d ago','item_attributes':{'data-comment-id':cid}}]},'2026-10-11T00:00:00+00:00')
    return s
def context(**kw):return dict(run_id='run-1',place_id='fixture-place',input_digest='fixture',next_page=1,provenance={'mode':'TEST'},**kw)

def test_offline_sqlite_to_parquet_rerun_and_resume(tmp_path):
    first=replay(tmp_path);assert first['records']==first['events']==7 and first['status']=='PARTIAL'
    assert replay(tmp_path)['events']==7
    assert replay(tmp_path,resume=True)['records']==7
    rows=parquet_rows(tmp_path);assert len(rows)==7
    assert all(r['canonical_comment_id'] is None and r['observed_comment_id'] is None for r in rows)
    assert all(r['identity_kind']=='SESSION_FALLBACK' and r['place_id']=='fixture-place' for r in rows)
    assert first['pending_events']==0
    repo=CommentPersistence(tmp_path/'comments.sqlite3',tmp_path/'lake')
    try:
        assert repo.db.execute('SELECT COUNT(*) FROM tiktok_comments').fetchone()[0]==len(rows)
        assert [r[1] for r in repo.db.execute('PRAGMA table_info(tiktok_comments)')]==list(FIELDS)
        assert all(set(FIELDS).issubset(r) for r in rows)
    finally:repo.close()

def test_changed_source_identity_content_and_revisions(tmp_path):
    repo=CommentPersistence(tmp_path/'comments.sqlite3',tmp_path/'lake')
    try:
        assert repo.persist(session(),**context())['INSERT']==1
        assert repo.persist(session(),**context())['UNCHANGED']==1
        assert repo.persist(session('edited'),**context())['UPDATE']==1
        assert repo.persist(session(),**context())['UPDATE']==1  # A -> B -> A is three events.
        repo.flush();rows=parquet_rows(tmp_path)
        assert len(rows)==3 and {r['revision'] for r in rows}=={1,2,3}
        assert repo.summary()['records']==1
        assert all(r['observed_comment_id']=='observed-1' and r['canonical_comment_id'] is None for r in rows)
    finally:repo.close()

def test_interruption_resume_partial_pages(tmp_path):
    first=replay(tmp_path,max_pages=1)
    assert first['records']==2 and first['status']=='PARTIAL'
    final=replay(tmp_path,resume=True)
    assert final['records']==7 and final['events']==7 and final['processed_pages']==1

def test_outbox_recovery_after_file_write_before_ack(tmp_path):
    repo=CommentPersistence(tmp_path/'comments.sqlite3',tmp_path/'lake')
    try:
        repo.persist(session(),**context());assert repo.summary()['pending_events']==1
        repo.flush()
        with repo.db:repo.db.execute('UPDATE tiktok_comment_events SET delivered=0')
    finally:repo.close()
    repo=CommentPersistence(tmp_path/'comments.sqlite3',tmp_path/'lake')
    try:
        assert repo.flush()==1 and repo.summary()['pending_events']==0
        assert len(list((tmp_path/'lake').rglob('*.parquet')))==1
    finally:repo.close()

def test_atomic_rollback_if_checkpoint_fails(tmp_path):
    repo=CommentPersistence(tmp_path/'comments.sqlite3',tmp_path/'lake')
    try:
        repo.db.execute("CREATE TRIGGER reject_checkpoint BEFORE INSERT ON tiktok_comment_checkpoints BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with pytest.raises(Exception):repo.persist(session(),**context())
        assert repo.summary()['records']==repo.summary()['events']==0
    finally:repo.close()

@pytest.mark.parametrize('pagination,evidence,status',[
    ({'exhausted':None,'status':'NO_GROWTH'},{},'PARTIAL'),
    ({'exhausted':None,'status':'LIMIT_REACHED'},{},'PARTIAL'),
    ({'exhausted':True},{},'COMPLETE'),
    ({'exhausted':None},{'required_http_status':403},'ACCESS_DENIED'),
    ({'exhausted':None},{'required_http_status':429},'RATE_LIMITED'),
    ({'exhausted':None},{'ancillary_http_status':403},'PARTIAL'),
    ({'exhausted':None},{'failed':True},'FAILED'),
    ({'stop_reason':'CHALLENGE'},{},'ACCESS_DENIED')])
def test_status_is_evidence_based(pagination,evidence,status):
    assert outcome({'pagination':pagination},evidence)==status

def test_gate_resume_cannot_clear_restriction(tmp_path):
    d=data();vid=d['videos'][0]['video_id']
    d['snapshots_by_video'][vid].append({'observed_at':'2026-10-11T00:00:00Z','snapshot':{'url':d['videos'][0]['video_url'],'challenge_present':True}})
    storage=Storage(tmp_path/'output','offline',{})
    args=dict(storage=storage,database=tmp_path/'comments.sqlite3',parquet_root=tmp_path/'lake',run_id='run-1',place_id='fixture-place')
    assert replay_comments(d,**args)['status']=='ACCESS_DENIED'
    resumed=replay_comments(d,resume=True,**args)
    assert resumed['status']=='ACCESS_DENIED' and resumed['processed_pages']==0

def test_fallback_is_not_cross_run_canonical_identity(tmp_path):
    replay(tmp_path)
    result=replay_comments(data(),storage=Storage(tmp_path/'other','offline',{}),database=tmp_path/'comments.sqlite3',parquet_root=tmp_path/'lake',run_id='run-2',place_id='fixture-place')
    assert result['records']==14  # Separate observation scopes, not 14 proven distinct TikTok comments.

def test_schema_drift_and_google_paths_rejected(tmp_path):
    with pytest.raises(ValueError):CommentPersistence(tmp_path/'crawler_state.sqlite3',tmp_path/'lake')
    with pytest.raises(ValueError):CommentPersistence(tmp_path/'new.sqlite3',tmp_path/'google_maps_reviews')
    repo=CommentPersistence(tmp_path/'comments.sqlite3',tmp_path/'lake')
    repo.db.execute('ALTER TABLE tiktok_comments ADD COLUMN unsupported TEXT');repo.close()
    with pytest.raises(ValueError,match='schema'):CommentPersistence(tmp_path/'comments.sqlite3',tmp_path/'lake')

def test_input_mutation_rejected_on_resume(tmp_path):
    replay(tmp_path,max_pages=1);d=data();d['videos'][0]['limit']=1
    with pytest.raises(ValueError,match='input changed'):
        replay_comments(d,storage=Storage(tmp_path/'output','offline',{}),database=tmp_path/'comments.sqlite3',parquet_root=tmp_path/'lake',run_id='run-1',place_id='fixture-place',resume=True)


def test_flush_interruption_keeps_checkpoint_and_resume(tmp_path,monkeypatch):
    original=CommentPersistence.flush;calls=[]
    def fail_after_commit(self):
        calls.append(1)
        if len(calls)==2:raise OSError('synthetic sink unavailable')
        return original(self)
    monkeypatch.setattr(CommentPersistence,'flush',fail_after_commit)
    with pytest.raises(OSError):replay(tmp_path)
    assert json.loads((tmp_path/'output/summary.json').read_text())['status']=='FAILED'
    monkeypatch.setattr(CommentPersistence,'flush',original)
    result=replay(tmp_path,resume=True)
    assert result['records']==result['events']==7 and result['pending_events']==0
    assert len(parquet_rows(tmp_path))==7

def test_explicit_end_is_complete_and_schema_typed(tmp_path):
    d=data();d['videos']=d['videos'][:1];vid=d['videos'][0]['video_id']
    d['snapshots_by_video'][vid][0]['snapshot']['end_of_comments_observed']=True
    result=replay_comments(d,storage=Storage(tmp_path/'output','offline',{}),database=tmp_path/'comments.sqlite3',parquet_root=tmp_path/'lake',run_id='run-1',place_id='fixture-place')
    assert result['status']=='COMPLETE' and result['records']==2
    schema=pq.read_schema(next((tmp_path/'lake').rglob('*.parquet')))
    assert schema.field('timestamp_raw').type==CommentPersistence.schema().field('timestamp_raw').type
    assert schema.metadata[b'tiktok_comment_schema_version']==b'1'
