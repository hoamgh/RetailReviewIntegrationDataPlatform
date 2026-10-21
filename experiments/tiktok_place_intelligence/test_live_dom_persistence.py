"""Fake browser -> existing collector -> SQLite/outbox -> Parquet integration."""
import json
from types import SimpleNamespace
import pytest
import pyarrow.parquet as pq
from experiments.tiktok_place_intelligence import probe
from experiments.tiktok_comment_dom.persistence import CommentPersistence
from experiments.tiktok_comment_dom.probe import SeleniumDOMPort

VIDEO='7565877317415816466'
URL='https://www.tiktok.com/@tiemgaquan4/video/'+VIDEO
PLACE='overture:canonical-popeyes-test'

@pytest.fixture
def match(monkeypatch):
    from crawl_experiment.storage import place_catalog
    canonical=SimpleNamespace(place_key=PLACE,name='Popeyes Khánh Hội',address='172 Khánh Hội')
    monkeypatch.setattr(place_catalog,'load_catalog',lambda _:SimpleNamespace(places=[canonical]))
    return {'place':{'place_id':PLACE,'canonical_name':canonical.name,'address':canonical.address,
                     'brand':'Popeyes','ward':'Khánh Hội','search_category':'gà rán'},
            'selected_video':{'video_id':VIDEO,'place_id':PLACE,'decision':'MATCH',
                'caption':'Popeyes 172 Khánh Hội','hashtags':[],'navigation_target':URL}}

def snapshot(cards=None,**kw):
    return {'url':URL,'cards':cards or [],'controls':[{}],'comment_panel_present':bool(cards),**kw}
def card(text='hello',cid='observed-1'):
    return {'text':text,'item_attributes':{'data-comment-id':cid},'created_at_raw':'2d ago','like_count_raw':'1'}

class Port:
    def __init__(self,states):
        self.states=iter(states);self.actions=[]
        self.driver=SimpleNamespace(current_url=URL,window_handles=['one'],save_screenshot=lambda _:None)
    def navigate(self,url):self.actions.append(('navigate',url))
    def wait_for(self,condition,timeout):return next(self.states)
    def has_comments_tab(self):return True
    def click_comment_icon(self):self.actions.append('icon');return {'ok':True}
    def click_comments_tab(self):self.actions.append('tab');return {'ok':True}
    def scroll_comments(self):pytest.fail('No scroll in single-video smoke')

def run(tmp_path,match,port,**kw):
    return probe.run_dom_comments(match,output=tmp_path/'run',database=tmp_path/'comments.sqlite3',
        parquet_root=tmp_path/'lake',run_id='browser-run-1',port=port,**kw)
def rows(tmp_path):return [r for p in (tmp_path/'lake').rglob('*.parquet') for r in pq.read_table(p,partitioning=None).to_pylist()]


def test_visible_replies_through_existing_runner_and_schema(match,tmp_path):
    parent=card('synthetic parent','parent-id')
    reply={**card('synthetic reply','reply-id'),'parent_card':parent}
    result=run(tmp_path,match,Port([snapshot([parent],replies=[reply],reply_controls_observed=1)]),reply_limit=4)
    assert result['status']=='PARTIAL' and result['records']==2 and result['pending_events']==0
    assert result['collection_quality']['top_level_comments']==1
    assert result['collection_quality']['replies_collected']==1
    stored=rows(tmp_path)
    assert len(stored)==2
    relations=[json.loads(r['provenance'])['comment_relationship'] for r in stored]
    assert {r['record_type'] for r in relations}=={'TOP_LEVEL','REPLY'}
    assert next(r for r in relations if r['record_type']=='REPLY')['parent_comment_id']=='parent-id'
    summary=json.loads((tmp_path/'run'/'summary.json').read_text(encoding='utf-8'))
    assert summary['collection_quality']['reply_coverage']=='PARTIAL_VISIBLE'
    import csv
    with (tmp_path/'run'/'comments.csv').open(encoding='utf-8-sig') as handle:
        csv_rows=list(csv.DictReader(handle))
    assert {r['record_type'] for r in csv_rows}=={'TOP_LEVEL','REPLY'}

def test_browser_click_dom_sqlite_parquet(match,tmp_path):
    port=Port([snapshot(),snapshot(),snapshot([card(),card()])])
    result=run(tmp_path,match,port)
    assert port.actions==[('navigate',URL),'icon','tab']
    assert result['records']==result['events']==1 and result['pending_events']==0
    assert result['status']=='PARTIAL' and rows(tmp_path)[0]['place_id']==PLACE
    assert rows(tmp_path)[0]['canonical_comment_id'] is None

def test_same_session_resume_changed_comment(match,tmp_path):
    run(tmp_path,match,Port([snapshot([card()])]))
    port=Port([snapshot([card('edited')])])
    result=run(tmp_path,match,port,resume=True)
    assert result['records']==1 and result['events']==2 and result['pending_events']==0
    assert port.actions==[]  # Existing page, no navigation/replacement.
    assert {r['text'] for r in rows(tmp_path)}=={'hello','edited'}


def test_manual_challenge_recovery_same_driver_checkpoint_and_idempotent_sink(match,tmp_path):
    from experiments.tiktok_comment_dom.probe import InteractiveChallengeResolver
    class ManualPort(Port):
        def __init__(self):
            super().__init__([snapshot(challenge_present=True,challenge_reason='ACTIVE_CAPTCHA_CONTAINER')])
            self.verified=False
            self.original_driver=self.driver
        def snapshot(self):
            self.verified=True  # Simulated HUMAN verification, not a CAPTCHA solver.
            return snapshot([card()])
    port=ManualPort()
    resolver=InteractiveChallengeResolver(timeout=1,poll_interval=.01,prompt_fn=lambda _: 'ready')
    first=run(tmp_path,match,port,challenge_resolver=resolver)
    assert port.driver is port.original_driver and port.driver.current_url==URL
    assert first['records']==first['events']==1 and first['pending_events']==0
    assert first['status']=='PARTIAL' and len(rows(tmp_path))==1
    repo=CommentPersistence(tmp_path/'comments.sqlite3',tmp_path/'lake')
    try:
        saved=repo.load_checkpoint('browser-run-1',PLACE,VIDEO)
        assert saved and saved['status']=='PARTIAL'
    finally:repo.close()
    port.states=iter([snapshot([card()])])
    second=run(tmp_path,match,port,resume=True)
    assert port.driver is port.original_driver and port.actions==[('navigate',URL)]
    assert second['records']==second['events']==1 and second['pending_events']==0
    assert len(rows(tmp_path))==1


def test_manual_challenge_wrong_video_stops_without_persisted_comments(match,tmp_path):
    from experiments.tiktok_comment_dom.probe import InteractiveChallengeResolver
    class RedirectedPort(Port):
        def __init__(self):super().__init__([snapshot(challenge_present=True,challenge_reason='ACTIVE_CAPTCHA_CONTAINER')])
        def snapshot(self):
            self.driver.current_url='https://www.tiktok.com/@someone/video/999'
            return snapshot([card()])
    port=RedirectedPort()
    result=run(tmp_path,match,port,challenge_resolver=InteractiveChallengeResolver(timeout=1,prompt_fn=lambda _: 'ready'))
    assert result['stop_reason']=='CHALLENGE_SESSION_CHANGED'
    assert result['records']==result['events']==result['pending_events']==0

def test_gate_stops_and_preserves_terminal_checkpoint(match,tmp_path):
    result=run(tmp_path,match,Port([snapshot(network_gate='ACCESS_DENIED')]))
    assert result['status']=='ACCESS_DENIED' and result['records']==0
    repo=CommentPersistence(tmp_path/'comments.sqlite3',tmp_path/'lake')
    try:assert repo.load_checkpoint('browser-run-1',PLACE,VIDEO)['status']=='ACCESS_DENIED'
    finally:repo.close()
    port=Port([]);result=run(tmp_path,match,port,resume=True)
    assert result['status']=='ACCESS_DENIED' and not port.actions

@pytest.mark.parametrize('gate,status',[('RATE_LIMITED','RATE_LIMITED'),('CHALLENGE','ACCESS_DENIED'),('UNKNOWN_403','FAILED')])
def test_gate_classification(match,tmp_path,gate,status):
    port=Port([snapshot(network_gate=gate)])
    assert run(tmp_path,match,port)['status']==status
    assert port.actions==[('navigate',URL)]

def test_interrupted_after_committed_batch_then_resume(match,tmp_path,monkeypatch):
    original=CommentPersistence.flush;calls=[]
    def interrupt(self):
        calls.append(1)
        if len(calls)==2:raise KeyboardInterrupt()
        return original(self)
    monkeypatch.setattr(CommentPersistence,'flush',interrupt)
    result=run(tmp_path,match,Port([snapshot([card()])]))
    assert result['status']=='PARTIAL' and result['records']==1
    monkeypatch.setattr(CommentPersistence,'flush',original)
    result=run(tmp_path,match,Port([snapshot([card(),card()])]),resume=True)
    assert result['records']==result['events']==1 and len(rows(tmp_path))==1

def test_no_growth_timeout_not_complete(match,tmp_path):
    assert run(tmp_path,match,Port([snapshot(wait_timeout=True,controls=[])]))['status']=='PARTIAL'

def test_bad_mapping_rejected_before_browser(match,tmp_path):
    match['selected_video']['place_id']='fixture-leetea'
    with pytest.raises(ValueError):run(tmp_path,match,Port([]))
    assert not (tmp_path/'comments.sqlite3').exists()

def test_resume_requires_same_existing_page(match,tmp_path):
    run(tmp_path,match,Port([snapshot([card()])]))
    port=Port([]);port.driver.current_url='https://www.tiktok.com/'
    with pytest.raises(ValueError):run(tmp_path,match,port,resume=True)

def test_owned_output_not_overwritten(match,tmp_path):
    run(tmp_path,match,Port([snapshot([card()])]))
    original=(tmp_path/'run/summary.json').read_bytes()
    with pytest.raises(ValueError):run(tmp_path,match,Port([]))
    assert (tmp_path/'run/summary.json').read_bytes()==original

def test_dom_port_injected_driver_not_closed_and_network_gate():
    class Driver:
        def set_page_load_timeout(self,_):pass
        def set_script_timeout(self,_):pass
        def execute_script(self,script):
            return snapshot() if 'comment-level-1' in script else {'dialog_evidence':[]}
        def quit(self):pytest.fail('Injected driver belongs to caller')
    network=SimpleNamespace(drain=lambda:None,gate=lambda _: 'ACCESS_DENIED')
    port=SeleniumDOMPort(Driver(),network);assert port.snapshot()['network_gate']=='ACCESS_DENIED'
    port.close()


def test_confirmed_onboarding_closed_once_before_comments(match,tmp_path):
    class OnboardingPort(Port):
        def dismiss_onboarding(self):self.actions.append('got-it');return {'ok':True,'target':'FEED_ONBOARDING_GOT_IT'}
    port=OnboardingPort([snapshot(dialog_kinds=['UNKNOWN_DIALOG']),snapshot([card()])])
    result=run(tmp_path,match,port)
    assert port.actions==[('navigate',URL),'got-it'] and result['records']==1
    summary=json.loads((tmp_path/'run/summary.json').read_text())
    assert summary['videos_attempted']==summary['videos_opened']==1

def test_unrecognized_dialog_not_dismissed(match,tmp_path):
    class UnknownPort(Port):
        def dismiss_onboarding(self):return {'ok':False,'reason':'NO_CONFIRMED_ONBOARDING'}
    port=UnknownPort([snapshot(dialog_kinds=['UNKNOWN_DIALOG'])])
    result=run(tmp_path,match,port)
    assert result['stop_reason']=='NON_ACCESS_DIALOG_PRESENT' and port.actions==[('navigate',URL)]


def test_late_scroll_tutorial_after_icon_dismissed_without_tab_click(match,tmp_path):
    class LatePort(Port):
        tutorial=False
        def has_onboarding(self):return self.tutorial
        def click_comment_icon(self):
            self.tutorial=True
            return super().click_comment_icon()
        def dismiss_onboarding(self):
            self.tutorial=False
            self.actions.append('got-it')
            return {'ok':True,'target':'FEED_ONBOARDING_GOT_IT'}
        def click_comments_tab(self):pytest.fail('Fresh visible comments require no tab click')
    port=LatePort([snapshot(),snapshot(),snapshot([card()])])
    result=run(tmp_path,match,port)
    assert result['records']==1 and result['pending_events']==0
    assert port.actions==[('navigate',URL),'icon','got-it']
    diagnostics=json.loads((tmp_path/'run/dialog_diagnostics.json').read_text())
    assert any(s['fresh_after_dismissal'] and s['dismissed_at'] for s in diagnostics['snapshots'])


def test_already_visible_comments_require_no_click(match,tmp_path):
    port=Port([snapshot([card()])])
    result=run(tmp_path,match,port)
    assert result['records']==1
    assert port.actions==[('navigate',URL)]


def test_overture_integration_mapping_survives_sqlite_outbox_parquet(match,tmp_path):
    match['integration_mapping']={'canonical_place_id':PLACE,'overture_place_id':'canonical-popeyes-test',
        'video_id':VIDEO,'search_query':'Popeyes 172 Khánh Hội','match_evidence':{'address':'172 Khánh Hội'}}
    result=run(tmp_path,match,Port([snapshot([card()])]))
    assert result['records']==1 and result['pending_events']==0
    payload=rows(tmp_path)[0]
    assert json.loads(payload['provenance'])['integration_mapping']==match['integration_mapping']
    assert payload['place_id']==PLACE and payload['video_id']==VIDEO


@pytest.mark.parametrize('kwargs', [{'limit':51},{'comment_scrolls':11}])
def test_dom_integration_rejects_out_of_bounds_before_browser(match,tmp_path,kwargs):
    with pytest.raises(ValueError):
        run(tmp_path,match,None,**kwargs)


def test_dom_integration_accepts_fifty_comments_ten_scrolls_and_reconciles(match,tmp_path):
    cards=[card(f'Synthetic comment {i}',f'fixture-{i}') for i in range(8)]
    result=run(tmp_path,match,Port([snapshot(cards)]),limit=50,comment_scrolls=10)
    assert result['records']==8 and result['pending_events']==0
    assert result['status']=='PARTIAL'
    assert len(rows(tmp_path))==8
