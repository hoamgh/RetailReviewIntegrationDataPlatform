"""Single UI verification in the caller's existing Selenium context."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from experiments.tiktok_access_benchmark.network import diagnostic, decision
from experiments.tiktok_access_benchmark.access import dialog_kinds, validate_search_body
from experiments.tiktok_access_benchmark.session_comments import response_stats, private_text, STATE_JS, TARGET
from experiments.tiktok_comment_dom.extractor import SNAPSHOT_JS, CommentSession

QUERY='Leetea 169 Tôn Đản'
VIDEO_ID='7638843509369474325'

class StopFlow(Exception): pass

class NetworkCapture:
    def __init__(self, driver):
        self.driver=driver;self.phase='BEFORE_SEARCH';self.entries=[];self.meta={};self.pending={};self.finished=set();self.attempted=set()
        self.request_count=0
    def start(self):
        self.driver.execute_cdp_cmd('Network.enable',{})
        self.driver.get_log('performance')  # Boundary before UI actions, never retained as new traffic.
    def drain(self):
        for event in self.driver.get_log('performance'):
            try:
                m=json.loads(event['message'])['message'];p=m.get('params',{});rid=p.get('requestId')
                if m['method']=='Network.requestWillBeSent':
                    self.request_count+=1
                    if 'redirectResponse' in p: self.retain(p['redirectResponse'],p.get('type','Unknown'),rid,'REDIRECT_RESPONSE')
                    self.meta[rid]={'request':p.get('request',{}),'initiator':p.get('initiator',{})}
                    if p.get('type')=='Document':
                        e=diagnostic(dict(url=p.get('request',{}).get('url',''),status=0),'Document',self.phase,p.get('request'),p.get('initiator'))
                        e.update(event='DOCUMENT_REQUEST',timestamp=p.get('timestamp'));self.entries.append(e)
                elif m['method']=='Network.responseReceived': self.retain(p['response'],p.get('type','Unknown'),rid,'RESPONSE')
                elif m['method']=='Network.loadingFinished': self.finished.add(rid)
                elif m['method']=='Network.loadingFailed' and rid in self.pending: self.pending[rid]['body_state']='LOADING_FAILED'
            except (ValueError,KeyError,TypeError): continue
        for rid,e in self.pending.items():
            if rid not in self.finished or rid in self.attempted: continue
            self.attempted.add(rid)
            try:
                body=self.driver.execute_cdp_cmd('Network.getResponseBody',{'requestId':rid})
                e.update(response_stats(body) if e['endpoint_kind']=='comments_api' else validate_search_body(body,e['status']))
            except Exception: e['body_state']='BODY_CAPTURE_FAILED'
    def retain(self,response,kind,rid,event):
        meta=self.meta.get(rid,{})
        e=diagnostic(response,kind,self.phase,meta.get('request'),meta.get('initiator'))
        if kind=='Document' or e['status']>=400 or e['endpoint_kind'] in ('search_api','comments_api','video_detail_api'):
            e['event']=event;self.entries.append(e)
        if e['endpoint_kind'] in ('search_api','comments_api'):
            e.update(application_status=None,item_count=None);self.pending[rid]=e
    def gate(self, state):
        kinds=dialog_kinds(state.get('dialog_evidence',[]))
        if state.get('challenge') or 'CHALLENGE' in kinds: return 'CHALLENGE'
        if state.get('access_denied'): return 'ACCESS_DENIED'
        if any(k in kinds for k in ('LOGIN_GATE','CONSENT_GATE')): return 'AUTH_GATE'
        block=decision(self.entries,bool(state.get('video_detail') or state.get('comments')))
        if block: return block
        if any(e['status']>=400 and e['endpoint_kind'] in ('document','search_api','comments_api','video_detail_api') for e in self.entries): return 'REQUIRED_ENDPOINT_ERROR'
        if any(e.get('application_status') not in (None,0) for e in self.entries if e['endpoint_kind'] in ('search_api','comments_api')): return 'APPLICATION_ERROR'
        return None

def verify_ui(driver, output=None, *, clock=time.monotonic, sleeper=time.sleep):
    current=urlparse(driver.current_url)
    if current.hostname!='www.tiktok.com' or current.path!=TARGET: raise ValueError('Expected video must already be open in the current session')
    if getattr(driver,'_ui_verification_attempted',False): raise ValueError('UI verification already attempted; do not retry automatically')
    if len(driver.window_handles)!=1: raise ValueError('Expected exactly one existing tab')
    driver._ui_verification_attempted=True
    handle=driver.current_window_handle
    output=Path(output) if output else Path(__file__).with_name('output')/('ui_session_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
    output.mkdir(parents=True,exist_ok=True)
    capture=NetworkCapture(driver)
    report={'mode':'existing_session_ui_verification','video_id':VIDEO_ID,'query':QUERY,
        'browser_created':False,'driver_get_used':False,'refresh_used':False,'scroll_used':False,
        'steps':{k:{'outcome':'NOT_ATTEMPTED','elapsed_seconds':None} for k in ('search','video_detail','comments')},
        'network_evidence':capture.entries,'comments':[],'extracted_comment_count':0,'comment_clicks':0,'stop_reason':'IN_PROGRESS',
        'network_scope':'Events after capture boundary. Missing responses are unobserved, never HTTP 200 or an empty list. Phase is observation time.',
        'pacing_scope':'Single UI verification; no TikTok rate threshold or recovery time measured.'}
    active=None;step_start=None
    def checkpoint():
        report['request_count']=capture.request_count
        report['comments_responses']=[e for e in capture.entries if e['endpoint_kind']=='comments_api']
        report['documents_during_video_click']=[e for e in capture.entries if e['resource_type']=='Document' and e['phase']=='VIDEO_CLICK']
        report['video_click_document_request_observed']=any(e.get('event')=='DOCUMENT_REQUEST' for e in report['documents_during_video_click'])
        tmp=output/'summary.json.tmp';tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(output/'summary.json')
    def check():
        capture.drain()
        if len(driver.window_handles)!=1 or driver.current_window_handle!=handle: raise StopFlow('BROWSER_CONTEXT_CHANGED')
        state=driver.execute_script('return ('+STATE_JS+')();')
        gate=capture.gate(state)
        if gate:
            driver.execute_cdp_cmd('Page.stopLoading',{});raise StopFlow(gate)
        return state
    def wait(condition,reason):
        deadline=clock()+20
        while True:
            state=check()
            if condition(state): return state
            if clock()>=deadline: raise StopFlow(reason)
            sleeper(.25)
    def visible(elements): return [e for e in elements if e.is_displayed()]
    def picture(name):
        driver.execute_script("window.__uiCaptureStyles=Array.from(document.querySelectorAll('input,textarea,._wafchallengeid')).map(e=>[e,e.getAttribute('style')]);window.__uiCaptureStyles.forEach(([e])=>e.style.setProperty('visibility','hidden','important'));")
        try:
            if driver.save_screenshot(str(output/name)): report['steps'][active]['screenshot']=name
        finally:
            driver.execute_script("(window.__uiCaptureStyles||[]).forEach(([e,s])=>s===null?e.removeAttribute('style'):e.setAttribute('style',s));delete window.__uiCaptureStyles;")
    def begin(step,phase):
        nonlocal active,step_start
        active=step;step_start=clock();capture.phase=phase;report['steps'][step]['outcome']='IN_PROGRESS';checkpoint();check()
    def finish():
        check();report['steps'][active].update(outcome='SUCCESS',elapsed_seconds=clock()-step_start)
        picture(active+'.png');checkpoint()
    try:
        capture.start()
        report['capture_started_at']=datetime.now(timezone.utc).isoformat()
        begin('search','SEARCH_UI')
        inputs=visible(driver.find_elements(By.CSS_SELECTOR,'input[data-e2e="search-user-input"],input[type="search"],input[placeholder*="Search"],input[placeholder*="Tìm kiếm"]'))
        if not inputs: raise StopFlow('SEARCH_INPUT_NOT_FOUND')
        inputs[0].clear();inputs[0].send_keys(QUERY);check();inputs[0].send_keys(Keys.ENTER)
        def search_ready(state):
            url=urlparse(driver.current_url)
            return url.hostname=='www.tiktok.com' and url.path in ('/search','/search/video') and parse_qs(url.query).get('q')==[QUERY] and bool(state.get('search'))
        wait(search_ready,'SEARCH_RESULTS_TIMEOUT');finish()
        begin('video_detail','VIDEO_SELECTION')
        links=visible(driver.find_elements(By.CSS_SELECTOR,'a[href*="/video/'+VIDEO_ID+'"]'))
        links=[e for e in links if urlparse(e.get_attribute('href') or '').path==TARGET]
        if not links: raise StopFlow('TARGET_VIDEO_NOT_VISIBLE_IN_RESULTS')
        if links[0].get_attribute('target') not in (None,'','_self'): raise StopFlow('TARGET_WOULD_OPEN_ANOTHER_TAB')
        check();capture.phase='VIDEO_CLICK';links[0].click()
        wait(lambda state:urlparse(driver.current_url).hostname=='www.tiktok.com' and urlparse(driver.current_url).path==TARGET and bool(state.get('video_detail')),'VIDEO_DETAIL_TIMEOUT')
        finish()
        begin('comments','COMMENTS_UI')
        state=check()
        if not state.get('comments'):
            tabs=visible(driver.find_elements(By.XPATH,'//button[normalize-space(.)="Comments" or normalize-space(.)="Bình luận"]'))
            icons=visible(driver.find_elements(By.CSS_SELECTOR,'[data-e2e="comment-icon"]'))
            controls=tabs or icons
            if controls: check();controls[0].click();report['comment_clicks']=1
        def comments_ready(state):
            return bool(state.get('comments')) or any(e.get('body_state')=='VALID_JSON' and e.get('application_status')==0 for e in capture.entries if e['endpoint_kind']=='comments_api')
        state=wait(comments_ready,'COMMENT_CARDS_OR_RESPONSE_TIMEOUT')
        snapshot=driver.execute_script('return ('+SNAPSHOT_JS+')();')
        snapshot['dialog_kinds']=dialog_kinds(state.get('dialog_evidence',[]))
        report['comments_visible']=bool(snapshot.get('comment_panel_present') or snapshot.get('cards'))
        if snapshot.get('cards'):
            session=CommentSession(VIDEO_ID,limit=50);session.add(snapshot,datetime.now(timezone.utc).isoformat())
            report['comments']=[{'text':private_text(r['text']),'created_at_raw':private_text(r['created_at_raw']),'like_count':r['like_count'],'reply_count':r['reply_count']} for r in session.records.values()]
            report['extracted_comment_count']=len(report['comments']);report['comments_result']='DOM_COMMENTS'
        else:
            responses=[e for e in capture.entries if e['endpoint_kind']=='comments_api' and e.get('body_state')=='VALID_JSON' and e.get('application_status')==0 and e['status']==200]
            report['comments_result']='EMPTY_RESPONSE' if responses and all(e['item_count']==0 for e in responses) else 'RESPONSE_WITHOUT_RENDER'
            if report['comments_result']!='EMPTY_RESPONSE': raise StopFlow('COMMENTS_RESPONSE_WITHOUT_RENDER')
        finish();report['stop_reason']='COMPLETED'
    except StopFlow as exc:
        report['stop_reason']=str(exc)
        if active:
            report['steps'][active].update(outcome='FAILED',reason=str(exc),elapsed_seconds=clock()-step_start)
            try: picture(active+'_stopped.png')
            except Exception: pass
    except Exception as exc:
        report['stop_reason']='CAPTURE_ERROR';report['error_type']=type(exc).__name__
        if active: report['steps'][active].update(outcome='FAILED',elapsed_seconds=clock()-step_start)
    finally: checkpoint()
    print('Artifacts:',output)
    print('Steps:',{k:v['outcome'] for k,v in report['steps'].items()},'Comments:',report['extracted_comment_count'])
    return report
