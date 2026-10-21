"""One first-page comment observation in an existing browser; never navigate."""
import base64
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from experiments.tiktok_access_benchmark.network import diagnostic, decision, clean
from experiments.tiktok_access_benchmark.access import dialog_kinds
from experiments.tiktok_comment_dom.extractor import SNAPSHOT_JS, CommentSession

TARGET='/@trasualeeteaq4/video/7638843509369474325'
STATE_JS=Path(__file__).with_name('browser_state.js').read_text(encoding='utf-8')

def response_stats(raw):
    result={'body_state':'UNAVAILABLE','application_status':None,'item_count':None}
    try:
        body=raw.get('body','')
        if len(body)>2_000_000: return dict(result,body_state='BODY_LIMIT')
        if raw.get('base64Encoded'): body=base64.b64decode(body,validate=True).decode('utf-8')
        payload=json.loads(body)
    except (ValueError,UnicodeError,TypeError): return dict(result,body_state='INVALID_JSON')
    if not isinstance(payload,dict): return dict(result,body_state='INVALID_SCHEMA')
    code=payload.get('status_code')
    if isinstance(code,str) and code.lstrip('-').isdigit(): code=int(code)
    if isinstance(code,bool) or not isinstance(code,int): code=None
    items=payload.get('comments')
    return dict(result,body_state='VALID_JSON' if isinstance(items,list) else 'INVALID_SCHEMA',
                application_status=code,item_count=len(items) if isinstance(items,list) else None)

def private_text(value):
    text=clean(value)
    text=re.sub(r'\b[^\s@]+@[^\s@]+\.[^\s@]+','[email redacted]',text)
    return re.sub(r'(?<!\w)\+?\d[\d .()-]{6,}\d(?!\w)','[number redacted]',text)

def capture_comments(driver, output=None, *, clock=time.monotonic, sleeper=time.sleep):
    url=urlparse(driver.current_url)
    if url.hostname!='www.tiktok.com' or url.path!=TARGET: raise ValueError('Expected video must already be open')
    if getattr(driver,'_first_comments_capture_done',False): raise ValueError('Capture already attempted in this session; no automatic retry')
    driver._first_comments_capture_done=True
    output=Path(output) if output else Path(__file__).with_name('output')/('comments_session_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
    output.mkdir(parents=True,exist_ok=True)
    evidence=[]; responses=[]; meta={}; pending={}; finished=set(); attempted=set()
    report={'video_id':'7638843509369474325','mode':'existing_session_first_page','timeout_seconds':20,
        'browser_created':False,'navigation_performed':False,'scroll_performed':False,'ui_clicks':0,
        'comments_visible':False,'extracted_comment_count':0,'comments':[],
        'network_errors':[],'comments_responses':responses,'outcome':'IN_PROGRESS',
        'network_scope':'Only events after capture boundary; already-rendered comments may produce no new response. No HTTP/application status is inferred from DOM.'}
    def checkpoint():
        report['network_errors']=[e for e in evidence if e['status']>=400]
        temp=output/'summary.json.tmp';temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(output/'summary.json')
    def drain():
        for event in driver.get_log('performance'):
            try:
                m=json.loads(event['message'])['message'];p=m.get('params',{});rid=p.get('requestId')
                if m['method']=='Network.requestWillBeSent':
                    if 'redirectResponse' in p and p['redirectResponse'].get('status',0)>=400:
                        evidence.append(diagnostic(p['redirectResponse'],p.get('type','Unknown'),'COMMENTS_CAPTURE'))
                    meta[rid]=dict(request=p.get('request',{}),initiator=p.get('initiator',{}))
                elif m['method']=='Network.responseReceived':
                    r=p['response'];data=meta.get(rid,{})
                    e=diagnostic(r,p.get('type','Unknown'),'COMMENTS_CAPTURE',data.get('request'),data.get('initiator'))
                    if e['status']>=400: evidence.append(e)
                    parsed=urlparse(r.get('url',''))
                    if parsed.hostname in ('www.tiktok.com','tiktok.com') and parsed.path.startswith('/api/comment/'):
                        e['endpoint_kind']='comments_api'
                        e.update(application_status=None,item_count=None)
                        pending[rid]=e;responses.append(e)
                elif m['method']=='Network.loadingFinished': finished.add(rid)
                elif m['method']=='Network.loadingFailed' and rid in pending: pending[rid]['body_state']='LOADING_FAILED'
            except (KeyError,ValueError,TypeError): continue
        for rid,e in pending.items():
            if rid not in finished or rid in attempted: continue
            attempted.add(rid)
            try: e.update(response_stats(driver.execute_cdp_cmd('Network.getResponseBody',{'requestId':rid})))
            except Exception: e['body_state']='BODY_CAPTURE_FAILED'
    started=clock();snapshot={}
    checkpoint()
    try:
        driver.execute_cdp_cmd('Network.enable',{})
        driver.get_log('performance')  # Establish boundary before any UI interaction; discard prior history.
        report['capture_started_at']=datetime.now(timezone.utc).isoformat()
        while True:
            drain()
            state=driver.execute_script('return ('+STATE_JS+')();')
            kinds=dialog_kinds(state.get('dialog_evidence',[]))
            gate='CHALLENGE' if state.get('challenge') or 'CHALLENGE' in kinds else 'AUTH_GATE' if any(k in kinds for k in ('LOGIN_GATE','CONSENT_GATE')) else 'ACCESS_DENIED' if state.get('access_denied') else decision(evidence,bool(state.get('comments')))
            if gate:
                report['outcome']=gate;driver.execute_cdp_cmd('Page.stopLoading',{});break
            current=urlparse(driver.current_url)
            if current.hostname!='www.tiktok.com' or current.path!=TARGET:
                report['outcome']='CONTEXT_CHANGED';break
            snapshot=driver.execute_script('return ('+SNAPSHOT_JS+')();')
            report['comments_visible']=bool(snapshot.get('comment_panel_present') or snapshot.get('cards'))
            if any(r.get('application_status') not in (None,0) for r in responses):
                report['outcome']='APPLICATION_ERROR';break
            if any(r['status']>=400 for r in responses):
                report['outcome']='COMMENTS_LOAD_ERROR';break
            if any(r.get('body_state') in ('INVALID_JSON','INVALID_SCHEMA','BODY_LIMIT','BODY_CAPTURE_FAILED','LOADING_FAILED') for r in responses):
                report['outcome']='COMMENTS_RESPONSE_UNREADABLE';break
            if snapshot.get('cards'):
                session=CommentSession('7638843509369474325',limit=50)
                snapshot['dialog_kinds']=kinds
                session.add(snapshot,datetime.now(timezone.utc).isoformat())
                report['comments']=[{'text':private_text(r['text']),'created_at_raw':private_text(r['created_at_raw']),
                                    'like_count':r['like_count'],'reply_count':r['reply_count']} for r in session.records.values()]
                report['extracted_comment_count']=len(report['comments']);report['outcome']='COMMENTS_RENDERED';break
            validated=[r for r in responses if r.get('body_state')=='VALID_JSON']
            if any(r.get('application_status') not in (None,0) for r in responses): report['outcome']='APPLICATION_ERROR';break
            if any(r['status']>=400 for r in responses): report['outcome']='COMMENTS_LOAD_ERROR';break
            if any(r.get('body_state')=='LOADING_FAILED' for r in responses): report['outcome']='COMMENTS_LOAD_ERROR';break
            if any(r['status']==200 and r.get('application_status')==0 and r.get('item_count')==0 for r in validated): report['outcome']='EMPTY_COMMENTS_RESPONSE';break
            if any(r['status']==200 and r.get('application_status')==0 and (r.get('item_count') or 0)>0 for r in validated): report['outcome']='COMMENTS_RESPONSE_WITHOUT_RENDER';break
            if clock()-started>=20: report['outcome']='TIMEOUT';break
            if not report['comments_visible'] and not report['ui_clicks']:
                from selenium.webdriver.common.by import By
                controls=driver.find_elements(By.XPATH,'//button[normalize-space(.)="Comments" or normalize-space(.)="Bình luận"]')
                controls += driver.find_elements(By.CSS_SELECTOR,'[data-e2e="comment-icon"]')
                control=next((c for c in controls if c.is_displayed()),None)
                if control is not None: control.click();report['ui_clicks']=1
            checkpoint();sleeper(.25)
        drain()
        # A gate arriving during DOM extraction takes priority, preserving observed DOM evidence.
        gate=decision(evidence,report['comments_visible'])
        if gate: report['outcome']=gate;driver.execute_cdp_cmd('Page.stopLoading',{})
        report['elapsed_seconds']=clock()-started
        report['response_observation']='OBSERVED' if responses else 'NOT_OBSERVED_DURING_CAPTURE'
        report['pagination_scope']='No scroll/load-more; current rendered top-level cards only. Prior pagination history is not established.'
    except Exception as exc:
        report['outcome']='CAPTURE_ERROR';report['error_type']=type(exc).__name__
    finally: checkpoint()
    print('Artifacts:',output)
    print('Outcome:',report['outcome'],'Comments:',report['extracted_comment_count'])
    return report
