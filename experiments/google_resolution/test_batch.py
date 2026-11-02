"""Offline provisional capture and DuckDB join contract tests."""
from pathlib import Path
import sys
import json
import pytest
import pyarrow.parquet as pq
import duckdb

sys.path.insert(0,str(Path(__file__).parent))
import batch

URL='https://www.google.com/maps/place/Other/data=!1s0x123:0xabc!8m2!3d10.76!4d106.7'


def observed(status='AMBIGUOUS'):
    return dict(source='foody',source_id='1',source_key='foody:1',source_name='Old name',
        source_address='42 Street',source_lat=10.76,source_lon=106.7,status=status,
        google_name='New name',google_identity_value='0x123:0xabc',google_id_kind='data_id',
        google_data_id='0x123:0xabc',resolved_url=URL,entity_ui_confirmed=True,reviews_collected=0)


def test_different_name_capture_does_not_claim_verified_match():
    r=batch.as_capture(observed(),{'snapshot_sha256':'x'},'LIVE','DIRECT_ENTITY')
    assert r['status']=='CAPTURED_FOR_REVIEW' and r['legacy_resolution_status']=='AMBIGUOUS'
    assert r['verified'] is False and r['source_name']!=r['google_name']


@pytest.mark.parametrize('status',['LIMITED_VIEW','CHALLENGE','BLOCKED','NAVIGATION_FAILED'])
def test_access_or_browser_error_never_becomes_joinable(status):
    r=batch.as_capture(observed(status),{'snapshot_sha256':'x'},'LIVE','DIRECT_ENTITY')
    assert r['status']==status and r['status'] not in batch.SUCCESS


def test_search_preview_without_open_entity_is_not_capture():
    r=observed();r['entity_ui_confirmed']=False
    captured=batch.as_capture(r,{'snapshot_sha256':'x'},'LIVE','NOT_OPENED')
    assert captured['status']=='NO_UNIQUE_ENTITY'


def test_entity_capture_accepts_name_difference_but_requires_real_place_panel():
    class Driver:
        def find_elements(self,*args):return [object()]
    nav=batch.CaptureNavigator(Driver())
    nav._place_title=lambda:'New name'
    nav._place_address=lambda:'42 Street'
    evidence=batch.poc.PageEvidence(has_place_shell=True)
    store=batch.poc.Store(id='x',name='Old name',query='Old name 42 Street')
    assert nav._resolved_place(store,URL,evidence,[])['place_entity_confirmed']
    assert nav._resolved_place(store,URL.replace('/maps/place/','/maps/search/'),evidence,[]) is None


def test_fixed_schema_empty_mapping_and_failed_write_preserve_previous(tmp_path,monkeypatch):
    batch.write_mapping(tmp_path,'foody',{'results':[]})
    path=tmp_path/'foody_mapping.parquet';before=path.read_bytes()
    assert pq.read_table(path).num_rows==0
    def fail(*args,**kwargs):raise OSError('fixture write failure')
    monkeypatch.setattr(batch.pq,'write_table',fail)
    with pytest.raises(OSError):batch.write_mapping(tmp_path,'foody',{'results':[]})
    assert path.read_bytes()==before


def test_duckdb_join_is_provisional_typed_and_flags_branch_collisions(tmp_path):
    f=batch.as_capture(observed(),{'snapshot_sha256':'x'},'LIVE','DIRECT_ENTITY')
    second=dict(f,source_id='2',source_key='foody:2')
    null=dict(f,source_id='null',google_identity_value=None)
    other_kind=dict(f,source_id='kind',google_id_kind='place_id')
    o=dict(f,source='overture',source_id='ov',source_key='overture:ov')
    batch.write_mapping(tmp_path,'foody',{'results':[f,second,null,other_kind]})
    batch.write_mapping(tmp_path,'overture',{'results':[o]})
    sql=(Path(batch.__file__).parent/'join_mappings.sql').read_text(encoding='utf-8')
    sql=sql.replace('D:/Projects/integration-recomentdation-fnb-data-platform-20261028/output/google-resolution-full-20261030-v2',tmp_path.as_posix())
    connection=duckdb.connect(':memory:');connection.execute(sql)
    result=connection.execute('SELECT match_status,verification_status,identity_collision FROM provisional_google_matches').fetchall()
    assert result==[('MATCH_BY_GOOGLE_ID','PENDING_REVIEW',True)]*2
    connection.close()


def stopped_root(root,reason='NAVIGATION_FAILED'):
    batch.poc.save(root/'SOURCE_STOP.json',dict(source='foody',reason=reason))
    row=observed();row['status']='CAPTURED_FOR_REVIEW'
    batch.poc.save(root/'foody_state.json',dict(status='STOPPED',error=reason,in_flight=None,
        results=[row,dict(row,status=reason,source_id='bad')],pending=['foody:bad']))


def test_navigation_review_preserves_checkpoint_and_archives_evidence(tmp_path):
    stopped_root(tmp_path)
    before=(tmp_path/'foody_state.json').read_bytes()
    original=(tmp_path/'SOURCE_STOP.json').read_bytes()
    result=batch.review_navigation_stop(tmp_path,lambda row:{'status':'PASS'})
    assert result['failed_source_still_stopped'] and not result['failed_job_retried']
    assert (tmp_path/'foody_state.json').read_bytes()==before
    assert not (tmp_path/'SOURCE_STOP.json').exists()
    assert Path(result['history']).read_bytes()==original


def test_failed_access_check_keeps_original_stop(tmp_path):
    stopped_root(tmp_path)
    before=(tmp_path/'SOURCE_STOP.json').read_bytes()
    with pytest.raises(ValueError,match='did not pass'):
        batch.review_navigation_stop(tmp_path,lambda row:{'status':'FAIL','reason':'LIMITED_VIEW'})
    assert (tmp_path/'SOURCE_STOP.json').read_bytes()==before


@pytest.mark.parametrize('reason',['CHALLENGE','BLOCKED','LIMITED_VIEW','BROWSER_OR_RUNNER_ERROR'])
def test_navigation_review_never_acknowledges_access_or_unknown_errors(tmp_path,reason):
    stopped_root(tmp_path,reason)
    def forbidden(row):raise AssertionError('must not even open browser')
    with pytest.raises(ValueError,match='Only a navigation-only'):
        batch.review_navigation_stop(tmp_path,forbidden)
    assert (tmp_path/'SOURCE_STOP.json').exists()
