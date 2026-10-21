"""Sequential, bounded TikTok access measurement. Offline replay by default."""
from __future__ import annotations
import argparse
import json
import statistics
import time
import sys
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.tiktok_access_benchmark.access import (
    dialog_kinds, retry_after_seconds, validate_search_body, search_reason, record_cooldown, remaining_cooldown, automatic_interstitial,
)
STATE_JS = Path(__file__).with_name('browser_state.js').read_text(encoding='utf-8')

SURFACES = ('homepage', 'search', 'video_detail', 'comments')
GATES = {'ACCESS_DENIED', 'RATE_LIMITED', 'CHALLENGE', 'AUTH_GATE', 'UNKNOWN_403'}

from experiments.tiktok_access_benchmark.network import diagnostic as response_diagnostic, decision, clean

def diagnosis(observation):
    outcome = classify(observation)
    if observation.get('wait_failure')=='INTERSTITIAL_TIMEOUT':
        return {'reason':'AUTOMATIC_INTERSTITIAL_NOT_RESOLVED_WITHIN_BUDGET','action':'STOP_NO_RETRY',
                'scope':'Please-wait page did not complete normally; no HTTP rate-limit cause established'}
    if outcome in GATES:
        return {'reason': outcome, 'action': 'STOP_NO_RETRY',
                'scope': 'Observed request gate; inspect network_evidence before attributing to target endpoint.'}
    if 'search_responses' in observation and not observation.get('error'):
        return {'reason': search_reason(observation['search_responses'], observation.get('render_status')=='READY'),
                'action':'CONTINUE_WITH_PACING' if outcome=='SUCCESS' else 'BACKOFF_NEXT_OPERATION_ONLY',
                'scope':'HTTP, application status and DOM are measured separately'}
    if outcome == 'SUCCESS':
        return {'reason': 'VISIBLE_TARGET_ELEMENTS', 'action': 'CONTINUE_WITH_PACING'}
    dom = observation.get('dom_state', {})
    reason = 'TARGET_NOT_RENDERED_WITHIN_BUDGET'
    if observation.get('error'):
        reason = 'BROWSER_ERROR'
    elif observation.get('search_responses') is not None:
        reason = search_reason(observation['search_responses'], observation.get('render_status') == 'READY')
    elif dom.get('target_path_matches') is False:
        reason = 'NAVIGATION_TARGET_MISMATCH'
    elif dom.get('empty_state'):
        reason = 'VISIBLE_EMPTY_OR_UNAVAILABLE_STATE'
    elif observation.get('wait_failure'):
        reason = observation['wait_failure']
    return {'reason': reason, 'action': 'BACKOFF_NEXT_OPERATION_ONLY',
            'scope': 'No rate-limit cause established'}

def classify(observation):
    if 'network_evidence' in observation:
        block = decision(observation['network_evidence'], observation.get('render_status') == 'READY')
        if block: return block
        statuses = []
    else:
        statuses = observation.get('http_statuses', [])
    if 429 in statuses:
        return 'RATE_LIMITED'  # HTTP evidence only; 403 does not imply throttling.
    if 403 in statuses and not observation.get('access_denied'):
        return 'UNKNOWN_403'
    if observation.get('access_denied'):
        return 'ACCESS_DENIED'
    if observation.get('challenge'):
        return 'CHALLENGE'
    if 401 in statuses or observation.get('auth_gate'):
        return 'AUTH_GATE'
    if observation.get('error'):
        return 'ERROR'
    if any(r.get('application_error') for r in observation.get('search_responses', [])):
        return 'APPLICATION_ERROR'
    if 'search_responses' in observation:
        reason = search_reason(observation['search_responses'], observation.get('render_status')=='READY')
        if reason == 'SEARCH_ZERO_VIDEOS':
            return 'EMPTY_SEARCH_RESPONSE'
        if reason in ('SEARCH_RESPONSE_NOT_OBSERVED','SEARCH_RESPONSE_NOT_VALIDATED','SEARCH_EMPTY_BODY','SEARCH_HTML_INSTEAD_OF_JSON','SEARCH_INVALID_JSON'):
            return 'SEARCH_RESPONSE_UNVALIDATED'
    return 'SUCCESS' if observation.get('render_status') == 'READY' else 'RENDER_UNAVAILABLE'

def percentile(values, quantile):
    if not values:
        return None
    values = sorted(values)
    position = (len(values)-1)*quantile
    low = int(position)
    return values[low] + (values[min(low+1, len(values)-1)]-values[low])*(position-low)

def summarize(rows, elapsed, mode, stop):
    result = {'mode': mode, 'stop_reason': stop, 'elapsed_seconds': elapsed,
              'recovery_policy': 'No gate retry or automatic circuit reset; recovery after gates is unmeasured.',
              'request_count_scope': 'Observed browser requestWillBeSent events; not logical operations or server total.',
              'surfaces': {}}
    cooldowns = [e['retry_after_seconds'] for r in rows for e in r.get('network_evidence', [])
                 if isinstance(e.get('retry_after_seconds'), int)]
    result['minimum_cooldown_seconds'] = max(cooldowns) if cooldowns else None
    result['cooldown_action'] = 'Do not start a new run before this cooldown; no automatic retry.'
    result['request_counts'] = {k:sum(r.get('request_counts',{}).get(k,0) for r in rows) for k in ('document','detail','comments','media','ancillary','search','unknown')}
    result['pacing_scope'] = 'Experimental configuration; not TikTok safe thresholds.'
    result['measured_totals'] = {
        'operations_attempted':len(rows), 'successful_operations':sum(r['outcome']=='SUCCESS' for r in rows),
        'observed_browser_requests':sum(r.get('request_count',0) for r in rows),
        'http_403_responses':sum(r.get('http_statuses',[]).count(403) for r in rows),
        'http_429_responses':sum(r.get('http_statuses',[]).count(429) for r in rows),
        'observed_requests_per_measured_second':sum(r.get('request_count',0) for r in rows)/elapsed if elapsed>0 else None,
        'first_gate_operation':next((r['operation'] for r in rows if r['outcome'] in GATES),None),
        'server_rate_limit_threshold':None,
        'threshold_evidence':'Not measured: a finite browser smoke is not a server threshold experiment.'}
    for surface in SURFACES:
        group = [r for r in rows if r['surface'] == surface]
        latency = [r['latency_seconds'] for r in group]
        success = sum(r['outcome'] == 'SUCCESS' for r in group)
        result['surfaces'][surface] = {
            'attempts': len(group), 'successes': success,
            'success_rate': success/len(group) if group else None,
            'throughput_operations_per_second': len(group)/elapsed if elapsed > 0 else None,
            'successful_operations_per_second': success/elapsed if elapsed > 0 else None,
            'latency_mean_seconds': statistics.mean(latency) if latency else None,
            'latency_p50_seconds': percentile(latency, .5), 'latency_p95_seconds': percentile(latency, .95),
            'request_count': sum(r.get('request_count', 0) for r in group),
            'http_403_responses': sum(r.get('http_statuses', []).count(403) for r in group),
            'http_429_responses': sum(r.get('http_statuses', []).count(429) for r in group),
            'outcomes': {key: sum(r['outcome'] == key for r in group) for key in sorted({r['outcome'] for r in group})},
            'render_statuses': {key: sum(r['render_status'] == key for r in group) for key in sorted({r['render_status'] for r in group})},
            'diagnostics': [r.get('diagnosis') for r in group],
            'network_evidence': [e for r in group for e in r.get('network_evidence', [])],
            'search_responses': [e for r in group for e in r.get('search_responses', [])],
            'p95_sample_warning': 'One sample is not a latency distribution' if len(group) < 2 else None,
        }
    # A later successful operation only measures recovery for transient failures, not a retry.
    result['transient_recoveries'] = sum(a['outcome'] not in GATES | {'SUCCESS'} and b['outcome'] == 'SUCCESS'
                                           and a['surface'] == b['surface'] for a, b in zip(rows, rows[1:]))
    return result

def validate_plan(plan):
    if not isinstance(plan, list) or not 1 <= len(plan) <= 9:
        raise ValueError('Plan must contain 1..9 sequential operations')
    for op in plan:
        if op.get('surface') not in SURFACES:
            raise ValueError('Unknown surface')
        url = urlparse(op.get('url', ''))
        if url.scheme != 'https' or url.hostname != 'www.tiktok.com' or url.username or url.password or url.fragment:
            raise ValueError('Only public HTTPS TikTok page URLs allowed')
        if op['surface'] == 'homepage':
            if url.path!='/' or url.query:
                raise ValueError('Homepage requires official root URL without query')
        elif op['surface'] == 'search':
            query = parse_qs(url.query)
            if url.path != '/search' or set(query) != {'q'} or len(query['q']) != 1:
                raise ValueError('Search URL must contain only q')
        elif url.query or not __import__('re').fullmatch(r'/@[\w.]+/video/\d+', url.path):
            raise ValueError('Detail/comments require public video URL without query')
    return plan

def run(plan, observer, output, *, mode='offline_replay', delay=10, pacing=None, sleeper=time.sleep, cooldown_file=None):
    validate_plan(plan)
    if not math.isfinite(delay) or not 10 <= delay <= 300:
        raise ValueError('Minimum spacing is ten seconds')
    pacing = pacing or {}
    if any(k not in SURFACES or not math.isfinite(v) or not 10 <= v <= 300 for k,v in pacing.items()):
        raise ValueError('Per-operation pacing must be 10..300 seconds')
    if mode=='live_browser' and cooldown_file and remaining_cooldown(cooldown_file):
        raise ValueError('Persisted Retry-After cooldown has not expired')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows, stop, failures, elapsed = [], 'COMPLETED', 0, 0.0
    def checkpoint():
        report = summarize(rows, elapsed, mode, stop)
        (output/'observations.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
        (output/'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        (output/'report.md').write_text('# TikTok access benchmark\n\nMode: '+mode+'\n\nStop: '+stop+
            '\n\n```json\n'+json.dumps(report, indent=2)+'\n```\n', encoding='utf-8')
    checkpoint()
    try:
        for index, op in enumerate(plan):
            if index:
                base = pacing.get(op['surface'], delay)
                wait = min(600, base * 2**failures)
                sleeper(wait)
                elapsed += wait
            started = time.monotonic()
            try:
                observed = observer(op)
            except Exception as exc:
                observed = {'error': type(exc).__name__, 'render_status': 'ERROR', 'http_statuses': [], 'request_count': 0}
            latency = observed.get('latency_seconds', time.monotonic()-started)
            if not isinstance(latency, (float, int)) or latency < 0:
                raise ValueError('Invalid observation latency')
            # Allowlist prevents URLs, cookies, page text and credentials entering artifacts.
            row = {k: observed[k] for k in ('request_count', 'http_statuses', 'render_status', 'error',
                    'challenge', 'auth_gate', 'access_denied', 'network_capture_complete') if k in observed}
            row['network_evidence'] = []
            for e in observed.get('network_evidence', [])[:100]:
                entry = {k:clean(e.get(k)) for k in ('hostname','pathname','path','method','resource_type','phase','endpoint_kind','host_scope','content_type','body_state')}
                for key in ('path','pathname'):
                    entry[key]='/'.join(clean(segment) for segment in str(e.get(key,'')).split('/'))
                entry.update(status=e.get('status'), retry_after_seconds=e.get('retry_after_seconds'),
                             body_excerpt=clean(e.get('body_excerpt')) if e.get('body_excerpt') is not None else None,
                             initiator={k:clean(v) for k,v in e.get('initiator',{}).items()})
                for key in ('path','pathname'):
                    if not entry[key].startswith('/'): entry[key]='[redacted]'
                if entry['host_scope'] not in ('tiktok','other'): entry['host_scope']='unknown'
                row['network_evidence'].append(entry)
            row['network_errors'] = [e for e in row['network_evidence'] if isinstance(e.get('status'),int) and e['status']>=400]
            row['request_counts'] = observed.get('request_counts', {})
            dom = observed.get('dom_state', {})
            row['dom_state'] = {k: dom.get(k) if isinstance(dom.get(k), bool) else None for k in (
                'comment_control_present', 'empty_state', 'playback_error', 'target_path_matches','interstitial_pending','surface_render_observed')}
            row['dom_state']['ready_state'] = dom.get('ready_state') if dom.get('ready_state') in {'loading', 'interactive', 'complete'} else None
            row['dom_state']['comment_card_count'] = dom.get('comment_card_count') if isinstance(dom.get('comment_card_count'), int) else None
            row['dom_state']['dialog_kinds'] = [d for d in dom.get('dialog_kinds', []) if d in {
                'LOGIN_GATE','CONSENT_GATE','CHALLENGE','COMMENT_PANEL','SHARE_DIALOG','UNKNOWN_DIALOG'}][:10]
            row['dom_state']['challenge_markers'] = [d for d in dom.get('challenge_markers', []) if d in {
                '#captcha_container','.captcha-verify-container','._wafchallengeid','[id*="captcha-verify"]'}]
            row['dom_state']['challenge_text_signals'] = [s for s in (dom.get('challenge_text_signals') or []) if s in {
                'verify you are human','verifying your browser','security check','security verification',
                'complete the captcha','please wait','access denied'}]
            row['dom_state']['challenge_details'] = [{
                'selector':d.get('selector'), 'tag':d.get('tag') if d.get('tag') in {'DIV','SPAN','BODY','MAIN','SECTION','IFRAME','P'} else 'OTHER',
                **{k:d.get(k) if isinstance(d.get(k),int) else None for k in ('width','height','descendant_count')}
            } for d in (dom.get('challenge_details') or []) if d.get('selector') in row['dom_state']['challenge_markers']][:4]
            row['gate_screenshot'] = observed.get('gate_screenshot') if observed.get('gate_screenshot') in {
                'gate_homepage.png','gate_search.png','gate_video_detail.png','gate_comments.png'} else None
            gate_evidence = observed.get('gate_evidence') or {}
            row['gate_evidence'] = {
                'phase':gate_evidence.get('phase') if gate_evidence.get('phase') in {'NAVIGATION','AFTER_COMMENT_ICON','AFTER_COMMENTS_TAB'} else None,
                'first_seen_seconds':gate_evidence.get('first_seen_seconds') if isinstance(gate_evidence.get('first_seen_seconds'),(int,float)) else None}
            row['actions'] = [a for a in observed.get('actions', []) if a in {
                'NAVIGATION_COMPLETED', 'NAVIGATION_ERROR', 'COMMENT_ICON_CLICKED', 'COMMENTS_TAB_CLICKED', 'REUSED_VIDEO_PAGE'}]
            if 'search_responses' in observed:
                row['search_responses'] = [{k:v for k,v in r.items() if k in {'http_status', 'application_status', 'item_count', 'video_count','body_bytes'} and (v is None or isinstance(v, int))}
                    | {'application_error': bool(r.get('application_error')), 'body_state': r.get('body_state') if r.get('body_state') in {
                        'UNAVAILABLE','BODY_LIMIT','INVALID_JSON','INVALID_SCHEMA','VALID_JSON','BODY_CAPTURE_FAILED'} else 'UNAVAILABLE',
                       'body_format':r.get('body_format') if r.get('body_format') in {'UNKNOWN','EMPTY','HTML','JSON_LIKE','OTHER_TEXT','JSON'} else 'UNKNOWN'}
                    for r in observed['search_responses'][:10]]
            row['wait_failure'] = observed.get('wait_failure') if observed.get('wait_failure') in {
                'INTERSTITIAL_TIMEOUT','HOMEPAGE_TIMEOUT','SEARCH_RESULTS_TIMEOUT','VIDEO_DETAIL_TIMEOUT','COMMENT_BUTTON_TIMEOUT','COMMENT_CARDS_TIMEOUT',
                'UNKNOWN_DIALOG_PRESENT','SHARE_DIALOG_PRESENT','NAVIGATION_TARGET_MISMATCH'} else None
            row['pacing_before_seconds'] = wait if index else 0
            row['diagnosis'] = diagnosis(dict(observed, dom_state=row['dom_state']))
            row.update(surface=op['surface'], operation=index+1, latency_seconds=latency,
                       render_status=observed.get('render_status', 'UNKNOWN'), outcome=classify(observed))
            row['operation_outcome'] = row['outcome']
            elapsed += latency
            rows.append(row)
            failures = 0 if row['outcome'] == 'SUCCESS' else failures+1
            if row['outcome'] in GATES:
                stop = 'CIRCUIT_OPEN:'+row['outcome']
            elif failures >= 2:
                stop = 'CIRCUIT_OPEN:CONSECUTIVE_FAILURES'
            with (output/'progress.log').open('a', encoding='utf-8') as log:
                log.write(f"operation={index+1} surface={op['surface']} outcome={row['outcome']}\n")
            checkpoint()
            if mode=='live_browser' and cooldown_file and row['outcome']=='RATE_LIMITED':
                seconds = [e['retry_after_seconds'] for e in row['network_evidence'] if isinstance(e.get('retry_after_seconds'),int)]
                if seconds:
                    record_cooldown(cooldown_file,max(seconds))
            if stop != 'COMPLETED':
                break
    except BaseException:
        stop = 'INTERRUPTED'
        checkpoint()
        raise
    return summarize(rows, elapsed, mode, stop)

class BrowserObserver:
    """One standard SeleniumBase browser; waits read real responses and never generate API requests."""
    def __init__(self, *, navigation_timeout=20, homepage_timeout=10, search_timeout=10, detail_timeout=10,
                 comment_button_timeout=8, comment_cards_timeout=10, capture_dir=None, profile_dir=None, driver_path=None):
        from selenium import webdriver
        self.timeouts = {'homepage':homepage_timeout, 'search':search_timeout, 'video_detail':detail_timeout,
                         'comment_button':comment_button_timeout, 'comments':comment_cards_timeout}
        if any(not math.isfinite(v) or not 1 <= v <= 30 for v in [navigation_timeout, *self.timeouts.values()]):
            raise ValueError('Timeouts must be finite and between 1 and 30 seconds')
        from experiments.tiktok_access_benchmark.session_manager import (
            TikTokSessionManager, standard_browser_factory)
        factory = standard_browser_factory(profile_dir=profile_dir, driver_path=driver_path)
        self.backend = 'seleniumbase-uc'
        self.lifecycle = TikTokSessionManager(factory, worker_id='tiktok-ui')
        # Acquire once only. Session loss/gates must never create a replacement browser.
        managed = self.lifecycle.acquire('tiktok-ui', 1)
        self.session, self.driver = managed.session, managed.driver
        self.last_video = None
        self.capture_dir = Path(capture_dir) if capture_dir else None
        try:
            self.driver.set_page_load_timeout(navigation_timeout)
            self.driver.set_script_timeout(5)
            self.driver.execute_cdp_cmd('Network.enable', {})
            self.driver.execute_cdp_cmd('Network.setBlockedURLs', {'urls': ['*.mp4*', '*.m3u8*', '*.ts?*']})
        except Exception:
            self.lifecycle.close()
            raise

    def __call__(self, op):
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.common.by import By
        timeouts = {'homepage':10, **getattr(self, 'timeouts', {'search':10, 'video_detail':10, 'comment_button':8, 'comments':10})}
        started = time.monotonic()
        statuses, evidence, actions, search_responses = [], [], [], []
        requests, navigation_error, wait_failure = 0, None, None
        pending, completed, attempted_bodies = {}, set(), set()
        snapshot, phase = {}, 'NAVIGATION'
        gate_evidence = {}
        metadata = getattr(self, 'request_metadata', {})
        self.request_metadata = metadata
        error_bodies = {}
        counts = dict.fromkeys(('document','detail','comments','media','ancillary','search','unknown'), 0)

        def retain(response, resource_type, request_id=None):
            status = int(response.get('status', 0))
            statuses.append(status)
            meta = metadata.get(request_id, {})
            entry = response_diagnostic(response, resource_type, phase, meta.get('request'), meta.get('initiator'))
            if status in (401,403,429): error_bodies[request_id] = entry
            if (status >= 400 or entry['endpoint_kind'] != 'ancillary') and len(evidence) < 100:
                evidence.append(entry)

        def drain():
            nonlocal requests
            for entry in self.driver.get_log('performance'):
                try:
                    message = json.loads(entry['message'])['message']
                    params = message.get('params', {})
                    request_id = params.get('requestId')
                    if message['method'] == 'Network.requestWillBeSent':
                        requests += 1
                        metadata[request_id] = {'request':params.get('request',{}), 'initiator':params.get('initiator',{})}
                        category = response_diagnostic(params.get('request',{}),params.get('type','Unknown'),phase)['endpoint_kind']
                        group = {'search_api':'search','video_detail_api':'detail','comments_api':'comments'}.get(category,category)
                        counts[group if group in counts else 'unknown'] += 1
                        if 'redirectResponse' in params:
                            retain(params['redirectResponse'], params.get('type', 'Unknown'))
                    elif message['method'] == 'Network.responseReceived':
                        response = params['response']
                        retain(response, params.get('type', 'Unknown'), request_id)
                        url = urlparse(response.get('url', ''))
                        if (op['surface'] == 'search' and url.hostname == 'www.tiktok.com'
                                and url.path == '/api/search/general/full/' and len(pending) < 10):
                            pending[request_id] = int(response.get('status', 0))
                    elif message['method'] == 'Network.loadingFinished':
                        completed.add(request_id)
                except (KeyError, ValueError, TypeError):
                    continue  # Malformed events never become gate evidence.
            for rid, item in error_bodies.items():
                if rid not in completed or item['body_state'] != 'NOT_CAPTURED': continue
                try:
                    raw = self.driver.execute_cdp_cmd('Network.getResponseBody', {'requestId':rid})
                    import base64
                    body = raw.get('body','')
                    if raw.get('base64Encoded'): body = base64.b64decode(body).decode('utf-8',errors='replace')
                    item['body_excerpt'] = clean(body[:65536])
                    item['body_state'] = 'CAPTURED'
                except Exception:
                    item['body_state'] = 'CAPTURE_FAILED'
            for request_id, status in pending.items():
                if request_id not in completed or request_id in attempted_bodies:
                    continue
                attempted_bodies.add(request_id)
                try:
                    body = self.driver.execute_cdp_cmd('Network.getResponseBody', {'requestId':request_id})
                    search_responses.append(validate_search_body(body, status))
                    try:
                        import re
                        payload = json.loads(body.get('body',''))
                        videos = getattr(self,'discovered_videos',[])
                        for record in payload.get('data',[]):
                            item = record.get('item',{})
                            vid = str(item.get('id','')); author = item.get('author',{}).get('uniqueId','')
                            if vid.isdigit() and re.fullmatch(r'[\w.]+', author) and vid not in {v['video_id'] for v in videos}:
                                videos.append({'video_id':vid,'url':f'https://www.tiktok.com/@{author}/video/{vid}'})
                        self.discovered_videos = videos[:5]
                    except (ValueError,TypeError,AttributeError): pass
                except Exception:
                    search_responses.append(dict(validate_search_body({}, status), body_state='BODY_CAPTURE_FAILED'))

        def gate():
            return (decision(evidence, bool(snapshot.get(op['surface'])))
                    or snapshot.get('challenge') or snapshot.get('auth_gate') or snapshot.get('access_denied'))

        def refresh():
            nonlocal snapshot
            drain()
            if 429 in statuses:
                self.driver.execute_cdp_cmd('Page.stopLoading', {})
                return
            snapshot = self.driver.execute_script('return ('+STATE_JS+')();')
            kinds = dialog_kinds(snapshot.get('dialog_evidence', []))
            snapshot['dialog_kinds'] = kinds
            snapshot['challenge'] = bool(snapshot.get('challenge') or 'CHALLENGE' in kinds)
            snapshot['interstitial_pending'] = automatic_interstitial(snapshot)
            if snapshot['interstitial_pending']:
                snapshot['challenge'] = False  # Observe normal page lifecycle; never solve or click a challenge.
            snapshot['auth_gate'] = any(k in kinds for k in ('LOGIN_GATE','CONSENT_GATE'))
            target = urlparse(op['url'])
            actual = urlparse(self.driver.current_url)
            snapshot['target_path_matches'] = (actual.hostname == target.hostname and
                (actual.path == target.path or (op['surface'] == 'search' and actual.path == '/search/video')
                 or (op['surface']=='homepage' and actual.path=='/foryou')))
            if not snapshot['target_path_matches']:
                snapshot[op['surface']] = False
            if gate():
                if not gate_evidence:
                    gate_evidence.update(phase=phase,first_seen_seconds=time.monotonic()-started)
                self.driver.execute_cdp_cmd('Page.stopLoading', {})

        def wait_for(condition, seconds, failure):
            nonlocal wait_failure
            def ready(_):
                refresh()
                return gate() or condition()
            try:
                WebDriverWait(self.driver, seconds, poll_frequency=.3).until(ready)
            except TimeoutException:
                wait_failure = failure

        rendered = False
        try:
            # Preserve tail events from the prior page: a delayed gate must still stop the session.
            drain()
            if not gate():
                reuse = (op['surface'] in ('video_detail','comments') and getattr(self,'last_video',None) == op['url']
                         and self.driver.current_url == op['url'])
                if reuse:
                    actions.append('REUSED_VIDEO_PAGE')
                else:
                    try:
                        self.driver.get(op['url'])
                        actions.append('NAVIGATION_COMPLETED')
                    except Exception as exc:
                        navigation_error = type(exc).__name__
                        actions.append('NAVIGATION_ERROR')
                if op['surface'] == 'comments':
                    wait_for(lambda: snapshot.get('comments') or snapshot.get('comment_control_present')
                             or any(k in snapshot.get('dialog_kinds',[]) for k in ('UNKNOWN_DIALOG','SHARE_DIALOG')),
                             timeouts['comment_button'], 'COMMENT_BUTTON_TIMEOUT')
                    if not gate() and not snapshot.get('comments') and not wait_failure:
                        kinds = snapshot.get('dialog_kinds', [])
                        if 'UNKNOWN_DIALOG' in kinds or 'SHARE_DIALOG' in kinds:
                            wait_failure = 'UNKNOWN_DIALOG_PRESENT' if 'UNKNOWN_DIALOG' in kinds else 'SHARE_DIALOG_PRESENT'
                        else:
                            controls = self.driver.find_elements(By.CSS_SELECTOR, '[data-e2e="comment-icon"]')
                            for control in controls:
                                if control.is_displayed():
                                    control.click()
                                    phase = 'AFTER_COMMENT_ICON'
                                    actions.append('COMMENT_ICON_CLICKED')
                                    break
                            tab_clicked = False
                            def cards_or_tab():
                                nonlocal tab_clicked, phase, wait_failure
                                if snapshot.get('comments'):
                                    return True
                                kinds = snapshot.get('dialog_kinds', [])
                                if 'UNKNOWN_DIALOG' in kinds or 'SHARE_DIALOG' in kinds:
                                    wait_failure = 'UNKNOWN_DIALOG_PRESENT' if 'UNKNOWN_DIALOG' in kinds else 'SHARE_DIALOG_PRESENT'
                                    return True
                                if not tab_clicked:
                                    tabs = self.driver.find_elements(By.XPATH,
                                        '//button[normalize-space(.)="Comments" or normalize-space(.)="Bình luận"]')
                                    for tab in tabs:
                                        if tab.is_displayed():
                                            tab.click()
                                            tab_clicked = True
                                            phase = 'AFTER_COMMENTS_TAB'
                                            actions.append('COMMENTS_TAB_CLICKED')
                                            break
                                return False
                            wait_for(cards_or_tab, timeouts['comments'], 'COMMENT_CARDS_TIMEOUT')
                else:
                    wait_for(lambda: (bool(snapshot.get(op['surface'])) and
                             (op['surface']!='search' or bool(search_responses)))
                             or (op['surface']=='search' and any(r.get('application_error') for r in search_responses)), timeouts[op['surface']],
                             'SEARCH_RESULTS_TIMEOUT' if op['surface']=='search' else 'HOMEPAGE_TIMEOUT' if op['surface']=='homepage' else 'VIDEO_DETAIL_TIMEOUT')
            drain()
            if snapshot.get('interstitial_pending'):
                snapshot['challenge'] = True
                wait_failure = 'INTERSTITIAL_TIMEOUT'
                if not gate_evidence:
                    gate_evidence.update(phase=phase,first_seen_seconds=time.monotonic()-started)
            if gate():
                if not gate_evidence:
                    gate_evidence.update(phase=phase,first_seen_seconds=time.monotonic()-started)
                self.driver.execute_cdp_cmd('Page.stopLoading', {})
            snapshot['surface_render_observed'] = bool(snapshot.get(op['surface']))
            rendered = bool(snapshot.get(op['surface']))
            if op['surface'] == 'video_detail' and rendered and not navigation_error:
                self.last_video = op['url']
            elif op['surface'] != 'comments':
                self.last_video = None
        except Exception as exc:
            navigation_error = type(exc).__name__
            rendered = False
            try:
                drain()
            except Exception:
                pass
        result = {k: bool(snapshot.get(k)) for k in ('challenge','auth_gate','access_denied')}
        result.update(http_statuses=statuses, request_count=requests,
            render_status='READY' if rendered else 'UNAVAILABLE', latency_seconds=time.monotonic()-started,
            network_capture_complete=False, network_evidence=evidence, request_counts=counts, actions=actions,
            wait_failure=wait_failure,
            gate_evidence=gate_evidence,
            dom_state={k:snapshot.get(k) for k in ('ready_state','comment_card_count','comment_control_present',
                        'empty_state','playback_error','target_path_matches','dialog_kinds','challenge_markers',
                        'challenge_details','challenge_text_signals','interstitial_pending','surface_render_observed')})
        if gate() and getattr(self,'capture_dir',None):
            try:
                self.capture_dir.mkdir(parents=True,exist_ok=True)
                # Mask user-input values and challenge identifiers before a local screenshot.
                self.driver.execute_script("""
document.querySelectorAll('input,textarea,._wafchallengeid').forEach(e=>{
 e.style.setProperty('color','transparent','important');
 e.style.setProperty('text-shadow','none','important');
});
""")
                name='gate_'+op['surface']+'.png'
                if self.driver.save_screenshot(str(self.capture_dir/name)):
                    result['gate_screenshot']=name
            except Exception:
                result['gate_screenshot']=None
        if op['surface']=='search':
            for request_id,status in pending.items():
                if request_id not in attempted_bodies:
                    search_responses.append(validate_search_body({},status))
            result['search_responses'] = search_responses
        if navigation_error:
            result['error'] = navigation_error
        return result

    def is_alive(self):
        return self.lifecycle.is_alive()

    def lifecycle_summary(self):
        return dict(self.lifecycle.summary(), backend=self.backend, uc=True)

    def close(self):
        lifecycle = getattr(self, 'lifecycle', None)
        if lifecycle is not None:
            lifecycle.close()
        else:
            self.session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--replay', type=Path)
    choice.add_argument('--live', action='store_true')
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--capture-gate',action='store_true',help='Save one local gate screenshot with input/identifier text masked')
    parser.add_argument('--pacing', type=float, default=10)
    for name in SURFACES:
        parser.add_argument('--'+name.replace('_','-')+'-pacing', type=float)
    parser.add_argument('--navigation-timeout', type=float, default=20)
    parser.add_argument('--homepage-timeout', type=float, default=10)
    parser.add_argument('--search-timeout', type=float, default=10)
    parser.add_argument('--detail-timeout', type=float, default=10)
    parser.add_argument('--comment-button-timeout', type=float, default=8)
    parser.add_argument('--comment-cards-timeout', type=float, default=10)
    args = parser.parse_args()
    pacing = {name:getattr(args,name+'_pacing') for name in SURFACES if getattr(args,name+'_pacing') is not None}
    if not math.isfinite(args.pacing) or not 10 <= args.pacing <= 300 or any(not math.isfinite(v) or not 10 <= v <= 300 for v in pacing.values()):
        parser.error('Pacing must be 10..300 seconds')
    if any(not math.isfinite(v) or not 1 <= v <= 30 for v in (args.navigation_timeout,args.homepage_timeout,args.search_timeout,args.detail_timeout,args.comment_button_timeout,args.comment_cards_timeout)):
        parser.error('Timeouts must be 1..30 seconds')
    output = args.output or Path(__file__).parent/'output'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    if args.replay:
        data = json.loads(args.replay.read_text(encoding='utf-8'))
        observations = iter(data['observations'])
        def replay(_):
            try:
                return next(observations)
            except StopIteration:
                raise ValueError('Replay observations exhausted')
        if len(data['plan']) != len(data['observations']):
            parser.error('Replay must provide one observation per planned operation')
        run(data['plan'], replay, output, delay=args.pacing, pacing=pacing, sleeper=lambda _: None)
    else:
        if not args.plan:
            parser.error('--live requires --plan')
        plan = validate_plan(json.loads(args.plan.read_text(encoding='utf-8')))
        if plan[0]['surface']!='homepage':
            parser.error('Live plans must visit the official homepage first')
        cooldown_file = Path(__file__).parent/'output'/'cooldown.json'
        try:
            remaining = remaining_cooldown(cooldown_file)
        except (ValueError,KeyError,TypeError):
            parser.error('Invalid cooldown state; refusing live access')
        if remaining:
            parser.error(f'Retry-After cooldown active: wait at least {remaining} seconds; browser not opened')
        browser = BrowserObserver(navigation_timeout=args.navigation_timeout, homepage_timeout=args.homepage_timeout, search_timeout=args.search_timeout,
            detail_timeout=args.detail_timeout, comment_button_timeout=args.comment_button_timeout,
            comment_cards_timeout=args.comment_cards_timeout,capture_dir=output if args.capture_gate else None)
        try:
            run(plan, browser, output, mode='live_browser', delay=args.pacing, pacing=pacing, cooldown_file=cooldown_file)
        finally:
            browser.close()
    print('Artifacts:', output.resolve())

if __name__ == '__main__':
    main()
