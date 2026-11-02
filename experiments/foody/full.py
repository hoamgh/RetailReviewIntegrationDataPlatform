"""Foody UI pagination + HTTPX detail collection, owner-run and resumable.

No numeric POI cap. Does not bypass restrictions or paginate review bodies.
Only operator-verified collection/storage rights authorize a live run.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, parse_qsl

from bs4 import BeautifulSoup

import crawl
from login_profile import profile_directory

VERSION = 1


def pagination_parameters(url, payload=None):
    """Allowlist pagination keys; opaque cursors are hashed, never logged raw."""
    values = list(parse_qsl(urlsplit(url).query))
    if payload and len(payload) <= 16384:
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                values += list(obj.items())
        except (ValueError, TypeError):
            values += list(parse_qsl(payload))
    result = {}
    for key, value in values:
        key = key.lower()
        if key not in ('page', 'pageindex', 'pagenumber', 'pagesize', 'offset', 'limit', 'cursor', 'nextcursor'):
            continue
        value = str(value)
        if re.fullmatch(r'\d{1,10}', value):
            result[key] = int(value)
        elif key in ('cursor', 'nextcursor'):
            result[key + '_sha256'] = hashlib.sha256(value.encode()).hexdigest()
    return result


def response_restaurant_ids(obj):
    """Conservative schemas only; unknown JSON is never treated as an empty page."""
    if not isinstance(obj, dict):
        return None
    for key in ('Restaurants', 'restaurants', 'Items', 'items'):
        rows = obj.get(key)
        if not isinstance(rows, list):
            continue
        if not rows and key.lower() == 'items':
            return None  # Generic empty Items does not establish restaurant schema.
        ids = []
        for row in rows:
            if not isinstance(row, dict):
                return None
            value = row.get('RestaurantId', row.get('restaurantId'))
            if value is None and key.lower() == 'restaurants':
                value = row.get('Id', row.get('id'))
            if isinstance(value, bool) or not re.fullmatch(r'[1-9]\d*', str(value)):
                return None
            ids.append(str(value))
        return ids
    return None


def response_id_metrics(ids, seen):
    if ids is None:
        return dict(id_schema='UNKNOWN', response_id_count=None, unique_id_count=None,
                    new_id_count=None, duplicate_id_count=None)
    unique = set(ids)
    new = unique - seen
    return dict(id_schema='RECOGNIZED', response_id_count=len(ids), unique_id_count=len(unique),
                new_id_count=len(new), duplicate_id_count=len(ids) - len(new))


def response_shape(obj, depth=0):
    """Only allowlisted field names/types; never arbitrary keys or scalar values."""
    if depth >= 4:
        return {'type': type(obj).__name__}
    if isinstance(obj, list):
        return dict(type='list', length=len(obj),
                    first=response_shape(obj[0], depth+1) if obj else None)
    if isinstance(obj, dict):
        allowed = ('Data','data','Results','results','Restaurants','restaurants',
                   'Items','items','List','list','Id','id','RestaurantId','restaurantId')
        return dict(type='dict', fields={k:response_shape(obj[k], depth+1)
                    for k in allowed if k in obj}, other_field_count=sum(k not in allowed for k in obj))
    return {'type': type(obj).__name__}


def observe_listing_response(response):
    """Inspect an already finished response. No requests, bodies or credentials logged.

    Playwright buffers the entire body; the decoded-size check is after retrieval,
    not a streaming memory cap. Missing length is normal for chunked/HTTP2 responses.
    """
    limit = 2_000_000
    headers = response.headers
    length = headers.get('content-length')
    declared = int(length) if length and re.fullmatch(r'\d{1,12}', length) else None
    metadata = dict(content_length_state=('MISSING' if length is None else
                    'VALID' if declared is not None else 'INVALID'),
                    declared_bytes=declared, decoded_bytes=None, body_observation='NOT_READ',
                    response_ids=None)
    if declared is not None and declared > limit:
        metadata.update(body_observation='DECLARED_TOO_LARGE', id_schema='UNKNOWN_DECLARED_TOO_LARGE')
        return metadata, None
    try:
        body = response.body()
    except Exception:
        metadata.update(body_observation='READ_ERROR', id_schema='UNREADABLE')
        return metadata, None
    metadata.update(decoded_bytes=len(body), body_sha256=hashlib.sha256(body).hexdigest())
    if len(body) > limit:
        metadata.update(body_observation='DECODED_TOO_LARGE', id_schema='UNKNOWN_DECODED_TOO_LARGE')
        return metadata, None
    try:
        obj = json.loads(body)
    except (ValueError, UnicodeError):
        metadata.update(body_observation='INVALID_JSON', id_schema='UNKNOWN_INVALID_JSON')
        return metadata, None
    ids = response_restaurant_ids(obj)
    metadata.update(body_observation='JSON_READ', response_shape=response_shape(obj),
                    response_ids=sorted(set(ids), key=int) if ids is not None else None)
    return metadata, ids
MORE_SELECTOR = '.btn-load-more'
PRIMARY_MORE_SELECTOR = '.btn-load-more[data-bind*="handleClickLoadMoreResult"]'
PROGRESS_JS = '''() => {
 const primary=[...document.querySelectorAll('.btn-load-more[data-bind*="handleClickLoadMoreResult"]')];
 const visible=n=>!!(n.offsetWidth||n.offsetHeight||n.getClientRects().length);
 const controls=primary.length?primary:[...document.querySelectorAll('.btn-load-more')].filter(visible);
 return {
   links:[...new Set([...document.querySelectorAll('.filter-results a[href],.branch-list a[href],.brand-list a[href],.restaurant-list a[href]')]
     .map(n=>n.getAttribute('href')))].sort(),
   counters:controls.map(n=>(n.innerText.match(/\\d[\\d,.]*\\s*\\/\\s*\\d[\\d,.]*/)||[])[0]||''),
   loading:controls.some(n=>visible(n)&&/loading|đang tải/i.test(n.innerText)),
   primary:primary.length>0
 };
}'''


def pagination_evidence(html, more_visible, *, brand=False):
    soup = BeautifulSoup(html, 'html.parser')
    controls = soup.select(PRIMARY_MORE_SELECTOR) or soup.select(MORE_SELECTOR)
    texts = [crawl.clean(x.get_text(' ', strip=True)) for x in controls]
    counters = []
    for text in texts:
        match = re.search(r'(\d[\d,.]*)\s*/\s*(\d[\d,.]*)', text)
        if match:
            counters.append(tuple(int(re.sub(r'\D', '', x)) for x in match.groups()))
    counter = counters[0] if len(set(counters)) == 1 else None
    if more_visible:
        status = 'MORE'
    elif counter and counter[0] >= counter[1] and counter[1] > 0:
        status = 'COUNTER_EXHAUSTED'
    elif counter:
        status = 'INCOMPLETE_COUNTER'
    elif brand:
        status = 'UNVERIFIED_STATIC_BRAND'
    else:
        status = 'UNVERIFIED_NO_TERMINAL_EVIDENCE'
    return dict(status=status, loaded=counter[0] if counter else None,
                total=counter[1] if counter else None)


def discover(snapshot, advance, on_page, *, brand=False, budget=lambda: True):
    """Injectable pagination state machine; snapshots carry rendered HTML only."""
    previous = None
    while True:
        if not budget():
            raise crawl.CrawlError('TIME_BUDGET')
        html, visible = snapshot()
        if crawl.RESTRICTION.search(BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)):
            raise crawl.CrawlError('ACCESS_RESTRICTION')
        evidence = pagination_evidence(html, visible, brand=brand)
        fingerprint = hashlib.sha256(html.encode('utf-8')).hexdigest()
        if previous == fingerprint:
            raise crawl.CrawlError('PAGINATION_STALLED')
        on_page(html, evidence)
        if evidence['status'] != 'MORE':
            return evidence
        previous = fingerprint
        advance()


class BrowserListing:
    def __init__(self, page, *, seen_ids=(), metadata_sink=None):
        self.page = page
        self.last_diagnostic = None
        self.seen_ids = set(map(str, seen_ids))
        self.metadata_sink = metadata_sink

    def open(self, url):
        response = self.page.goto(url, wait_until='domcontentloaded', timeout=30000)
        if response is None or response.status != 200:
            raise crawl.CrawlError('BROWSER_HTTP_' + str(response.status if response else 'UNKNOWN'))
        if crawl.canonical(self.page.url) != url:
            raise crawl.CrawlError('REDIRECT_REQUIRES_REVIEW')
        if self.page.locator('input[type="password"]:visible').count():
            raise crawl.CrawlError('LOGIN_REQUIRED')
        # Wait for actual results, not h1 or merely DOMContentLoaded.
        self.page.wait_for_function('''brand => {
          const text=document.body?.innerText||'';
          if (/verify you are human|access denied|just a moment|captcha/i.test(text)) return true;
          const nodes=brand ? document.querySelectorAll('a[href]') :
            document.querySelectorAll('.filter-results a[href],.branch-list a[href],.brand-list a[href],.restaurant-list a[href]');
          const brandName=document.title.split('|')[0].trim().toLowerCase();
          return [...nodes].some(a => {
            const href=a.getAttribute('href')||'', label=(a.title||a.innerText).trim();
            return /\\/(ho-chi-minh|thuong-hieu)\\//.test(href) && !href.includes('{{') &&
              label && (!brand || (brandName && label.toLowerCase().includes(brandName) && href.includes('/ho-chi-minh/')));
          });
        }''', arg='/thuong-hieu/' in url, timeout=20000)

    def snapshot(self):
        controls = self.page.locator(PRIMARY_MORE_SELECTOR)
        if not controls.count():
            controls = self.page.locator(MORE_SELECTOR)
        visible = any(controls.nth(i).is_visible() for i in range(controls.count()))
        html = self.page.content()
        if len(html.encode('utf-8')) > 64 * 1024**2:
            raise crawl.CrawlError('RENDERED_PAGE_SIZE_LIMIT')
        return html, visible

    def advance(self):
        from playwright.sync_api import TimeoutError as BrowserTimeout
        controls = self.page.locator(PRIMARY_MORE_SELECTOR)
        if not controls.count():
            controls = self.page.locator(MORE_SELECTOR)
        visible = [controls.nth(i) for i in range(controls.count()) if controls.nth(i).is_visible()]
        if len(visible) > 1:
            raise crawl.CrawlError('AMBIGUOUS_LOAD_MORE_CONTROLS')
        control = visible[0] if visible else None
        if control is None:
            raise crawl.CrawlError('LOAD_MORE_CONTROL_DISAPPEARED')
        # Compare only results and pagination, not unrelated clocks/ads/reviews.
        before = self.page.evaluate(PROGRESS_JS)
        before_urls = set(crawl.parse_page(self.page.content(), self.page.url)['links'])
        responses = []
        response_objects = []
        started = time.monotonic()
        requests = {}
        phase = 'settle_before_click'
        def request_event(request, state):
            # Never retain URL queries, headers, bodies or raw failure messages.
            parts = urlsplit(request.url)
            if request.resource_type not in ('xhr', 'fetch') or not (
                    parts.hostname == 'foody.vn' or (parts.hostname or '').endswith('.foody.vn')):
                return
            key = id(request)
            if key not in requests and len(requests) < 100:
                requests[key] = dict(method=request.method, state=state,
                    pagination=pagination_parameters(request.url, getattr(request, 'post_data', None)),
                    started_seconds=round(time.monotonic() - started, 3))
            if key in requests:
                requests[key].update(state=state,
                    elapsed_seconds=round(time.monotonic() - started - requests[key]['started_seconds'], 3))
        listeners = {'request': lambda r: request_event(r, 'pending'),
                     'requestfinished': lambda r: request_event(r, 'finished'),
                     'requestfailed': lambda r: request_event(r, 'failed')}
        def capture(response):
            try:
                request = response.request
                parts = urlsplit(response.url)
                if request.resource_type not in ('xhr', 'fetch') or not (
                        parts.hostname == 'foody.vn' or (parts.hostname or '').endswith('.foody.vn')):
                    return
                safe = ('date', 'server', 'content-type', 'retry-after', 'via', 'x-cache')
                headers = response.headers
                if len(responses) < 30:
                    entry = dict(status=response.status, method=request.method,
                        pagination=pagination_parameters(response.url, getattr(request, 'post_data', None)),
                        origin=parts.scheme + '://' + parts.netloc,
                        path=re.sub(r'[A-Fa-f0-9]{24,}', '[REDACTED]', parts.path),
                        headers={key: headers[key] for key in safe if key in headers},
                        **response_id_metrics(None, self.seen_ids))
                    responses.append(entry)
                    if parts.path == '/ho-chi-minh/dia-diem':
                        response_objects.append((response, entry))
            except Exception:
                pass  # Never log raw response/error objects or request payloads.
        analyzed = set()
        def analyze_responses():
            for response, entry in response_objects:
                if id(entry) in analyzed:
                    continue
                # Do not wait for incomplete bodies or retain raw JSON in artifacts.
                request_state = requests.get(id(response.request), {}).get('state')
                if response.status != 200 or request_state != 'finished':
                    continue
                analyzed.add(id(entry))
                metadata, ids = observe_listing_response(response)
                entry.update(metadata)
                if ids is not None:
                    entry.update(response_id_metrics(ids, self.seen_ids))
                    self.seen_ids.update(ids)
        self.last_diagnostic = dict(before=before['counters'], responses=responses,
                                    primary_control=before.get('primary', False),
                                    wait_timeout_seconds=60, requests=[],
                                    click_state='NOT_ATTEMPTED', parser_url_count=None,
                                    new_parser_url_count=None,
                                    id_comparison_basis='completed_records_and_prior_recognized_responses',
                                    before_result_links=len(before['links']))
        self.page.on('response', capture)
        for event, listener in listeners.items():
            self.page.on(event, listener)
        time.sleep(3)
        try:
            phase = 'scroll'
            # Position the viewport without Playwright's separate stability wait.
            # Normal click below still enforces visibility, stability and hit testing.
            control.evaluate("node => node.scrollIntoView({block:'center', inline:'nearest', behavior:'instant'})",
                             timeout=10000)
            phase = 'click'
            self.last_diagnostic['click_state'] = 'ATTEMPTED'
            try:
                control.click(timeout=10000, no_wait_after=True)
                self.last_diagnostic['click_state'] = 'RETURNED'
            except (BrowserTimeout, TimeoutError) as exc:
                # A click may already have reached the server. Never click twice:
                # allow the same request/DOM update to complete before deciding.
                self.last_diagnostic['click_timeout'] = True
                self.last_diagnostic['click_state'] = 'TIMEOUT'
                message = str(exc).lower()
                self.last_diagnostic['click_blockers'] = [label for label, pattern in (
                    ('UNSTABLE', 'not stable'), ('INTERCEPTED', 'intercepts pointer events'),
                    ('OUTSIDE_VIEWPORT', 'outside of the viewport'), ('DETACHED', 'detached'),
                    ('NAVIGATION_WAIT', 'navigation')) if pattern in message]
            phase = 'wait_for_results'
            self.page.wait_for_function('''before => {
              if (/verify you are human|access denied|just a moment|captcha/i.test(document.body?.innerText||'')) return true;
              const current=(''' + PROGRESS_JS + ''')();
              const progress=JSON.stringify(current.links)!==JSON.stringify(before.links) ||
                (current.counters.some(Boolean) && JSON.stringify(current.counters)!==JSON.stringify(before.counters));
              return progress && !current.loading;
            }''', arg=before, timeout=60000)
            if any(x['status'] in (401, 403, 429) for x in responses):
                raise crawl.CrawlError('PAGINATION_HTTP_ACCESS_RESTRICTION')
            if any(x['status'] >= 500 for x in responses):
                raise crawl.CrawlError('PAGINATION_SERVER_ERROR')
            self.page.wait_for_timeout(1000)
            settled_html, settled_visible = self.snapshot()
            self.last_diagnostic['after'] = pagination_evidence(settled_html, settled_visible)
            if crawl.RESTRICTION.search(BeautifulSoup(settled_html, 'html.parser').get_text(' ', strip=True)):
                raise crawl.CrawlError('ACCESS_RESTRICTION')
            if urlsplit(self.page.url).hostname == 'id.foody.vn' or self.page.locator('input[type="password"]:visible').count():
                raise crawl.CrawlError('LOGIN_REQUIRED')
            parsed_urls = set(crawl.parse_page(settled_html, self.page.url)['links'])
            self.last_diagnostic.update(parser_url_count=len(parsed_urls),
                new_parser_url_count=len(parsed_urls - before_urls))
            analyze_responses()
            recognized = [e for _, e in response_objects if e['id_schema'] == 'RECOGNIZED']
            if recognized and not any(e['new_id_count'] for e in recognized):
                raise crawl.CrawlError('PAGINATION_NO_NEW_IDS')
            if not parsed_urls - before_urls:
                raise crawl.CrawlError('PAGINATION_NO_NEW_URLS')
        except Exception as exc:
            self.last_diagnostic['stop_code'] = (str(exc) if isinstance(exc, crawl.CrawlError)
                                                  else 'PAGINATION_STALLED_OR_TIMEOUT')
            analyze_responses()
            html, visible = self.snapshot()
            after = pagination_evidence(html, visible)
            self.last_diagnostic.update(after=after, error_type=type(exc).__name__)
            if crawl.RESTRICTION.search(BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)):
                self.last_diagnostic['stop_code'] = 'ACCESS_RESTRICTION'
                raise crawl.CrawlError('ACCESS_RESTRICTION') from exc
            host = urlsplit(self.page.url).hostname
            if host == 'id.foody.vn' or self.page.locator('input[type="password"]:visible').count():
                self.last_diagnostic['stop_code'] = 'LOGIN_REQUIRED'
                raise crawl.CrawlError('LOGIN_REQUIRED') from exc
            if any(x['status'] in (401, 403, 429) for x in responses):
                self.last_diagnostic['stop_code'] = 'PAGINATION_HTTP_ACCESS_RESTRICTION'
                raise crawl.CrawlError('PAGINATION_HTTP_ACCESS_RESTRICTION') from exc
            if any(x['status'] >= 500 for x in responses):
                self.last_diagnostic['stop_code'] = 'PAGINATION_SERVER_ERROR'
                raise crawl.CrawlError('PAGINATION_SERVER_ERROR') from exc
            recognized = [e for _, e in response_objects if e['id_schema'] == 'RECOGNIZED']
            if recognized and not any(e['new_id_count'] for e in recognized):
                self.last_diagnostic['stop_code'] = 'PAGINATION_NO_NEW_IDS'
                raise crawl.CrawlError('PAGINATION_NO_NEW_IDS') from exc
            if isinstance(exc, crawl.CrawlError):
                raise
            raise crawl.CrawlError('PAGINATION_STALLED_OR_TIMEOUT') from exc
        finally:
            self.page.remove_listener('response', capture)
            for event, listener in listeners.items():
                self.page.remove_listener(event, listener)
            elapsed = time.monotonic() - started
            for entry in requests.values():
                if entry['state'] == 'pending':
                    entry['elapsed_seconds'] = round(elapsed - entry['started_seconds'], 3)
            self.last_diagnostic.update(elapsed_seconds=round(elapsed, 3),
                last_phase=phase, requests=list(requests.values()))
            try:
                progress = self.page.evaluate(PROGRESS_JS)
                urls = set(crawl.parse_page(self.page.content(), self.page.url)['links'])
                self.last_diagnostic.update(after_result_links=len(progress['links']),
                    parser_url_count=len(urls), new_parser_url_count=len(urls - before_urls),
                    loading=progress.get('loading'), online=self.page.evaluate('() => navigator.onLine'),
                    control_visible=control.is_visible(), control_enabled=control.is_enabled())
                self.last_diagnostic['control_geometry'] = control.evaluate('''node => {
                  const r=node.getBoundingClientRect(), s=getComputedStyle(node);
                  const x=r.left+r.width/2,y=r.top+r.height/2;
                  const hit=document.elementFromPoint(x,y);
                  return {x:Math.round(x),y:Math.round(y),width:Math.round(r.width),
                    height:Math.round(r.height),in_viewport:x>=0&&x<innerWidth&&y>=0&&y<innerHeight,
                    center_hits_control:!!hit&&(hit===node||node.contains(hit)),
                    hit_tag:hit?.tagName||null,pointer_events:s.pointerEvents,
                    animation_active:node.getAnimations().some(a=>a.playState==='running')};
                }''', timeout=3000)
            except Exception:
                self.last_diagnostic['dom_probe_unavailable'] = True
            if self.metadata_sink:
                self.metadata_sink(self.last_diagnostic)


def coverage(state):
    statuses = [x['status'] for x in state['discovery'].values()]
    return dict(status=('ALL_DISCOVERED_PAGES_EXHAUSTED_WITH_COUNTER_EVIDENCE'
                        if not state.get('deferred_discovery_urls') and not state['pending'] and statuses and
                        all(x == 'COUNTER_EXHAUSTED' for x in statuses)
                        else 'PARTIAL_OR_UNVERIFIED'),
                is_full_district_count=False,
                note='Even UI exhaustion covers source results, not all real-world venues. Brand completeness and source caps may be unknown.',
                discovery_statuses=dict(Counter(statuses)))


def recover_discovered(source, seed, review_limit):
    """Create a new detail-only worklist, preserving the stopped source checkpoint."""
    source = Path(source)
    data = source.read_bytes()
    prior = json.loads(data)
    if (prior.get('version') != VERSION or prior.get('seed') != seed
            or prior.get('review_limit') != review_limit):
        raise ValueError('Recovery checkpoint configuration mismatch')
    reviewed_navigation_error = (prior.get('error') == 'EMPTY_OR_UNSUPPORTED_DETAIL'
        and prior.get('pending') and crawl.source_link_kind(prior['pending'][0]) == 'navigation')
    if prior.get('status') != 'STOPPED' or not (reviewed_navigation_error or prior.get('error') in (
            'PAGINATION_STALLED', 'PAGINATION_STALLED_OR_TIMEOUT')):
        raise ValueError('Recovery allowed only for reviewed pagination stalls, never access/challenge stops')
    pending = list(dict.fromkeys(crawl.canonical(x) for x in prior['pending']))
    deferred = list(dict.fromkeys(prior.get('deferred_discovery_urls', []) +
                    [x for x in pending if crawl.source_link_kind(x) in ('listing', 'brand')]))
    skipped = [x for x in pending if crawl.source_link_kind(x) == 'navigation']
    details = [x for x in pending if crawl.source_link_kind(x) == 'detail' and x not in prior['visited']]
    if not details:
        raise ValueError('No previously discovered detail URLs to collect')
    state = dict(version=VERSION, seed=seed, review_limit=review_limit,
        pending=details, visited=list(prior['visited']), records=dict(prior['records']),
        discovery={url: dict(proof) for url, proof in prior['discovery'].items()},
        deferred_discovery_urls=deferred, run_mode='DISCOVERED_DETAILS_ONLY',
        skipped_navigation_urls=prior.get('skipped_navigation_urls', []) + skipped,
        source_checkpoint_sha256=hashlib.sha256(data).hexdigest(), status='READY', error=None)
    return state


def continue_discovery(source, seed, review_limit):
    """Reopen only deferred discovery while retaining completed detail records."""
    data = Path(source).read_bytes()
    prior = json.loads(data)
    if (prior.get('version') != VERSION or prior.get('seed') != seed
            or prior.get('review_limit') != review_limit
            or prior.get('status') != 'EXHAUSTED_DISCOVERED_URLS'
            or prior.get('run_mode') != 'DISCOVERED_DETAILS_ONLY' or prior.get('pending')):
        raise ValueError('Continuation requires a completed detail-only checkpoint with matching configuration')
    pending = list(dict.fromkeys(crawl.canonical(x) for x in prior.get('deferred_discovery_urls', [])))
    if seed not in pending or any(crawl.source_link_kind(x) not in ('listing', 'brand') for x in pending):
        raise ValueError('Expected explicitly deferred seed/brand discovery URLs')
    return dict(version=VERSION, seed=seed, review_limit=review_limit, pending=pending,
                visited=list(prior['visited']), records=dict(prior['records']),
                discovery={url:dict(proof) for url,proof in prior['discovery'].items()},
                skipped_navigation_urls=list(prior.get('skipped_navigation_urls', [])),
                run_mode='CONTINUED_FULL_DISCOVERY', source_checkpoint_sha256=hashlib.sha256(data).hexdigest(),
                status='READY', error=None)


def fetch_detail(context, url):
    """Use the browser's own authenticated request context; never export cookies."""
    started = time.monotonic()
    response = context.request.get(url, timeout=20000, max_redirects=0)
    try:
        if response.status != 200:
            raise crawl.CrawlError('DETAIL_HTTP_' + str(response.status))
        if 'text/html' not in response.headers.get('content-type', '').lower():
            raise crawl.CrawlError('UNEXPECTED_CONTENT_TYPE')
        data = response.body()
        if len(data) > 2_000_000:
            raise crawl.CrawlError('RESPONSE_SIZE_LIMIT')
        return data.decode('utf-8', errors='replace'), dict(status=200, bytes=len(data),
            elapsed_seconds=round(time.monotonic() - started, 3),
            response_sha256=hashlib.sha256(data).hexdigest(),
            retrieved_at=datetime.now(timezone.utc).isoformat(), session='PRIVATE_FOODY_PROFILE')
    finally:
        response.dispose()


def run(seed, output, *, review_limit=5, resume=False, max_minutes=180, headless=False,
        storage_state=None, profile_directory_override=None, collect_discovered_from=None,
        continue_discovery_from=None):
    from playwright.sync_api import sync_playwright
    seed = crawl.canonical(seed)
    if profile_directory_override and storage_state:
        raise ValueError('Use either dedicated profile or storage state, not both')
    if collect_discovered_from and continue_discovery_from:
        raise ValueError('Use only one checkpoint continuation mode')
    manual_profile = None
    if profile_directory_override:
        from profile_access import available_profile
        manual_profile = available_profile(profile_directory_override)
    if not (0 <= review_limit <= 5 and 1 <= max_minutes <= 1440):
        raise ValueError('Review limit 0–5; time budget 1–1440 minutes')
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    checkpoint = output / 'full_state.json'
    if checkpoint.exists() and not resume:
        raise ValueError('Output exists; use --resume or a fresh --root')
    if not checkpoint.exists() and (output / 'pois.json').exists():
        raise ValueError('Refusing to mix full discovery with an existing static-crawl output; use a fresh --root')
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding='utf-8'))
    elif collect_discovered_from:
        state = recover_discovered(collect_discovered_from, seed, review_limit)
    elif continue_discovery_from:
        state = continue_discovery(continue_discovery_from, seed, review_limit)
    else:
        state = dict(version=VERSION, seed=seed, review_limit=review_limit, pending=[seed],
                     visited=[], records={}, discovery={}, status='READY', error=None)
    if (state['version'] != VERSION or state['seed'] != seed or state['review_limit'] != review_limit):
        raise ValueError('Full checkpoint configuration mismatch')
    if state['status'] == 'STOPPED':
        raise ValueError('Access/error stop requires manual review; no automatic retry')
    deadline = time.monotonic() + max_minutes * 60

    def save():
        crawl.save(checkpoint, state)
        crawl.save(output / 'pois.json', list(state['records'].values()))
        crawl.save(output / 'summary.json', dict(status=state['status'], error=state['error'],
                   places=len(state['records']), pending=len(state['pending']), coverage=coverage(state),
                   pagination='PLAYWRIGHT_UI_LOAD_MORE', review_limit=review_limit,
                   phase=state.get('phase'),
                   run_mode=state.get('run_mode', 'FULL_DISCOVERY'),
                   deferred_discovery_count=len(state.get('deferred_discovery_urls', [])),
                   skipped_navigation_count=len(state.get('skipped_navigation_urls', [])),
                   pagination_diagnostic=state.get('pagination_diagnostic')))
        with (output / 'progress.log').open('a', encoding='utf-8') as log:
            line = (f"{datetime.now(timezone.utc).isoformat()} status={state['status']} "
                    f"phase={state.get('phase','STARTUP')} places={len(state['records'])} pending={len(state['pending'])}")
            log.write(line + '\n')
        print(line, flush=True)

    state.update(status='RUNNING', error=None)
    listing = None
    try:
        with sync_playwright() as playwright:
            owned_browser = None
            if storage_state is not None:
                state_file = Path(storage_state)
                if not state_file.is_file():
                    raise crawl.CrawlError('STORAGE_STATE_MISSING')
                owned_browser = playwright.chromium.launch(channel='chrome', headless=headless)
                context = owned_browser.new_context(storage_state=str(state_file),
                                                    viewport={'width':1280, 'height':900})
            else:
                private_profile = manual_profile or profile_directory()
                if not private_profile.exists():
                    raise crawl.CrawlError('PRIVATE_FOODY_PROFILE_MISSING_RUN_LOGIN_HELPER')
                context = playwright.chromium.launch_persistent_context(str(private_profile),
                    channel='chrome', headless=headless, viewport={'width':1280, 'height':900})
            try:
                page = context.pages[0] if context.pages else context.new_page()
                def write_pagination_metadata(metadata):
                    # One sanitized record per attempted load; no bodies/auth state.
                    with (output / 'pagination.jsonl').open('a', encoding='utf-8') as log:
                        log.write(json.dumps(dict(recorded_at=datetime.now(timezone.utc).isoformat(),
                                                  **metadata), ensure_ascii=False) + '\n')
                        log.flush()
                listing = BrowserListing(page, seen_ids=[r['source_id'] for r in state['records'].values()
                                                        if r.get('source_id')],
                                         metadata_sink=write_pagination_metadata)
                while state['pending']:
                    if time.monotonic() >= deadline:
                        raise crawl.CrawlError('TIME_BUDGET')
                    url = state['pending'][0]
                    is_listing = url == seed or '/thuong-hieu/' in url
                    state['phase'] = 'DISCOVERY' if is_listing else 'DETAILS'
                    if is_listing:
                        listing.open(url)

                        def on_page(html, evidence):
                            parsed = crawl.parse_page(html, url)
                            if not parsed['links']:
                                raise crawl.CrawlError('EMPTY_OR_UNSUPPORTED_DISCOVERY')
                            # Queue actual observed branch/brand links only.
                            state['pending'].extend(x for x in parsed['links']
                                if x not in state['visited'] and x not in state['pending'])
                            state['discovery'][url] = dict(**evidence,
                                observed_links=len(parsed['links']),
                                rendered_html_sha256=hashlib.sha256(html.encode('utf-8')).hexdigest())
                            save()

                        evidence = discover(listing.snapshot, listing.advance, on_page,
                            brand='/thuong-hieu/' in url,
                            budget=lambda: time.monotonic() < deadline)
                        state['discovery'][url].update(evidence)
                    else:
                        time.sleep(3)
                        html, provenance = fetch_detail(context, url)
                        record = crawl.parse_page(html, url, review_limit)['record']
                        if record is None:
                            raise crawl.CrawlError('EMPTY_OR_UNSUPPORTED_DETAIL')
                        record['provenance'] = provenance
                        key = record['source_id'] or url
                        prior = state['records'].get(key)
                        if prior and prior['url'] != url:
                            raise crawl.CrawlError('SOURCE_ID_URL_CONFLICT')
                        state['records'][key] = record
                    state['pending'].pop(0)
                    state['visited'].append(url)
                    save()
                state['status'] = 'EXHAUSTED_DISCOVERED_URLS'
            finally:
                context.close()
                if owned_browser:
                    owned_browser.close()
    except KeyboardInterrupt:
        state.update(status='PAUSED', error='INTERRUPTED')
    except Exception as exc:
        error = str(exc) if isinstance(exc, crawl.CrawlError) else type(exc).__name__
        state.update(status='PAUSED' if error == 'TIME_BUDGET' else 'STOPPED', error=error)
        if listing is not None and listing.last_diagnostic is not None:
            state['pagination_diagnostic'] = listing.last_diagnostic
    finally:
        save()
    return state
