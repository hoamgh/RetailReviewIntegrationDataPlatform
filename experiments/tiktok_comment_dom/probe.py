"""Bounded DOM-only MVP runner: four MATCH videos, fifty top-level comments each."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.tiktok_comment_dom.extractor import (
    CommentSession, SNAPSHOT_JS, access_failure, utc_now, write_artifacts,
)
from experiments.tiktok_access_benchmark.access import dialog_kinds, automatic_interstitial
STATE_JS = (ROOT/'experiments/tiktok_access_benchmark/browser_state.js').read_text(encoding='utf-8')

POPEYES = {"video_id":"7565877317415816466",
    "video_url":"https://www.tiktok.com/@tiemgaquan4/video/7565877317415816466",
    "decision":"MATCH", "matching_signals":["ADDRESS_MATCH"], "match_score":.85, "comment_count":7,
    "match_evidence_source":"existing Popeyes selected-video diagnostic and known branch caption"}


def select_videos(records, limit=4):
    if not 1 <= limit <= 4:
        raise ValueError("video limit must be between 1 and 4")
    selected, seen = [], set()
    for record in records:
        if record.get("decision") != "MATCH" or record.get("match_score", 0) < .8:
            continue
        if not set(record.get("matching_signals", [])).intersection({"ADDRESS_MATCH", "EXACT_NAME"}):
            continue
        url = urlparse(record.get("video_url") or "")
        if (url.scheme != "https" or url.hostname not in {"www.tiktok.com", "tiktok.com"}
                or url.username or url.password or url.query or url.fragment
                or not url.path.endswith("/video/"+str(record.get("video_id")))):
            continue
        vid = str(record["video_id"])
        if vid not in seen:
            selected.append(dict(record))
            seen.add(vid)
        if len(selected) >= limit:
            break
    return selected


class InteractiveChallengeResolver:
    """Pauses the browser session and prompts the human operator to complete CAPTCHA manually."""
    def __init__(self, timeout=120, poll_interval=1.0, prompt_fn=None, clock=time.monotonic, sleep_fn=time.sleep, ready_predicate=None):
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.prompt_fn = prompt_fn
        self.clock = clock
        self.sleep_fn = sleep_fn
        self.ready_predicate = ready_predicate

    def __call__(self, port, state):
        reason = state.get('challenge_reason') or 'ACTIVE_CHALLENGE'
        msg = (
            f"\n====================================================================\n"
            f"[MANUAL CAPTCHA REQUIRED]\n"
            f"Active challenge detected ({reason}).\n"
            f"Browser session is PAUSED. Please solve the verification challenge\n"
            f"in the open browser window.\n"
            f"====================================================================\n"
        )
        if self.prompt_fn:
            user_choice = self.prompt_fn(msg)
            if user_choice and str(user_choice).strip().lower() in ('abort', 'q', 'quit', 'exit'):
                return {"ok": False, "reason": "CHALLENGE_ABORTED_BY_USER"}
        else:
            print(msg, flush=True)

        driver = getattr(port, 'driver', None)
        original_url = getattr(driver, 'current_url', None)
        original_handle = getattr(driver, 'current_window_handle', None)
        start_time = self.clock()
        while self.clock() - start_time < self.timeout:
            snap = port.snapshot()
            if driver is not None and (getattr(port, 'driver', None) is not driver
                    or getattr(driver, 'current_window_handle', None) != original_handle
                    or getattr(driver, 'current_url', None) != original_url):
                return {'ok': False, 'reason': 'CHALLENGE_SESSION_CHANGED'}
            if original_url and snap.get('url') and snap['url'] != original_url:
                return {'ok': False, 'reason': 'CHALLENGE_VIDEO_CHANGED'}
            if (not access_failure(snap) and not snap.get('challenge_present')
                    and 'CHALLENGE' not in snap.get('dialog_kinds', [])
                    and (self.ready_predicate(snap) if self.ready_predicate is not None else
                         (snap.get('cards') or snap.get('controls') or snap.get('comment_panel_present')))):
                print("[MANUAL CAPTCHA RESOLVED] Verification confirmed. Resuming session...", flush=True)
                return {"ok": True}
            self.sleep_fn(self.poll_interval)

        print("[MANUAL CAPTCHA TIMEOUT] Verification timed out.", flush=True)
        return {"ok": False, "reason": "CHALLENGE_TIMEOUT"}


def collect_video(port, video, limit=50, max_scrolls=5, no_growth_limit=2, checkpoint=None,
                  button_timeout=10, panel_timeout=6, cards_timeout=8, collector=None, on_collect=None,
                  resume_in_place=False, challenge_resolver=None, telemetry=None, reply_expand_limit=0):
    if not isinstance(reply_expand_limit,int) or isinstance(reply_expand_limit,bool) or not 0<=reply_expand_limit<=5:
        raise ValueError('Reply expansion limit must be 0..5')
    if reply_expand_limit and not (collector and collector.reply_limit):
        raise ValueError('Reply expansion requires a reply-enabled collector')
    if not 0 <= max_scrolls <= 10 or not 1 <= no_growth_limit <= 2:
        raise ValueError("bounded scroll/no-growth limits required")
    if any(not 1 <= t <= 30 for t in (button_timeout,panel_timeout,cards_timeout)):
        raise ValueError('Wait budgets must be 1..30 seconds')
    session = collector or CommentSession(video["video_id"], limit)
    if session.video_id != str(video["video_id"]):raise ValueError("Collector video mismatch")
    snapshots, actions = [], []
    def stage(name):
        if telemetry is not None: telemetry.stage(name)
    def capture(snapshot):
        if telemetry is not None: telemetry.observe(snapshot)
        snapshots.append({"observed_at":utc_now(), "snapshot":snapshot})
        if snapshot.get('url') and urlparse(snapshot['url']).path.rstrip('/').split('/')[-1] != session.video_id:
            if access_failure(snapshot): return 0
            from experiments.tiktok_access_benchmark.crawler import UIFailure
            raise UIFailure('VIDEO_CONTEXT_CHANGED')
        collection_started = time.monotonic()
        # A loader may coexist with already rendered comments (pagination or
        # background placeholders). Preserve valid cards; extractor rejects
        # empty/non-comment cards. A busy marker is not a reason to lose data.
        added = session.add(snapshot, snapshots[-1]["observed_at"])
        if telemetry is not None: telemetry.add_duration("collection", time.monotonic()-collection_started)
        if on_collect:on_collect(session,len(snapshots),snapshot)
        if checkpoint:
            checkpoint(snapshots, actions)
        return added

    def check_gate(s):
        nonlocal state
        fail = access_failure(s)
        if not fail:
            return None
        if fail == 'CHALLENGE' and challenge_resolver:
            res = challenge_resolver(port, s)
            if res.get('ok'):
                if session.pagination.get('status') == 'STOPPED_GATE':
                    session.pagination.update(status='PAGINATING', stop_reason=None)
                actions.append({'target': 'MANUAL_CHALLENGE_RECOVERY', 'ok': True})
                reval = port.snapshot()
                capture(reval)
                state = reval
                return access_failure(reval)
            else:
                reason = res.get('reason', 'CHALLENGE_UNRESOLVED')
                actions.append({'target': 'MANUAL_CHALLENGE_RECOVERY', 'ok': False, 'reason': reason})
                return reason
        return fail

    def handle_late_onboarding():
        # Reuse the existing safe handler when the tutorial mounts after a click.
        nonlocal state
        gate = check_gate(state)
        if gate: return gate
        if not hasattr(port, 'has_onboarding') or not port.has_onboarding(): return None
        if snapshots: snapshots[-1]['before_onboarding_dismissal_at'] = utc_now()
        action = port.dismiss_onboarding()
        actions.append(action)
        if not action.get('ok'): return action.get('reason', 'ONBOARDING_DISMISSAL_FAILED')
        dismissed_at = utc_now()
        state = port.wait_for(lambda s: access_failure(s) or (
            not port.has_onboarding() and not any(k in s.get('dialog_kinds', [])
            for k in ('UNKNOWN_DIALOG', 'SHARE_DIALOG'))), panel_timeout)
        capture(state)
        snapshots[-1].update(fresh_after_dismissal=True, dismissed_at=dismissed_at)
        gate = check_gate(state)
        if gate: return gate
        if port.has_onboarding(): return 'ONBOARDING_REMAINS_VISIBLE'
        if any(k in state.get('dialog_kinds', []) for k in ('UNKNOWN_DIALOG', 'SHARE_DIALOG')):
            return 'NON_ACCESS_DIALOG_PRESENT'
        return None

    stage("navigation")
    if not resume_in_place:port.navigate(video["video_url"])
    state = port.wait_for(lambda s: access_failure(s) or s.get("cards") or s.get("controls") or s.get('comment_panel_present') or s.get('empty_comments_observed'), button_timeout)
    capture(state)
    failure = check_gate(state)
    if failure:
        return snapshots, actions, failure
    stage("onboarding")
    # Handle feed onboarding tutorial before clicking Comments
    if hasattr(port, 'has_onboarding') and port.has_onboarding():
        if snapshots: snapshots[-1]['before_onboarding_dismissal_at'] = utc_now()
        action = port.dismiss_onboarding()
        if action.get('ok'):
            dismissed_at = utc_now()
            actions.append(action)
            # Both the tutorial control and its dialog must disappear before
            # proceeding; their DOM updates may occur in separate frames.
            state = port.wait_for(
                lambda s: access_failure(s) or (
                    not port.has_onboarding()
                    and not any(k in s.get('dialog_kinds', [])
                                for k in ('UNKNOWN_DIALOG', 'SHARE_DIALOG'))
                ), panel_timeout)
            capture(state)
            snapshots[-1]['fresh_after_dismissal'] = True
            snapshots[-1]['dismissed_at'] = dismissed_at
            gate = check_gate(state)
            if gate:
                return snapshots, actions, gate
            if port.has_onboarding():
                return snapshots, actions, 'ONBOARDING_REMAINS_VISIBLE'
        elif action.get('reason') in ('ACCESS_GATE', 'LOGIN_OR_CHALLENGE_CONTAINER'):
            return snapshots, actions, action['reason']
        else:
            return snapshots, actions, action.get('reason', 'ONBOARDING_DISMISSAL_FAILED')
    elif 'UNKNOWN_DIALOG' in state.get('dialog_kinds', []) and hasattr(port, 'dismiss_onboarding'):
        if snapshots: snapshots[-1]['before_onboarding_dismissal_at'] = utc_now()
        action = port.dismiss_onboarding()
        if action.get('ok'):
            dismissed_at = utc_now()
            actions.append(action)
            state = port.wait_for(
                lambda s: access_failure(s) or (
                    not port.has_onboarding()
                    and not any(k in s.get('dialog_kinds', [])
                                for k in ('UNKNOWN_DIALOG', 'SHARE_DIALOG'))
                ), panel_timeout)
            capture(state)
            snapshots[-1]['fresh_after_dismissal'] = True
            snapshots[-1]['dismissed_at'] = dismissed_at
            gate = check_gate(state)
            if gate:
                return snapshots, actions, gate
    if any(k in state.get('dialog_kinds', []) for k in ('UNKNOWN_DIALOG','SHARE_DIALOG')):
        return snapshots, actions, 'NON_ACCESS_DIALOG_PRESENT'
    if state.get('wait_timeout') and not state.get('cards') and not state.get('controls') and not state.get('comment_panel_present'):
        return snapshots, actions, 'COMMENT_BUTTON_TIMEOUT'
    stage("comments")
    gate = handle_late_onboarding()
    if gate: return snapshots, actions, gate
    if state.get('empty_comments_observed') and session.pagination.get('exhausted') is True:
        return snapshots, actions, 'EXPLICIT_EMPTY_MARKER'
    if not state.get("cards") and not state.get('comment_panel_present'):
        click_action = port.click_comment_icon()
        actions.append(click_action)
        if not click_action.get('ok'):
            if click_action.get('error_type') == 'ElementClickInterceptedException':
                return snapshots, actions, 'COMMENT_CLICK_INTERCEPTED'
            return snapshots, actions, click_action.get('error_type') or click_action.get('reason') or 'COMMENT_BUTTON_FAILED'
        state = port.wait_for(lambda s: access_failure(s) or s.get("cards") or port.has_comments_tab(), panel_timeout)
        capture(state)
        gate = check_gate(state)
        if gate:
            return snapshots, actions, gate
        if any(k in state.get('dialog_kinds', []) for k in ('UNKNOWN_DIALOG','SHARE_DIALOG')):
            return snapshots, actions, 'NON_ACCESS_DIALOG_PRESENT'
        gate = handle_late_onboarding()
        if gate: return snapshots, actions, gate
        if not state.get("cards") and port.has_comments_tab():
            tab_action = port.click_comments_tab()
            actions.append(tab_action)
            if not tab_action.get('ok'):
                if tab_action.get('error_type') == 'ElementClickInterceptedException':
                    return snapshots, actions, 'COMMENT_CLICK_INTERCEPTED'
                return snapshots, actions, tab_action.get('error_type') or 'COMMENTS_TAB_FAILED'
            state = port.wait_for(lambda s: access_failure(s) or s.get("cards") or s.get('empty_comments_observed'), cards_timeout)
            capture(state)
            gate = check_gate(state)
            if gate:
                return snapshots, actions, gate
    gate = check_gate(state)
    if gate:
        return snapshots, actions, gate
    if not state.get("cards"):
        if not state.get('wait_timeout'):
            state = port.wait_for(lambda s: access_failure(s) or s.get("cards") or s.get('empty_comments_observed'), cards_timeout)
            capture(state)
            gate = check_gate(state)
            if gate:
                return snapshots, actions, gate
    if state.get('comments_loading') and not state.get('cards'):
        return snapshots, actions, 'COMMENT_CARDS_TIMEOUT'
    if not state.get("cards"):
        return snapshots, actions, "COMMENT_CARDS_TIMEOUT" if state.get('wait_timeout') else "COMMENT_SURFACE_UNAVAILABLE"
    gate = handle_late_onboarding()
    if gate: return snapshots, actions, gate
    stage("collection")
    if telemetry is not None: telemetry.observe(port.snapshot() if hasattr(port, "snapshot") else {})
    no_growth = 0
    def expand_visible():
        nonlocal state
        from .extractor import normalize_card, record_key
        attempts = session.pagination.setdefault('reply_expansions', [])
        while len(attempts) < reply_expand_limit and len(session.records)-session.top_level_count < session.reply_limit:
            gate = access_failure(state)
            if gate: return gate
            eligible = []
            for target in state.get('reply_targets', []):
                parent = normalize_card(target['parent_card'],session.video_id,utc_now())
                key = record_key(parent)
                # Do not expand threads outside the bounded top-level collection.
                if key not in session.records: continue
                token = __import__('hashlib').sha256(__import__('json').dumps([key,target['label']],ensure_ascii=False).encode()).hexdigest()
                if any(a['token']==token for a in attempts): continue
                eligible.append((target,token))
            if not eligible: return None
            target,token=eligible[0]
            attempt={'token':token,'status':'INTENT'};attempts.append(attempt)
            actions.append({'target':'REPLY_EXPANSION','token':token,'status':'INTENT'})
            # Commit intent before an unfenceable UI side effect; resume never replays an uncertain click.
            if on_collect:on_collect(session,len(snapshots),state)
            if checkpoint:checkpoint(snapshots,actions)
            action=port.expand_reply(target);actions.append(action)
            attempt['status']='CLICKED' if action.get('ok') else 'FAILED'
            if not action.get('ok'):
                state=port.snapshot();capture(state)
                return access_failure(state) or action.get('reason','REPLY_CLICK_FAILED')
            known=set(session.records)
            def reply_changed(s):
                if access_failure(s):return True
                test=CommentSession(session.video_id,session.limit,session.reply_limit);test.add(s,utc_now())
                # A newly rendered top-level card is not evidence that the
                # asynchronous reply request has finished.
                return any(k not in known and row.get('is_reply') for k,row in test.records.items())
            state=port.wait_for(reply_changed,3)
            added=capture(state)
            gate=access_failure(state)
            if gate:return gate # No manual challenge resolver/retry for expansion restrictions.
            attempt['status']='OBSERVED' if added else 'NO_PROGRESS'
            action.update(new_records=added,reply_count=len(session.records)-session.top_level_count)
            if on_collect:on_collect(session,len(snapshots),state)
            if checkpoint:checkpoint(snapshots,actions)
            if not added:return 'REPLY_EXPANSION_NO_GROWTH'
        return None
    if reply_expand_limit:
        failure=expand_visible()
        if failure:return snapshots,actions,failure
    for _ in range(max_scrolls):
        if session.top_level_count >= limit:
            return snapshots, actions, "COMMENT_LIMIT"
        if session.pagination.get('exhausted') is True:
            return snapshots, actions, 'EXPLICIT_END_MARKER'
        if not state.get("scroll_container"):
            return snapshots, actions, "NO_SAFE_COMMENT_SCROLL_CONTAINER"
        action = port.scroll_comments()
        actions.append(action)
        if not action.get("ok"):
            return snapshots, actions, action.get('reason', "NO_SAFE_COMMENT_SCROLL_CONTAINER")
        known = set(session.records)
        def changed(s):
            if access_failure(s) or s.get('end_of_comments_observed') or s.get('empty_comments_observed'):
                return True
            test = CommentSession(video["video_id"], limit, session.reply_limit)
            test.add(s, utc_now())
            return bool(set(test.records) - known)
        state = port.wait_for(changed, 3)
        added = capture(state)
        gate = check_gate(state)
        if gate:
            return snapshots, actions, gate
        if reply_expand_limit:
            failure=expand_visible()
            if failure:return snapshots,actions,failure
        # Expansion may add records after the scroll snapshot itself had none.
        grew = bool(set(session.records) - known)
        no_growth = 0 if grew else no_growth+1
        action.update(unique_comment_count=len(session.records), unique_top_level_count=session.top_level_count,
                      unique_reply_count=len(session.records)-session.top_level_count,
                      no_progress_count=no_growth, after_observation=state.get('scroll_container'),
                      confirmed_end_of_list=session.pagination.get('exhausted') is True)
        if session.pagination.get('exhausted') is True:
            return snapshots, actions, 'EXPLICIT_END_MARKER'
        if no_growth >= no_growth_limit:

            return snapshots, actions, "BOUNDED_NO_GROWTH"  # Not authoritative end-of-list.
    return snapshots, actions, "COMMENT_LIMIT" if session.top_level_count >= limit else "EXPLICIT_END_MARKER" if session.pagination.get('exhausted') is True else "MAX_SCROLLS"


class SeleniumDOMPort:
    """No CDP collector, logging gate, cookies, persistent profile or stealth settings."""
    def __init__(self, driver=None, network=None):
        self.owns_driver = driver is None
        self.network = network
        self.lifecycle = None
        if driver is None:
            from experiments.tiktok_access_benchmark.session_manager import TikTokSessionManager, standard_browser_factory
            self.lifecycle = TikTokSessionManager(standard_browser_factory())
            driver = self.lifecycle.acquire('tiktok-dom', 1).driver
        self.driver = driver
        self.backend = getattr(driver, '_tiktok_browser_backend', 'INJECTED_UNOBSERVED')
        try:
            self.driver.set_page_load_timeout(30)
            self.driver.set_script_timeout(8)
        except Exception:
            self.close()
            raise

    def navigate(self, url):
        self.expected_video_id = urlparse(url).path.rstrip('/').split('/')[-1]
        if getattr(self, 'navigation_handler', None) is not None:
            return self.navigation_handler(url)
        if self.driver.current_url != url:
            self.driver.get(url)

    def snapshot(self):
        state = self.driver.execute_script("return ("+SNAPSHOT_JS+")();")
        access = self.driver.execute_script('return ('+STATE_JS+')();')
        state['dialog_kinds'] = dialog_kinds(access.get('dialog_evidence', []))
        state['dialog_diagnostics'] = [dict(kind=kind, **e.get('diagnostic', {}))
            for e,kind in zip(access.get('dialog_evidence', []), state['dialog_kinds'])]
        state['video_detail_rendered'] = bool(access.get('video_detail'))
        state['challenge_details'] = access.get('challenge_details', [])
        state['challenge_markers'] = access.get('challenge_markers', [])
        state['challenge_text_signals'] = access.get('challenge_text_signals', [])
        state['challenge_reason'] = access.get('challenge_reason')
        if automatic_interstitial(access):
            state['interstitial_pending'] = True
            state['challenge_present'] = False
            access['challenge'] = False
        else:
            state['challenge_present'] = bool(state.get('challenge_present') or access.get('challenge'))
        if self.network is not None:
            self.network.drain()
            reason = self.network.gate(access)
            if reason:
                state['network_gate'] = reason
        return state

    def wait_for(self, condition, timeout):
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.common.exceptions import TimeoutException
        def ready(_):
            state = self.snapshot()
            if access_failure(state): return state
            expected = getattr(self, 'expected_video_id', None)
            if expected and urlparse(state.get('url') or '').path.rstrip('/').split('/')[-1] != expected:
                return False
            return state if condition(state) else False
        try:
            return WebDriverWait(self.driver, timeout, poll_frequency=.3).until(ready)
        except TimeoutException:
            state = self.snapshot()
            expected = getattr(self, 'expected_video_id', None)
            if not access_failure(state) and expected and urlparse(state.get('url') or '').path.rstrip('/').split('/')[-1] != expected:
                from experiments.tiktok_access_benchmark.crawler import UIFailure
                raise UIFailure('VIDEO_TARGET_TIMEOUT')
            state['wait_timeout'] = True
            return state

    def has_comments_tab(self):
        from selenium.webdriver.common.by import By
        return any(e.is_displayed() for e in self.driver.find_elements(By.XPATH,
            '//button[normalize-space(.)="Comments" or normalize-space(.)="Bình luận"]'))

    def _click(self, by, selector, target):
        try:
            gate=access_failure(self.snapshot())
            if gate:return {'target':target,'ok':False,'gate':gate}
            elements = self.driver.find_elements(by, selector)
            element = next((e for e in elements if e.is_displayed()), None)
            if element is None:
                return {"target":target, "ok":False, "reason":"NOT_FOUND"}
            element.click()
            return {"target":target, "ok":True}
        except Exception as exc:
            return {"target":target, "ok":False, "error_type":type(exc).__name__}

    def has_onboarding(self):
        """Check if the feed tutorial overlay is currently displayed."""
        from selenium.webdriver.common.by import By
        for button in self.driver.find_elements(By.XPATH, '//button[normalize-space(.)="Got it"]'):
            try:
                if not button.is_displayed():
                    continue
                container = button
                for _ in range(8):
                    parent = container.find_element(By.XPATH, '..')
                    if not parent or getattr(parent, 'tag_name', '').lower() in ('html', 'body'):
                        break
                    container = parent
                    text = container.text or ''
                    if 'browse your feed' in text and 'Scroll, use the' in text:
                        return True
            except Exception:
                continue
        return False

    def dismiss_onboarding(self):
        """Close only the observed feed tutorial; never a login/challenge dialog."""
        from selenium.webdriver.common.by import By
        state = self.snapshot()
        if access_failure(state):
            return {"ok": False, "reason": "ACCESS_GATE"}
        for button in self.driver.find_elements(By.XPATH, '//button[normalize-space(.)="Got it"]'):
            try:
                if not button.is_displayed():
                    continue
                container = button
                for _ in range(8):
                    parent = container.find_element(By.XPATH, '..')
                    if not parent or getattr(parent, 'tag_name', '').lower() in ('html', 'body'):
                        break
                    container = parent
                    text = container.text or ''
                    if 'browse your feed' in text and 'Scroll, use the' in text:
                        # Reject if this container also looks like a login/challenge dialog.
                        text_lower = text.lower()
                        if any(w in text_lower for w in ('log in', 'đăng nhập', 'verify you are human', 'security check', 'complete the captcha')):
                            return {"ok": False, "reason": "LOGIN_OR_CHALLENGE_CONTAINER"}
                        login_markers = container.find_elements(By.CSS_SELECTOR,
                            '#login-modal,[data-e2e="login-modal"],input[type="password"],#captcha_container,.captcha-verify-container')
                        for m in login_markers:
                            if m.is_displayed():
                                size = getattr(m, 'size', None)
                                if size is None or (size.get('width', 0) > 0 and size.get('height', 0) > 0):
                                    return {"ok": False, "reason": "LOGIN_OR_CHALLENGE_CONTAINER"}
                        button.click()
                        return {"ok": True, "target": "FEED_ONBOARDING_GOT_IT"}
            except Exception:
                continue
        return {"ok": False, "reason": "NO_CONFIRMED_ONBOARDING"}

    def click_comment_icon(self):
        from selenium.webdriver.common.by import By
        return self._click(By.CSS_SELECTOR, '[data-e2e="comment-icon"]', "COMMENT_ICON")

    def click_comments_tab(self):
        from selenium.webdriver.common.by import By
        return self._click(By.XPATH, '//button[normalize-space(.)="Comments" or normalize-space(.)="Bình luận"]', "COMMENTS_TAB")

    def scroll_comments(self):
        # Only scroll an identified ancestor of the top-level comment list.
        gate = access_failure(self.snapshot())
        if gate: return {'ok': False, 'reason': gate}
        from .extractor import SCROLL_CONTAINER_JS
        return self.driver.execute_script('const e=(' + SCROLL_CONTAINER_JS + r""")();
if (!e) return {ok:false,reason:'NO_SAFE_COMMENT_SCROLL_CONTAINER'};
const before=e.scrollTop;
e.scrollTop=Math.min(e.scrollHeight-e.clientHeight,before+Math.max(100,e.clientHeight*.8));
return {ok:true,target:'COMMENT_CONTAINER',before,after:e.scrollTop,
 scroll_height:e.scrollHeight,client_height:e.clientHeight,
 moved:e.scrollTop>before,at_bottom:e.scrollTop+e.clientHeight>=e.scrollHeight-2};
""")

    def expand_reply(self, target):
        """Resolve a unique observed control, then use native WebElement.click only."""
        state=self.snapshot()
        gate=access_failure(state)
        if gate:return {'target':'REPLY_EXPANSION','ok':False,'reason':gate}
        expected_url=state.get('url')
        try:
            element=self.driver.execute_script('return ('+SNAPSHOT_JS+')(arguments[0]);',target)
            if element is None:return {'target':'REPLY_EXPANSION','ok':False,'reason':'REPLY_TARGET_CHANGED_OR_AMBIGUOUS'}
            if self.driver.current_url!=expected_url:return {'target':'REPLY_EXPANSION','ok':False,'reason':'REPLY_VIDEO_CHANGED'}
            element.click()
            return {'target':'REPLY_EXPANSION','ok':True}
        except Exception as exc:
            return {'target':'REPLY_EXPANSION','ok':False,'reason':'REPLY_CLICK_FAILED','error_type':type(exc).__name__}

    def close(self):
        if self.lifecycle is not None:self.lifecycle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--videos-file", type=Path)
    parser.add_argument("--snapshot-file", type=Path, help="Import rendered DOM snapshots from a supported browser; no network needed")
    parser.add_argument("--video-limit", type=int, default=4)
    parser.add_argument("--comment-limit", type=int, default=50)
    parser.add_argument("--max-scrolls", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument('--button-timeout', type=float, default=10)
    parser.add_argument('--panel-timeout', type=float, default=6)
    parser.add_argument('--cards-timeout', type=float, default=8)
    args = parser.parse_args()
    if any(not 1 <= t <= 30 for t in (args.button_timeout,args.panel_timeout,args.cards_timeout)):
        parser.error('Timeouts must be 1..30 seconds')
    if not 1 <= args.comment_limit <= 50 or not 0 <= args.max_scrolls <= 10:
        parser.error("limit must be 1..50; max-scrolls must be 0..10")
    output = args.output or Path(__file__).parent/"output"/utc_now().replace("+00:00", "Z").replace(":", "")
    if args.snapshot_file:
        if args.live:
            parser.error("Snapshot import cannot also run live")
        data = json.loads(args.snapshot_file.read_text(encoding="utf-8"))
        for video in data["videos"]:
            video["limit"] = args.comment_limit
        summary = write_artifacts(output, data["videos"], data["snapshots_by_video"], data.get("stop_reasons"))
        summary["validation_method"] = "RENDERED_DOM_SNAPSHOT_IMPORT"
        summary["validation_browser"] = data.get("validation_browser", "not recorded")
        summary["selenium_live_validated"] = False
        (output/"crawl_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    elif args.live:
        records = json.loads(args.videos_file.read_text(encoding="utf-8")) if args.videos_file else [POPEYES]
        videos = select_videos(records, args.video_limit)
        for video in videos:
            video["limit"] = args.comment_limit
        snapshots, reasons, attempted = {}, {}, []
        for video in videos:
            attempted.append(video)
            vid = video["video_id"]
            port = None
            def checkpoint(entries, actions):
                snapshots[vid] = list(entries)
                write_artifacts(output, attempted, snapshots, reasons)
            try:
                port = SeleniumDOMPort()
                entries, actions, reason = collect_video(port, video, args.comment_limit, args.max_scrolls,
                    checkpoint=checkpoint, button_timeout=args.button_timeout,
                    panel_timeout=args.panel_timeout, cards_timeout=args.cards_timeout)
                snapshots[vid], reasons[vid] = entries, reason
                (output/(vid+"_actions.json")).write_text(json.dumps(actions,ensure_ascii=False,indent=2),encoding="utf-8")
            except Exception as exc:
                reasons[vid] = "BROWSER_ERROR:"+type(exc).__name__
            finally:
                if port:
                    try:
                        port.close()
                    except Exception as exc:
                        reasons.setdefault(vid, "BROWSER_ERROR:"+type(exc).__name__)
                write_artifacts(output, attempted, snapshots, reasons)
            print(vid, reasons[vid], flush=True)
            if reasons[vid] in {"ACCESS_DENIED", "CHALLENGE", "LOGIN_OR_CONSENT_GATE"}:
                break  # No browser recreation/retry to work around an access gate.
    else:
        parser.error("Use --snapshot-file for an offline import or --live for one bounded DOM run")
    print("Results:", output.resolve())


if __name__ == "__main__":
    main()
