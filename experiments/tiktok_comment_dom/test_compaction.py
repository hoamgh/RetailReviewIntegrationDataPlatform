"""Offline sealed compaction; live outbox and historical files remain untouched."""
import hashlib
import json
import sqlite3
import pytest
import pyarrow.parquet as pq
from experiments.tiktok_comment_dom.compaction import compact
from experiments.tiktok_comment_dom.compaction import finalize_analysis
from experiments.tiktok_comment_dom.persistence import CommentPersistence
from experiments.tiktok_comment_dom.extractor import CommentSession


def seed(tmp_path):
    db, lake = tmp_path/'comments.sqlite3', tmp_path/'lake'
    repo = CommentPersistence(db, lake)
    try:
        for video, count in (('123', 7), ('456', 2)):
            session = CommentSession(video)
            session.add({'url':f'https://www.tiktok.com/@fixture/video/{video}',
                'cards':[{'text':f'comment-{i}', 'created_at_raw':'1d ago'} for i in range(count)]},
                '2026-10-12T00:00:00+00:00')
            repo.persist(session,run_id='fixture-run',place_id='fixture-place',input_digest='fixture',
                         next_page=1,provenance={'mode':'OFFLINE'})
        repo.flush()
    finally:repo.close()
    return db,lake


def fingerprints(db,lake):
    return [hashlib.sha256(db.read_bytes()).hexdigest()]+[
        hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(lake.rglob('*.parquet'))]


def test_nine_legacy_files_to_two_groups_without_ack_or_source_changes(tmp_path):
    db,lake=seed(tmp_path);before=fingerprints(db,lake);output=tmp_path/'compacted'
    report=compact(db,lake,output)
    assert report['source_files']==9 and report['compacted_files']==2 and report['event_rows']==9
    assert report['partial_or_unknown_groups']==2
    assert report['outbox_acknowledgements_changed']==0
    assert fingerprints(db,lake)==before
    assert sum(pq.read_table(p,partitioning=None).num_rows for p in output.rglob('*.parquet'))==9
    assert compact(db,lake,output)==report


@pytest.mark.parametrize('failure',['before_write','during_write','after_rename','before_complete'])
def test_crash_recovery_sealed_membership(tmp_path,failure):
    db,lake=seed(tmp_path);output=tmp_path/'compacted';before=fingerprints(db,lake)
    def fault(stage):
        if stage==failure:
            if stage=='during_write':
                for p in output.rglob('*.parquet.tmp'):p.write_bytes(b'truncated-write')
            raise RuntimeError('simulated crash')
    with pytest.raises(RuntimeError,match='simulated crash'):compact(db,lake,output,fault=fault)
    assert json.loads((output/'summary.json').read_text())['status']!='VERIFIED'
    report=compact(db,lake,output)
    assert report['event_rows']==9 and report['compacted_files']==2
    assert fingerprints(db,lake)==before
    rows=[r for p in output.rglob('*.parquet') for r in pq.read_table(p,partitioning=None).to_pylist()]
    assert len({r['event_id'] for r in rows})==9


def test_corrupt_published_group_cannot_be_accepted(tmp_path):
    db,lake=seed(tmp_path);output=tmp_path/'compacted';compact(db,lake,output)
    p=next(output.rglob('*.parquet'));table=pq.read_table(p,partitioning=None)
    rows=table.to_pylist();rows[0]['text']='corrupted'
    import pyarrow as pa
    pq.write_table(pa.Table.from_pylist(rows,schema=table.schema),p)
    with pytest.raises(RuntimeError,match='sealed manifest'):compact(db,lake,output)


def test_pending_events_and_duplicate_source_rejected(tmp_path):
    db,lake=seed(tmp_path)
    with sqlite3.connect(db) as conn:conn.execute('UPDATE tiktok_comment_events SET delivered=0')
    with pytest.raises(RuntimeError,match='Pending'):compact(db,lake,tmp_path/'pending')
    with sqlite3.connect(db) as conn:conn.execute('UPDATE tiktok_comment_events SET delivered=1')
    p=next(lake.rglob('*.parquet'));(lake/'duplicate.parquet').write_bytes(p.read_bytes())
    with pytest.raises(RuntimeError,match='membership'):compact(db,lake,tmp_path/'duplicate')


def test_sealed_snapshot_growth_requires_new_destination(tmp_path):
    db,lake=seed(tmp_path);output=tmp_path/'compacted';compact(db,lake,output)
    repo=CommentPersistence(db,lake)
    session=CommentSession('123');session.add({'url':'https://www.tiktok.com/@fixture/video/123',
        'cards':[{'text':'new crawl comment'}]},'2026-10-12T01:00:00+00:00')
    repo.persist(session,run_id='second-run',place_id='fixture-place',input_digest='fixture',next_page=1,provenance={})
    repo.flush();repo.close()
    with pytest.raises(RuntimeError,match='membership changed'):compact(db,lake,output)
    report=compact(db,lake,tmp_path/'snapshot-two')
    assert report['event_rows']==10 and report['compacted_files']==3


def test_source_overlap_and_schema_drift_rejected(tmp_path):
    db,lake=seed(tmp_path)
    with pytest.raises(ValueError,match='separate'):compact(db,lake,lake/'nested')
    import pyarrow as pa
    pq.write_table(pa.Table.from_pylist([{'event_id':'bad'}]),lake/'bad.parquet')
    with pytest.raises(RuntimeError,match='schema mismatch'):compact(db,lake,tmp_path/'bad')


def test_current_sqlite_revision_without_event_rejected(tmp_path):
    db,lake=seed(tmp_path)
    with sqlite3.connect(db) as conn:conn.execute('UPDATE tiktok_comments SET revision=99')
    with pytest.raises(RuntimeError,match='SQLite revision'):compact(db,lake,tmp_path/'bad-current')


def test_runner_finalization_groups_and_replays_without_source_changes(tmp_path):
    db,lake=seed(tmp_path);before=fingerprints(db,lake)
    result=finalize_analysis(tmp_path)
    assert result['status']=='VERIFIED' and result['readable'] is True
    assert result['source_files']==9 and result['compacted_files']==2
    assert result['event_rows']==9 and result['raw_retained'] is True
    assert result['partial_or_unknown_groups']==2
    assert finalize_analysis(tmp_path)==result
    assert fingerprints(db,lake)==before
    assert json.loads((tmp_path/'analysis_output.json').read_text())==result


def test_runner_finalization_does_not_publish_pending_source(tmp_path):
    db,lake=seed(tmp_path)
    with sqlite3.connect(db) as conn:conn.execute('UPDATE tiktok_comment_events SET delivered=0')
    before=fingerprints(db,lake)
    result=finalize_analysis(tmp_path)
    assert result['status']=='FAILED' and result['readable'] is False
    assert not (tmp_path/'analysis_lake'/'summary.json').exists()
    assert fingerprints(db,lake)==before


def test_runner_finalization_without_collection_has_no_fake_success(tmp_path):
    result=finalize_analysis(tmp_path)
    assert result['status']=='NOT_CREATED' and result['readable'] is False
    assert not (tmp_path/'analysis_lake').exists()


def test_runner_finalization_corruption_cannot_mask_as_readable(tmp_path):
    seed(tmp_path);finalize_analysis(tmp_path)
    target=next((tmp_path/'analysis_lake').rglob('*.parquet'))
    target.write_bytes(b'broken')
    result=finalize_analysis(tmp_path)
    assert result['status']=='FAILED' and result['readable'] is False
    assert target.read_bytes()==b'broken'


def test_long_windows_destination_uses_file_handles(tmp_path):
    from experiments.tiktok_comment_dom.compaction import filesystem_path
    db,lake=seed(tmp_path)
    output=tmp_path/('long-output-'+'x'*90)/('nested-'+'y'*40)
    report=compact(db,lake,output)
    assert report['event_rows']==9 and report['compacted_files']==2
    paths=list(filesystem_path(output).rglob('*.parquet'))
    assert len(paths)==2 and all(len(str(p.resolve()))>260 for p in paths)
    assert compact(db,lake,output)==report
