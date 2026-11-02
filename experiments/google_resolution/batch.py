"""Independent source-wide Google ID capture for provisional joins. No reviews."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from difflib import SequenceMatcher

import pyarrow as pa
import pyarrow.parquet as pq
from shapely import from_wkb

import poc
from crawl_experiment.sources.google_maps.place_resolution import normalized,google_identity

ROOT=poc.ROOT
SUCCESS={'CAPTURED_FOR_REVIEW','NO_UNIQUE_ENTITY','NOT_FOUND'}
COLUMNS={
    'source':pa.string(),'source_id':pa.string(),'source_key':pa.string(),
    'source_snapshot_sha256':pa.string(),'source_name':pa.string(),'source_address':pa.string(),
    'source_lat':pa.float64(),'source_lon':pa.float64(),'query':pa.string(),
    'google_id_kind':pa.string(),'google_identity_value':pa.string(),
    'google_place_id':pa.string(),'google_data_id':pa.string(),
    'google_name':pa.string(),'google_address':pa.string(),'google_lat':pa.float64(),'google_lon':pa.float64(),
    'resolved_url':pa.string(),'status':pa.string(),'legacy_resolution_status':pa.string(),
    'selection_method':pa.string(),'retrieved_at':pa.string(),'capture_origin':pa.string(),
    'coordinate_distance_m':pa.float64(),'name_similarity':pa.float64(),
    'address_similarity':pa.float64(),'match_score':pa.float64(),'candidate_count':pa.int64(),
    'verified':pa.bool_(),'reviews_collected':pa.int64()}
SCHEMA=pa.schema(list(COLUMNS.items()))


class CaptureNavigator(poc.GoogleMapsNavigator):
    """Experimental entity capture; no source-name threshold means verified match."""
    def _best_exact_candidate(self,store,candidates):
        exact=super()._best_exact_candidate(store,candidates)
        if exact:
            self.capture_selection='EXACT_CANDIDATE_FOR_REVIEW'
            return exact
        ranked=sorted([(SequenceMatcher(None,normalized(store.name),normalized(str(c.get('text') or ''))).ratio(),i,c)
                       for i,c in enumerate(candidates)],key=lambda r:(-r[0],r[1]))
        if not ranked or (len(ranked)>1 and ranked[0][0]-ranked[1][0]<.05):
            self.capture_selection='NO_CLEAR_CANDIDATE'
            return None
        self.capture_selection='RANKED_CANDIDATE_FOR_REVIEW'
        return ranked[0][2]

    def _resolved_place(self,store,current_url,evidence,candidates):
        url=poc.safe_url(current_url)
        title=self._place_title();address=self._place_address()
        if not (url and '/maps/place/' in url and google_identity(url)[0] and title and evidence.has_place_shell
                and self.driver.find_elements('css selector',poc.S.PLACE_DETAIL_PANE)
                and self.driver.find_elements('css selector',poc.S.PLACE_ENTITY_MARKER)):
            return None
        return dict(resolved_place_title=title,resolved_place_address=address,resolved_url=url,
                    place_entity_confirmed=True)


def as_capture(row,job,origin,selection):
    out=dict(row)
    eligible=(row.get('status') in ('RESOLVED','AMBIGUOUS','CAPTURED_FOR_REVIEW') and
              (row.get('entity_ui_confirmed') or origin=='PRIOR_POC_REVIEW') and
              row.get('google_name') and row.get('google_identity_value') and
              row.get('google_id_kind') in ('data_id','place_id') and
              poc.safe_url(row.get('resolved_url')) and '/maps/place/' in row['resolved_url'])
    out.update(status='CAPTURED_FOR_REVIEW' if eligible else 'NO_UNIQUE_ENTITY'
               if row.get('status')=='AMBIGUOUS' else row['status'],
               legacy_resolution_status=row.get('legacy_resolution_status',row['status']),
               source_snapshot_sha256=job['snapshot_sha256'],verified=False,
               capture_origin=origin,selection_method=selection)
    if eligible and out['google_id_kind']=='data_id':
        out['google_identity_value']=out['google_identity_value'].lower()
        out['google_data_id']=out['google_identity_value']
    return {key:out.get(key) for key in COLUMNS}


def prepare_plan():
    foody=ROOT/'output/foody-q4-discovered-20261029-v2/raw/pois.json'
    overture=ROOT/'output/district4-profile/runs/15d25e5b4b9e6f1c66e9/overture_fnb_district4.parquet'
    fixture=ROOT/'experiments/browser_framework/historical_center_framework_benchmark.json'
    hints={r['overture_id']:poc.safe_url(r['url']) for r in json.loads(fixture.read_text(encoding='utf-8'))['cases']
           if r.get('overture_id') and r.get('google_place_id') and r.get('url')}
    jobs=[]
    foody_sha=poc.sha(foody);overture_sha=poc.sha(overture)
    for r in json.loads(foody.read_text(encoding='utf-8')):
        jobs.append(dict(source='foody',source_id=str(r['source_id']),source_key='foody:'+str(r['source_id']),
            name=r['name'],address=' '.join(r['fields']['streetAddress']),lat=r['latitude'],lon=r['longitude'],
            source_url=r['url'],snapshot_sha256=foody_sha,cached_url=None))
    for r in pq.read_table(overture,columns=['id','names','addresses','geometry']).to_pylist():
        p=from_wkb(r['geometry'])
        jobs.append(dict(source='overture',source_id=r['id'],source_key='overture:'+r['id'],name=r['names']['primary'],
            address=next((a.get('freeform') for a in r['addresses'] or [] if a.get('freeform')),None),
            lat=p.y,lon=p.x,source_url=None,snapshot_sha256=overture_sha,cached_url=hints.get(r['id'])))
    if len({j['source_key'] for j in jobs})!=len(jobs):raise ValueError('Duplicate source identity')
    previous=ROOT/'output/google-resolution-poc-20261030-v1/state.json'
    old=json.loads(previous.read_text(encoding='utf-8')) if previous.exists() else {'results':[]}
    bykey={j['source_key']:j for j in jobs};seeds=[]
    for row in old['results']:
        job=bykey.get(row['source_key'])
        if job and all(row.get(k)==job.get(v) for k,v in [('source_name','name'),('source_address','address'),
                                                        ('source_lat','lat'),('source_lon','lon')]):
            capture=as_capture(row,job,'PRIOR_POC_REVIEW','PRIOR_OPEN_ENTITY')
            if capture['status']=='CAPTURED_FOR_REVIEW':seeds.append(capture)
    return dict(version=2,policy='CAPTURE_ONLY_MATCH_BY_TYPED_GOOGLE_ID_PROVISIONAL',
        inputs={str(p.relative_to(ROOT)).replace('\\','/'):poc.sha(p) for p in (foody,overture,fixture)},
        jobs=sorted(jobs,key=lambda j:j['source_key']),seeds=seeds,
        seed_origin_sha256=poc.sha(previous) if previous.exists() else None,
        counts=dict(Counter(j['source'] for j in jobs)),
        reviews=False,concurrency=1,retries=0,foody_undetailed_urls_excluded=898,
        cached_overture_urls=sum(bool(j['cached_url']) for j in jobs))


def write_mapping(root,source,state):
    target=root/(source+'_mapping.parquet');temp=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    table=pa.Table.from_pylist([{k:r.get(k) for k in COLUMNS} for r in state['results']],schema=SCHEMA)
    pq.write_table(table,temp,compression='zstd')
    if pq.read_metadata(temp).num_rows!=len(state['results']):raise ValueError('Mapping row validation failed')
    os.replace(temp,target)


class HintDriver:
    def __init__(self,driver,url):self.driver=driver;self.url=url
    def get(self,url):
        self.driver.get(self.url if self.url and '/maps/search/' in url else url)
    def __getattr__(self,key):return getattr(self.driver,key)


def capture(job,driver):
    used=[]
    def factory(d):
        nav=CaptureNavigator(d);used.append(nav);return nav
    row=poc.resolve_one(job,HintDriver(driver,job.get('cached_url')),navigator_factory=factory)
    method=getattr(used[0],'capture_selection','DIRECT_ENTITY') if used else 'NOT_OPENED'
    if job.get('cached_url'):method='SOURCE_OWN_CACHED_URL_RECHECK'
    return as_capture(row,job,'LIVE',method)


def review_navigation_stop(root,access_check):
    """Explicit review only. Keep failed source STOPPED; never retry its job."""
    stop_path=root/'SOURCE_STOP.json'
    stop=json.loads(stop_path.read_text(encoding='utf-8'))
    source=stop.get('source')
    if stop.get('reason')!='NAVIGATION_FAILED' or source not in ('foody','overture'):
        raise ValueError('Only a navigation-only stop can be reviewed here; access restrictions remain blocked')
    state=json.loads((root/(source+'_state.json')).read_text(encoding='utf-8'))
    if (root/'running.lock').exists() or state.get('in_flight') or state['status']!='STOPPED' or state.get('error')!='NAVIGATION_FAILED':
        raise ValueError('Active, unknown or inconsistent failed state; refusing acknowledgement')
    original=stop_path.read_bytes()
    candidates=[r for r in reversed(state['results']) if r.get('status')=='CAPTURED_FOR_REVIEW'
                and poc.safe_url(r.get('resolved_url')) and r.get('google_identity_value')]
    if not candidates:raise ValueError('No previously opened known entity for bounded access check')
    proof=access_check(candidates[0])
    history=root/'stop_reviews';history.mkdir(exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    poc.save(history/(stamp+'-access.json'),proof)
    if proof.get('status')!='PASS':
        raise ValueError('Access check did not pass; original source stop preserved')
    if stop_path.read_bytes()!=original or (root/'running.lock').exists():
        raise ValueError('State changed during review; stop preserved')
    destination=history/(stamp+'-original-stop.json')
    if not destination.resolve().is_relative_to(root.resolve()):raise ValueError('Invalid stop history path')
    os.replace(stop_path,destination)
    # Both checkpoints and mapping files remain byte-for-byte untouched.
    return dict(status='NAVIGATION_STOP_REVIEWED',failed_source=source,
                failed_source_still_stopped=True,failed_job_retried=False,history=str(destination))


def check_known_entity(row,profile,env):
    from experiments.browser_framework.adapters import SeleniumBaseBrowser
    browser=SeleniumBaseBrowser(env['chrome_path'],env['driver_version'],env['driver_path'],profile)
    proof=dict(status='FAIL',checked_at=datetime.now(timezone.utc).isoformat(),
               reviews_collected=0,failed_job_retried=False)
    try:
        browser.start()
        driver=browser.driver;driver.get(poc.safe_url(row['resolved_url']))
        from selenium.webdriver.support.ui import WebDriverWait
        def entity_ready(d):
            poc.guard(d)
            return bool(d.find_elements('css selector',poc.S.PLACE_ENTITY_MARKER) and
                        d.find_elements('css selector',poc.S.PLACE_TITLE))
        WebDriverWait(driver,10).until(entity_ready)
        identity,kind=google_identity(poc.safe_url(driver.current_url))
        markers=bool(driver.find_elements('css selector',poc.S.PLACE_ENTITY_MARKER))
        title=bool(driver.find_elements('css selector',poc.S.PLACE_TITLE))
        if markers and title and kind==row['google_id_kind'] and identity==row['google_identity_value']:
            proof.update(status='PASS',entity_panel=True,same_known_identity=True)
        else:proof['reason']='KNOWN_ENTITY_NOT_CONFIRMED'
    except poc.SourceStop as exc:proof['reason']=str(exc)
    except Exception:proof['reason']='BROWSER_OR_NAVIGATION_ERROR'
    finally:browser.close()
    return proof


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--review-navigation-stop',action='store_true',
                        help='One known-entity health check; archive navigation-only stop on PASS; never retry failed job')
    parser.add_argument('--source',choices=['foody','overture'])
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--limit',type=int,default=2000)
    parser.add_argument('--max-minutes',type=int,default=180)
    parser.add_argument('--profile',type=Path,default=Path(os.environ['LOCALAPPDATA'])/'FnbBrowserBenchmark/profiles-20261028T222347/seleniumbase')
    args=parser.parse_args();root=args.root.resolve()
    if not root.is_relative_to((ROOT/'output').resolve()):parser.error('Use dedicated output root')
    if not 1<=args.limit<=2000 or not 1<=args.max_minutes<=180:parser.error('Use <=2000 jobs and <=180 minutes')
    if args.prepare:
        if root.exists() and any(root.iterdir()):parser.error('Fresh output root required')
        root.mkdir(parents=True,exist_ok=True);plan=prepare_plan();poc.save(root/'plan.json',plan)
        for source in ('foody','overture'):
            seeds=[r for r in plan['seeds'] if r['source']==source]
            done={r['source_key'] for r in seeds}
            state=dict(status='READY',results=seeds,pending=[j['source_key'] for j in plan['jobs']
                       if j['source']==source and j['source_key'] not in done],in_flight=None,
                       plan_sha256=poc.sha(root/'plan.json'),error=None)
            poc.save(root/(source+'_state.json'),state);write_mapping(root,source,state)
        print(json.dumps(dict(status='PREPARED',jobs=plan['counts'],reused=len(plan['seeds']),
                              cached_urls=plan['cached_overture_urls'])),flush=True)
    if not (args.live or args.check or args.review_navigation_stop):return
    plan=json.loads((root/'plan.json').read_text(encoding='utf-8'))
    for name,digest in plan['inputs'].items():
        if poc.sha(ROOT/name)!=digest:raise ValueError('Input changed')
    if args.review_navigation_stop and (args.live or args.prepare):parser.error('Stop review must be separate from prepare/live')
    if (root/'SOURCE_STOP.json').exists() and not args.review_navigation_stop:
        raise ValueError('Source stop requires manual review; no auto retry/profile switching')
    profile=args.profile.resolve();allowed=(Path(os.environ['LOCALAPPDATA'])/'FnbBrowserBenchmark').resolve()
    if not profile.is_relative_to(allowed) or not (profile/'Local State').is_file() or not (profile/'Default/Preferences').is_file():
        raise ValueError('Existing dedicated benchmark profile required')
    from experiments.browser_framework.profiles import assert_available
    from experiments.browser_framework.run import environment
    assert_available([profile]);env=environment(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
    print('PREFLIGHT_PASS',flush=True)
    if args.review_navigation_stop:
        print(json.dumps(review_navigation_stop(root,lambda row:check_known_entity(row,profile,env))),flush=True)
        return
    if not args.live:return
    if not args.source:parser.error('--source required for --live')
    source=args.source;statepath=root/(source+'_state.json')
    state=json.loads(statepath.read_text(encoding='utf-8'))
    if state['plan_sha256']!=poc.sha(root/'plan.json') or state['in_flight'] or state['status'] not in ('READY','PAUSED'):
        raise ValueError('Changed/in-flight/stopped state requires review')
    if (state['status']=='PAUSED')!=args.resume:raise ValueError('Use --resume only after PAUSED')
    lock=root/'running.lock';descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    os.close(descriptor)
    from experiments.browser_framework.adapters import SeleniumBaseBrowser
    browser=SeleniumBaseBrowser(env['chrome_path'],env['driver_version'],env['driver_path'],profile)
    jobs={j['source_key']:j for j in plan['jobs']};start=time.monotonic();completed=0

    def checkpoint():
        poc.save(statepath,state);write_mapping(root,source,state)
        summary=dict(source=source,status=state['status'],total=plan['counts'][source],
            attempted=len(state['results']),pending=len(state['pending']),
            counts=dict(Counter(r['status'] for r in state['results'])),error=state.get('error'),
            verified=False,reviews_collected=0)
        poc.save(root/(source+'_summary.json'),summary)
        with (root/(source+'_progress.jsonl')).open('a',encoding='utf-8') as log:log.write(json.dumps(summary)+'\n')
        print(json.dumps(summary),flush=True)
    state.update(status='RUNNING',error=None)
    poc.save(root/(source+'-batch-'+uuid.uuid4().hex[:12]+'.json'),dict(started_at=datetime.now(timezone.utc).isoformat(),
             parser_sha256=poc.sha(Path(__file__)),poc_sha256=poc.sha(Path(poc.__file__)),chrome=env['chrome'],
             driver=env['driver_version'],source=source,limit=args.limit))
    try:
        checkpoint();browser.start()
        while state['pending'] and completed<args.limit and time.monotonic()-start<args.max_minutes*60:
            key=state['pending'][0];state['in_flight']=key;checkpoint();time.sleep(3)
            row=capture(jobs[key],browser.driver)
            state['results'].append(row);state['in_flight']=None;completed+=1
            if row['status'] not in SUCCESS:
                state.update(status='STOPPED',error=row['status'])
                poc.save(root/'SOURCE_STOP.json',dict(source=source,reason=row['status']))
                checkpoint();break
            state['pending'].pop(0);checkpoint()
        else:state['status']='PAUSED' if state['pending'] else 'COMPLETED'
    except KeyboardInterrupt:
        state.update(status='STOPPED' if state['in_flight'] else 'PAUSED',error='INTERRUPTED')
    except Exception:
        state.update(status='STOPPED',error='BROWSER_OR_RUNNER_ERROR')
        poc.save(root/'SOURCE_STOP.json',dict(source=source,reason=state['error']))
    finally:
        try:browser.close()
        finally:
            try:checkpoint()
            finally:lock.unlink()  # Only the lock this invocation created.


if __name__=='__main__':main()
