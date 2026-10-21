"""Overture-first study orchestration. Existing discovery and comment runners only."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path
from crawl_experiment.storage.place_catalog import DEFAULT_CATALOG, load_catalog
from experiments.tiktok_place_discovery.probe import normalized, run as discover_place, score_video, write
from experiments.tiktok_place_intelligence.probe import validate_dom_match
from .reliability import load_batch


def local_place_query(name, address):
    """Selected catalog scope is Khanh Hoi, District 4; anchor ambiguous business names."""
    street = address.split(',')[0].strip()
    return f"{name.split(' - ')[0].strip()} {street} Quận 4 TP Hồ Chí Minh"


def select_places(catalog, limit=30):
    groups=defaultdict(list);rejected=[]
    for p in load_catalog(catalog).places:
        reason=None
        if p.source!='overture' or normalized(p.admin_area_name).find('khanh hoi')<0: reason='NOT_OVERTURE_KHANH_HOI'
        elif not p.name or not normalized(p.name) or not p.address or not re.search(r'\d',p.address): reason='MISSING_SEARCHABLE_NAME_OR_PRECISE_ADDRESS'
        elif any(x in normalized(p.address) for x in ('go vap','quan 1 ','quan 7 ','tan hung','thu duc','binh thanh')): reason='ADDRESS_LOCATION_CONFLICT'
        elif any(m.group(1) != '4' for m in re.finditer(r'(?:quan|\bq)\s*(\d+)\b', normalized(p.address))): reason='ADDRESS_DISTRICT_CONFLICT'
        elif any(x in normalized(p.name) for x in ('homestay','hotel','khach san','bds','bat dong san','may in','studio','channel','officetel')): reason='NON_FOOD_NAME_REVIEW_REQUIRED'
        elif p.product_category_group=='bar' and not any(x in normalized(p.name) for x in ('bar','beer','pub','quan','lau','nuong','cocktail')): reason='BAR_CATEGORY_ONLY_AMBIGUOUS_NAME'
        elif p.primary_category=='food_and_drink' and not any(x in normalized(p.name) for x in ('banh','quan','lau','nuong','an vat','bbq','food','cafe','coffee','tea','tra','bun','pho','com','hu tieu')): reason='GENERIC_CATEGORY_AMBIGUOUS_BUSINESS_NAME'
        elif normalized(p.name).startswith('popeyes'): reason='PREVIOUS_MVP_BUSINESS_EXCLUDED_FOR_DIVERSITY'
        if reason:
            rejected.append({'canonical_place_id':p.place_key,'reason':reason});continue
        item={'place_id':p.place_key,'canonical_place_id':p.place_key,'overture_place_id':p.source_place_id,
              'canonical_name':p.name,'place_name':p.name,'address':p.address,'category':p.primary_category,
              'categories':p.categories,'product_category_group':p.product_category_group,
              'ward':'Khánh Hội','admin_area_name':p.admin_area_name,'lat':p.lat,'lng':p.lng,
              'brand':p.name,'search_category':'', 'catalog_path':str(Path(catalog).resolve())}
        item['queries']=[local_place_query(p.name,p.address)]
        groups[p.product_category_group].append(item)
    for values in groups.values():values.sort(key=lambda p:(normalized(p['place_name']),p['canonical_place_id']))
    selected=[];names=set()
    while len(selected)<limit and any(groups.values()):
        for group in sorted(groups):
            while groups[group]:
                p=groups[group].pop(0);name=normalized(p['place_name'])
                if name in names:continue
                names.add(name);selected.append(p);break
            if len(selected)>=limit:break
    return selected,rejected


def prepare(output,catalog=DEFAULT_CATALOG):
    if output.exists():raise ValueError('Use a fresh study preparation directory')
    places,rejected=select_places(catalog)
    output.mkdir(parents=True)
    digest=hashlib.sha256(Path(catalog).read_bytes()).hexdigest()
    plan={'study_id':output.name,'catalog_path':str(Path(catalog).resolve()),'catalog_sha256':digest,
          'places':places,'rejected_catalog_records':rejected,'verified_videos':0,
          'status':'DISCOVERY_PENDING_NOT_A_BENCHMARK_DATASET',
          'flow':'Overture → canonical places → existing TikTok discovery → strict matching → comment benchmark',
          'limits':{'places_per_discovery_batch':10,'queries_per_place':1,'results_per_query':10}}
    write(output/'plan.json',plan)
    for index in range(3):
        write(output/f'place-batch-{index+1:02d}.json',{'batch_id':f'place-batch-{index+1:02d}',
            'study_id':plan['study_id'],'catalog_sha256':digest,'places':places[index*10:(index+1)*10],
            'status':'REQUIRES_DISCOVERY_APPROVAL'})
    with (output/'places_queries.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=('canonical_place_id','overture_place_id','place_name','address','category','lat','lng','search_query'))
        writer.writeheader()
        for p in places:
            for q in p['queries']:writer.writerow({**{k:p[k] for k in writer.fieldnames if k!='search_query'},'search_query':q})
    return plan


def validate_plan(plan):
    catalog=Path(plan['catalog_path'])
    if hashlib.sha256(catalog.read_bytes()).hexdigest()!=plan['catalog_sha256']:raise ValueError('Catalog changed; review study again')
    canonical={p.place_key:p for p in load_catalog(catalog).places}
    for p in plan['places']:
        c=canonical[p['canonical_place_id']]
        if (c.source_place_id,c.name,c.address)!=(p['overture_place_id'],p['place_name'],p['address']):raise ValueError('Catalog mapping mismatch')
    return plan


def discover(plan,batch_index,output):
    # One existing UC owner, sequential existing discovery.run calls, no comments.
    from experiments.tiktok_access_benchmark.benchmark import BrowserObserver
    validate_plan(plan)
    places=plan['places'][(batch_index-1)*10:batch_index*10]
    if not places:raise ValueError('Empty discovery batch')
    if output.exists():raise ValueError('Fresh discovery output required')
    output.mkdir(parents=True);owner=None;rows=[];stop='STARTING'
    def checkpoint():
        write(output/'discovery_batch_summary.json',{'study_id':plan['study_id'],'batch_index':batch_index,
            'stop_reason':stop,'places':rows,'automatic_next_batch':False,'comments_collected':0})
    checkpoint()
    try:
        owner=BrowserObserver(capture_dir=output)
        if not getattr(owner.driver,'_is_using_uc',False):raise RuntimeError('UC_REQUIRED')
        for index,place in enumerate(places):
            folder=output/f'place-{index+1:02d}'
            report=discover_place(place,place['queries'],folder,live=True,limit=10,driver=owner.driver)
            rows.append({'canonical_place_id':place['canonical_place_id'],'overture_place_id':place['overture_place_id'],
                         'folder':folder.name,'stop_reason':report['stopped_reason']})
            stop=report['stopped_reason'];checkpoint()
            if stop!='COMPLETED':break
    except Exception as exc:
        stop=type(exc).__name__;checkpoint()
    finally:
        if owner:owner.close()
        checkpoint()
    return {'stop_reason':stop,'attempted_places':len(rows),'automatic_next_batch':False}


def review(plan,source,output, *, multiple_per_place=False, creator_limit=2):
    validate_plan(plan)
    places={p['canonical_place_id']:p for p in plan['places']}
    accepted=[];rejected=[];seen_videos=set();seen_places=set();creator_counts=Counter()
    for file in sorted(source.rglob('unique_videos.json')):
        canonical_file=file.with_name('canonical_place.json')
        if not canonical_file.exists():continue
        evidence_place=json.loads(canonical_file.read_text(encoding='utf-8'))
        place=places.get(evidence_place.get('place_id'))
        if not place:continue
        if (evidence_place.get('canonical_name'),evidence_place.get('address'))!=(place['place_name'],place['address']):raise ValueError('Discovery place evidence mismatch')
        for video in json.loads(file.read_text(encoding='utf-8')):
            match=score_video(video,place);reason=None
            query=video.get('query_used') or video.get('query')
            creator=video.get('creator_username') or video.get('author_username')
            if query not in place['queries']:reason='QUERY_NOT_IN_OVERTURE_PLAN'
            elif match['decision']!='MATCH' or 'ADDRESS_MATCH' not in match['signals']:reason='AMBIGUOUS_OR_REJECTED_LOCATION'
            elif not creator or not re.fullmatch(r'[\w.]+',creator):reason='MISSING_PUBLIC_CREATOR'
            elif video['video_id'] in seen_videos:reason='DUPLICATE_VIDEO'
            elif not multiple_per_place and place['place_id'] in seen_places:reason='EXTRA_VIDEO_SAME_BUSINESS'
            elif creator_limit is not None and creator_counts[creator]>=creator_limit:reason='CREATOR_DIVERSITY_CAP'
            if reason:
                rejected.append({'video_id':video.get('video_id'),'canonical_place_id':place['place_id'],'reason':reason});continue
            url=video.get('video_url') or f"https://www.tiktok.com/@{creator}/video/{video['video_id']}"
            selected={**video,'decision':'MATCH','place_id':place['place_id'],'navigation_target':url}
            mapping={'canonical_place_id':place['place_id'],'overture_place_id':place['overture_place_id'],
                'place_name':place['place_name'],'search_query':query,'video_url':url,'video_id':video['video_id'],
                'creator':creator,'discovery_timestamp':video.get('observed_at'),
                'match_confidence':match['match_score'],'match_confidence_kind':'HEURISTIC_NOT_CALIBRATED_PROBABILITY',
                'match_evidence':{'signals':match['signals'],'caption':video.get('caption'),'address':place['address'],
                                  'category':place['category'],'lat':place['lat'],'lng':place['lng']},
                'source_file':str(file),'source_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
                'url_provenance':'OBSERVED_SOURCE_URL' if video.get('video_url') else 'DERIVED_FROM_OBSERVED_PUBLIC_ID_AND_CREATOR',
                'verification':'CATALOG_AND_EXPLICIT_NAME_ADDRESS_TEXT; current availability and visual content unverified'}
            item={'selected_video':selected,'place':place,'integration_mapping':mapping}
            validate_dom_match(item,plan['catalog_path'])
            accepted.append(item);seen_videos.add(video['video_id']);seen_places.add(place['place_id']);creator_counts[creator]+=1
    if output.exists():raise ValueError('Fresh reviewed output required')
    output.mkdir(parents=True)
    for index in range(3):
        inputs=accepted[index*10:(index+1)*10]
        manifest={'study_id':plan['study_id'],'batch_id':f'batch-{index+1:02d}','inputs':inputs,
            'inputs_sha256':hashlib.sha256(json.dumps(inputs,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
            'status':'READY_FOR_MAPPING_REVIEW' if inputs else 'EMPTY_NOT_EXECUTABLE','max_comments':7}
        path=output/f'batch-{index+1:02d}.json';write(path,manifest)
        if inputs:load_batch(path)
    summary={'accepted_mappings':len(accepted),'rejected_candidates':rejected,
        'unmatched_places':[p['canonical_place_id'] for p in plan['places'] if p['place_id'] not in seen_places],
        'benchmark_live_approved':False,'next_action':'Review every mapping and explicitly approve one nonempty batch.'}
    write(output/'mapping_summary.json',summary)
    return summary


def query_candidates(place,source,limit=10, *, observed_links=None):
    """Search results are explicitly UNVERIFIED associations, not verified matches."""
    if not 1<=limit<=10:raise ValueError('Candidate limit must be 1..10')
    records=json.loads((source/'unique_videos.json').read_text(encoding='utf-8'))
    inputs=[];seen=set()
    observed={r['video_id']:r['video_url'] for r in observed_links} if observed_links is not None else None
    for video in records:
        query=video.get('query_used') or video.get('query')
        creator=video.get('creator_username') or video.get('author_username')
        video_id=str(video.get('video_id') or '')
        if query not in place['queries'] or not video_id.isdigit() or video_id in seen:
            continue
        if not creator or not re.fullmatch(r'[\w.]+',creator):continue
        if observed is not None and video_id not in observed:continue
        url=observed[video_id] if observed is not None else video.get('video_url') or f'https://www.tiktok.com/@{creator}/video/{video_id}'
        selected={**video,'decision':'REVIEW','place_id':place['place_id'],'navigation_target':url,
                  'video_url':url,'navigation_provenance':'OBSERVED_SEARCH_CARD' if observed is not None else 'SOURCE_OR_DERIVED'}
        inputs.append({'place':place,'selected_video':selected,
            'association_policy':'QUERY_CANDIDATE_UNVERIFIED',
            'integration_mapping':{'canonical_place_id':place['place_id'],'overture_place_id':place['overture_place_id'],
                'place_name':place['place_name'],'video_id':video_id,'video_url':url,'search_query':query,
                'navigation_provenance':selected['navigation_provenance'],
                'match_confidence':None,'verification_status':'QUERY_CANDIDATE_UNVERIFIED',
                'match_evidence':{'retrieval_query':query},'creator':creator,
                'discovery_timestamp':video.get('observed_at'),'view_count':video.get('view_count'),
                'like_count':video.get('like_count'),'stats_source':'DISCOVERY_SEARCH_RESPONSE'}})
        seen.add(video_id)
        if len(inputs)>=limit:break
    return inputs


def automatic_crawl(plan,batch_index,output,*,run_id,max_videos=100,videos_per_store=10,persistent_profile=False,manual_login=False,auth_wait_seconds=20,manual_captcha=False,manual_captcha_timeout=300,reply_limit=0,reply_expand_limit=0,comment_limit=7,comment_scrolls=3):
    """Search one store, collect its strict matches, then move to the next store."""
    from experiments.tiktok_access_benchmark.benchmark import BrowserObserver
    from experiments.tiktok_access_benchmark.ui_flow import NetworkCapture
    from experiments.tiktok_comment_dom.probe import SeleniumDOMPort
    from .reliability import run_live, write_report
    if not 1<=max_videos<=100 or not 1<=videos_per_store<=10 or not re.fullmatch(r'[A-Za-z0-9_.-]{1,65}',run_id):
        raise ValueError('One bounded batch: max 100 videos total, 10 per store; valid run ID required')
    if manual_login and not persistent_profile:raise ValueError('Manual login requires persistent profile')
    if manual_captcha and not persistent_profile:raise ValueError('Manual CAPTCHA requires persistent profile')
    if not 0<manual_captcha_timeout<=300:raise ValueError('Manual CAPTCHA timeout must be between 0 and 300 seconds')
    if not 0<=auth_wait_seconds<=300:raise ValueError('Auth wait must be between 0 and 300 seconds')
    if not isinstance(reply_limit,int) or isinstance(reply_limit,bool) or not 0<=reply_limit<=50:raise ValueError('Reply limit must be 0..50')
    if not isinstance(reply_expand_limit,int) or isinstance(reply_expand_limit,bool) or not 0<=reply_expand_limit<=5 or (reply_expand_limit and not reply_limit):raise ValueError('Reply expansion requires reply_limit>0 and expansion limit0..5')
    if type(comment_limit) is not int or not 1<=comment_limit<=50:raise ValueError('Comment limit must be 1..50')
    if type(comment_scrolls) is not int or not 0<=comment_scrolls<=10:raise ValueError('Comment scrolls must be 0..10')
    profile=None
    if persistent_profile:
        import os
        local=os.environ.get('LOCALAPPDATA')
        if not local:raise ValueError('LOCALAPPDATA required for existing login profile')
        profile=Path(local)/'TikTokCrawler'/'Profile'
        if not profile.is_dir():raise ValueError('Existing login profile missing; manual login required')
    validate_plan(plan)
    places=plan['places'][(batch_index-1)*10:batch_index*10]
    if not places:raise ValueError('Empty place batch')
    if output.exists():raise ValueError('Fresh output required')
    output.mkdir(parents=True)
    rows=[];inputs=[];stores=[];owner=None;stop='STARTING'
    authentication={'stage':'NOT_REACHED','state':None,'reason':None,'challenge_signal_observed':None,
                    'manual_verification_waits':0,'verification_outcome':'NOT_ATTEMPTED',
                    'evidence_kind':'AUTH_DOM_UI_ONLY','rate_scope':'EXCLUDED_FROM_PER_VIDEO_DENOMINATOR'}
    manifest={'study_id':plan['study_id'],'batch_id':f'batch-{batch_index:02d}',
        'run_id':run_id,'planned_videos':max_videos,'mode':'OVERTURE_SEARCH_MATCH_COLLECT_PER_STORE',
        'automatic_next_batch':False,'catalog_sha256':plan['catalog_sha256'],
        'videos_per_store':videos_per_store,'association_policy':'QUERY_CANDIDATE_UNVERIFIED',
        'profile_mode':'EXISTING_DEDICATED_LOGIN' if persistent_profile else 'DEFAULT',
        'authentication_policy':'REQUIRE_SIGNED_IN_UI' if persistent_profile else 'DEFAULT_PUBLIC_POLICY',
        'manual_login':manual_login,'auth_wait_seconds':auth_wait_seconds,
        'manual_captcha':manual_captcha,'manual_captcha_timeout':manual_captcha_timeout,'reply_limit':reply_limit,'reply_expand_limit':reply_expand_limit,'comment_limit':comment_limit,'comment_scrolls':comment_scrolls}
    write(output/'run_manifest.json',manifest)
    def checkpoint():
        write(output/'store_progress.json',{'stores':stores,'matched_inputs':inputs,'stop_reason':stop,
                                           'comments_scope':'TOP_LEVEL_AND_VISIBLE_REPLIES' if reply_limit else 'TOP_LEVEL_ONLY'})
        return write_report(output,rows,stop,session_authentication=authentication)
    def observe_auth(result):
        authentication.update(stage='AUTHENTICATION',state=result['state'],reason=result['reason'])
        if result['reason'] in ('MANUAL_VERIFICATION_REQUIRED','CHALLENGE'):
            authentication['challenge_signal_observed']=True
    checkpoint()
    try:
        owner=BrowserObserver(capture_dir=output,**({'profile_dir':profile} if profile is not None else {}))
        if not getattr(owner.driver,'_is_using_uc',False):raise RuntimeError('UC_REQUIRED')
        network=NetworkCapture(owner.driver);network.start()
        port=SeleniumDOMPort(driver=owner.driver,network=network)
        if persistent_profile:
            from .auth_session import AuthMonitor,wait_for_auth,manual_auth,checked_auth
            from .network import decision
            network.phase='AUTHENTICATION'
            owner.driver.get('https://www.tiktok.com/')
            monitor=AuthMonitor(owner.driver,output=output/'authentication')
            def restriction_check(result):
                network.drain()
                write(output/'authentication'/'network_evidence.json',network.entries)
                return decision(network.entries,result['state']=='AUTH_READY')
            print(f'Waiting up to {auth_wait_seconds:g}s for signed-in UI on the same browser.',flush=True)
            auth=wait_for_auth(monitor,auth_wait_seconds,restriction_check=restriction_check)
            observe_auth(auth)
            if (manual_captcha and auth['state']=='AUTH_REQUIRED' and
                    auth['reason']=='MANUAL_VERIFICATION_REQUIRED'):
                from types import SimpleNamespace
                from experiments.tiktok_comment_dom.probe import InteractiveChallengeResolver
                from .ui_flow import StopFlow
                authentication.update(manual_verification_waits=1,verification_outcome='WAITING_FOR_HUMAN',
                                      timeout_seconds=manual_captcha_timeout)
                stop='WAITING_MANUAL_CAPTCHA';checkpoint()
                print(f'CAPTCHA pause: verify and finish login in the same Chrome window (max {manual_captcha_timeout:g}s). No automatic solving or new session.',flush=True)
                def auth_snapshot():
                    current=checked_auth(monitor,restriction_check);observe_auth(current)
                    if current['state'] in ('AUTH_BLOCKED','AUTH_EXPIRED'):
                        raise StopFlow(current['reason'] if current['state']=='AUTH_BLOCKED' else current['state'])
                    return {'url':owner.driver.current_url,'authentication_state':current['state'],
                            'challenge_present':current['reason']=='MANUAL_VERIFICATION_REQUIRED'}
                auth_port=SimpleNamespace(driver=owner.driver,snapshot=auth_snapshot)
                resolver=InteractiveChallengeResolver(timeout=manual_captcha_timeout,
                    ready_predicate=lambda snap:snap.get('authentication_state')=='AUTH_READY')
                try:
                    resolved=resolver(auth_port,{'challenge_reason':'AUTHENTICATION_CHALLENGE_SIGNAL'})
                except StopFlow as exc:
                    resolved={'ok':False,'reason':str(exc)}
                if not resolved.get('ok'):
                    stop=resolved.get('reason','CHALLENGE_UNRESOLVED')
                    authentication['verification_outcome']=stop
                    return checkpoint()
                auth=checked_auth(monitor,restriction_check);observe_auth(auth)
                authentication['verification_outcome']='CLEARED_UI' if auth['state']=='AUTH_READY' else 'REVALIDATION_FAILED'
            if (manual_login and (auth['state']=='AUTH_READY' or
                    (auth['state']=='AUTH_REQUIRED' and
                     auth['reason'] in ('NO_VERIFIED_SIGNED_IN_UI','RETURN_TO_ONE_TIKTOK_TAB')))):
                print('Browser retained for manual login. Finish login, then enter READY; CLOSE cancels.',flush=True)
                accepted=manual_auth(owner,output=output/'authentication',navigate=False,allow_public=False,
                                     monitor=monitor,restriction_check=restriction_check)
                auth=monitor.current_result
                observe_auth(auth)
                if not accepted:
                    stop=auth['reason'] if auth['state']=='AUTH_BLOCKED' else auth['state'] if auth['state']=='AUTH_EXPIRED' else 'MANUAL_AUTH_CANCELLED'
                    return checkpoint()
                auth=checked_auth(monitor,restriction_check)
                observe_auth(auth)
            print(f"Authentication: {auth['state']} ({auth['reason']})",flush=True)
            if auth['state']!='AUTH_READY':
                stop=auth['reason'] if auth['reason'] in ('ACCESS_DENIED','RATE_LIMITED','AUTH_GATE','UNKNOWN_403','CHALLENGE') else 'CHALLENGE' if auth['reason']=='MANUAL_VERIFICATION_REQUIRED' else auth['state']
                return checkpoint()
        for index,place in enumerate(places):
            folder=output/f'store-{index+1:02d}';folder.mkdir()
            print(f"Store {index+1}/{len(places)}: {place['place_name']} | {place['address']}",flush=True)
            discovery=discover_place(place,place['queries'],folder/'discovery',live=True,limit=10,driver=owner.driver)
            stores.append({'canonical_place_id':place['place_id'],'discovery_stop':discovery['stopped_reason']})
            if discovery['stopped_reason']!='COMPLETED':
                stop='DISCOVERY_'+discovery['stopped_reason'];checkpoint();break
            from experiments.tiktok_access_benchmark.crawler import UIPort, LINKS_JS, candidates as observed_candidates
            navigation_dir=folder/'navigation';navigation_dir.mkdir()
            ui=UIPort(owner.driver,navigation_dir)
            ui.observed_card_navigation=True
            query=place['queries'][0]
            ui.search(query)
            observed={}
            for scan in range(4):
                ui.state()
                for item in observed_candidates(owner.driver.execute_script(LINKS_JS)):
                    observed[item['video_id']]=item
                if len(observed)>=videos_per_store or scan==3:break
                owner.driver.execute_script('window.scrollBy(0, Math.max(400, innerHeight * 0.8));')
                ui.sleep(.5)
            candidates=query_candidates(place,folder/'discovery',videos_per_store,observed_links=list(observed.values()))
            write(folder/'query_candidates.json',candidates)
            seen={i['selected_video']['video_id'] for i in inputs}
            candidates=[i for i in candidates if i['selected_video']['video_id'] not in seen][:max_videos-len(rows)]
            if not candidates:
                stores[-1]['comments_stop']='NO_SEARCH_VIDEO_CANDIDATE';stop='IN_PROGRESS';checkpoint();continue
            inputs.extend(candidates)
            print(f"Collecting {len(candidates)} search-card videos for this store",flush=True)
            network=ui.network;port.network=network
            by_id={item['selected_video']['video_id']:item for item in candidates}
            def navigate_card(url):
                from urllib.parse import urlparse
                video_id=urlparse(url).path.rsplit('/',1)[-1]
                item=by_id[video_id]['selected_video']
                ui.close_overlay()
                if not ui.search_ready(query) and '/video/' in urlparse(owner.driver.current_url).path:
                    owner.driver.back()
                    ui.wait(lambda state:ui.search_ready(query),'SEARCH_RETURN_TIMEOUT')
                action={'video_id':video_id,'observed_url':item['navigation_target'],
                        'method':'EXISTING_UIPort_NATIVE_CARD_CLICK','status':'ATTEMPTED'}
                write(navigation_dir/f'{video_id}.json',action)
                try:
                    ui.open_video({'video_id':video_id,'video_url':item['navigation_target']},query)
                except Exception as exc:
                    action.update(status='FAILED',error_type=type(exc).__name__)
                    write(navigation_dir/f'{video_id}.json',action)
                    raise
                action['status']='OPENED';write(navigation_dir/f'{video_id}.json',action)
            port.navigation_handler=navigate_card
            report=run_live(candidates,folder/'comments',run_id=f'{run_id}-s{index+1}',limit=comment_limit,
                existing_port=port,persistence_root=output,allow_query_candidates=True,comment_scrolls=comment_scrolls,reply_limit=reply_limit,reply_expand_limit=reply_expand_limit,batch_metadata={'study_id':plan['study_id'],
                'batch_id':f'batch-{batch_index:02d}'})
            rows.extend(report['videos']);stores[-1]['comments_stop']=report['stop_reason']
            stop=report['stop_reason'];checkpoint()
            if stop!='TEST_SET_COMPLETED' or len(rows)>=max_videos:break
        else:stop='PLACE_BATCH_COMPLETED'
    except KeyboardInterrupt:
        stop='OPERATOR_CANCELLED'
        if authentication['verification_outcome']=='WAITING_FOR_HUMAN':
            authentication['verification_outcome']='OPERATOR_CANCELLED'
    except Exception as exc:
        stop=type(exc).__name__
        # No exception text/locals: they can contain URLs, tokens or personal data.
        import traceback
        frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                for f in traceback.extract_tb(exc.__traceback__)]
        write(output/'error.json',{'error_type':stop,'frames':frames})
        print('Runner error: '+stop+'; see '+str(output/'error.json'),flush=True)
    finally:
        if owner:owner.close()
        report=checkpoint()
        # All per-video persistence owners and the browser have now closed.
        # Keep analysis outside the raw lake to avoid double-counting events.
        from experiments.tiktok_comment_dom.compaction import finalize_analysis, publish_json
        report['analysis_output'] = finalize_analysis(output)
        publish_json(output/'reliability_report.json', report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('prepare','discover','review','auto-crawl'),required=True)
    parser.add_argument('--catalog',type=Path,default=DEFAULT_CATALOG)
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--batch-index',type=int,choices=(1,2,3),default=1)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run-id')
    parser.add_argument('--max-videos',type=int,default=100)
    parser.add_argument('--videos-per-store',type=int,default=10)
    parser.add_argument('--persistent-profile',action='store_true',help='Use the existing private TikTok login profile; require signed-in UI before discovery')
    parser.add_argument('--manual-login',action='store_true',help='Keep the same browser for operator login if signed-in UI is not confirmed; requires persistent profile')
    parser.add_argument('--auth-wait-seconds',type=float,default=20,help='Bounded UI hydration wait before login decision (0-300 seconds)')
    parser.add_argument('--manual-captcha',action='store_true',help='Pause the same browser for human CAPTCHA verification during authentication; requires persistent profile')
    parser.add_argument('--manual-captcha-timeout',type=float,default=300,help='Human verification wait budget, at most 300 seconds')
    parser.add_argument('--comment-limit',type=int,default=7,help='Top-level limit per video, 1..50')
    parser.add_argument('--comment-scrolls',type=int,default=3,help='Bounded comment scroll attempts, 0..10')
    parser.add_argument('--reply-limit',type=int,default=0,help='Bounded visible replies per video (0-50); no automatic expansion, full reply coverage unverified')
    parser.add_argument('--reply-expand-limit',type=int,default=0,help='Bounded native reply-control clicks per video (0-5); requires reply-limit>0')
    args=parser.parse_args()
    if not 1<=args.comment_limit<=50 or not 0<=args.comment_scrolls<=10:parser.error('Comment limit1..50 and scrolls0..10 required')
    if args.manual_login and not args.persistent_profile:parser.error('--manual-login requires --persistent-profile')
    if args.manual_captcha and not args.persistent_profile:parser.error('--manual-captcha requires --persistent-profile')
    if not 0<args.manual_captcha_timeout<=300:parser.error('--manual-captcha-timeout must be between 0 and 300')
    if not 0<=args.auth_wait_seconds<=300:parser.error('--auth-wait-seconds must be between 0 and 300')
    if not 0<=args.reply_limit<=50:parser.error('--reply-limit must be 0..50')
    if not 0<=args.reply_expand_limit<=5 or (args.reply_expand_limit and not args.reply_limit):parser.error('--reply-expand-limit must be0..5 and requires --reply-limit>0')
    if args.mode=='prepare':
        r=prepare(args.output,args.catalog);print(json.dumps({'selected_places':len(r['places']),'verified_videos':0}));return
    if not args.plan:parser.error('--plan required')
    plan=json.loads(args.plan.read_text(encoding='utf-8'))
    if args.mode=='auto-crawl':
        if not args.run_id:parser.error('--run-id required for auto-crawl')
        r=automatic_crawl(plan,args.batch_index,args.output,run_id=args.run_id,max_videos=args.max_videos,videos_per_store=args.videos_per_store,persistent_profile=args.persistent_profile,manual_login=args.manual_login,auth_wait_seconds=args.auth_wait_seconds,manual_captcha=args.manual_captcha,manual_captcha_timeout=args.manual_captcha_timeout,reply_limit=args.reply_limit,reply_expand_limit=args.reply_expand_limit,comment_limit=args.comment_limit,comment_scrolls=args.comment_scrolls)
        print(json.dumps({'stop_reason':r['stop_reason'],'session_authentication':r.get('session_authentication'),'aggregate':r['aggregate'],'analysis_output':r.get('analysis_output')},indent=2));return
    if args.mode=='discover':r=discover(plan,args.batch_index,args.output)
    else:
        if not args.source:parser.error('--source required')
        r=review(plan,args.source,args.output)
    print(json.dumps({k:v for k,v in r.items() if k not in ('rejected_candidates','unmatched_places')},indent=2))


if __name__=='__main__':main()
