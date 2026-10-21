"""Offline Overture-first planning, provenance, rejection and existing-runner reuse."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from experiments.tiktok_access_benchmark import overture_study as study
from crawl_experiment.storage.place_catalog import DEFAULT_CATALOG


@pytest.fixture(autouse=True)
def synthetic_catalog(tmp_path,monkeypatch):
    """Portable offline contract: no operator catalog or generated data required."""
    from functools import partial
    from crawl_experiment.core.place_catalog import CanonicalPlace
    from crawl_experiment.storage.place_catalog import PlaceCatalog,persist_catalog
    groups=('restaurant','cafe_coffee','bakery_dessert','beverage','fast_food')
    places=[CanonicalPlace(place_key=f'overture:ci-fixture-{i}',source='overture',
        source_place_id=f'ci-fixture-{i}',name=f'Fixture Coffee {i:02d}',lat=10.76,lng=106.70,
        address=f'{100+i} Duong Gia Lap, Quan 4, TP Ho Chi Minh',
        primary_category='coffee_shop',categories=['coffee_shop'],
        category_hierarchy=['food_and_drink','coffee_shop'],product_category_group=groups[i%5],
        category_source='overture',confidence=.9,admin_area_id='fixture-ward',
        admin_area_name='Phuong Khanh Hoi',first_seen_at='2026-10-21T00:00:00Z',
        last_seen_at='2026-10-21T00:00:00Z') for i in range(30)]
    catalog=tmp_path/'catalog'/'canonical_places.parquet'
    persist_catalog(PlaceCatalog(places,{}, {'canonical_persisted':30}),catalog,
                    {'fixture':'SYNTHETIC_NO_LIVE_DATA'})
    monkeypatch.setattr(study,'prepare',partial(study.prepare,catalog=catalog))
    monkeypatch.setattr(study,'DEFAULT_CATALOG',catalog)
    from crawl_experiment.storage import place_catalog
    monkeypatch.setattr(place_catalog,'DEFAULT_CATALOG',catalog)
    monkeypatch.setitem(globals(),'DEFAULT_CATALOG',catalog)


def test_plan_uses_persisted_catalog_and_diverse_places(tmp_path):
    plan=study.prepare(tmp_path/'plan')
    assert len(plan['places'])==30 and plan['verified_videos']==0
    assert len({p['canonical_place_id'] for p in plan['places']})==30
    assert len({p['product_category_group'] for p in plan['places']})>=5
    assert all(p['overture_place_id'] and p['address'] and p['queries'] for p in plan['places'])
    assert not any('popeyes' in p['place_name'].lower() for p in plan['places'])
    assert study.validate_plan(plan)==plan
    assert all((tmp_path/'plan'/f'place-batch-{i:02d}.json').exists() for i in (1,2,3))


def test_mapping_requires_name_address_and_planned_query(tmp_path):
    plan=study.prepare(tmp_path/'plan');place=plan['places'][0]
    source=tmp_path/'discovery';source.mkdir()
    (source/'canonical_place.json').write_text(json.dumps(place),encoding='utf-8')
    good={'video_id':'123','creator_username':'fixture','caption':place['place_name']+' '+place['address'],
          'hashtags':[],'query_used':place['queries'][0],'observed_at':'2026-10-12T00:00:00+00:00'}
    wrong={**good,'video_id':'456','caption':place['place_name']}
    unrelated={**good,'video_id':'789','query_used':'random search'}
    (source/'unique_videos.json').write_text(json.dumps([good,wrong,unrelated]),encoding='utf-8')
    result=study.review(plan,source,tmp_path/'reviewed')
    assert result['accepted_mappings']==1
    assert len(result['rejected_candidates'])==2
    batch=json.loads((tmp_path/'reviewed/batch-01.json').read_text(encoding='utf-8'))
    mapping=batch['inputs'][0]['integration_mapping']
    assert mapping['overture_place_id']==place['overture_place_id']
    assert mapping['canonical_place_id']==place['canonical_place_id']
    assert mapping['search_query']==place['queries'][0]
    assert mapping['match_confidence_kind']=='HEURISTIC_NOT_CALIBRATED_PROBABILITY'
    assert json.loads((tmp_path/'reviewed/batch-02.json').read_text())['inputs']==[]


def test_empty_discovery_never_fabricates_matches(tmp_path):
    plan=study.prepare(tmp_path/'plan');source=tmp_path/'empty';source.mkdir()
    result=study.review(plan,source,tmp_path/'reviewed')
    assert result['accepted_mappings']==0 and len(result['unmatched_places'])==30
    assert result['benchmark_live_approved'] is False


def test_discovery_reuses_one_uc_owner_and_stops_existing_runner_gate(tmp_path,monkeypatch):
    from experiments.tiktok_access_benchmark import benchmark
    plan=study.prepare(tmp_path/'plan');calls=[]
    driver=SimpleNamespace(_is_using_uc=True)
    def owner(**kw):
        calls.append('start')
        return SimpleNamespace(driver=driver,close=lambda:calls.append('close'))
    monkeypatch.setattr(benchmark,'BrowserObserver',owner)
    def existing(place,queries,output,**kw):
        assert kw['driver'] is driver and kw['live'] is True
        calls.append(place['canonical_place_id'])
        return {'stopped_reason':'ACCESS_BLOCKED'}
    monkeypatch.setattr(study,'discover_place',existing)
    result=study.discover(plan,1,tmp_path/'discovered')
    assert calls==['start',plan['places'][0]['canonical_place_id'],'close']
    assert result['attempted_places']==1 and result['automatic_next_batch'] is False


def test_discovery_access_evidence_retains_429_retry_after_and_endpoint():
    from experiments.tiktok_place_discovery.probe import discovery_access_evidence
    from experiments.tiktok_access_benchmark.network import decision
    response={'url':'https://www.tiktok.com/api/search/general/full/?token=hidden',
              'status':429,'headers':{'Retry-After':'120'}}
    events=[{'message':json.dumps({'message':{'method':'Network.responseReceived',
              'params':{'type':'Fetch','response':response}}})}]
    evidence=discovery_access_evidence(events)
    assert evidence[0]['retry_after_seconds']==120 and evidence[0]['endpoint_kind']=='search_api'
    assert 'hidden' not in json.dumps(evidence)
    assert decision(evidence,True)=='RATE_LIMITED'


def test_automatic_matching_allows_multiple_videos_for_same_store_without_crawl(tmp_path):
    plan=study.prepare(tmp_path/'plan');place=plan['places'][0]
    source=tmp_path/'source';source.mkdir()
    (source/'canonical_place.json').write_text(json.dumps(place),encoding='utf-8')
    records=[{'video_id':str(i),'creator_username':'same_creator',
        'caption':place['place_name']+' '+place['address'],'hashtags':[],
        'query_used':place['queries'][0],'observed_at':'2026-10-12T00:00:00+00:00'} for i in (123,456)]
    (source/'unique_videos.json').write_text(json.dumps(records),encoding='utf-8')
    result=study.review(plan,source,tmp_path/'matched',multiple_per_place=True,creator_limit=None)
    assert result['accepted_mappings']==2
    manifest=json.loads((tmp_path/'matched/batch-01.json').read_text(encoding='utf-8'))
    assert {i['selected_video']['video_id'] for i in manifest['inputs']}=={'123','456'}


def test_short_store_query_keeps_street_and_city_scope(tmp_path):
    plan=study.prepare(tmp_path/'plan')
    for p in plan['places']:
        assert p['queries']==[p['place_name'].split(' - ')[0].strip()+' '+p['address'].split(',')[0].strip()+' Quận 4 TP Hồ Chí Minh']


def test_query_candidates_keep_unverified_label_and_view_like_counts(tmp_path):
    plan=study.prepare(tmp_path/'plan');place=plan['places'][0]
    source=tmp_path/'source';source.mkdir()
    records=[{'video_id':str(i),'creator_username':'fixture','caption':'no address evidence',
        'query_used':place['queries'][0],'view_count':1200,'like_count':35,'observed_at':'2026-10-12'} for i in range(1,13)]
    (source/'unique_videos.json').write_text(json.dumps(records),encoding='utf-8')
    candidates=study.query_candidates(place,source)
    assert len(candidates)==10
    row=candidates[0]
    assert row['association_policy']=='QUERY_CANDIDATE_UNVERIFIED'
    assert row['integration_mapping']['match_confidence'] is None
    assert row['integration_mapping']['view_count']==1200 and row['integration_mapping']['like_count']==35
    from experiments.tiktok_place_intelligence.probe import validate_dom_match
    with pytest.raises(ValueError,match='MATCH'):validate_dom_match(row)
    assert validate_dom_match(row,allow_query_candidates=True)[0]['video_id']=='1'


def test_observed_search_link_replaces_derived_handle_without_crawl(tmp_path):
    plan=study.prepare(tmp_path/'plan');place=plan['places'][0]
    source=tmp_path/'source';source.mkdir()
    records=[{'video_id':str(i),'creator_username':'metadata_handle','query_used':place['queries'][0],
              'view_count':12,'like_count':3} for i in (123,456)]
    (source/'unique_videos.json').write_text(json.dumps(records),encoding='utf-8')
    link='https://www.tiktok.com/@actual_card_handle/video/123'
    result=study.query_candidates(place,source,observed_links=[{'video_id':'123','video_url':link}])
    assert len(result)==1
    assert result[0]['selected_video']['navigation_target']==link
    assert result[0]['selected_video']['navigation_provenance']=='OBSERVED_SEARCH_CARD'
    assert result[0]['integration_mapping']['video_url']==link
    assert study.query_candidates(place,source,observed_links=[])==[]


def test_search_card_candidates_clean_actual_href_and_reject_other_hosts():
    from experiments.tiktok_access_benchmark.crawler import candidates
    links=[{'href':'https://www.tiktok.com/@actual/video/123?q=public&t=123'},
           {'href':'https://example.com/@actual/video/456'}, {'href':'https://www.tiktok.com/@actual'}]
    result=candidates(links)
    assert len(result)==1 and result[0]['video_id']=='123'
    assert result[0]['video_url']=='https://www.tiktok.com/@actual/video/123'


@pytest.mark.parametrize('auth,expected_discovery,expected_stop,manual_action,captcha_case',[
    ({'state':'AUTH_READY','reason':'SIGNED_IN_UI_OBSERVED'},True,'DISCOVERY_ACCESS_BLOCKED',None,None),
    ({'state':'AUTH_READY','reason':'SIGNED_IN_UI_OBSERVED'},True,'DISCOVERY_ACCESS_BLOCKED','READY',None),
    ({'state':'AUTH_READY','reason':'SIGNED_IN_UI_OBSERVED'},False,'MANUAL_AUTH_CANCELLED','CLOSE',None),
    ({'state':'AUTH_REQUIRED','reason':'NO_VERIFIED_SIGNED_IN_UI'},False,'AUTH_REQUIRED',None,None),
    ({'state':'AUTH_REQUIRED','reason':'MANUAL_VERIFICATION_REQUIRED'},False,'CHALLENGE',None,None),
    ({'state':'AUTH_BLOCKED','reason':'GOOGLE_UNSAFE_BROWSER'},False,'AUTH_BLOCKED',None,None),
    ({'state':'AUTH_REQUIRED','reason':'NO_VERIFIED_SIGNED_IN_UI'},True,'DISCOVERY_ACCESS_BLOCKED','READY',None),
    ({'state':'AUTH_REQUIRED','reason':'NO_VERIFIED_SIGNED_IN_UI'},False,'MANUAL_AUTH_CANCELLED','CLOSE',None),
    ({'state':'AUTH_REQUIRED','reason':'MANUAL_VERIFICATION_REQUIRED'},True,'DISCOVERY_ACCESS_BLOCKED',None,'clear'),
    ({'state':'AUTH_REQUIRED','reason':'MANUAL_VERIFICATION_REQUIRED'},False,'CHALLENGE_TIMEOUT',None,'timeout'),
    ({'state':'AUTH_REQUIRED','reason':'MANUAL_VERIFICATION_REQUIRED'},False,'ACCESS_DENIED',None,'denied'),
    ({'state':'AUTH_REQUIRED','reason':'MANUAL_VERIFICATION_REQUIRED'},False,'RATE_LIMITED',None,'rate-limited'),
    ({'state':'AUTH_REQUIRED','reason':'MANUAL_VERIFICATION_REQUIRED'},False,'CHALLENGE',None,'late-challenge'),
    ({'state':'AUTH_REQUIRED','reason':'MANUAL_VERIFICATION_REQUIRED'},False,'CHALLENGE_SESSION_CHANGED',None,'session-changed'),
    ({'state':'AUTH_REQUIRED','reason':'MANUAL_VERIFICATION_REQUIRED'},False,'OPERATOR_CANCELLED',None,'cancel'),
])
def test_existing_login_profile_is_forwarded_and_revalidated_before_discovery(tmp_path,monkeypatch,auth,expected_discovery,expected_stop,manual_action,captcha_case):
    from experiments.tiktok_access_benchmark import benchmark,ui_flow,auth_session
    from experiments.tiktok_comment_dom import probe
    from experiments.tiktok_access_benchmark import reliability
    local=tmp_path/'private';profile=local/'TikTokCrawler'/'Profile';profile.mkdir(parents=True)
    monkeypatch.setenv('LOCALAPPDATA',str(local))
    monkeypatch.setattr(study,'validate_plan',lambda p:p)
    plan={'study_id':'fixture','catalog_sha256':'fixture-hash','places':[{
        'place_id':'fixture-place','canonical_place_id':'fixture-place','place_name':'Fixture bakery',
        'address':'1 Test Street','queries':['Fixture bakery 1 Test Street']}]}
    auth=dict(auth)
    calls=[];driver=SimpleNamespace(_is_using_uc=True,get=lambda url:calls.append(('homepage',url)),
                                   current_url='https://www.tiktok.com/',current_window_handle='original',
                                   service=SimpleNamespace(service_url='http://localhost:12345'))
    def owner(**kw):
        assert kw['profile_dir']==profile
        calls.append(('owner',kw['profile_dir']))
        return SimpleNamespace(driver=driver,close=lambda:calls.append(('close',)))
    monkeypatch.setattr(benchmark,'BrowserObserver',owner)
    network=SimpleNamespace(start=lambda:calls.append(('network',)),drain=lambda:calls.append(('network-drain',)),entries=[])
    monkeypatch.setattr(ui_flow,'NetworkCapture',lambda d:network)
    monkeypatch.setattr(probe,'SeleniumDOMPort',lambda **kw:SimpleNamespace())
    class Monitor:
        def __init__(self,d,output):
            assert d is driver
            self.output=output;self.history=[]
        def check(self):
            self.output.mkdir(parents=True,exist_ok=True)
            calls.append(('auth',));self.history.append(dict(auth));return dict(auth)
        def record(self,result):pass
    monkeypatch.setattr(auth_session,'AuthMonitor',Monitor)
    if captcha_case:
        real_resolver=probe.InteractiveChallengeResolver;now=[0.0]
        def sleep(dt):
            if captcha_case=='cancel':raise KeyboardInterrupt
            now[0]+=dt
        def resolver(**kw):
            assert kw['timeout']==2
            calls.append(('resolver',))
            return real_resolver(**kw,poll_interval=.5,clock=lambda:now[0],sleep_fn=sleep)
        monkeypatch.setattr(probe,'InteractiveChallengeResolver',resolver)
        original_check=Monitor.check
        def captcha_check(self):
            observations=calls.count(('auth',))+1
            if observations>=2:
                if captcha_case in ('clear','late-challenge','session-changed'):
                    auth.update(state='AUTH_READY',reason='SIGNED_IN_UI_OBSERVED')
                if captcha_case=='late-challenge' and observations>=3:
                    auth.update(state='AUTH_REQUIRED',reason='MANUAL_VERIFICATION_REQUIRED')
                if captcha_case=='session-changed':driver.current_window_handle='different'
                if captcha_case in ('denied','rate-limited'):
                    network.entries[:]=[{'status':403 if captcha_case=='denied' else 429,
                                         'endpoint_kind':'document','retry_after_seconds':120}]
            return original_check(self)
        monkeypatch.setattr(Monitor,'check',captcha_check)
    def discovery(*args,**kw):
        assert kw['driver'] is driver
        calls.append(('discovery',));return {'stopped_reason':'ACCESS_BLOCKED'}
    monkeypatch.setattr(study,'discover_place',discovery)
    monkeypatch.setattr(reliability,'write_report',lambda out,rows,stop,**kw:{'stop_reason':stop,'videos':rows,**kw})
    def manual_input(prompt):
        assert manual_action and ('close',) not in calls
        assert prompt=='Manual auth [READY/CLOSE]: '
        calls.append(('human',))
        if manual_action=='READY':auth.update(state='AUTH_READY',reason='SIGNED_IN_UI_OBSERVED')
        return manual_action
    monkeypatch.setattr('builtins.input',manual_input)
    out=tmp_path/'run'
    result=study.automatic_crawl(plan,1,out,run_id='fixture-login',max_videos=1,videos_per_store=1,
                                persistent_profile=True,manual_login=bool(manual_action),auth_wait_seconds=0,
                                manual_captcha=bool(captcha_case),manual_captcha_timeout=2)
    assert result['stop_reason']==expected_stop
    assert (('discovery',) in calls)==expected_discovery
    assert calls.count(('owner',profile))==1 and calls[-1]==('close',)
    saved=json.loads((out/'reliability_report.json').read_text(encoding='utf-8'))
    assert saved['stop_reason']==expected_stop
    assert saved['analysis_output']['status']=='NOT_CREATED'
    assert saved['analysis_output']['readable'] is False
    assert calls.index(('homepage','https://www.tiktok.com/'))<calls.index(('auth',))
    assert calls.count(('homepage','https://www.tiktok.com/'))==1
    if manual_action=='READY':assert calls.index(('human',))<calls.index(('discovery',))
    manifest=json.loads((out/'run_manifest.json').read_text(encoding='utf-8'))
    assert manifest['profile_mode']=='EXISTING_DEDICATED_LOGIN'
    assert manifest['authentication_policy']=='REQUIRE_SIGNED_IN_UI'
    if captcha_case:
        assert calls.count(('resolver',))==1
        assert calls.index(('resolver',))<calls.index(('close',))
        assert result['session_authentication']['challenge_signal_observed'] is True
        assert result['session_authentication']['manual_verification_waits']==1
        if captcha_case=='clear':
            assert result['session_authentication']['verification_outcome']=='CLEARED_UI'
            assert calls.index(('resolver',))<calls.index(('discovery',))
        if captcha_case=='late-challenge':
            assert result['session_authentication']['verification_outcome']=='REVALIDATION_FAILED'


def test_persistent_run_refuses_missing_profile_before_browser_start(tmp_path,monkeypatch):
    from experiments.tiktok_access_benchmark import benchmark
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'missing'))
    monkeypatch.setattr(benchmark,'BrowserObserver',lambda **kw:pytest.fail('Browser must not start'))
    with pytest.raises(ValueError,match='Existing login profile missing'):
        study.automatic_crawl({},1,tmp_path/'run',run_id='fixture-login',persistent_profile=True)
    assert not (tmp_path/'run').exists()


@pytest.mark.parametrize('comment_args,expected_limit,expected_scrolls', [([],7,3),(['--comment-limit','50','--comment-scrolls','10'],50,10)])
def test_cli_forwards_login_profile_flag_to_live_runner(tmp_path,monkeypatch,capsys,comment_args,expected_limit,expected_scrolls):
    import sys
    plan=tmp_path/'plan.json';plan.write_text('{}',encoding='utf-8');calls=[]
    def runner(*args,**kw):
        calls.append(kw);return {'stop_reason':'TEST_SET_COMPLETED','aggregate':{}}
    monkeypatch.setattr(study,'automatic_crawl',runner)
    monkeypatch.setattr(sys,'argv',['study','--mode','auto-crawl','--plan',str(plan),
        '--run-id','fixture-cli','--output',str(tmp_path/'run'),'--max-videos','1',
        '--videos-per-store','1','--persistent-profile','--manual-login','--auth-wait-seconds','25',
        '--manual-captcha','--manual-captcha-timeout','300','--reply-limit','3','--reply-expand-limit','2']+comment_args)
    study.main()
    assert calls==[{'run_id':'fixture-cli','max_videos':1,'videos_per_store':1,'persistent_profile':True,
                   'manual_login':True,'auth_wait_seconds':25,'manual_captcha':True,'manual_captcha_timeout':300,'reply_limit':3,'reply_expand_limit':2,'comment_limit':expected_limit,'comment_scrolls':expected_scrolls}]
    assert 'TEST_SET_COMPLETED' in capsys.readouterr().out


def test_existing_uc_factory_passes_dedicated_profile_without_backend_change(tmp_path,monkeypatch):
    import seleniumbase
    from experiments.tiktok_access_benchmark.session_manager import standard_browser_factory,TikTokSessionManager
    calls=[];driver=SimpleNamespace(_is_using_uc=True,quit=lambda:None)
    def builder(**kw):calls.append(kw);return driver
    monkeypatch.setattr(seleniumbase,'Driver',builder)
    profile=tmp_path/'Profile';owner=TikTokSessionManager(standard_browser_factory(profile_dir=profile))
    try:assert owner.acquire('fixture-login',1).driver is driver
    finally:owner.close()
    assert len(calls)==1 and calls[0]['user_data_dir']==str(profile.resolve())
    assert calls[0]['uc'] is True and calls[0]['headless'] is False


@pytest.mark.parametrize('kwargs', [{'comment_limit':0},{'comment_limit':51},
    {'comment_scrolls':11},{'comment_scrolls':-1},{'comment_limit':True}])
def test_comment_bounds_rejected_before_browser(tmp_path,kwargs):
    with pytest.raises(ValueError,match='Comment'):
        study.automatic_crawl({},1,tmp_path/'unused',run_id='fixture',**kwargs)



def test_ambiguous_business_name_query_retains_street_and_local_city():
    query=study.local_place_query('Bạc Liêu Quán','123 Vĩnh Khánh, Phường 10, Quận 4, Thành phố Hồ Chí Minh')
    assert query=='Bạc Liêu Quán 123 Vĩnh Khánh Quận 4 TP Hồ Chí Minh'


def test_prepared_catalog_queries_include_local_scope(tmp_path):
    plan=study.prepare(tmp_path/'local-plan')
    assert plan['places']
    for place in plan['places']:
        assert place['queries'][0].endswith('Quận 4 TP Hồ Chí Minh')
        assert place['address'].split(',')[0].strip() in place['queries'][0]
