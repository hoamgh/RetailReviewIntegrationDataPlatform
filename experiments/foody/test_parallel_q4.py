"""Two-worker Foody coordinator contracts, entirely offline."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from queue import Queue
import sys
from threading import Event

import pytest
from shapely.geometry import box

sys.path.insert(0,str(Path(__file__).parent))
import parallel_q4 as q


def state():
    return q.seed_state(dict(source=q.INDEX,status=200,entries=[
        dict(href=q.INDEX+'/tren-duong-a'),dict(href=q.INDEX+'/tren-duong-b'),dict(href=None)]))


def record(url,sid='1',lon=1,lat=1):
    return dict(url=url,source='foody',source_id=sid,name='Fixture',longitude=lon,latitude=lat,reviews_collected=0)


def test_only_observed_routes_are_queued():
    s=state()
    assert len(s['pending'])==2 and s['unresolved_street_entries']==1
    with pytest.raises(ValueError):
        q.seed_state(dict(source='wrong',status=200,entries=[]))


def test_overlapping_streets_merge_urls_and_keep_both_provenances():
    s=state();place=dict(url='https://www.foody.vn/ho-chi-minh/fixture',name='Fixture')
    for street in s['pending']:
        q.absorb(s,dict(street=street,places=[place],load=dict(click=0)))
    assert len(s['places'])==1 and len(s['places'][place['url']]['streets'])==2


def test_ids_deduplicate_and_polygon_is_applied_including_edge():
    s=state();poly=box(0,0,2,2)
    q.merge_record(s,record('https://www.foody.vn/ho-chi-minh/a',lon=0),'a',poly)
    q.merge_record(s,record('https://www.foody.vn/ho-chi-minh/b',lon=0),'b',poly)
    q.merge_record(s,record('outside',sid='2',lon=3),'outside',poly)
    q.merge_record(s,record('missing',sid='3',lon=None,lat=None),'missing',poly)
    assert len(s['records'])==3 and s['records']['1']['observed_urls']==['a','b']
    assert s['records']['1']['geographic_scope']=='INSIDE_Q4_2020'
    assert s['records']['2']['geographic_scope']=='OUTSIDE_Q4_2020'
    assert s['records']['3']['geographic_scope']=='UNKNOWN'


def test_local_error_goes_to_dlq_access_error_preserves_pending():
    s=state();url=s['pending'][0];task=dict(kind='street',url=url)
    q.accept_result(s,task,dict(outcome='ERROR',local=True,reason='NAVIGATION_TIMEOUT'),box(0,0,2,2))
    assert len(s['dlq'])==1 and url not in s['pending'] and not s['error']
    url=s['pending'][0]
    q.accept_result(s,dict(kind='street',url=url),dict(outcome='ERROR',local=False,reason='ACCESS_RESTRICTION'),box(0,0,2,2))
    assert url in s['pending'] and s['error']=='ACCESS_RESTRICTION'


def test_incomplete_street_index_never_dispatches_details(tmp_path,monkeypatch):
    (tmp_path/'raw').mkdir();s=state();events=Queue();stop=Event();calls=[]
    url='https://www.foody.vn/ho-chi-minh/fixture'
    def execute(task):
        calls.append((task['kind'],task['url']))
        if task['kind']=='street':
            events.put(dict(street=task['url'],places=[dict(url=url,name='Fixture')],load=dict(click=0)))
            return dict(outcome='NO_PROGRESS_UNVERIFIED')
        return dict(outcome='OK',record=record(url))
    monkeypatch.setattr(q,'execute',execute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        q.run(tmp_path,s,box(0,0,2,2),pool,stop,events,1,2,spacing=0)
    assert s['status']=='BLOCKED' and s['error']=='STREET_INDEX_INCOMPLETE'
    assert not any(kind=='detail' for kind,_ in calls)
    assert url in s['places']


def test_verified_street_index_dispatches_unique_detail_once(tmp_path,monkeypatch):
    (tmp_path/'raw').mkdir();s=state();events=Queue();stop=Event();calls=[]
    url='https://www.foody.vn/ho-chi-minh/fixture'
    s['unresolved_street_entries']=0
    s['observed_street_routes']=121
    s['pending']=[f'https://www.foody.vn/ho-chi-minh/khu-vuc-quan-4/tren-duong-{i}' for i in range(121)]
    def execute(task):
        calls.append((task['kind'],task['url']))
        if task['kind']=='street':
            events.put(dict(street=task['url'],places=[dict(url=url,name='Fixture')],load=dict(click=0)))
            return dict(outcome='SOURCE_EXHAUSTED')
        return dict(outcome='OK',record=record(url))
    monkeypatch.setattr(q,'execute',execute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        q.run(tmp_path,s,box(0,0,2,2),pool,stop,events,1,2,spacing=0)
    assert s['status']=='COMPLETED_PARTIAL' and calls.count(('detail',url))==1
    assert len(s['records'])==1


def test_unverified_pagination_prevents_detail_phase(tmp_path,monkeypatch):
    (tmp_path/'raw').mkdir();s=state();s['unresolved_street_entries']=0
    s['observed_street_routes']=121
    s['pending']=[f'street-{i}' for i in range(121)]
    monkeypatch.setattr(q,'execute',lambda task: dict(outcome='NO_PROGRESS_UNVERIFIED'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        q.run(tmp_path,s,box(0,0,2,2),pool,Event(),Queue(),1,2,spacing=0)
    assert s['status']=='BLOCKED' and s['error']=='STREET_DISCOVERY_NOT_VERIFIED'


def test_budget_pause_preserves_pending_and_existing_output(tmp_path):
    (tmp_path/'raw').mkdir();s=state();before=list(s['pending'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        q.run(tmp_path,s,box(0,0,2,2),pool,Event(),Queue(),0,2)
    assert s['status']=='PAUSED' and s['pending']==before


def test_known_nested_ids_and_unknown_are_distinct():
    class Response:
        def __init__(self,obj): self.obj=obj
        def body(self): return json.dumps(self.obj).encode()
    meta=dict(body_observation='JSON_READ')
    assert q.public_ids(Response({'data':{'Items':[{'Id':12},{'Id':12}]}}),meta)==['12','12']
    assert q.public_ids(Response({'data':{'Items':[]}}),meta)==[]
    assert q.public_ids(Response({'data':{'Other':[]}}),meta) is None
    assert q.public_ids(Response({'data':{'Items':[{'Id':'token'}]}}),meta) is None


def test_atomic_save_failure_preserves_last_good_output(tmp_path,monkeypatch):
    (tmp_path/'raw').mkdir();s=state();q.save(tmp_path,s)
    target=tmp_path/'raw/pois.json';before=target.read_bytes()
    original=q.crawl.os.replace
    def fail(source,destination):
        if Path(destination)==target: raise OSError('fixture')
        return original(source,destination)
    monkeypatch.setattr(q.crawl.os,'replace',fail)
    with pytest.raises(OSError): q.save(tmp_path,s)
    assert target.read_bytes()==before
