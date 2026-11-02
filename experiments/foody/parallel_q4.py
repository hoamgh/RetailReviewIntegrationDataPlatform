"""Two private profiles, observed Q4 streets + details. No reviews or retries."""
import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing as mp
from multiprocessing.util import Finalize
import os
from pathlib import Path
from queue import Empty
import re
import time

from bs4 import BeautifulSoup
from shapely.geometry import shape, Point
import crawl
from full import fetch_detail, observe_listing_response, pagination_parameters
from streets import INDEX, ROOT, street_url, parse_street
from profile_access import available_profile
from search_fallback import street_name, street_evidence, search_task

MANIFEST = ROOT/'output/foody-q4-street-inspection/street-entries-121.json'
BOUNDARY = ROOT/'output/district4-profile/cache/geoBoundaries-VNM-ADM2-9469f09.geojson'
SEEDS = [ROOT/'output'/p for p in (
    'foody-q4-discovered-20261029-v2/raw/pois.json',
    'foody-load-more-check-0198321f2518/summary.json',
    'foody-street-load-more-4563312fea73/summary.json',
    'foody-street-load-more-764adcd44218/summary.json',
    'foody-alternative-listing-cc7577bacffe/places-one-page.json')]
_context = None
ACCESS = {'ACCESS_RESTRICTION', 'LOGIN_REQUIRED', 'STREET_REDIRECT_REQUIRES_REVIEW'}
LOCAL = {'NAVIGATION_TIMEOUT', 'NO_VALID_SOURCE_ID', 'EMPTY_OR_UNSUPPORTED_DETAIL',
         'EMPTY_OR_UNSUPPORTED_STREET_LAYOUT', 'NO_VALID_STREET_PLACE_URLS',
         'DETAIL_HTTP_404', 'STREET_HTTP_404', 'SEARCH_UI_UNVERIFIED',
         'SEARCH_LAYOUT_UNVERIFIED'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def public_ids(response, metadata):
    """Observed street endpoint's data.Items[].Id, including explicit empty array."""
    if metadata.get('body_observation') != 'JSON_READ':
        return None
    obj = json.loads(response.body())
    rows = obj.get('data', {}).get('Items') if isinstance(obj, dict) and isinstance(obj.get('data'), dict) else None
    if not isinstance(rows, list):
        return None
    if not all(isinstance(row, dict) and re.fullmatch(r'[1-9]\d*', str(row.get('Id'))) for row in rows):
        return None
    return [str(row['Id']) for row in rows]


def initialize(assignments, stop, events):
    global _profile, _stop, _events
    _profile, _stop, _events = assignments.get(), stop, events


def close_worker():
    if _context is not None:
        try:
            _context.close()
        except Exception as exc:
            # A closed browser can already be gone when multiprocessing finalizes.
            if type(exc).__name__ != 'TargetClosedError':
                raise
    if globals().get('_playwright'):
        _playwright.stop()


def context():
    global _context, _playwright
    if _context is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        Finalize(None, close_worker, exitpriority=10)
        _context = _playwright.chromium.launch_persistent_context(
            _profile, channel='chrome', headless=False, viewport={'width':1280,'height':900})
    return _context


def street_task(url, max_clicks):
    from playwright.sync_api import TimeoutError as BrowserTimeout
    page = context().new_page()
    try:
        response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
        if response and response.status in (401,403,429):
            raise crawl.CrawlError('ACCESS_RESTRICTION')
        if not response or response.status != 200:
            raise crawl.CrawlError('STREET_HTTP_'+str(response.status if response else 'UNKNOWN'))
        if page.url != url:
            raise crawl.CrawlError('STREET_REDIRECT_REQUIRES_REVIEW')
        page.wait_for_selector('.res-nearby-content-items', timeout=10000)
        parsed = parse_street(page.content())
        seen = {r['url'] for r in parsed['places']}
        seen_ids = set()
        _events.put(dict(street=url, places=parsed['places'], load=dict(click=0, new_urls=len(seen))))
        for click in range(1,max_clicks+1):
            if _stop.wait(3):
                return dict(outcome='CANCELLED')
            controls = page.locator('a.load-more-result:visible')
            if not controls.count():
                return dict(outcome='NO_VISIBLE_MORE_UNVERIFIED')
            if controls.count()!=1:
                raise crawl.CrawlError('AMBIGUOUS_STREET_MORE_CONTROL')
            load = dict(click=click, responses=[], click_state='NOT_ATTEMPTED')
            responses=[]
            def observe(response):
                from urllib.parse import urlsplit
                parts=urlsplit(response.url)
                if parts.hostname=='www.foody.vn' and parts.path=='/LandingPage/GetMoreNearbyStreet':
                    responses.append(response)
            page.on('response',observe)
            try:
                control=controls.first
                control.evaluate("n=>n.scrollIntoView({block:'center',behavior:'instant'})",timeout=10000)
                try:
                    control.click(timeout=10000,no_wait_after=True)
                    load['click_state']='RETURNED'
                except BrowserTimeout:
                    load['click_state']='TIMEOUT'
                try:
                    page.wait_for_function('''before =>
                      /captcha|access denied|just a moment/i.test(document.body?.innerText||'') ||
                      [...document.querySelectorAll('.res-nearby-content-items a[href]')]
                      .some(a=>/^\\/ho-chi-minh\\/[^/]+$/.test(new URL(a.href).pathname) &&
                        !new URL(a.href).search && !before.includes(a.href))''',arg=sorted(seen),timeout=15000)
                except BrowserTimeout:
                    load['result_wait']='TIMEOUT'
                page.wait_for_timeout(1000)
            finally:
                page.remove_listener('response',observe)
            new_ids=set()
            for response in responses:
                item=dict(status=response.status,pagination=pagination_parameters(
                    response.url,response.request.post_data))
                if response.status in (401,403,429):
                    _stop.set()
                    raise crawl.CrawlError('ACCESS_RESTRICTION')
                if response.status==200:
                    metadata,ids=observe_listing_response(response)
                    if ids is None: ids=public_ids(response,metadata)
                    item.update(body_sha256=metadata.get('body_sha256'),
                        response_id_count=len(ids) if ids is not None else None,
                        new_id_count=len(set(ids)-seen_ids) if ids is not None else None,
                        duplicate_id_count=len(ids)-len(set(ids)-seen_ids) if ids is not None else None)
                    if ids is not None:
                        new_ids.update(set(ids)-seen_ids);seen_ids.update(ids)
                load['responses'].append(item)
            if page.url!=url:
                raise crawl.CrawlError('STREET_REDIRECT_REQUIRES_REVIEW')
            parsed=parse_street(page.content())
            urls={r['url'] for r in parsed['places']}
            load.update(parser_urls=len(urls),new_urls=len(urls-seen),new_source_ids=len(new_ids))
            _events.put(dict(street=url,places=parsed['places'],load=load))
            if not urls-seen:
                return dict(outcome='PARSER_DIVERGENCE' if new_ids else 'NO_PROGRESS_UNVERIFIED')
            seen.update(urls)
        return dict(outcome='CLICK_BUDGET_PARTIAL')
    finally:
        page.close()


def execute(task):
    if _stop.is_set(): return dict(outcome='CANCELLED')
    try:
        if task['kind']=='street':
            return street_task(task['url'],task['max_clicks'])
        if task['kind']=='search':
            return search_task(context(),task['street'])
        html,provenance=fetch_detail(context(),task['url'])
        soup=BeautifulSoup(html,'html.parser')
        if soup.select('input[type=password]'): raise crawl.CrawlError('LOGIN_REQUIRED')
        record=crawl.parse_page(html,task['url'],review_limit=0)['record']
        if not record: raise crawl.CrawlError('EMPTY_OR_UNSUPPORTED_DETAIL')
        if not record.get('source_id'): raise crawl.CrawlError('NO_VALID_SOURCE_ID')
        record['provenance']=provenance
        return dict(outcome='OK',record=record)
    except Exception as exc:
        from playwright.sync_api import TimeoutError as BrowserTimeout
        reason=str(exc) if isinstance(exc,crawl.CrawlError) else ('NAVIGATION_TIMEOUT' if isinstance(exc,BrowserTimeout) else 'BROWSER_OR_RUNNER_ERROR')
        if reason in ('DETAIL_HTTP_401','DETAIL_HTTP_403','DETAIL_HTTP_429'):
            reason='ACCESS_RESTRICTION'
        if reason not in LOCAL:
            _stop.set()
        return dict(outcome='ERROR',reason=reason if reason in LOCAL|ACCESS else 'BROWSER_OR_RUNNER_ERROR',
                    local=reason in LOCAL,exception_type=type(exc).__name__,task_kind=task['kind'])


def absorb(state,event):
    street=event['street']
    for place in event['places']:
        entry=state['places'].setdefault(place['url'],dict(**place,streets=[]))
        if street not in entry['streets']: entry['streets'].append(street)
    state['street_observations'].setdefault(street,[]).append(event['load'])


def seed_state(manifest):
    if manifest.get('source')!=INDEX or manifest.get('status')!=200:
        raise ValueError('Unexpected street manifest')
    routes=sorted({street_url(r['href']) for r in manifest['entries'] if r.get('href')})
    fallback={f"search:{r.get('index',i)}":street_name(r) for i,r in enumerate(manifest['entries'])
              if not r.get('href') and street_name(r)}
    return dict(status='READY',error=None,phase='STREETS',pending=routes+sorted(fallback),in_flight=[],
        street_outcomes={},street_observations={},places={},records={},detail_done=[],dlq=[],
        unresolved_street_entries=sum(not r.get('href') for r in manifest['entries']),
        observed_street_routes=len(routes),search_fallbacks=fallback,
        coverage='PARTIAL_NOT_PROVEN_COMPLETE',reviews_collected=0)


def merge_record(state,record,url,polygon):
    sid=record['source_id']
    lat,lon=record.get('latitude'),record.get('longitude')
    record['geographic_scope']='UNKNOWN' if lat is None or lon is None else ('INSIDE_Q4_2020' if polygon.covers(Point(lon,lat)) else 'OUTSIDE_Q4_2020')
    if sid not in state['records']:
        state['records'][sid]=record
    else:
        record=state['records'][sid]
    aliases=record.setdefault('observed_urls',[])
    if url not in aliases: aliases.append(url)
    address=' '.join(record.get('fields',{}).get('streetAddress',[])) or record.get('address') or record.get('street_address')
    for origin in state['places'].get(url,{}).get('search_streets',[]):
        evidence=street_evidence(address,origin)
        matches=record.setdefault('search_street_evidence',{})
        matches[origin]=evidence
    if url not in state['detail_done']: state['detail_done'].append(url)


def add_seeds(state,polygon):
    def urls(obj):
        if isinstance(obj,dict):
            for key,value in obj.items():
                if key in ('url','initial_urls','urls','discovered_urls','places','cases','loads'):
                    yield from urls(value)
        elif isinstance(obj,list):
            for value in obj: yield from urls(value)
        elif isinstance(obj,str):
            try:
                url=crawl.canonical(obj)
                if crawl.source_link_kind(url)=='detail': yield url
            except ValueError: pass
    for path in SEEDS[1:]:
        for url in urls(json.loads(path.read_text(encoding='utf-8'))):
            state['places'].setdefault(url,dict(url=url,name=None,streets=[],origin='PRIOR_DISCOVERY'))
    for record in json.loads(SEEDS[0].read_text(encoding='utf-8')):
        # Preserve original source provenance/time, exclude historical review bodies.
        record={k:v for k,v in record.items() if k not in ('review_samples','review_dom_blocks','reviews_collected')}
        record['reviews_collected']=0
        if not record.get('source_id'): continue
        url=crawl.canonical(record['url'])
        state['places'].setdefault(url,dict(url=url,name=record['name'],streets=[],origin='PRIOR_DETAIL'))
        merge_record(state,record,url,polygon)


def accept_result(state,task,result,polygon):
    url=task['url']
    if result['outcome']=='CANCELLED': return
    if result['outcome']=='ERROR':
        if result['local']:
            state['dlq'].append(dict(kind=task['kind'],url=url,reason=result['reason'],retry='MANUAL_ONLY'))
        else:
            state['error']=result['reason']
            state['failure_diagnostic']={k:result[k] for k in ('exception_type','task_kind') if k in result}
            return
    elif task['kind']=='search':
        state['street_outcomes'][url]=result['outcome']
        for item in result['places']:
            entry=state['places'].setdefault(item['url'],dict(**item,streets=[],search_streets=[]))
            names=entry.setdefault('search_streets',[])
            if task['street'] not in names: names.append(task['street'])
        state['street_observations'][url]=result['observations']
    elif task['kind']=='street':
        state['street_outcomes'][url]=result['outcome']
    else:
        merge_record(state,result['record'],url,polygon)
    state['pending'].remove(url)


def save(root,state):
    crawl.save(root/'state.json',state)
    crawl.save(root/'raw/pois.json',list(state['records'].values()))
    crawl.save(root/'raw/pois_q4.json',[r for r in state['records'].values() if r.get('geographic_scope')=='INSIDE_Q4_2020'])
    crawl.save(root/'dlq.json',state['dlq'])
    summary={k:state[k] for k in ('status','error','phase','observed_street_routes','unresolved_street_entries','coverage','reviews_collected')}
    summary.update(pending=len(state['pending']),in_flight=len(state['in_flight']),
        streets_finished=len(state['street_outcomes']),unique_urls=len(state['places']),
        unique_foody_ids=len(state['records']),inside_q4=sum(r.get('geographic_scope')=='INSIDE_Q4_2020' for r in state['records'].values()),dlq=len(state['dlq']))
    if state.get('failure_diagnostic'):
        summary['failure_diagnostic']=state['failure_diagnostic']
    crawl.save(root/'summary.json',summary)
    with (root/'progress.jsonl').open('a',encoding='utf-8') as log:
        log.write(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(),**summary))+'\n')
    print(json.dumps(summary),flush=True)


def run(root,state,polygon,pool,stop,events,minutes,max_clicks,*,spacing=3):
    active={};deadline=time.monotonic()+minutes*60;last=float('-inf')
    state.update(status='RUNNING',error=None)
    try:
        while True:
            while True:
                try: absorb(state,events.get_nowait());save(root,state)
                except Empty: break
            if not state['pending'] and not active:
                if state['phase']=='STREETS':
                    if state['observed_street_routes'] + state['unresolved_street_entries'] != 121:
                        state.update(status='BLOCKED',error='STREET_INDEX_INCOMPLETE');break
                    # Missing href headings remain uncovered; do not claim exhaustive discovery.
                    if (len(state['street_outcomes']) != state['observed_street_routes'] + len(state.get('search_fallbacks',{}))
                            or state['dlq'] or not state['places']):
                        state.update(status='BLOCKED',error='STREET_DISCOVERY_NOT_VERIFIED');break
                    state['phase']='DETAILS'
                    state['pending']=sorted(set(state['places'])-set(state['detail_done']))
                else:
                    state['status']='COMPLETED_PARTIAL';break
            if not active and (stop.is_set() or time.monotonic()>=deadline):
                state['status']='STOPPED' if stop.is_set() else 'PAUSED';break
            ready=[u for u in state['pending'] if u not in state['in_flight']]
            if ready and len(active)<2 and not stop.is_set() and time.monotonic()<deadline and time.monotonic()-last>=spacing:
                kind=('search' if ready[0] in state.get('search_fallbacks',{}) else 'street') if state['phase']=='STREETS' else 'detail'
                task=dict(kind=kind,url=ready[0],max_clicks=max_clicks)
                if kind=='search': task['street']=state['search_fallbacks'][ready[0]]
                state['in_flight'].append(task['url']);save(root,state)
                active[pool.submit(execute,task)]=task;last=time.monotonic()
            if not active:
                stop.wait(.2)
                continue
            finished,_=wait(active,timeout=.2,return_when=FIRST_COMPLETED)
            for future in finished:
                task=active.pop(future);result=future.result()
                state['in_flight'].remove(task['url'])
                accept_result(state,task,result,polygon)
                if state['error']: stop.set()
                save(root,state)
        while True:
            try: absorb(state,events.get_nowait())
            except Empty: break
    except BaseException:
        stop.set();state.update(status='STOPPED',error='INTERRUPTED_OR_RUNNER_FAILURE')
        raise
    finally:
        save(root,state)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--rights-confirmed',action='store_true')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--max-minutes',type=int,default=180)
    parser.add_argument('--max-clicks',type=int,default=70)
    args=parser.parse_args();root=args.root.resolve()
    allowed=(ROOT/'output').resolve()
    if root==allowed or not root.is_relative_to(allowed) or not 1<=args.max_minutes<=180 or not 1<=args.max_clicks<=70:
        raise ValueError('Dedicated output root and bounded budgets required')
    if args.live and not args.rights_confirmed: raise ValueError('Collection/storage rights confirmation required')
    profiles=[Path(os.environ['LOCALAPPDATA'])/'FnbFoodyResearch'/n for n in ('manual-foody-20261029-135111','manual-foody-20261031-173744')]
    for profile in profiles: available_profile(profile)
    features=json.loads(BOUNDARY.read_text(encoding='utf-8'))['features']
    selected=[f for f in features if f['properties'].get('shapeID')=='81297802B29563104023098']
    if len(selected)!=1: raise ValueError('Historical boundary identity mismatch')
    polygon=shape(selected[0]['geometry'])
    if not polygon.is_valid: raise ValueError('Invalid boundary')
    inputs={str(p.relative_to(ROOT)):digest(p) for p in (MANIFEST,BOUNDARY,*SEEDS)}
    if (root/'state.json').exists():
        state=json.loads((root/'state.json').read_text(encoding='utf-8'))
        if not args.resume or state['status']!='PAUSED' or state['in_flight'] or state['inputs']!=inputs:
            raise ValueError('Only PAUSED unchanged inputs may resume')
    else:
        if args.resume or root.exists(): raise ValueError('Fresh output root required')
        state=seed_state(json.loads(MANIFEST.read_text(encoding='utf-8')))
        add_seeds(state,polygon)
        state.update(inputs=inputs,max_clicks=args.max_clicks)
        if not args.live:
            print(json.dumps(dict(status='PREFLIGHT_PASS',routes=state['observed_street_routes'],unresolved=state['unresolved_street_entries'],workers=2,reused_records=len(state['records']),known_urls=len(state['places']))));return
        (root/'raw').mkdir(parents=True)
    if state['max_clicks']!=args.max_clicks: raise ValueError('Resume configuration changed')
    if not args.live: return
    lock=root/'running.lock';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.close(fd)
    try:
        ctx=mp.get_context('spawn');stop=ctx.Event();assignments=ctx.Queue()
        for profile in profiles: assignments.put(str(profile))
        with ctx.Manager() as manager:
            events=manager.Queue()
            with ProcessPoolExecutor(max_workers=2,mp_context=ctx,initializer=initialize,initargs=(assignments,stop,events)) as pool:
                run(root,state,polygon,pool,stop,events,args.max_minutes,args.max_clicks)
    finally:
        lock.unlink()


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(json.dumps(dict(status='BLOCKED',error_type=type(exc).__name__)),flush=True)
        raise SystemExit(1)
