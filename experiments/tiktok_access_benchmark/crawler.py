"""Bounded TikTok UI crawler. DOM first, one browser/session, durable outputs."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from experiments.tiktok_access_benchmark.ui_flow import NetworkCapture, STATE_JS
from experiments.tiktok_access_benchmark.access import dialog_kinds
from experiments.tiktok_access_benchmark.session_comments import private_text
from experiments.tiktok_comment_dom.extractor import SNAPSHOT_JS, CommentSession
from experiments.tiktok_comment_dom.probe import SeleniumDOMPort

FIELDS=('video_id','video_url','query','captured_at','comment_id','author','text','time','likes','reply_count','source','dedup_key','dedup_key_kind','record_type','parent_comment_id','parent_session_key','parent_link_kind')
GATE_ROOT=Path(__file__).with_name('output')/'session_gates'
HIT_JS="""const e=arguments[0],r=e.getBoundingClientRect();
const x=Math.max(0,r.left)+(Math.min(innerWidth,r.right)-Math.max(0,r.left))/2;
const y=Math.max(0,r.top)+(Math.min(innerHeight,r.bottom)-Math.max(0,r.top))/2;
const inside=r.width>0&&r.height>0&&r.right>0&&r.bottom>0&&r.left<innerWidth&&r.top<innerHeight;
const hit=inside?document.elementFromPoint(x,y):null;
const link=e.closest('a[href*="/video/"]');const hitLink=hit?.closest('a[href*="/video/"]');
const cardSelector='[data-e2e="search_top-item"],[data-e2e="search-video-item"],[data-e2e="search-card"]';
const card=e.closest(cardSelector),hitCard=hit?.closest(cardSelector);
const sameCard=!!card&&card===hitCard&&!!link&&!!hitLink&&new URL(link.href).pathname===new URL(hitLink.href).pathname;
return {in_viewport:inside,unobscured:!!hit&&(hit===e||e.contains(hit)||!!link&&hitLink===link||sameCard),
blocking_tag:hit&&!e.contains(hit)?hit.tagName:null,same_link:!!link&&link===hitLink};"""
OVERLAY_CONTROLS_JS=r"""return Array.from(document.querySelectorAll('[data-e2e="browse-close"],[data-e2e="modal-close"],[aria-label="Close"]')).filter(e=>{
  const visible=n=>!!(n && n.getClientRects().length && getComputedStyle(n).visibility!=='hidden');
  if(!visible(e))return false;
  if(e.matches('[data-e2e="browse-close"]') && /^\/@[\w.]+\/video\/\d+$/.test(location.pathname))return true;
  const modal=e.closest('[role="dialog"],[aria-modal="true"]');
  return !!(modal && visible(modal) && Array.from(modal.querySelectorAll('video,[data-e2e="video-desc"]')).some(visible));
});"""
OVERLAY_JS=OVERLAY_CONTROLS_JS.replace('return Array.from(', 'return !!Array.from(',1).replace('});','}).length;',1)
LINKS_JS="""return Array.from(document.querySelectorAll('a[href*="/video/"]')).filter(e=>e.getClientRects().length).map(e=>({href:e.href}));"""

class Gate(Exception): pass
class UIFailure(Exception): pass

def utc(): return datetime.now(timezone.utc).isoformat()

def session_gate_file(driver):
    identity=getattr(driver,'session_id',None)
    return GATE_ROOT/(hashlib.sha256(identity.encode()).hexdigest()+'.json') if identity else None

def session_fingerprint(driver):
    identity=getattr(driver,'session_id',None)
    return hashlib.sha256(identity.encode()).hexdigest() if identity else None

def candidates(links):
    result=[];seen=set()
    for link in links:
        url=urlparse(link.get('href',''))
        match=re.fullmatch(r'/@[\w.]+/video/(\d+)',url.path)
        if url.hostname!='www.tiktok.com' or url.scheme!='https' or not match or match[1] in seen: continue
        seen.add(match[1]);result.append({'video_id':match[1],'video_url':'https://www.tiktok.com'+url.path})
    return result

def output_record(row, video, query):
    # Retain public comment content; omit all raw DOM attributes and signed URLs.
    comment_id=private_text(row.get('comment_id_candidate')) or None
    author=row.get('author_username') or row.get('author_display_name') or row.get('author_id_candidate')
    text=private_text(row.get('text'))
    raw_time=private_text(row.get('created_at_raw')) or None
    fallback=hashlib.sha256(json.dumps([video['video_id'],author,text,raw_time],ensure_ascii=False).encode()).hexdigest()
    if row.get('is_reply'):
        fallback=hashlib.sha256(json.dumps(['REPLY',row.get('parent_comment_id') or row.get('parent_session_key'),fallback],ensure_ascii=False).encode()).hexdigest()
    return dict(video_id=video['video_id'],video_url=video['video_url'],query=query,captured_at=row['observed_at'],
        comment_id=comment_id or None,author=private_text(author) or None,text=text,time=raw_time,
        likes=row.get('like_count'),reply_count=row.get('reply_count'),source='DOM',
        record_type=row.get('record_type','TOP_LEVEL'),parent_comment_id=row.get('parent_comment_id'),
        parent_session_key=row.get('parent_session_key'),parent_link_kind=row.get('parent_link_kind'),
        dedup_key=('reply:' if row.get('is_reply') else '')+str(comment_id) if comment_id else fallback,
        dedup_key_kind='OBSERVED_COMMENT_ID' if comment_id else 'COMPOSITE_FALLBACK')

class Storage:
    def __init__(self,path,query,config):
        self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True)
        self.comments=[];self.keys=set();self.videos=[]
        self.comment_repository=None
        self.summary={'query':query,'captured_at':utc(),'configuration':config,'status':'RUNNING',
            'rate_limit_threshold':'UNKNOWN','cooldown':'UNKNOWN','videos_attempted':0,'videos_opened':0,'comments_count':0,
            'pacing_note':'Initial experiment configuration, not TikTok safe thresholds.',
            'response_note':'Missing browser response is UNOBSERVED, never HTTP 200 or an empty response.',
            'fallback_key_note':'Composite key is a session/video dedup substitute, not a source comment ID.'}
    def add(self,rows):
        for row in rows:
            key=(row['video_id'],row['dedup_key_kind'],row['dedup_key'])
            limit=self.summary.get('configuration',{}).get('max_comments_per_video',20)
            if key in self.keys:
                for i,old in enumerate(self.comments):
                    if (old['video_id'],old['dedup_key_kind'],old['dedup_key'])==key:self.comments[i]=row;break
            if key not in self.keys and sum(r['video_id']==row['video_id'] for r in self.comments)<limit:self.keys.add(key);self.comments.append(row)
    def persist_collector(self,session,**context):
        if self.comment_repository is None:raise ValueError('Comment persistence is not configured')
        return self.comment_repository.persist(session,**context)
    def restore(self,summary):
        self.summary=summary;self.videos=summary.get('videos',[])
        path=self.path/'comments.jsonl'
        if path.exists():self.add([json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()])
    def checkpoint(self,network):
        self.summary.update(videos_attempted=len(self.videos),videos_opened=sum(v.get('opened',False) for v in self.videos),
            comments_count=len(self.comments),videos=self.videos,request_count=network.request_count,network_evidence=network.entries)
        files={'comments.jsonl':''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in self.comments),
               'videos.jsonl':''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in self.videos),
               'summary.json':json.dumps(self.summary,ensure_ascii=False,indent=2)}
        for name,text in files.items():
            tmp=self.path/(name+'.tmp');tmp.write_text(text,encoding='utf-8');tmp.replace(self.path/name)
        tmp=self.path/'comments.csv.tmp'
        with tmp.open('w',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=FIELDS);writer.writeheader();writer.writerows(self.comments)
        tmp.replace(self.path/'comments.csv')
        with (self.path/'progress.log').open('a',encoding='utf-8') as handle:
            handle.write(json.dumps({'at':utc(),'status':self.summary['status'],'videos':len(self.videos),'comments':len(self.comments)})+'\n')

class UIPort:
    def __init__(self,driver,output,timeout=20,clock=time.monotonic,sleeper=time.sleep):
        self.driver=driver;self.output=Path(output);self.timeout=timeout;self.clock=clock;self.sleep=sleeper
        self.network=NetworkCapture(driver);self.network.start();self.handle=driver.current_window_handle
    def state(self):
        self.network.drain()
        if len(self.driver.window_handles)!=1 or self.driver.current_window_handle!=self.handle:raise Gate('BROWSER_CONTEXT_CHANGED')
        state=self.driver.execute_script('return ('+STATE_JS+')();')
        kinds=dialog_kinds(state.get('dialog_evidence',[]))
        reason='CHALLENGE' if state.get('challenge') or 'CHALLENGE' in kinds else 'ACCESS_DENIED' if state.get('access_denied') else 'AUTH_GATE' if any(k in kinds for k in ('LOGIN_GATE','CONSENT_GATE')) else None
        for e in self.network.entries:
            status=e.get('status',0);required=e['endpoint_kind'] in ('document','search_api','comments_api','video_detail_api')
            if status==429:reason=reason or 'RATE_LIMITED'
            elif status in (401,403) and required:reason=reason or ('ACCESS_DENIED' if status==403 else 'AUTH_GATE')
            elif status==403 and e['endpoint_kind']=='related_items_api':
                from experiments.tiktok_access_benchmark.network import decision
                if decision([e],bool(state.get('video_detail') or state.get('comments'))) is not None:reason=reason or 'UNKNOWN_403'
            elif status in (401,403) and (e['endpoint_kind']=='unknown' or e['resource_type'] in ('Fetch','XHR') or re.search(r'(?i)captcha|challenge|secsdk|verify|auth',e.get('pathname',''))):reason=reason or 'UNKNOWN_403'
        if reason:
            if reason=='AUTH_GATE' and ('LOGIN_GATE' in kinds or any(e.get('status')==401 and e.get('endpoint_kind') in ('document','search_api','comments_api','video_detail_api') for e in self.network.entries)):
                from experiments.tiktok_access_benchmark.auth_session import record_login_gate
                record_login_gate(self.driver,self.output)
            self.driver.execute_cdp_cmd('Page.stopLoading',{});raise Gate(reason)
        # Known static media/font/style errors are telemetry, not denial of the main operation.
        return state
    def wait(self,predicate,reason):
        deadline=self.clock()+self.timeout
        while True:
            state=self.state()
            if predicate(state):return state
            if self.clock()>=deadline:raise UIFailure(reason)
            self.sleep(.25)
    def screenshot(self,name):
        self.driver.execute_script("window.__crawlerStyles=Array.from(document.querySelectorAll('input,textarea,._wafchallengeid')).map(e=>[e,e.getAttribute('style')]);window.__crawlerStyles.forEach(([e])=>e.style.setProperty('visibility','hidden','important'));")
        try:self.driver.save_screenshot(str(self.output/name))
        finally:self.driver.execute_script("(window.__crawlerStyles||[]).forEach(([e,s])=>s===null?e.removeAttribute('style'):e.setAttribute('style',s));delete window.__crawlerStyles;")
    def elements(self,by,selector):return [e for e in self.driver.find_elements(by,selector) if e.is_displayed()]
    def click(self,element,video_open=False):
        self.state()
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});",element)
        self.wait(lambda _:self.driver.execute_script(HIT_JS,element).get('unobscured'),'UI_ELEMENT_OCCLUDED')
        # Hit-test recognizes a thumbnail child or the same link/card; never dispatch a JS click.
        if video_open:self.last_open_at=self.clock()
        element.click();self.state()
    def close_overlay(self):
        self.state()
        if not self.driver.execute_script(OVERLAY_JS):
            # A standalone video/feed route is not a modal. Search can use its UI directly.
            return
        controls=self.driver.execute_script(OVERLAY_CONTROLS_JS)
        if controls:self.click(controls[0])
        else:self.driver.find_element(By.TAG_NAME,'body').send_keys(Keys.ESCAPE)
        self.wait(lambda _:not self.driver.execute_script(OVERLAY_JS),'OVERLAY_CLOSE_TIMEOUT')
    def search_ready(self,query):
        url=urlparse(self.driver.current_url)
        return url.path in ('/search','/search/video') and parse_qs(url.query).get('q')==[query] and not self.driver.execute_script(OVERLAY_JS) and bool(candidates(self.driver.execute_script(LINKS_JS)))
    def search(self,query):
        self.network.phase='SEARCH_UI';self.close_overlay()
        self.last_search_reused=self.search_ready(query)
        if self.last_search_reused:
            self.screenshot('search.png');return candidates(self.driver.execute_script(LINKS_JS))
        inputs=self.elements(By.CSS_SELECTOR,'input[data-e2e="search-user-input"],input[type="search"],input[placeholder*="Search"],input[placeholder*="Tìm kiếm"]')
        if not inputs:
            controls=self.elements(By.CSS_SELECTOR,'[data-e2e="nav-search"],[data-e2e="search-icon"],[aria-label="Search"]')
            if controls:self.click(controls[0])
            self.wait(lambda _:bool(self.elements(By.CSS_SELECTOR,'input[data-e2e="search-user-input"],input[type="search"],input[placeholder*="Search"],input[placeholder*="Tìm kiếm"]')),'SEARCH_INPUT_TIMEOUT')
            inputs=self.elements(By.CSS_SELECTOR,'input[data-e2e="search-user-input"],input[type="search"],input[placeholder*="Search"],input[placeholder*="Tìm kiếm"]')
        self.state();inputs[0].clear();inputs[0].send_keys(query);inputs[0].send_keys(Keys.ENTER)
        self.wait(lambda _:self.search_ready(query),'SEARCH_RESULTS_TIMEOUT')
        self.screenshot('search.png');return candidates(self.driver.execute_script(LINKS_JS))
    def open_video(self,video,query):
        self.close_overlay()
        if not self.search_ready(query):self.search(query)
        self.network.phase='VIDEO_CLICK'
        links=self.elements(By.CSS_SELECTOR,'a[href*="/video/'+video['video_id']+'"]')
        links=[e for e in links if urlparse(e.get_attribute('href') or '').path==urlparse(video['video_url']).path]
        if not links and getattr(self,'observed_card_navigation',False):
            self.driver.execute_script('window.scrollTo(0,0);')
            for scan in range(4):
                self.state()
                links=self.elements(By.CSS_SELECTOR,'a[href*="/video/'+video['video_id']+'"]')
                links=[e for e in links if urlparse(e.get_attribute('href') or '').path==urlparse(video['video_url']).path]
                if links or scan==3:break
                self.driver.execute_script('window.scrollBy(0,Math.max(400,innerHeight*0.8));')
                self.sleep(.5)
        if not links:raise UIFailure('VIDEO_NOT_IN_RESULTS')
        if links[0].get_attribute('target') not in (None,'','_self'):raise UIFailure('VIDEO_LINK_OPENS_NEW_TAB')
        self.click(links[0],video_open=True)
        self.wait(lambda s:urlparse(self.driver.current_url).path==urlparse(video['video_url']).path and s.get('video_detail'),'VIDEO_DETAIL_TIMEOUT')
    def snapshot(self):
        state=self.state();snapshot=self.driver.execute_script('return ('+SNAPSHOT_JS+')();')
        snapshot['dialog_kinds']=dialog_kinds(state.get('dialog_evidence',[]))
        # Use visible structural state; a hidden legacy marker must not block extraction.
        snapshot['challenge_present']=bool(state.get('challenge'))
        return snapshot
    def comments(self,video,limit,max_scrolls,on_batch=lambda rows:None):
        self.network.phase='COMMENTS_UI';start=len(self.network.entries);state=self.state()
        if not state.get('comments'):
            controls=self.elements(By.XPATH,'//button[starts-with(normalize-space(.),"Comments") or starts-with(normalize-space(.),"Bình luận")]')
            controls=controls or self.elements(By.CSS_SELECTOR,'[data-e2e="comment-icon"]')
            if controls:self.click(controls[0])
        def ready(s):
            observed=[e for e in self.network.entries[start:] if e['endpoint_kind']=='comments_api']
            if any(e.get('application_status') not in (None,0) or e.get('status',0)>=400 or e.get('body_state')=='LOADING_FAILED' for e in observed):raise UIFailure('COMMENTS_LOAD_ERROR')
            return s.get('comments') or any(e.get('status')==200 and e.get('application_status')==0 and e.get('item_count')==0 for e in observed)
        self.wait(ready,'COMMENT_CARDS_TIMEOUT')
        session=CommentSession(video['video_id'],limit=limit);last_scroll=self.clock();stop='FIRST_PAGE'
        for index in range(max_scrolls+1):
            previous=len(session.records);session.add(self.snapshot(),utc())
            on_batch(list(session.records.values()))
            if len(session.records)>=limit:stop='LIMIT';break
            if index and len(session.records)==previous:stop='NO_GROWTH';break
            if index==max_scrolls:stop='SCROLL_BUDGET';break
            if last_scroll is not None:self.pause(max(0,5-(self.clock()-last_scroll)))
            self.state();last_scroll=self.clock()
            scroll=SeleniumDOMPort.scroll_comments(self)
            if not scroll.get('ok') or scroll.get('before')==scroll.get('after'):stop='END_OR_NO_SAFE_CONTAINER';break
            try:
                self.wait(lambda _:len({r.get('comment_id_candidate') or r.get('session_dedup_fingerprint') for r in self._normalized_cards(video) }-set(session.records))>0,'COMMENT_SCROLL_NO_GROWTH')
            except UIFailure:stop='NO_GROWTH_WITHIN_TIMEOUT';break
        self.state()
        responses=[e for e in self.network.entries[start:] if e['endpoint_kind']=='comments_api']
        return list(session.records.values()),{'comments_response':'OBSERVED' if responses else 'UNOBSERVED','comments_responses':responses,'collection_stop':stop}
    def _normalized_cards(self,video):
        from experiments.tiktok_comment_dom.extractor import normalize_card
        return [normalize_card(card,video['video_id'],utc()) for card in self.snapshot().get('cards',[])]
    def pause(self,seconds):
        deadline=self.clock()+seconds
        while self.clock()<deadline:self.state();self.sleep(min(.5,deadline-self.clock()))

def crawl(driver,query,output=None,*,max_videos=5,comment_limit=20,max_scrolls=2,timeout=20,clock=time.monotonic,sleeper=time.sleep,bootstrap=False,resume_checkpoint=None,manual_verified=False,restart_checkpoint=None):
    if not 1<=max_videos<=5 or not 1<=comment_limit<=20 or not 0<=max_scrolls<=4 or not math.isfinite(timeout) or not 1<=timeout<=30:raise ValueError('Limits: 1..5 videos, 1..20 comments, 0..4 scrolls, timeout 1..30s')
    if len(driver.window_handles)!=1:raise ValueError('Use one existing tab; close DevTools window')
    prior=None;current_resume=None
    if resume_checkpoint or restart_checkpoint:
        if resume_checkpoint and restart_checkpoint:raise ValueError('Choose resume or restart, not both')
        if bootstrap and not restart_checkpoint:raise ValueError('Resume never bootstraps or creates a browser')
        output=Path(resume_checkpoint or restart_checkpoint);prior=json.loads((output/'summary.json').read_text(encoding='utf-8'))
        if prior['query']!=query:raise ValueError('Checkpoint query mismatch')
        if prior.get('status')=='STOPPED_GATE' and (prior.get('stop_reason')!='CHALLENGE' or not restart_checkpoint and not manual_verified):
            raise ValueError('Challenge resume requires explicit manual verification; other access gates cannot be retried')
        expected=prior.get('session_fingerprint')
        if not restart_checkpoint and expected and expected!=session_fingerprint(driver):raise ValueError('Resume requires the original browser session')
        interrupted=[v for v in prior.get('videos',[]) if v.get('outcome') in ('STOPPED_GATE','IN_PROGRESS','UI_ERROR')]
        if not interrupted:raise ValueError('No unfinished video in checkpoint; no automatic repeat')
        current_resume=interrupted[-1]
        url=urlparse(driver.current_url)
        if not restart_checkpoint and (url.hostname!='www.tiktok.com' or url.path!=urlparse(current_resume['video_url']).path):
            raise ValueError('Keep the checkpoint video open; resume will not navigate to it')
    gate_file=session_gate_file(driver)
    if (not prior or restart_checkpoint) and (getattr(driver,'_tiktok_crawler_gate',False) or gate_file and gate_file.exists()):raise ValueError('Session stopped at gate; do not reset or retry')
    output=Path(output) if output else Path(__file__).with_name('output')/('crawl_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
    config={'browsers':1,'tabs':1,'concurrency':1,'max_videos':max_videos,'max_comments_per_video':comment_limit,'max_comment_scrolls':max_scrolls,'video_spacing_seconds':15,'comment_load_spacing_seconds':5,'timeout_seconds':timeout}
    store=Storage(output,query,config)
    if prior:
        store.restore(prior)
        # Preserve a historical snapshot before modifying the resumed checkpoint.
        (output/('before_resume_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'.json')).write_text(json.dumps(prior,ensure_ascii=False,indent=2),encoding='utf-8')
    port=UIPort(driver,output,timeout,clock,sleeper);last_open=None
    if prior:
        store.summary.setdefault('resume_history',[]).append({'captured_at':utc(),'manual_verified':manual_verified,
            'session_continuity':'NEW_SESSION_EXPLICIT_RESTART' if restart_checkpoint else 'MATCHED_FINGERPRINT' if prior.get('session_fingerprint') else 'UNVERIFIABLE_LEGACY_CHECKPOINT',
            'scope':'One checkpoint video via UI Search' if restart_checkpoint else 'Current video only; no Search or video navigation'})
        # Old network responses remain historical, not current-operation success evidence.
        store.summary.setdefault('network_history',[]).append({'captured_at':utc(),'request_count':prior.get('request_count',0),'entries':prior.get('network_evidence',[])})
    store.summary['session_fingerprint']=session_fingerprint(driver)
    store.summary['status']='RUNNING';store.checkpoint(port.network)
    try:
        # Challenge must be gone before any UI operation, extraction, or authorization to resume.
        port.state()
        if bootstrap:
            port.network.phase='HOMEPAGE'
            driver.get('https://www.tiktok.com/')
            port.wait(lambda s:s.get('homepage'),'HOMEPAGE_TIMEOUT')
        if prior:
            selected=[current_resume];store.summary['resume_history'][-1]['challenge_clear_observed']=True
            if restart_checkpoint:
                discovered=port.search(query)
                if not any(v['video_id']==current_resume['video_id'] for v in discovered):raise UIFailure('CHECKPOINT_VIDEO_NOT_IN_RESULTS')
        else:
            before=clock();discovered=port.search(query);selected=discovered[:max_videos]
            store.summary['selected_videos']=selected
            store.summary['search']={'outcome':'SUCCESS','elapsed_seconds':clock()-before,'videos_discovered':len(discovered),'videos_selected':len(selected),'reused_rendered_results':getattr(port,'last_search_reused',None)}
        if not selected:raise UIFailure('NO_VIDEO_RESULTS')
        for video in selected:
            item=video if prior else dict(video,query=query,captured_at=utc(),opened=False,outcome='IN_PROGRESS',comments_count=0)
            if not prior:store.videos.append(item)
            item['outcome']='IN_PROGRESS';begin=clock()
            try:
                if prior and not restart_checkpoint:
                    port.state();item.update(opened=True,resumed_existing_page=True)
                else:
                    if last_open is not None:port.pause(max(0,15-(clock()-last_open)))
                    last_open=clock();before=clock();port.open_video(video,query)
                    item.update(opened=True,open_seconds=clock()-before)
                port.screenshot(video['video_id']+('_resumed_detail.png' if prior else '_detail.png'))
                def preserve(rows):
                    store.add([output_record(r,video,query) for r in rows]);item['comments_count']=sum(r['video_id']==video['video_id'] for r in store.comments);store.checkpoint(port.network)
                before=clock();rows,telemetry=port.comments(video,comment_limit,max_scrolls,preserve)
                store.add([output_record(r,video,query) for r in rows]);item.update(telemetry,comments_count=sum(r['video_id']==video['video_id'] for r in store.comments),comments_seconds=clock()-before,outcome='SUCCESS' if rows or item.get('comments_count') else 'EMPTY_CONFIRMED_RESPONSE')
                item.pop('error',None)
                port.screenshot(video['video_id']+('_resumed_comments.png' if prior else '_comments.png'))
            except UIFailure as exc:
                item.update(outcome='UI_ERROR',error=str(exc))
                try:port.screenshot(video['video_id']+'_error.png')
                except Exception:pass
            except Gate:raise
            except Exception as exc:
                item.update(outcome='UI_ERROR',error=type(exc).__name__)
                # A dead browser will terminate at the next state check rather than create a replacement.
            finally:
                last_open=getattr(port,'last_open_at',last_open)
                item['elapsed_seconds']=clock()-begin;store.checkpoint(port.network)
            port.state()
        store.summary['status']=('RESUME_UI_ERROR' if current_resume.get('outcome')=='UI_ERROR' else 'RESUMED_CURRENT_VIDEO') if prior else 'COMPLETED_WITH_ERRORS' if any(v['outcome']=='UI_ERROR' for v in store.videos) else 'COMPLETED'
        if prior:store.summary.pop('stop_reason',None)
    except Gate as exc:
        driver._tiktok_crawler_gate=True;store.summary.update(status='STOPPED_GATE',stop_reason=str(exc))
        if gate_file:
            gate_file.parent.mkdir(parents=True,exist_ok=True)
            gate_file.write_text(json.dumps({'reason':str(exc),'captured_at':utc(),'policy':'No automatic reset or retry; recovery UNKNOWN'}),encoding='utf-8')
        if store.videos and store.videos[-1]['outcome']=='IN_PROGRESS':store.videos[-1].update(outcome='STOPPED_GATE',error=str(exc))
        try:port.screenshot('gate.png')
        except Exception:pass
    except UIFailure as exc:
        store.summary.update(status='UI_ERROR',stop_reason=str(exc),search={'outcome':'FAILED','reason':str(exc)})
        try:
            port.screenshot('search_error.png')
            store.summary['ui_diagnostic']={'pathname':urlparse(driver.current_url).path,
                'video_overlay':bool(driver.execute_script(OVERLAY_JS))}
        except Exception:
            store.summary['diagnostic_capture']='UNAVAILABLE'
    except BaseException as exc:
        store.summary.update(status='INTERRUPTED' if isinstance(exc,KeyboardInterrupt) else 'BROWSER_ERROR',error_type=type(exc).__name__)
        if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
    finally:store.checkpoint(port.network)
    print('Artifacts:',output);print('Videos:',store.summary['videos_attempted'],'Opened:',store.summary['videos_opened'],'Comments:',store.summary['comments_count'],'Status:',store.summary['status'])
    return store.summary

def hold_session(browser,report,checkpoint):
    """Block on explicit terminal input; never poll or retry while a human verifies."""
    if report.get('status')!='STOPPED_GATE':return
    from urllib.parse import urlparse
    port=urlparse(browser.driver.service.service_url).port
    print('Browser/session retained. ChromeDriver port:',port)
    print('After manual verification use resume from another PowerShell terminal, or type RESUME here. Type CLOSE to close this browser.')
    while True:
        try:command=input('Manual action [RESUME/CLOSE]: ').strip().upper()
        except (EOFError,KeyboardInterrupt):
            print('Controller terminal ended; browser session will close. No automatic resume performed.');return
        if command=='CLOSE':return
        if command!='RESUME':continue
        if report.get('stop_reason')!='CHALLENGE':
            print('Only a manually cleared challenge can resume; other gates remain stopped.');continue
        try:
            report=crawl(browser.driver,report['query'],resume_checkpoint=checkpoint,manual_verified=True,max_scrolls=0,max_videos=1)
        except ValueError as exc:print(str(exc));continue
        if report.get('status')!='STOPPED_GATE':return

def wait_for_manual_login(browser, input_fn=None, output=None):
    from experiments.tiktok_access_benchmark.auth_session import manual_auth
    return manual_auth(browser,input_fn=input_fn,output=output)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--query');parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--max-videos',type=int,default=5);parser.add_argument('--comment-limit',type=int,default=20);parser.add_argument('--max-scrolls',type=int);parser.add_argument('--output',type=Path)
    parser.add_argument('--attach-port',type=int,help='Reuse exactly one existing local ChromeDriver session; never create or close its browser')
    parser.add_argument('--resume',type=Path,help='Resume only the already-open unfinished checkpoint video, never Search or navigation')
    parser.add_argument('--restart-from-checkpoint',type=Path,help='Explicit new browser, UI Search and one unfinished checkpoint video; preserve and dedup data')
    parser.add_argument('--manual-verified',action='store_true',help='Explicit human confirmation after manually completing challenge; DOM must also confirm it is gone')
    parser.add_argument('--chromedriver',type=Path,help='Explicit local ChromeDriver executable; no automatic download or driver replacement')
    parser.add_argument('--persistent-profile',action='store_true',help='Reuse one dedicated local browser profile; never rotate profiles')
    parser.add_argument('--manual-login',action='store_true',help='Wait on the homepage for manual login before Search')
    args=parser.parse_args()
    if args.manual_login and not args.persistent_profile:parser.error('--manual-login requires --persistent-profile')
    if args.attach_port and args.chromedriver:parser.error('--chromedriver cannot be used with attach')
    if args.chromedriver and not args.chromedriver.is_file():parser.error('Configured ChromeDriver executable does not exist')
    if args.attach_port and (args.persistent_profile or args.manual_login):parser.error('Profile/login options require a standalone browser')
    if args.restart_from_checkpoint:
        if args.resume or args.attach_port:parser.error('Restart creates a new session; cannot combine with resume or attach')
        prior=json.loads((args.restart_from_checkpoint/'summary.json').read_text(encoding='utf-8'))
        if prior.get('status')=='STOPPED_GATE' and prior.get('stop_reason')!='CHALLENGE':parser.error('Cannot restart an access-denied checkpoint')
        args.query=prior['query'];args.max_videos=1
    if args.resume:
        if not args.attach_port:parser.error('--resume requires --attach-port; no browser will be created')
        prior=json.loads((args.resume/'summary.json').read_text(encoding='utf-8'))
        if args.query and args.query!=prior['query']:parser.error('Checkpoint query mismatch')
        args.query=prior['query']
    elif not args.query:parser.error('--query is required for a new crawl')
    max_scrolls=args.max_scrolls if args.max_scrolls is not None else 0 if args.resume else 2
    if args.attach_port:
        from experiments.tiktok_access_benchmark.existing_session import attach
        try:driver=attach(args.attach_port)
        except ValueError as exc:parser.error(str(exc))
        crawl(driver,args.query,args.output,max_videos=args.max_videos,comment_limit=args.comment_limit,max_scrolls=max_scrolls,resume_checkpoint=args.resume,manual_verified=args.manual_verified)
        return
    from experiments.tiktok_access_benchmark.benchmark import BrowserObserver
    profile=None
    if args.persistent_profile:
        import os
        local=os.environ.get('LOCALAPPDATA')
        if not local:parser.error('LOCALAPPDATA is required for the dedicated private profile')
        profile=Path(local)/'TikTokCrawler'/'Profile'
    output=args.output or Path(__file__).with_name('output')/('crawl_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
    output=args.restart_from_checkpoint or output
    output.mkdir(parents=True,exist_ok=True)
    try:
        browser=BrowserObserver(profile_dir=profile,driver_path=args.chromedriver)
    except Exception as exc:
        cause=exc.__cause__ or exc
        startup={'status':'BROWSER_STARTUP_FAILED','exception_type':type(cause).__name__,
                 'navigation_attempted':False,'driver_selection':'EXPLICIT' if args.chromedriver else 'SELENIUM_DEFAULT'}
        (output/'startup.json').write_text(json.dumps(startup,indent=2)+'\n',encoding='utf-8')
        print('Artifacts:',output);print('Status: BROWSER_STARTUP_FAILED');return
    try:
        # Official homepage bootstrap only. All Search/video navigation thereafter is UI.
        if args.manual_login and not wait_for_manual_login(browser,output=output):return
        report=crawl(browser.driver,args.query,output,max_videos=args.max_videos,comment_limit=args.comment_limit,max_scrolls=max_scrolls,bootstrap=not args.manual_login,restart_checkpoint=args.restart_from_checkpoint)
        hold_session(browser,report,output)
    finally:
        browser.close()
        # Owner-only telemetry; attached sessions stay under their original owner.
        if 'output' in locals():
            output.mkdir(parents=True, exist_ok=True)
            lifecycle_path=output/'browser_lifecycle.json'
            temporary=lifecycle_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(browser.lifecycle_summary(),indent=2)+'\n',encoding='utf-8')
            temporary.replace(lifecycle_path)
if __name__=='__main__':main()
