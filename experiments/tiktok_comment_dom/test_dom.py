import json
import pytest

from experiments.tiktok_comment_dom.extractor import CommentSession, normalize_card, write_artifacts, access_failure, utc_now
from experiments.tiktok_comment_dom.probe import POPEYES, collect_video, select_videos

VID = POPEYES['video_id']
NOW = '2026-10-08T17:00:00+00:00'

def card(**kw):
    base = dict(text='  Xin chào 😂\n', author_href_raw='/@example', author_display_name='Example',
                created_at_raw='2d ago', like_count_raw='0', reply_count_raw='Reply')
    return {**base, **kw}

def snapshot(cards=None, **kw):
    return {**dict(url=POPEYES['video_url'], title='Popeyes', cards=cards or [], controls=[{}],
                comment_panel_present=bool(cards)), **kw}

def test_raw_values_and_unknown_ids():
    row = normalize_card(card(), VID, NOW)
    assert row['text'] == '  Xin chào 😂\n'
    assert row['created_at_raw'] == '2d ago'
    assert row['comment_id_candidate'] is None and row['author_id_candidate'] is None
    assert row['like_count'] == 0 and row['reply_count'] is None
    assert row['raw_evidence']['author_href_raw'] == '/@example'
    assert row['parent_comment_id'] is None and row['is_reply'] is False

def test_source_identity_and_session_dedup():
    s = CommentSession(VID)
    c = card(item_attributes={'data-comment-id':'real-id'})
    s.add(snapshot([c]), NOW)
    s.add(snapshot([{**c,'text':'edited'}]), NOW)
    assert len(s.records) == 1 and s.records['real-id']['text'] == 'edited'
    assert s.records['real-id']['session_dedup_fingerprint'] is None
    s = CommentSession(VID)
    s.add(snapshot([card(),card()]),NOW)
    assert len(s.records) == 1 and s.duplicates == 1
    s.add(snapshot([{**card(),'created_at_raw':'3d ago'}]),NOW)
    assert len(s.records) == 2
    assert normalize_card(card(), 'other', NOW)['session_dedup_fingerprint'] != normalize_card(card(), VID, NOW)['session_dedup_fingerprint']

@pytest.mark.parametrize('href', ['https://evil.test/@example','/@example?token=secret','javascript:alert(1)'])
def test_unsafe_href_not_persisted(href):
    row=normalize_card({**card(),'author_href_raw':href},VID,NOW)
    assert row['raw_evidence']['author_href_raw'] is None

def test_limit_and_video_identity():
    s=CommentSession(VID,2)
    s.add(snapshot([{**card(),'text':str(i)} for i in range(5)]),NOW)
    assert len(s.records)==2
    with pytest.raises(ValueError): s.add(snapshot(url='https://www.tiktok.com/@example/video/other'),NOW)
    with pytest.raises(ValueError): CommentSession(VID,51)

def test_selection_and_gate():
    assert select_videos([{**POPEYES,'decision':'REVIEW'},POPEYES,POPEYES]) == [POPEYES]
    assert not select_videos([{**POPEYES,'matching_signals':['BRAND_MATCH']}])
    assert access_failure(snapshot(body_text_snippet='Log in to comment')) is None
    assert access_failure(snapshot(body_text_snippet='HTTP ERROR 403')) == 'ACCESS_DENIED'

class Port:
    def __init__(self, states): self.states=iter(states); self.actions=[]
    def navigate(self,url): self.actions.append('navigate')
    def wait_for(self,condition,timeout): return next(self.states)
    def click_comment_icon(self): self.actions.append('icon'); return {'ok':True}
    def has_comments_tab(self): return True
    def click_comments_tab(self): self.actions.append('tab'); return {'ok':True}
    def scroll_comments(self): self.actions.append('comment_scroll'); return {'ok':True}

class PollingPort(Port):
    def wait_for(self, condition, timeout):
        for state in self.states:
            self.last=state
            if access_failure(state) or condition(state):return state
        return {**self.last,'wait_timeout':True}


def test_visible_panel_waits_for_skeleton_to_finish_without_reclicking():
    loading=snapshot(comment_panel_present=True,comments_loading=True)
    ready=snapshot([card()],comments_loading=False)
    port=PollingPort([loading,loading,ready])
    session=CommentSession(VID,1)
    _,_,reason=collect_video(port,POPEYES,limit=1,collector=session)
    assert reason=='COMMENT_LIMIT' and len(session.records)==1
    assert port.actions==['navigate']


def test_loading_timeout_preserves_partial_and_does_not_collect_placeholders():
    loading=snapshot(comment_panel_present=True,comments_loading=True)
    port=PollingPort([loading,loading])
    session=CommentSession(VID,1)
    _,_,reason=collect_video(port,POPEYES,limit=1,collector=session)
    assert reason=='COMMENT_CARDS_TIMEOUT' and not session.records
    assert session.pagination.get('exhausted') is not True
    assert port.actions==['navigate']


def test_challenge_during_comment_loading_stops_immediately():
    port=PollingPort([snapshot(comment_panel_present=True,comments_loading=True),
                      snapshot(challenge_present=True)])
    session=CommentSession(VID,1)
    assert collect_video(port,POPEYES,limit=1,collector=session)[2]=='CHALLENGE'
    assert not session.records and port.actions==['navigate']


def test_dom_wait_skips_previous_video_but_never_skips_gate(monkeypatch):
    from experiments.tiktok_comment_dom.probe import SeleniumDOMPort
    import selenium.webdriver.support.ui as ui
    class Wait:
        def __init__(self,*args,**kwargs):pass
        def until(self,predicate):
            for _ in range(3):
                result=predicate(None)
                if result:return result
            pytest.fail('Expected a matching snapshot or gate')
    monkeypatch.setattr(ui,'WebDriverWait',Wait)
    port=SeleniumDOMPort.__new__(SeleniumDOMPort)
    port.driver=object();port.expected_video_id=VID
    old=snapshot([card()],url='https://www.tiktok.com/@fixture/video/999')
    ready=snapshot([card()]);states=iter([old,ready])
    port.snapshot=lambda:next(states)
    assert port.wait_for(lambda s:bool(s.get('cards')),5)==ready
    gate={**old,'challenge_present':True};port.snapshot=lambda:gate
    assert port.wait_for(lambda s:False,5)==gate


def test_changed_video_never_reaches_collector_or_sink():
    from experiments.tiktok_access_benchmark.crawler import UIFailure
    session=CommentSession(VID,1);writes=[]
    port=Port([snapshot([card()],url='https://www.tiktok.com/@fixture/video/999')])
    with pytest.raises(UIFailure,match='VIDEO_CONTEXT_CHANGED'):
        collect_video(port,POPEYES,limit=1,collector=session,on_collect=lambda *args:writes.append(args))
    assert not session.records and not writes


def test_visible_loading_panel_without_icon_waits_for_actual_cards():
    loading=snapshot(controls=[],comment_panel_present=True,comments_loading=True)
    port=PollingPort([loading,snapshot([card()])])
    assert collect_video(port,POPEYES,limit=1)[2]=='COMMENT_LIMIT'
    assert port.actions==['navigate']


def test_real_comments_are_not_discarded_when_loader_still_visible():
    loading=snapshot(comment_panel_present=True,comments_loading=True)
    rendered=snapshot([card(text='actual rendered comment')],comments_loading=True)
    port=PollingPort([loading,rendered]);session=CommentSession(VID,1)
    _,_,reason=collect_video(port,POPEYES,limit=1,collector=session)
    assert reason=='COMMENT_LIMIT' and len(session.records)==1
    assert next(iter(session.records.values()))['text']=='actual rendered comment'
    assert session.pagination.get('exhausted') is not True
    assert port.actions==['navigate']


def test_target_video_wait_timeout_is_explicit_and_preserves_no_wrong_data(monkeypatch):
    from experiments.tiktok_comment_dom.probe import SeleniumDOMPort
    from experiments.tiktok_access_benchmark.crawler import UIFailure
    from selenium.common.exceptions import TimeoutException
    import selenium.webdriver.support.ui as ui
    class Wait:
        def __init__(self,*args,**kwargs):pass
        def until(self,predicate):
            assert predicate(None) is False
            raise TimeoutException()
    monkeypatch.setattr(ui,'WebDriverWait',Wait)
    port=SeleniumDOMPort.__new__(SeleniumDOMPort)
    port.driver=object();port.expected_video_id=VID
    port.snapshot=lambda:snapshot([card()],url='https://www.tiktok.com/@fixture/video/999')
    with pytest.raises(UIFailure,match='VIDEO_TARGET_TIMEOUT'):
        port.wait_for(lambda s:True,5)


def test_icon_then_tab_no_feed_scroll():
    p=Port([snapshot(),snapshot(),snapshot([card()])])
    _,_,reason=collect_video(p,POPEYES)
    assert p.actions==['navigate','icon','tab']
    assert reason=='NO_SAFE_COMMENT_SCROLL_CONTAINER'

def test_no_growth_is_bounded_not_end_of_list():
    state=snapshot([card()],scroll_container={'class':'comment-only'})
    p=Port([state,state,state])
    _,_,reason=collect_video(p,POPEYES)
    assert reason=='BOUNDED_NO_GROWTH'
    assert p.actions.count('comment_scroll')==2

def test_gate_stops_and_limit_does_not_scroll():
    p=Port([snapshot(body_text_snippet='HTTP ERROR 403')])
    assert collect_video(p,POPEYES)[2]=='ACCESS_DENIED' and p.actions==['navigate']
    p=Port([snapshot([card()])])
    assert collect_video(p,POPEYES,limit=1)[2]=='COMMENT_LIMIT' and p.actions==['navigate']

def test_artifacts_and_failure(tmp_path):
    state=snapshot([{**card(),'text':str(i)} for i in range(7)],comment_heading='7 comments')
    summary=write_artifacts(tmp_path,[POPEYES],{VID:[{'observed_at':NOW,'snapshot':state}]})
    assert summary['unique_comments']==7 and summary['videos_successfully_opened']==1
    assert summary['missing_reply_count']==7 and summary['missing_like_count']==0
    assert summary['videos'][0]['ui_comment_count']==7
    assert len(json.loads((tmp_path/'comments.json').read_text(encoding='utf-8')))==7
    summary=write_artifacts(tmp_path,[POPEYES],{}, {VID:'BROWSER_ERROR:TimeoutError'})
    assert summary['access_render_failures'] and summary['unique_comments']==0


def test_selenium_port_dismiss_onboarding_success_and_safety():
    from experiments.tiktok_comment_dom.probe import SeleniumDOMPort
    from types import SimpleNamespace

    class FakeElement:
        def __init__(self, tag='div', text='', parent=None, displayed=True, markers=None):
            self.tag_name = tag
            self.text = text
            self._parent = parent
            self._displayed = displayed
            self._markers = markers or []
            self.clicked = False

        def is_displayed(self):
            return self._displayed

        def click(self):
            self.clicked = True

        def find_element(self, by, value):
            if value == '..':
                return self._parent
            raise ValueError(f"Unexpected selector: {value}")

        def find_elements(self, by, value):
            return self._markers

    class FakeDriver:
        def __init__(self, buttons):
            self.buttons = buttons
        def set_page_load_timeout(self, _): pass
        def set_script_timeout(self, _): pass
        def execute_script(self, script):
            return {'cards': [], 'controls': [], 'dialog_evidence': []}
        def find_elements(self, by, value):
            return self.buttons

    # 1. Success case: tooltip overlay (not role=dialog) contains onboarding text
    grandparent = FakeElement('div', text='Scroll, use the ↑ ↓ keys, or click the arrows on the right to browse your feed.')
    parent = FakeElement('div', text='Got it', parent=grandparent)
    btn = FakeElement('button', text='Got it', parent=parent)
    port = SeleniumDOMPort(FakeDriver([btn]))
    res = port.dismiss_onboarding()
    assert res == {'ok': True, 'target': 'FEED_ONBOARDING_GOT_IT'}
    assert btn.clicked is True

    # 2. Safety rejection: container has onboarding text BUT ALSO login marker
    login_marker = FakeElement('div', displayed=True)
    grandparent_login = FakeElement('div', text='Scroll, use the browse your feed', markers=[login_marker])
    parent_login = FakeElement('div', text='Got it', parent=grandparent_login)
    btn_login = FakeElement('button', text='Got it', parent=parent_login)
    port_login = SeleniumDOMPort(FakeDriver([btn_login]))
    res_login = port_login.dismiss_onboarding()
    assert res_login == {'ok': False, 'reason': 'LOGIN_OR_CHALLENGE_CONTAINER'}
    assert btn_login.clicked is False

    # 3. Text mismatch
    other_parent = FakeElement('div', text='Some other unrelated dialog')
    btn_other = FakeElement('button', text='Got it', parent=other_parent)
    port_other = SeleniumDOMPort(FakeDriver([btn_other]))
    assert port_other.dismiss_onboarding() == {'ok': False, 'reason': 'NO_CONFIRMED_ONBOARDING'}


def test_safe_stop_and_challenge_classification_fixtures():
    """Offline regression fixtures distinguishing passive WAF infrastructure from confirmed active challenges."""
    from experiments.tiktok_access_benchmark.access import dialog_kinds, automatic_interstitial
    from experiments.tiktok_access_benchmark.network import diagnostic, decision

    # Fixture 1: WAF script present without active challenge
    # A 200 OK script from WAF (e.g. dd9808.js) in network trace is passive infrastructure.
    waf_script_entry = diagnostic(
        {'url': 'https://sf16-website-login.neutral.ttwstatic.com/obj/tiktok_web_login_static/obj/waf-aiso/dd9808.js', 'status': 200},
        'Script', 'BEFORE_SEARCH'
    )
    assert decision([waf_script_entry], rendered=True) is None
    snap_waf_passive = snapshot(
        controls=[{'data-e2e': 'comment-icon'}],
        video_detail_rendered=True,
        challenge_present=False,
        dialog_evidence=[],
        dialog_kinds=[],
        body_text_snippet='Popeyes Khanh Hoi video detail'
    )
    assert access_failure(snap_waf_passive) is None

    # Fixture 2: Hidden WAF marker
    # A hidden WAF element (visible=False) must NOT be classified as an active dialog challenge.
    hidden_marker_evidence = [{'visible': False, 'challenge_marker': True, 'login_marker': False}]
    assert dialog_kinds(hidden_marker_evidence) == []
    snap_hidden_marker = snapshot(
        video_detail_rendered=True,
        dialog_evidence=hidden_marker_evidence,
        dialog_kinds=dialog_kinds(hidden_marker_evidence),
        challenge_present=False,
        body_text_snippet='Normal content'
    )
    assert access_failure(snap_hidden_marker) is None
    # Verify automatic interstitial distinguishes please-wait transient page from active CAPTCHA
    interstitial_snap = {
        'challenge_markers': ['._wafchallengeid'],
        'challenge_text_signals': ['please wait'],
        'homepage': False, 'search': False, 'video_detail': False, 'comments': False
    }
    assert automatic_interstitial(interstitial_snap) is True
    # If active CAPTCHA marker is present, it is not an interstitial
    assert automatic_interstitial(dict(interstitial_snap, challenge_markers=['#captcha_container'])) is False

    # Fixture 3: Visible active CAPTCHA
    # Visible active CAPTCHA marker or challenge text MUST trigger CHALLENGE stop.
    visible_captcha_evidence = [{'visible': True, 'challenge_marker': True}]
    assert dialog_kinds(visible_captcha_evidence) == ['CHALLENGE']
    snap_visible_captcha = snapshot(
        video_detail_rendered=True,
        dialog_evidence=visible_captcha_evidence,
        dialog_kinds=['CHALLENGE'],
        challenge_present=True,
        body_text_snippet='verify you are human'
    )
    assert access_failure(snap_visible_captcha) == 'CHALLENGE'
    p_captcha = Port([snap_visible_captcha])
    _, actions_captcha, stop_captcha = collect_video(p_captcha, POPEYES)
    assert stop_captcha == 'CHALLENGE'
    assert actions_captcha == []
    assert p_captcha.actions == ['navigate']

    # Fixture 4: Access-denied document
    # HTTP 403 on document endpoint or chrome-error or HTTP 403 in body text MUST trigger ACCESS_DENIED.
    doc_403_entry = diagnostic(
        {'url': POPEYES['video_url'], 'status': 403},
        'Document', 'NAVIGATION'
    )
    assert decision([doc_403_entry], rendered=False) == 'ACCESS_DENIED'
    snap_doc_denied = snapshot(
        url='chrome-error://chromewebdata',
        body_text_snippet='HTTP ERROR 403',
        network_gate='ACCESS_DENIED'
    )
    assert access_failure(snap_doc_denied) == 'ACCESS_DENIED'
    p_denied = Port([snap_doc_denied])
    _, actions_denied, stop_denied = collect_video(p_denied, POPEYES)
    assert stop_denied == 'ACCESS_DENIED'
    assert actions_denied == []
    assert p_denied.actions == ['navigate']

    # Fixture 5: Ordinary onboarding dialog
    # Feed tutorial overlay (Got it) without login/challenge markers is not a gate; dismiss_onboarding succeeds.
    snap_onboarding = snapshot(
        controls=[{'data-e2e': 'comment-icon'}],
        dialog_evidence=[{'visible': True, 'comment_panel': False, 'share_marker': False,
                          'login_marker': False, 'auth_form': False, 'challenge_marker': False, 'consent_marker': False}],
        dialog_kinds=['UNKNOWN_DIALOG'],
        body_text_snippet='Scroll, use the up down keys to browse your feed. Got it'
    )
    assert access_failure(snap_onboarding) is None


def test_production_classifier_js_and_snapshot_diagnostics():
    from experiments.tiktok_comment_dom.extractor import SNAPSHOT_JS
    from experiments.tiktok_comment_dom.probe import SeleniumDOMPort, STATE_JS

    # Verify SNAPSHOT_JS does not blindly match ._wafchallengeid
    assert '._wafchallengeid,[id*="captcha-verify"]' not in SNAPSHOT_JS
    assert 'activeCaptcha' in SNAPSHOT_JS
    assert 'challengeSignals' in SNAPSHOT_JS

    # Verify STATE_JS records bounding_rect and reason safely
    assert 'bounding_rect' in STATE_JS
    assert 'challenge_reason' in STATE_JS
    assert 'isChallenge' in STATE_JS

    # Verify SeleniumDOMPort.snapshot attaches diagnostics without losing fields
    class MockDriver:
        def __init__(self, snap_data, state_data):
            self.snap_data = snap_data
            self.state_data = state_data
        def set_page_load_timeout(self, t): pass
        def set_script_timeout(self, t): pass
        def execute_script(self, script):
            if "snapshot" in script.lower() or "cards" in script:
                return dict(self.snap_data)
            return dict(self.state_data)

    driver = MockDriver(
        snap_data={'url': 'https://www.tiktok.com/@user/video/123', 'cards': [], 'challenge_present': False},
        state_data={
            'dialog_evidence': [],
            'video_detail': True,
            'challenge': False,
            'challenge_markers': ['._wafchallengeid'],
            'challenge_details': [{'selector': '._wafchallengeid', 'tag': 'DIV', 'visible': False,
                                   'bounding_rect': {'width': 0, 'height': 0, 'top': 0, 'left': 0},
                                   'width': 0, 'height': 0, 'descendant_count': 0}],
            'challenge_text_signals': [],
            'challenge_reason': None
        }
    )
    port = SeleniumDOMPort(driver=driver)
    state = port.snapshot()
    assert state['challenge_present'] is False
    assert state['challenge_details'] == driver.state_data['challenge_details']
    assert state['challenge_markers'] == ['._wafchallengeid']
    assert state['challenge_reason'] is None
    assert access_failure(state) is None

    # Test with confirmed active CAPTCHA
    driver_active = MockDriver(
        snap_data={'url': 'https://www.tiktok.com/@user/video/123', 'cards': [], 'challenge_present': True},
        state_data={
            'dialog_evidence': [{'visible': True, 'challenge_marker': True}],
            'video_detail': True,
            'challenge': True,
            'challenge_markers': ['#captcha_container'],
            'challenge_details': [{'selector': '#captcha_container', 'tag': 'DIV', 'visible': True,
                                   'bounding_rect': {'width': 340, 'height': 280, 'top': 100, 'left': 200},
                                   'width': 340, 'height': 280, 'descendant_count': 5}],
            'challenge_text_signals': ['verify you are human'],
            'challenge_reason': 'ACTIVE_CAPTCHA_CONTAINER'
        }
    )
    port_active = SeleniumDOMPort(driver=driver_active)
    state_active = port_active.snapshot()
    assert state_active['challenge_present'] is True
    assert state_active['challenge_reason'] == 'ACTIVE_CAPTCHA_CONTAINER'
    assert state_active['challenge_details'][0]['bounding_rect']['width'] == 340
    assert access_failure(state_active) == 'CHALLENGE'


def test_onboarding_interaction_order_scenarios():
    # 1. Visible ordinary onboarding -> dismiss -> comment click
    class OnboardingDismissPort:
        def __init__(self):
            self.onboarding = True
            self.actions = []
            self.states = [
                snapshot(controls=[{'data-e2e': 'comment-icon'}]),
                snapshot(controls=[{'data-e2e': 'comment-icon'}]),
                snapshot([card()]),
            ]
            self._state_idx = 0

        def navigate(self, url):
            self.actions.append('navigate')

        def has_onboarding(self):
            return self.onboarding

        def dismiss_onboarding(self):
            self.actions.append({'ok': True, 'target': 'FEED_ONBOARDING_GOT_IT'})
            self.onboarding = False
            return {'ok': True, 'target': 'FEED_ONBOARDING_GOT_IT'}

        def wait_for(self, condition, timeout):
            st = self.states[min(self._state_idx, len(self.states) - 1)]
            self._state_idx += 1
            return st

        def click_comment_icon(self):
            self.actions.append({'target': 'COMMENT_ICON', 'ok': True})
            return {'target': 'COMMENT_ICON', 'ok': True}

        def has_comments_tab(self):
            return False

        def scroll_comments(self):
            return {'ok': True}

    p1 = OnboardingDismissPort()
    snaps, actions, stop_reason = collect_video(p1, POPEYES, limit=1)
    assert stop_reason == 'COMMENT_LIMIT'
    assert actions == [
        {'ok': True, 'target': 'FEED_ONBOARDING_GOT_IT'},
        {'target': 'COMMENT_ICON', 'ok': True}
    ]

    # 2. No onboarding -> comment click normally
    class NoOnboardingPort:
        def __init__(self):
            self.actions = []
            self.states = [
                snapshot(controls=[{'data-e2e': 'comment-icon'}]),
                snapshot([card()]),
            ]
            self._state_idx = 0

        def navigate(self, url):
            self.actions.append('navigate')

        def has_onboarding(self):
            return False

        def wait_for(self, condition, timeout):
            st = self.states[min(self._state_idx, len(self.states) - 1)]
            self._state_idx += 1
            return st

        def click_comment_icon(self):
            self.actions.append({'target': 'COMMENT_ICON', 'ok': True})
            return {'target': 'COMMENT_ICON', 'ok': True}

        def has_comments_tab(self):
            return False

        def scroll_comments(self):
            return {'ok': True}

    p2 = NoOnboardingPort()
    snaps, actions, stop_reason = collect_video(p2, POPEYES, limit=1)
    assert stop_reason == 'COMMENT_LIMIT'
    assert actions == [{'target': 'COMMENT_ICON', 'ok': True}]

    # 3. Active CAPTCHA -> stop without clicking
    class CaptchaPort:
        def __init__(self):
            self.actions = []

        def navigate(self, url):
            self.actions.append('navigate')

        def wait_for(self, condition, timeout):
            return snapshot(
                video_detail_rendered=True,
                dialog_evidence=[{'visible': True, 'challenge_marker': True}],
                dialog_kinds=['CHALLENGE'],
                challenge_present=True
            )

        def has_onboarding(self):
            raise AssertionError("Should not check onboarding on active challenge")

        def click_comment_icon(self):
            raise AssertionError("Should not click comment icon on active challenge")

    p3 = CaptchaPort()
    snaps, actions, stop_reason = collect_video(p3, POPEYES)
    assert stop_reason == 'CHALLENGE'
    assert actions == []
    assert p3.actions == ['navigate']

    # 4. Login gate -> stop without clicking
    class LoginGatePort:
        def __init__(self):
            self.actions = []

        def navigate(self, url):
            self.actions.append('navigate')

        def wait_for(self, condition, timeout):
            return snapshot(
                video_detail_rendered=True,
                dialog_evidence=[{'visible': True, 'login_marker': True}],
                dialog_kinds=['LOGIN_GATE']
            )

        def has_onboarding(self):
            raise AssertionError("Should not check onboarding on login gate")

        def click_comment_icon(self):
            raise AssertionError("Should not click comment icon on login gate")

    p4 = LoginGatePort()
    snaps, actions, stop_reason = collect_video(p4, POPEYES)
    assert stop_reason == 'LOGIN_OR_CONSENT_GATE'
    assert actions == []
    assert p4.actions == ['navigate']

    # 5. Onboarding remains visible -> stop safely
    class StubbornOnboardingPort:
        def __init__(self):
            self.actions = []

        def navigate(self, url):
            self.actions.append('navigate')

        def has_onboarding(self):
            return True  # Remains visible despite dismissal attempt

        def dismiss_onboarding(self):
            self.actions.append({'ok': True, 'target': 'FEED_ONBOARDING_GOT_IT'})
            return {'ok': True, 'target': 'FEED_ONBOARDING_GOT_IT'}

        def wait_for(self, condition, timeout):
            return snapshot(wait_timeout=True, controls=[{'data-e2e': 'comment-icon'}])

        def click_comment_icon(self):
            raise AssertionError("Should not click comment icon if onboarding remains visible")

    p5 = StubbornOnboardingPort()
    snaps, actions, stop_reason = collect_video(p5, POPEYES)
    assert stop_reason == 'ONBOARDING_REMAINS_VISIBLE'
    assert actions == [{'ok': True, 'target': 'FEED_ONBOARDING_GOT_IT'}]

    # 6. Unexpected click interception -> record blocker, no repeated clicks
    class InterceptedClickPort:
        def __init__(self):
            self.actions = []
            self.click_count = 0

        def navigate(self, url):
            self.actions.append('navigate')

        def has_onboarding(self):
            return False

        def wait_for(self, condition, timeout):
            return snapshot(controls=[{'data-e2e': 'comment-icon'}])

        def click_comment_icon(self):
            self.click_count += 1
            return {'target': 'COMMENT_ICON', 'ok': False, 'error_type': 'ElementClickInterceptedException'}

    p6 = InterceptedClickPort()
    snaps, actions, stop_reason = collect_video(p6, POPEYES)
    assert stop_reason == 'COMMENT_CLICK_INTERCEPTED'
    assert len(actions) == 1
    assert actions[0] == {'target': 'COMMENT_ICON', 'ok': False, 'error_type': 'ElementClickInterceptedException'}
    assert p6.click_count == 1  # Exactly one click attempted, no repeated clicks


def test_onboarding_dialog_transition_waits_for_fresh_clean_snapshot():
    """Run5 regression: Got it disappears before its dialog unmounts."""
    initial = snapshot(controls=[{'data-e2e': 'comment-icon'}], dialog_kinds=['UNKNOWN_DIALOG'])
    stale = snapshot(controls=[{'data-e2e': 'comment-icon'}], dialog_kinds=['UNKNOWN_DIALOG'])
    clean = snapshot(controls=[{'data-e2e': 'comment-icon'}], dialog_kinds=[])
    comments = snapshot([card()], dialog_kinds=[])

    class TransitionPort:
        def __init__(self, after_dismiss, dialog_persists=False):
            self.onboarding = True
            self.states = iter([initial, *after_dismiss])
            self.visited = []
            self.actions = []
            self.dialog_persists = dialog_persists

        def navigate(self, url):
            self.actions.append('navigate')

        def has_onboarding(self):
            return self.onboarding

        def dismiss_onboarding(self):
            self.onboarding = False
            return {'ok': True, 'target': 'FEED_ONBOARDING_GOT_IT'}

        def wait_for(self, condition, timeout):
            for state in self.states:
                self.visited.append(state)
                if condition(state):
                    return state
            return {**self.visited[-1], 'wait_timeout': True}

        def click_comment_icon(self):
            self.actions.append('COMMENT_ICON')
            return {'ok': True, 'target': 'COMMENT_ICON'}

        def has_comments_tab(self):
            return False

    delayed = TransitionPort([stale, clean, comments])
    _, actions, reason = collect_video(delayed, POPEYES, limit=1, max_scrolls=0)
    assert reason == 'COMMENT_LIMIT'
    assert [action['target'] for action in actions] == ['FEED_ONBOARDING_GOT_IT', 'COMMENT_ICON']
    assert delayed.visited[:3] == [initial, stale, clean]

    blocked = TransitionPort([stale, stale])
    _, actions, reason = collect_video(blocked, POPEYES, limit=1, max_scrolls=0)
    assert reason == 'NON_ACCESS_DIALOG_PRESENT'
    assert [action['target'] for action in actions] == ['FEED_ONBOARDING_GOT_IT']
    assert 'COMMENT_ICON' not in blocked.actions

    challenge = snapshot(challenge_present=True, challenge_reason='ACTIVE_CAPTCHA_CONTAINER',
                         dialog_kinds=['CHALLENGE'])
    gated = TransitionPort([stale, challenge])
    _, actions, reason = collect_video(gated, POPEYES, limit=1, max_scrolls=0)
    assert reason == 'CHALLENGE'
    assert [action['target'] for action in actions] == ['FEED_ONBOARDING_GOT_IT']
    assert 'COMMENT_ICON' not in gated.actions


def test_run5_diagnostic_timestamps_do_not_change_safe_stop():
    initial = snapshot(controls=[{'data-e2e':'comment-icon'}],dialog_kinds=['UNKNOWN_DIALOG'])
    class Port:
        def __init__(self): self.calls = 0
        def navigate(self,url): pass
        def has_onboarding(self): return self.calls == 0
        def dismiss_onboarding(self):
            self.calls += 1
            return {'ok':True,'target':'FEED_ONBOARDING_GOT_IT'}
        def wait_for(self,condition,timeout): return dict(initial,wait_timeout=True)
        def click_comment_icon(self): raise AssertionError('Safe Stop must prevent click')
    snapshots,actions,reason = collect_video(Port(),POPEYES,limit=1,max_scrolls=0)
    assert reason == 'NON_ACCESS_DIALOG_PRESENT'
    assert snapshots[0]['before_onboarding_dismissal_at']
    assert snapshots[-1]['fresh_after_dismissal'] is True
    assert snapshots[-1]['dismissed_at']
    assert [item['target'] for item in actions] == ['FEED_ONBOARDING_GOT_IT']


def test_manual_captcha_pause_and_resume_success():
    from experiments.tiktok_comment_dom.probe import InteractiveChallengeResolver

    # Port starts with video, opens comments, encounters active challenge, resolves, then returns cards
    states = [
        snapshot(controls=[{'data-e2e': 'comment-icon'}]),  # initial
        snapshot(challenge_present=True, challenge_markers=['ACTIVE_CAPTCHA_CONTAINER'], challenge_reason='ACTIVE_CAPTCHA_CONTAINER'),  # challenge on open
    ]
    resolved_state = snapshot(challenge_present=False, cards=[card(item_attributes={'data-comment-id': 'c1'}, text='great place')])

    class ChallengeRecoveryPort:
        def __init__(self):
            self.actions = []
            self.state_idx = 0
            self.resolved = False

        def navigate(self, url):
            self.actions.append(('navigate', url))

        def has_onboarding(self):
            return False

        def has_comments_tab(self):
            return False

        def click_comment_icon(self):
            self.actions.append('click_comment_icon')
            return {'target': 'COMMENT_ICON', 'ok': True}

        def wait_for(self, condition, timeout):
            if self.resolved:
                return resolved_state
            if self.state_idx < len(states):
                s = states[self.state_idx]
                self.state_idx += 1
                return s
            return states[-1]

        def snapshot(self):
            # When resolver polls snapshot, simulate human solving CAPTCHA
            self.resolved = True
            return resolved_state

        def scroll_comments(self):
            return {'ok': True}

    port = ChallengeRecoveryPort()
    resolver = InteractiveChallengeResolver(timeout=2.0, poll_interval=0.01)
    session = CommentSession(POPEYES['video_id'], limit=7)
    # A single visible window is enough to verify recovery; this fixture has no safe scroll container.
    snaps, actions, stop_reason = collect_video(port, POPEYES, limit=7, max_scrolls=0, collector=session, challenge_resolver=resolver)

    assert any(a.get('target') == 'MANUAL_CHALLENGE_RECOVERY' and a.get('ok') is True for a in actions)
    assert len(session.records) == 1
    assert session.records['c1']['text'] == 'great place'
    assert stop_reason == 'MAX_SCROLLS'


def test_manual_captcha_timeout_clean_stop():
    from experiments.tiktok_comment_dom.probe import InteractiveChallengeResolver

    challenge_snap = snapshot(challenge_present=True, challenge_markers=['ACTIVE_CAPTCHA_CONTAINER'], challenge_reason='ACTIVE_CAPTCHA_CONTAINER')

    class TimeoutPort:
        def navigate(self, url): pass
        def has_onboarding(self): return False
        def wait_for(self, cond, timeout): return challenge_snap
        def snapshot(self): return challenge_snap

    port = TimeoutPort()
    # Fast timeout for testing
    resolver = InteractiveChallengeResolver(timeout=0.05, poll_interval=0.01)
    snaps, actions, stop_reason = collect_video(port, POPEYES, challenge_resolver=resolver)

    assert stop_reason == 'CHALLENGE_TIMEOUT'
    recovery_action = next(a for a in actions if a.get('target') == 'MANUAL_CHALLENGE_RECOVERY')
    assert recovery_action['ok'] is False
    assert recovery_action['reason'] == 'CHALLENGE_TIMEOUT'


def test_manual_captcha_aborted_by_user():
    from experiments.tiktok_comment_dom.probe import InteractiveChallengeResolver

    challenge_snap = snapshot(challenge_present=True, challenge_markers=['ACTIVE_CAPTCHA_CONTAINER'], challenge_reason='ACTIVE_CAPTCHA_CONTAINER')

    class AbortPort:
        def navigate(self, url): pass
        def has_onboarding(self): return False
        def wait_for(self, cond, timeout): return challenge_snap
        def snapshot(self): return challenge_snap

    port = AbortPort()
    resolver = InteractiveChallengeResolver(prompt_fn=lambda msg: 'abort')
    snaps, actions, stop_reason = collect_video(port, POPEYES, challenge_resolver=resolver)

    assert stop_reason == 'CHALLENGE_ABORTED_BY_USER'
    recovery_action = next(a for a in actions if a.get('target') == 'MANUAL_CHALLENGE_RECOVERY')
    assert recovery_action['ok'] is False
    assert recovery_action['reason'] == 'CHALLENGE_ABORTED_BY_USER'


def test_manual_captcha_preserves_session_progress_and_dedup():
    from experiments.tiktok_comment_dom.probe import InteractiveChallengeResolver

    session = CommentSession(POPEYES['video_id'], limit=10)
    # Simulate 2 comments already collected
    session.add(snapshot(cards=[card(item_attributes={'data-comment-id': 'c1'}, text='c1'), card(item_attributes={'data-comment-id': 'c2'}, text='c2')]), utc_now())
    assert len(session.records) == 2

    # Port yields challenge then resolved cards with existing c1, c2 and new c3
    resolved_snap = snapshot(cards=[card(item_attributes={'data-comment-id': 'c1'}, text='c1'), card(item_attributes={'data-comment-id': 'c2'}, text='c2'), card(item_attributes={'data-comment-id': 'c3'}, text='c3')])

    class ResumePreservePort:
        def __init__(self):
            self.resolved = False
        def navigate(self, url): pass
        def has_onboarding(self): return False
        def has_comments_tab(self): return False
        def click_comment_icon(self): return {'ok': True, 'target': 'COMMENT_ICON'}
        def wait_for(self, cond, timeout):
            if not self.resolved:
                return snapshot(challenge_present=True, challenge_markers=['ACTIVE_CAPTCHA_CONTAINER'])
            return resolved_snap
        def snapshot(self):
            self.resolved = True
            return resolved_snap
        def scroll_comments(self): return {'ok': True}

    port = ResumePreservePort()
    resolver = InteractiveChallengeResolver(timeout=2.0, poll_interval=0.01)
    snaps, actions, stop_reason = collect_video(port, POPEYES, collector=session, challenge_resolver=resolver)

    assert any(a.get('target') == 'MANUAL_CHALLENGE_RECOVERY' and a.get('ok') is True for a in actions)
    # Total unique records is 3 (c1, c2, c3); no duplication of c1, c2
    assert len(session.records) == 3
    assert set(session.records.keys()) == {'c1', 'c2', 'c3'}
