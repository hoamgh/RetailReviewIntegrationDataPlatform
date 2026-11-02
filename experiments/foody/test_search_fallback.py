"""Offline contracts for street search and conservative candidate matching."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import search_fallback as sf
import parallel_q4 as q
from shapely.geometry import box


def test_missing_href_street_is_search_task_but_heading_is_not():
    state = q.seed_state(dict(source=q.INDEX,status=200,entries=[
        dict(index=0, text='10 địa điểm',href=None),
        dict(index=1,text='Lê Quốc Hưng\n20 địa điểm',href=None),
        dict(index=2,text='Tôn Đản\n706 địa điểm',href=q.INDEX+'/tren-duong-ton-dan')]))
    assert state['search_fallbacks']=={'search:1':'Lê Quốc Hưng'}
    assert 'search:1' in state['pending']
    assert state['observed_street_routes']==1


def test_parse_scoped_search_cards_only_and_deduplicates():
    html='''<a href="/ho-chi-minh/not-result">Wrong</a>
    <div class="filter-result-item"><a href="/ho-chi-minh/quan-a">A</a>
    <a href="/ho-chi-minh/quan-a">A</a></div>
    <div class="filter-result-item"><a href="/ho-chi-minh/khu-vuc-quan-4">Navigation</a></div>'''
    items=sf.parse_results(html)
    assert len(items)==1 and items[0]['url'].endswith('/quan-a')
    assert items[0]['validation_status']=='PENDING_DETAIL_VALIDATION'


def test_address_matching_is_unicode_aware_and_conservative():
    assert sf.street_evidence('12 Lê Quốc Hưng, Quận 4','Lê Quốc Hưng')=='STREET_MATCH'
    assert sf.street_evidence('12 Tôn Đản, Quận 4','Lê Quốc Hưng')=='STREET_MISMATCH'
    assert sf.street_evidence(None,'Lê Quốc Hưng')=='STREET_UNVERIFIED'


def test_search_result_provenance_and_polygon(tmp_path):
    s=q.seed_state(dict(source=q.INDEX,status=200,entries=[dict(index=1,text='Lê Quốc Hưng\n20 địa điểm',href=None)]))
    url='https://www.foody.vn/ho-chi-minh/quan-a'
    task=dict(kind='search',url='search:1',street='Lê Quốc Hưng')
    q.accept_result(s,task,dict(outcome='SEARCH_PARTIAL_UNVERIFIED',places=[dict(url=url,name='A')],observations=[]),box(0,0,2,2))
    assert s['places'][url]['search_streets']==['Lê Quốc Hưng']
    q.merge_record(s,dict(source_id='1',latitude=1,longitude=1,fields={'streetAddress':['12 Tôn Đản']}),url,box(0,0,2,2))
    assert s['records']['1']['geographic_scope']=='INSIDE_Q4_2020'
    assert s['records']['1']['search_street_evidence']['Lê Quốc Hưng']=='STREET_MISMATCH'
