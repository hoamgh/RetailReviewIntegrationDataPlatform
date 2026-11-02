"""Fixture-only identity/bridge/session tests; no browser or network."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).parent))
import poc

URL='https://www.google.com/maps/place/Test/data=!1s0x123:0xabc!8m2!3d10.76!4d106.7?token=NEVER_LOG_THIS'


def job():
    return dict(source='foody',source_id='1',source_key='foody:1',name='Test',address='42 Street',
                lat=10.76,lon=106.7,source_url='https://www.foody.vn/ho-chi-minh/test')


def row(source,identity='0x123:0xabc',kind='data_id',status='RESOLVED',source_id='1'):
    return dict(source=source,source_id=source_id,status=status,google_id_kind=kind,google_identity_value=identity)


def test_safe_url_drops_all_tracking_or_auth_queries():
    assert 'NEVER_LOG_THIS' not in poc.safe_url(URL)
    assert poc.safe_url('https://accounts.google.com/Login?token=NEVER_LOG_THIS') is None
    assert poc.safe_url('https://google.com.evil.test/maps/place/x') is None


def test_entity_coordinates_never_use_camera_position():
    assert poc.entity_point('https://www.google.com/maps/@10,106,15z')==(None,None)
    assert poc.entity_point(URL)==(10.76,106.7)
    assert poc.entity_point(URL.replace('!3d10.76','!3d100'))==(None,None)


def test_bridge_same_typed_id_suggests_but_never_confirms_merge():
    result=poc.bridge([row('foody'),row('overture')])
    assert len(result)==1 and result[0]['status']=='BRIDGE_SUGGESTED'
    assert not result[0]['confirmed']


def test_bridge_rejects_different_id_types_or_unresolved_rows():
    assert poc.bridge([row('foody'),row('overture',kind='place_id')])==[]
    assert poc.bridge([row('foody',status='AMBIGUOUS'),row('overture')])==[]
    assert poc.bridge([row('foody',status='LIMITED_VIEW'),row('overture')])==[]


def test_bridge_preserves_same_source_branch_collisions_for_review():
    result=poc.bridge([row('foody',source_id='one'),row('foody',source_id='two'),row('overture')])
    assert result[0]['status']=='IDENTITY_COLLISION_REVIEW'
    assert set(result[0]['foody_ids'])=={'one','two'}


def test_shared_identity_with_ambiguous_names_is_review_only():
    rows=[row('foody',status='AMBIGUOUS'),row('overture',status='AMBIGUOUS')]
    assert poc.bridge(rows)==[]
    candidates=poc.shared_candidates(rows)
    assert len(candidates)==1 and not candidates[0]['confirmed']
    assert candidates[0]['status']=='SHARED_CANDIDATE_NOT_CONFIRMED'


def test_legacy_navigator_preserves_source_stop_type():
    class Driver:
        current_url='https://www.google.com/maps/search/test/'
        def get(self,url):pass
    navigator=poc.GoogleMapsNavigator(Driver())
    def stop():raise poc.SourceStop('LIMITED_VIEW')
    navigator.after_search_navigation=stop
    with pytest.raises(poc.SourceStop,match='LIMITED_VIEW'):
        navigator.open_store(poc.Store(id='local',name='Test',query='Test'))


def setup_navigator(monkeypatch, failure=None):
    class Navigator:
        def __init__(self,driver):
            self.last_resolution_diagnostics={}
        def open_store(self,store):
            assert store.id.startswith('resolution-')
            assert store.query=='Test 42 Street Quận 4, TP.HCM'
            self.after_search_navigation()
            if failure: raise failure('NEVER_LOG_THIS')
            self.last_resolution_diagnostics=dict(resolved_place_title='Test',resolved_place_address='42 Street',
                resolved_url=URL,place_entity_confirmed=True,search_candidate_count=1)
    monkeypatch.setattr(poc,'GoogleMapsNavigator',Navigator)
    monkeypatch.setattr(poc,'guard',lambda driver:None)


def test_resolve_reuses_legacy_assessment_and_preserves_source_keys(monkeypatch):
    setup_navigator(monkeypatch)
    result=poc.resolve_one(job(),object())
    assert result['source_key']=='foody:1' and result['status']=='RESOLVED'
    assert result['google_data_id']=='0x123:0xabc' and result['google_place_id'] is None
    assert result['google_lat']==10.76 and result['source_lat']==10.76
    assert 'NEVER_LOG_THIS' not in str(result)
    assert result['reviews_collected']==0


def test_navigation_failure_is_sanitized_and_never_resolved(monkeypatch):
    setup_navigator(monkeypatch,poc.NavigationError)
    result=poc.resolve_one(job(),object())
    assert result['status']=='NAVIGATION_FAILED'
    assert 'NEVER_LOG_THIS' not in str(result)


@pytest.mark.parametrize('status',['LIMITED_VIEW','CHALLENGE','BLOCKED'])
def test_access_failure_stops_before_accepting_entity(monkeypatch,status):
    setup_navigator(monkeypatch)
    def blocked(driver):raise poc.SourceStop(status)
    monkeypatch.setattr(poc,'guard',blocked)
    result=poc.resolve_one(job(),object())
    assert result['status']==status and not result['google_identity_value']
