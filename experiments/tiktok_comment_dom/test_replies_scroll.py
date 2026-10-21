"""Offline collector/persistence recovery and real JS execution against a synthetic DOM."""
import json
import multiprocessing
import os
import shutil
import subprocess
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from experiments.tiktok_comment_dom.extractor import CommentSession, normalize_card
from experiments.tiktok_comment_dom.persistence import CommentPersistence, FIELDS, outcome
from experiments.tiktok_comment_dom.probe import collect_video

NOW = '2026-10-14T00:00:00+00:00'
FIXTURES = Path(__file__).with_name('fixtures')


def projection():
    return json.loads((FIXTURES/'replies.json').read_text(encoding='utf-8'))


def test_visible_replies_separate_limits_links_and_unknown_parents():
    s = CommentSession('123', 3, 10)
    s.add(projection(), NOW)
    assert s.top_level_count == 3 and len(s.records) == 7
    assert s.records['reply:reply-a']['parent_comment_id'] == 'parent-a'
    rows = [r for r in s.records.values() if r['is_reply']]
    assert {r['record_type'] for r in rows} == {'REPLY'}
    fallback = next(r for r in rows if r['text'] == 'Synthetic reply without source IDs')
    assert fallback['parent_comment_id'] is None and fallback['parent_session_key']
    assert fallback['parent_link_kind'] == 'SESSION_OBSERVATION'
    assert next(r for r in rows if r['text'] == 'Synthetic orphan reply')['parent_link_kind'] == 'UNKNOWN'
    assert s.quality()['reply_coverage'] == 'PARTIAL_VISIBLE'
    assert s.quality()['replies_without_parent_id'] == 2
    assert s.records[next(k for k,r in s.records.items() if r['text']=='Synthetic no-reply parent')]['reply_count'] == 0
    assert s.pagination['exhausted'] is None


def test_default_no_replies_and_no_false_complete_after_reply_limit():
    snap = projection()
    s = CommentSession('123')
    s.add(snap, NOW)
    assert len(s.records) == 3 and s.quality()['reply_coverage'] == 'NOT_COLLECTED'
    s = CommentSession('123', 7, 1)
    s.add({**snap, 'end_of_comments_observed': True}, NOW)
    assert len(s.records) == 4 and s.quality()['replies_collected'] == 1
    assert s.pagination['exhausted'] is True  # Top-level evidence only.
    assert outcome(s.checkpoint()) == 'PARTIAL'


def test_partial_reply_expansion_duplicates_and_checkpoint_resume():
    snap = projection()
    s = CommentSession('123', 7, 10)
    s.add({**snap, 'replies': snap['replies'][:1]}, NOW)
    saved = s.checkpoint()
    restored = CommentSession.from_checkpoint(json.loads(json.dumps(saved)))
    restored.add(snap, NOW)
    assert len(restored.records) == 7 and restored.duplicates == 4
    assert restored.quality()['reply_coverage'] == 'PARTIAL_VISIBLE'
    assert restored.pagination['reply_controls_observed'] == 1
    # Reply ID namespace cannot collide with a top-level observation.
    restored.add({**snap, 'replies': [{**snap['replies'][0], 'item_attributes': {'data-comment-id':'parent-a'}}]}, NOW)
    assert 'parent-a' in restored.records and 'reply:parent-a' in restored.records


def test_reply_does_not_inherit_parent_id_and_fallback_is_parent_scoped():
    r = {'record_type':'REPLY', 'text':'synthetic identical reply', 'root_attributes':{'data-comment-id':'parent-only'}}
    a = normalize_card({**r, 'parent_card':{'text':'parent A'}}, '123', NOW)
    b = normalize_card({**r, 'parent_card':{'text':'parent B'}}, '123', NOW)
    assert a['comment_id_candidate'] is None
    assert a['session_dedup_fingerprint'] != b['session_dedup_fingerprint']
    assert a['parent_comment_id'] is None


def test_reply_reobserved_without_parent_keeps_link():
    snap = projection();s = CommentSession('123', 7, 10)
    s.add(snap, NOW)
    r = dict(snap['replies'][0]);r.pop('parent_card')
    s.add({**snap, 'replies':[r]}, NOW)
    assert s.records['reply:reply-a']['parent_comment_id'] == 'parent-a'
    assert s.records['reply:reply-a']['parent_link_kind'] == 'DOM_ID_CANDIDATE'


def test_scroll_instrumentation_bottom_not_end_and_explicit_end():
    snap = {**projection(), 'scroll_container': {'scroll_top': 10, 'scroll_height': 200, 'client_height':100}}
    class Port:
        def __init__(self,end): self.end=end;self.waits=0
        def navigate(self,url): pass
        def wait_for(self,predicate,timeout):
            self.waits+=1
            if self.waits==1:return snap
            return {**snap, 'end_of_comments_observed': self.end,
                    'scroll_container':{'scroll_top':100,'scroll_height':200,'client_height':100,'at_bottom':True}}
        def scroll_comments(self):return {'ok':True,'before':10,'after':100,'scroll_height':200,'client_height':100}
    s=CommentSession('123',7,10)
    _, actions, reason=collect_video(Port(False), {'video_id':'123','video_url':snap['url']},limit=7,max_scrolls=1,collector=s)
    assert reason=='MAX_SCROLLS' and s.pagination['exhausted'] is None
    assert actions[-1]['unique_top_level_count']==3 and actions[-1]['unique_reply_count']==4
    assert actions[-1]['no_progress_count']==1 and not actions[-1]['confirmed_end_of_list']
    s=CommentSession('123',7)
    _, actions, reason=collect_video(Port(True), {'video_id':'123','video_url':snap['url']},limit=7,max_scrolls=1,collector=s)
    assert reason=='EXPLICIT_END_MARKER' and actions[-1]['confirmed_end_of_list']


def context():
    return dict(run_id='reply-fixture',place_id='synthetic-place',input_digest='fixture',next_page=1,provenance={'mode':'OFFLINE_SYNTHETIC'})


def crash_after_commit(database,lake):
    s=CommentSession('123',7,10)
    snap=projection();s.add({**snap,'replies':snap['replies'][:1]},NOW)
    repo=CommentPersistence(database,lake);repo.persist(s,**context())
    os._exit(31)


def test_process_crash_resume_replies_reconciles_without_duplicate_events(tmp_path):
    database=tmp_path/'comments.sqlite3';lake=tmp_path/'lake'
    process=multiprocessing.get_context('spawn').Process(target=crash_after_commit,args=(database,lake))
    process.start();process.join(20)
    if process.is_alive():process.terminate();process.join();pytest.fail('fixture child timed out')
    assert process.exitcode==31
    repo=CommentPersistence(database,lake)
    try:
        assert repo.summary()=={'records':4,'events':4,'pending_events':4}
        saved=repo.load_checkpoint('reply-fixture','synthetic-place','123')
        s=CommentSession.from_checkpoint(json.loads(saved['state_json']))
        repo.flush();s.add(projection(),NOW);repo.persist(s,**context());repo.flush()
        for _ in range(2):repo.persist(s,**context());repo.flush()
        assert repo.summary()=={'records':7,'events':7,'pending_events':0}
        rows=[r for p in lake.rglob('*.parquet') for r in pq.read_table(p,partitioning=None).to_pylist()]
        sql=[dict(r) for r in repo.db.execute('SELECT * FROM tiktok_comments')]
        assert len(rows)==len(sql)==7 and len({r['event_id'] for r in rows})==7
        assert {r['storage_key']: {k:r[k] for k in FIELDS} for r in rows}=={r['storage_key']:r for r in sql}
        assert sum(json.loads(r['provenance'])['comment_relationship']['record_type']=='REPLY' for r in rows)==4
        assert [r[1] for r in repo.db.execute('PRAGMA table_info(tiktok_comments)')]==list(FIELDS)
        assert repo.load_checkpoint('reply-fixture','synthetic-place','123')['status']=='PARTIAL'
    finally:repo.close()


def test_javascript_comment_ancestor_and_reply_projection():
    node=shutil.which('node')
    if not node:pytest.skip('Node required for browser-free JavaScript structure regression')
    subprocess.run([node,str(Path(__file__).with_name('test_dom_structure.js'))],check=True,capture_output=True,text=True,timeout=15)


def expansion_snapshot(expanded=False,**flags):
    snap=projection()
    target={'parent_card':snap['cards'][0],'label':'View 3 replies'}
    return {**snap,'replies':snap['replies'][:2] if expanded else [],'reply_targets':[] if expanded else [target],**flags}


class ExpansionPort:
    def __init__(self,after=None):self.after=after;self.current=expansion_snapshot();self.clicks=0;self.waits=0
    def navigate(self,url):pass
    def snapshot(self):return self.current
    def wait_for(self,predicate,timeout):
        self.waits+=1
        if self.waits>1:self.current=self.after or expansion_snapshot(True)
        return self.current
    def expand_reply(self,target):self.clicks+=1;return {'target':'REPLY_EXPANSION','ok':True}


def test_bounded_native_expansion_intent_durable_before_click(tmp_path):
    s=CommentSession('123',7,10);p=ExpansionPort();repo=CommentPersistence(tmp_path/'db.sqlite3',tmp_path/'lake')
    def save(session,count,snapshot):
        repo.persist(session,**context())
        if session.pagination.get('reply_expansions') and session.pagination['reply_expansions'][-1]['status']=='INTENT':
            assert p.clicks==0
            stored=json.loads(repo.load_checkpoint('reply-fixture','synthetic-place','123')['state_json'])
            assert stored['pagination']['reply_expansions'][0]['status']=='INTENT'
    try:
        _,actions,_=collect_video(p,{'video_id':'123','video_url':projection()['url']},limit=7,max_scrolls=0,collector=s,on_collect=save,reply_expand_limit=1)
        assert p.clicks==1 and s.quality()['replies_collected']==2
        assert s.pagination['reply_expansions'][0]['status']=='OBSERVED'
        repo.flush();assert repo.summary()['pending_events']==0
        assert actions[-1]['new_records']==2
    finally:repo.close()


@pytest.mark.parametrize('gate',['CHALLENGE','AUTH_GATE','ACCESS_DENIED','RATE_LIMITED'])
def test_expansion_post_click_restriction_stops_without_retry(gate):
    p=ExpansionPort(expansion_snapshot(True,network_gate=gate));s=CommentSession('123',7,10)
    _,_,reason=collect_video(p,{'video_id':'123','video_url':projection()['url']},limit=7,max_scrolls=3,collector=s,reply_expand_limit=5)
    assert reason==gate and p.clicks==1 and s.quality()['replies_collected']==0


def test_uncertain_click_checkpoint_resume_never_replays_intent():
    s=CommentSession('123',7,10);p=ExpansionPort()
    saved=[]
    def crash(session,count,snapshot):
        if session.pagination.get('reply_expansions'):
            saved.append(json.loads(json.dumps(session.checkpoint())));raise RuntimeError('simulated crash before UI side effect')
    with pytest.raises(RuntimeError):
        collect_video(p,{'video_id':'123','video_url':projection()['url']},limit=7,max_scrolls=0,collector=s,on_collect=crash,reply_expand_limit=2)
    assert p.clicks==0
    restored=CommentSession.from_checkpoint(saved[0]);p=ExpansionPort()
    collect_video(p,{'video_id':'123','video_url':projection()['url']},limit=7,max_scrolls=0,collector=restored,reply_expand_limit=2,resume_in_place=True)
    assert p.clicks==0 and restored.quality()['reply_expansion_uncertain']==1


def test_expansion_no_growth_has_one_click_and_stays_partial():
    p=ExpansionPort(expansion_snapshot());s=CommentSession('123',7,10)
    _,_,reason=collect_video(p,{'video_id':'123','video_url':projection()['url']},limit=7,max_scrolls=3,collector=s,reply_expand_limit=5)
    assert reason=='REPLY_EXPANSION_NO_GROWTH' and p.clicks==1 and outcome(s.checkpoint())=='PARTIAL'


def test_native_expansion_port_checks_gate_and_resolves_unique_target(monkeypatch):
    from types import SimpleNamespace
    from experiments.tiktok_comment_dom.probe import SeleniumDOMPort
    port=object.__new__(SeleniumDOMPort);clicked=[];scripts=[]
    element=SimpleNamespace(click=lambda:clicked.append('native'))
    port.driver=SimpleNamespace(current_url=projection()['url'],execute_script=lambda script,target:(scripts.append(script) or element))
    monkeypatch.setattr(port,'snapshot',lambda:expansion_snapshot(network_gate='RATE_LIMITED'))
    assert port.expand_reply({})['reason']=='RATE_LIMITED' and not scripts
    monkeypatch.setattr(port,'snapshot',lambda:expansion_snapshot())
    assert port.expand_reply(expansion_snapshot()['reply_targets'][0])['ok'] and clicked==['native']
    assert 'arguments[0]' in scripts[0] and '.click(' not in scripts[0]


def test_reply_wait_ignores_new_top_level_and_loading_until_reply_arrives():
    class DelayedPort(ExpansionPort):
        def wait_for(self, predicate, timeout):
            self.waits += 1
            if self.clicks:
                loading = expansion_snapshot()
                extra = dict(loading['cards'][0], text='Synthetic new unrelated top level',
                             item_attributes={'data-comment-id':'new-unrelated'})
                loading['cards'] = [*loading['cards'], extra]
                loading['reply_targets'] = []  # Control disappears while loading.
                assert predicate(loading) is False
                self.current = expansion_snapshot(True)
                assert predicate(self.current) is True
            return self.current
    p=DelayedPort();s=CommentSession('123',7,10)
    collect_video(p,{'video_id':'123','video_url':projection()['url']},limit=7,
                  max_scrolls=0,collector=s,reply_expand_limit=1)
    assert p.clicks==1 and s.quality()['replies_collected']==2
    assert s.pagination['reply_expansions'][0]['status']=='OBSERVED'


def test_virtualized_snapshots_accumulate_eight_parents_and_three_replies():
    template=projection();base=template['cards'][0]
    parents=[dict(base,text=f'Synthetic parent {i}',item_attributes={'data-comment-id':f'p{i}'}) for i in range(8)]
    children=[dict(base,text=f'Synthetic child {i}',record_type='REPLY',
                   item_attributes={'data-comment-id':f'r{i}'},parent_card=parents[i]) for i in range(3)]
    session=CommentSession('123',50,10)
    for i in range(8):
        session.add({**template,'cards':[parents[i]],'replies':[children[i]] if i<3 else [],
                     'reply_controls_observed':0},NOW)
    assert session.top_level_count==8 and session.quality()['replies_collected']==3
    assert len(session.records)==11
    restored=CommentSession.from_checkpoint(json.loads(json.dumps(session.checkpoint())))
    restored.add({**template,'cards':parents[-2:],'replies':[]},NOW)
    assert len(restored.records)==11
    assert outcome(restored.checkpoint())=='PARTIAL'  # Count agreement alone is not end evidence.



def test_view_more_after_visible_reply_collects_remaining_three_and_reconciles(tmp_path):
    import copy
    snap=projection();parent=snap['cards'][0]
    children=[dict(snap['replies'][0],text=f'Synthetic reply {i}',
                   item_attributes={'data-comment-id':f'more-{i}'},parent_card=parent) for i in range(4)]
    before={**snap,'cards':snap['cards'][:2],'replies':children[:1],
            'reply_targets':[{'parent_card':parent,'label':'View 3 more'}]}
    after={**before,'replies':children,'reply_targets':[]}
    class MorePort(ExpansionPort):
        def __init__(self):super().__init__(after);self.current=copy.deepcopy(before)
    port=MorePort();session=CommentSession('123',50,20)
    repo=CommentPersistence(tmp_path/'db.sqlite3',tmp_path/'lake')
    try:
        def save(current,count,state):repo.persist(current,**context())
        collect_video(port,{'video_id':'123','video_url':snap['url']},limit=50,max_scrolls=0,
                      collector=session,reply_expand_limit=5,on_collect=save)
        assert port.clicks==1 and session.top_level_count==2
        assert session.quality()['replies_collected']==4 and len(session.records)==6
        repo.flush();summary=repo.summary()
        assert summary['records']==6 and summary['pending_events']==0
        stored=[r for p in (tmp_path/'lake').rglob('*.parquet') for r in pq.read_table(p,partitioning=None).to_pylist()]
        assert len(stored)==6 and len({r['event_id'] for r in stored})==6
        repo.flush();assert len(list((tmp_path/'lake').rglob('*.parquet')))==6
    finally:repo.close()
