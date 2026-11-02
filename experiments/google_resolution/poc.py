"""Small owner-run cross-source Google identity experiment; no reviews or merges."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import unquote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from crawl_experiment.core.models import Store
from crawl_experiment.core.place_catalog import CanonicalPlace
from crawl_experiment.sources.google_maps.place_resolution import assess_resolution, google_identity
from crawl_experiment.sources.google_maps.navigator import GoogleMapsNavigator
from crawl_experiment.sources.google_maps.health import classify_page, limited_signals, PageEvidence, HealthState
from crawl_experiment.sources.google_maps import selectors as S
from crawl_experiment.core.errors import ChallengeError, RateLimitedError, LimitedViewError, NavigationError, PlaceResolutionError


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, obj):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    os.replace(temporary,path)


def safe_url(value):
    p = urlsplit(value or '')
    if p.scheme != 'https' or p.hostname not in ('google.com','www.google.com') or not p.path.startswith('/maps/'):
        return None
    if p.username or p.password or p.port is not None:
        return None
    return urlunsplit((p.scheme,p.netloc,p.path,'',''))


def entity_point(url):
    match = re.search(r'!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)', unquote(url or ''))
    if not match:
        return None,None
    lat,lon = map(float,match.groups())
    return (lat,lon) if math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180 else (None,None)


def make_plan():
    import pyarrow.parquet as pq
    from shapely import from_wkb
    source = ROOT/'output/foody-q4-discovered-20261029-v2/raw/pois.json'
    overture = ROOT/'output/district4-profile/runs/15d25e5b4b9e6f1c66e9/overture_fnb_district4.parquet'
    pairs_path = ROOT/'output/district4-profile/overlap/6bff2af2a0e29760a122/strict53_vs_fnb1531-pairs.json'
    pairs = json.loads(pairs_path.read_text(encoding='utf-8'))
    foody = json.loads(source.read_text(encoding='utf-8'))
    jobs = {}
    for r in foody:
        key = 'foody:'+str(r['source_id'])
        jobs[key] = dict(source='foody',source_id=str(r['source_id']),source_key=key,
            name=r['name'],address=' '.join(r['fields']['streetAddress']),
            lat=r['latitude'],lon=r['longitude'],source_url=r['url'])
    selected = {r['overture_id'] for r in pairs}
    for r in pq.read_table(overture,columns=['id','names','addresses','geometry']).to_pylist():
        if r['id'] not in selected:
            continue
        point = from_wkb(r['geometry']); key='overture:'+r['id']
        jobs[key] = dict(source='overture',source_id=r['id'],source_key=key,
            name=r['names']['primary'],address=next((a['freeform'] for a in r['addresses'] or [] if a.get('freeform')),None),
            lat=point.y,lon=point.x,source_url=None)
    order = []
    for pair in sorted(pairs,key=lambda r:r['foody_id']):
        order.extend(['foody:'+pair['foody_id'],'overture:'+pair['overture_id']])
    order = list(dict.fromkeys(order+sorted(jobs)))
    if set(order)!=set(jobs):
        raise ValueError('Missing candidate source record')
    for job in jobs.values():
        if not job['name'] or not all(math.isfinite(job[k]) for k in ('lat','lon')):
            raise ValueError('Missing source identity/name/geometry')
    catalog = ROOT/'data/place_catalog/khanh_hoi/canonical_places.parquet'
    cache = pq.read_table(catalog,columns=['source_place_id','google_place_id','google_match_status']).to_pylist()
    cache_ids = {r['source_place_id'] for r in cache if r.get('google_place_id') and r.get('google_match_status')=='RESOLVED'}
    fixture = ROOT/'experiments/browser_framework/historical_center_framework_benchmark.json'
    historical = json.loads(fixture.read_text(encoding='utf-8'))
    history_ids = {r['overture_id'] for r in historical['cases'] if r.get('overture_id') and r.get('google_place_id')}
    plan = dict(version=1,scope='57 Foody details +6 existing Overture candidate records; not all Overture',
        jobs=[jobs[key] for key in order],
        inputs={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in (source,overture,pairs_path,catalog,fixture)},
        cache=dict(catalog_resolved=len(cache_ids),applicable_catalog=len(selected&cache_ids),
                   historical_id_count=len(history_ids),applicable_historical=len(selected&history_ids)),
        policy=dict(reviews=False,concurrency=1,retries=0,default_live_job_limit=2,
                    identity_key='google_id_kind + google_identity_value',
                    bridge='both independently RESOLVED and unique source identity on each side',
                    source_keys_preserved=True))
    # This exact sample has no reusable mappings. Fail closed rather than ignore a
    # mapping if future input changes require a separate cached-verification path.
    if plan['cache']['applicable_catalog'] or plan['cache']['applicable_historical']:
        raise ValueError('Applicable cached mapping found; verify cache before live search')
    return plan


class SourceStop(LimitedViewError):
    pass


def guard(driver):
    url=driver.current_url
    if urlsplit(url).hostname in ('accounts.google.com','consent.google.com'):
        raise SourceStop('AUTH_OR_CONSENT_REQUIRED')
    body=driver.find_element('tag name','body').text
    evidence=PageEvidence(has_place_tabs=bool(driver.find_elements('css selector',S.PLACE_TABS)),
        has_place_shell=bool(driver.find_elements('css selector',S.PLACE_SHELL)),
        has_sign_in_prompt=bool(driver.find_elements('css selector',S.SIGN_IN_PROMPT)))
    health=classify_page(url,body,evidence=evidence)
    if health==HealthState.CHALLENGE: raise SourceStop('CHALLENGE')
    if health==HealthState.RATE_LIMITED: raise SourceStop('BLOCKED')
    if health==HealthState.LIMITED or limited_signals(body,evidence): raise SourceStop('LIMITED_VIEW')


def resolve_one(job,driver,*,navigator_factory=None):
    now=datetime.now(timezone.utc).isoformat()
    place=CanonicalPlace(place_key=job['source_key'],source=job['source'],source_place_id=job['source_id'],
        name=job['name'],lat=job['lat'],lng=job['lon'],address=job['address'],primary_category=None,
        categories=[],category_hierarchy=[],product_category_group='unverified',category_source=job['source'],
        confidence=None,admin_area_id='district4-historical-2020',admin_area_name='Quận 4, TP.HCM',
        first_seen_at=now,last_seen_at=now)
    query=' '.join(filter(None,[place.name,place.address,place.admin_area_name]))
    navigator=(navigator_factory or GoogleMapsNavigator)(driver)
    navigator.after_search_navigation=lambda:guard(driver)
    error=None
    try:
        navigator.open_store(Store(id='resolution-'+hashlib.sha256(place.place_key.encode()).hexdigest()[:16],
                                   name=place.name,query=query))
    except PlaceResolutionError:
        pass  # Preserve diagnostics but never persist exception strings/URLs.
    except SourceStop as exc:
        error=str(exc)
    except ChallengeError:
        error='CHALLENGE'
    except RateLimitedError:
        error='BLOCKED'
    except LimitedViewError:
        error='LIMITED_VIEW'
    except NavigationError:
        error='NAVIGATION_FAILED'
    except Exception:
        error='BROWSER_ERROR'
    if error is None:
        try: guard(driver)
        except SourceStop as exc: error=str(exc)
        except Exception: error='BROWSER_ERROR'
    diagnostics=navigator.last_resolution_diagnostics
    assessed=assess_resolution(place,diagnostics)
    url=safe_url(diagnostics.get('resolved_url'))
    value,kind=google_identity(url)
    lat,lon=entity_point(url)
    status=error or assessed['status']
    if status=='RESOLVED' and (not url or not value or lat is None or lon is None):
        status='AMBIGUOUS'
    if error is None and status=='AMBIGUOUS':
        body=driver.find_element('tag name','body').text
        if re.search(r'no results found|không tìm thấy kết quả|không có kết quả',body,re.I):
            status='NOT_FOUND'
    return dict(source=job['source'],source_id=job['source_id'],source_key=job['source_key'],
        source_name=job['name'],source_address=job['address'],source_lat=job['lat'],source_lon=job['lon'],
        query=query,status=status,retrieved_at=now,google_name=assessed.get('google_name'),
        google_address=assessed.get('google_address'),google_identity_value=value,google_id_kind=kind,
        google_place_id=value if kind=='place_id' else None,google_data_id=value if kind=='data_id' else None,
        google_lat=lat,google_lon=lon,resolved_url=url,
        coordinate_distance_m=assessed.get('coordinate_distance_m'),name_similarity=assessed.get('name_similarity'),
        address_similarity=assessed.get('address_similarity'),match_score=assessed.get('match_score'),
        candidate_count=diagnostics.get('search_candidate_count'),
        entity_ui_confirmed=bool(diagnostics.get('place_entity_confirmed')),reviews_collected=0)


def bridge(results):
    identities=defaultdict(lambda:defaultdict(list))
    for row in results:
        if row.get('status')!='RESOLVED' or not row.get('google_identity_value') or not row.get('google_id_kind'):
            continue
        value=row['google_identity_value']
        if row['google_id_kind']=='data_id': value=value.lower()
        identities[(row['google_id_kind'],value)][row['source']].append(row['source_id'])
    output=[]
    for (kind,value),sources in sorted(identities.items()):
        if not sources.get('foody') or not sources.get('overture'): continue
        output.append(dict(google_id_kind=kind,google_identity_value=value,foody_ids=sources['foody'],
            overture_ids=sources['overture'],status='BRIDGE_SUGGESTED' if
            len(sources['foody'])==len(sources['overture'])==1 else 'IDENTITY_COLLISION_REVIEW',
            confirmed=False))
    return output


def shared_candidates(results):
    groups=defaultdict(lambda:defaultdict(list))
    for row in results:
        if row.get('status') not in ('RESOLVED','AMBIGUOUS'):
            continue
        kind=row.get('google_id_kind'); value=row.get('google_identity_value')
        if kind not in ('place_id','data_id') or not value:
            continue
        groups[(kind,value.lower() if kind=='data_id' else value)][row['source']].append(
            dict(source_id=row['source_id'],status=row['status']))
    return [dict(google_id_kind=kind,google_identity_value=value,sources=dict(sources),
                 status='SHARED_CANDIDATE_NOT_CONFIRMED',confirmed=False)
            for (kind,value),sources in sorted(groups.items())
            if sources.get('foody') and sources.get('overture')]


def write_reports(root,plan,state):
    links=bridge(state['results']); candidates=shared_candidates(state['results'])
    save(root/'bridge.json',links);save(root/'shared_candidates.json',candidates)
    summary=dict(status=state['status'],jobs=len(plan['jobs']),pending=len(state['pending']),
        result_status=dict(Counter(r['status'] for r in state['results'])),
        suggested_links=sum(r['status']=='BRIDGE_SUGGESTED' for r in links),
        shared_candidate_links=len(candidates),reviews_collected=0,
        source_wide_overlap='NOT_MEASURED',error=state.get('error'))
    save(root/'summary.json',summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--report',action='store_true',help='Rebuild derived reports offline, without changing checkpoint')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--limit',type=int,default=2)
    parser.add_argument('--max-minutes',type=int,default=3)
    parser.add_argument('--profile',type=Path,default=Path(os.environ['LOCALAPPDATA'])/'FnbBrowserBenchmark/profiles-20261028T222347/seleniumbase')
    args=parser.parse_args()
    if not 1<=args.limit<=63 or not 1<=args.max_minutes<=60:
        parser.error('Use1..63 jobs and1..60 minutes')
    root=args.root.resolve()
    if not root.is_relative_to((ROOT/'output').resolve()): parser.error('Use a dedicated root under output/')
    if args.prepare:
        if root.exists() and any(root.iterdir()): parser.error('Prepare requires fresh output')
        root.mkdir(parents=True,exist_ok=True)
        plan=make_plan();save(root/'plan.json',plan)
        save(root/'state.json',dict(status='READY',results=[],pending=[j['source_key'] for j in plan['jobs']],
                                   in_flight=None,plan_sha256=sha(root/'plan.json')))
        print(json.dumps(dict(status='PREPARED',jobs=len(plan['jobs']),cache=plan['cache'])),flush=True)
    if not (args.check or args.live or args.report): return
    plan=json.loads((root/'plan.json').read_text(encoding='utf-8'))
    state=json.loads((root/'state.json').read_text(encoding='utf-8'))
    if sha(root/'plan.json')!=state['plan_sha256']: raise ValueError('Plan changed')
    for name,digest in plan['inputs'].items():
        if sha(ROOT/name)!=digest: raise ValueError('Source input changed')
    if args.report:
        print(json.dumps(write_reports(root,plan,state)),flush=True)
        if not (args.check or args.live):return
    profile=args.profile.resolve()
    allowed=(Path(os.environ['LOCALAPPDATA'])/'FnbBrowserBenchmark').resolve()
    if not profile.is_relative_to(allowed) or not (profile/'Default/Preferences').is_file() or not (profile/'Local State').is_file():
        raise ValueError('Use existing dedicated benchmark profile, never personal/fresh profile')
    from experiments.browser_framework.profiles import assert_available
    from experiments.browser_framework.run import environment
    assert_available([profile])
    env=environment(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
    save(root/'preflight.json',dict(chrome=env['chrome'],driver_version=env['driver_version'],
        chrome_path=env['chrome_path'],driver_path=env['driver_path'],uc=False,reviews=False))
    print('PREFLIGHT_PASS',flush=True)
    if not args.live:return
    if state['in_flight'] or state['status'] not in ('READY','PAUSED'):
        raise ValueError('Stop/in-flight state needs manual review; no automatic retry')
    if (state['status']=='PAUSED') != args.resume:
        raise ValueError('Use --resume only for PAUSED runs')
    from experiments.browser_framework.adapters import SeleniumBaseBrowser
    browser=SeleniumBaseBrowser(env['chrome_path'],env['driver_version'],env['driver_path'],profile)
    bykey={j['source_key']:j for j in plan['jobs']}
    start=time.monotonic(); count=0
    batches=root/'batches';batches.mkdir(exist_ok=True)
    save(batches/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'.json'),
         dict(resolver_sha256=sha(Path(__file__)),
              navigator_sha256=sha(ROOT/'crawl_experiment/sources/google_maps/navigator.py'),
              assessment_sha256=sha(ROOT/'crawl_experiment/sources/google_maps/place_resolution.py'),
              started_at=datetime.now(timezone.utc).isoformat(),job_limit=args.limit,reviews=False))

    def checkpoint():
        save(root/'state.json',state)
        write_reports(root,plan,state)
    state.update(status='RUNNING',error=None);checkpoint()
    try:
        browser.start()
        while state['pending'] and count<args.limit and time.monotonic()-start<args.max_minutes*60:
            key=state['pending'][0];state['in_flight']=key;checkpoint()
            time.sleep(3)
            row=resolve_one(bykey[key],browser.driver)
            state['results'].append(row);state['in_flight']=None;count+=1
            if row['status'] not in ('RESOLVED','AMBIGUOUS','NOT_FOUND'):
                state.update(status='STOPPED',error=row['status']);checkpoint();break
            state['pending'].pop(0);checkpoint()
            print(json.dumps(dict(source_key=key,status=row['status'],google_id_kind=row['google_id_kind'],
                                  completed=len(state['results']),pending=len(state['pending']))),flush=True)
        else:
            state['status']='PAUSED' if state['pending'] else 'COMPLETED'
    except KeyboardInterrupt:
        state.update(status='STOPPED' if state['in_flight'] else 'PAUSED',error='INTERRUPTED')
    except Exception:
        state.update(status='STOPPED',error='BROWSER_OR_RUNNER_ERROR')
    finally:
        try: browser.close()
        finally: checkpoint()
    print(json.dumps(dict(status=state['status'],report=str(root/'summary.json'),error=state.get('error'))),flush=True)


if __name__=='__main__':
    main()
