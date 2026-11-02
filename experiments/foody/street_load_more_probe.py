"""Bounded street-list load-more diagnosis, no POI details/reviews/publication."""
import argparse
import json
from pathlib import Path
import re
import time
import uuid
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout

import crawl
from full import observe_listing_response, pagination_parameters, response_id_metrics
from profile_access import available_profile
from streets import ROOT, INDEX, street_url, parse_street


def select_cases(entries, count, only_url=None):
    cases = {}
    for row in entries:
        match = re.search(r'([\d.,]+)\s*địa điểm', row['text'])
        if not match or not row.get('href'):
            continue
        displayed = int(re.sub(r'\D', '', match[1]))
        if displayed > 100:
            url = street_url(row['href'])
            cases[url] = dict(url=url, source_displayed_count=displayed,
                              name=row['text'].splitlines()[0])
    if only_url:
        url = street_url(only_url)
        if url not in cases:
            raise ValueError('Requested street must be an observed >100-count route')
        return [cases[url]]
    return sorted(cases.values(), key=lambda r:(-r['source_displayed_count'], r['url']))[:count]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-directory', type=Path, required=True)
    parser.add_argument('--entries', type=Path,
                        default=ROOT/'output/foody-q4-street-inspection/street-entries-121.json')
    parser.add_argument('--streets', type=int, default=2)
    parser.add_argument('--street-url', help='Select one observed >100-count street instead of ranked cases')
    parser.add_argument('--clicks', type=int, default=1)
    parser.add_argument('--max-minutes', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.streets <= 12 or not 1 <= args.clicks <= 70 or not 1 <= args.max_minutes <= 60:
        parser.error('Use <=12 streets, <=70 clicks, <=60 minutes')
    manifest = json.loads(args.entries.read_text(encoding='utf-8'))
    if manifest.get('source') != INDEX or manifest.get('status') != 200:
        parser.error('Expected verified Q4 street entries')
    cases = select_cases(manifest['entries'], args.streets, args.street_url)
    profile = available_profile(args.profile_directory)
    out = ROOT/'output'/('foody-street-load-more-'+uuid.uuid4().hex[:12])
    out.mkdir()
    report = dict(status='RUNNING', cases=[], reviews_collected=0, detail_requests=0,
                  click_limit_per_street=args.clicks, error=None)
    deadline = time.monotonic()+args.max_minutes*60

    def save():
        crawl.save(out/'summary.json', report)

    print(json.dumps(dict(report=str(out/'summary.json'), cases=cases)), flush=True)
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(profile), channel='chrome', headless=False,
                                                         viewport={'width':1280,'height':900})
            try:
                page = context.pages[0] if context.pages else context.new_page()
                for case in cases:
                    if time.monotonic() >= deadline:
                        raise crawl.CrawlError('DIAGNOSTIC_TIME_BUDGET')
                    current = dict(**case, loads=[], status='RUNNING')
                    report['cases'].append(current)
                    response = page.goto(case['url'], wait_until='domcontentloaded', timeout=30000)
                    if not response or response.status != 200 or page.url != case['url']:
                        raise crawl.CrawlError('STREET_NAVIGATION_REQUIRES_REVIEW')
                    page.wait_for_selector('.res-nearby-content-items', timeout=10000)
                    initial = parse_street(page.content())
                    seen = {r['url'] for r in initial['places']}
                    seen_ids = set()
                    current.update(initial_urls=sorted(seen), initial_count=len(seen))
                    save()
                    for click in range(1, args.clicks+1):
                        if time.monotonic() >= deadline:
                            raise crawl.CrawlError('DIAGNOSTIC_TIME_BUDGET')
                        controls = page.locator('a.load-more-result')
                        visible = [controls.nth(i) for i in range(controls.count()) if controls.nth(i).is_visible()]
                        if not visible:
                            current['status'] = 'NO_VISIBLE_MORE_UNVERIFIED'
                            break
                        if len(visible) != 1:
                            raise crawl.CrawlError('AMBIGUOUS_STREET_MORE_CONTROL')
                        metadata = dict(click=click, responses=[], click_state='NOT_ATTEMPTED')
                        current['loads'].append(metadata)
                        objects = []
                        finished = set()
                        def capture(response):
                            parts = urlsplit(response.url)
                            if response.request.resource_type in ('xhr','fetch') and (
                                    parts.hostname == 'foody.vn' or (parts.hostname or '').endswith('.foody.vn')):
                                if len(objects) < 20:
                                    objects.append(response)
                        def complete(request):
                            finished.add(id(request))
                        page.on('response', capture)
                        page.on('requestfinished', complete)
                        try:
                            time.sleep(3)
                            control = visible[0]
                            control.evaluate("n=>n.scrollIntoView({block:'center',behavior:'instant'})", timeout=10000)
                            try:
                                control.click(timeout=10000, no_wait_after=True)
                                metadata['click_state'] = 'RETURNED'
                            except BrowserTimeout:
                                metadata['click_state'] = 'TIMEOUT'
                            try:
                                page.wait_for_function('''before => {
                                  if (/captcha|access denied|just a moment/i.test(document.body?.innerText||'')) return true;
                                  return location.pathname!==before.path ||
                                    [...document.querySelectorAll('.res-nearby-content-items a[href]')]
                                    .some(a=>/^\\/ho-chi-minh\\/[^/]+$/.test(new URL(a.href).pathname) &&
                                      !new URL(a.href).search && !before.urls.includes(a.href));
                                }''', arg=dict(path=urlsplit(case['url']).path, urls=sorted(seen)), timeout=15000)
                            except BrowserTimeout:
                                metadata['result_wait'] = 'TIMEOUT'
                            page.wait_for_timeout(1000)
                        finally:
                            page.remove_listener('response', capture)
                            page.remove_listener('requestfinished', complete)
                            for response in objects:
                                part = urlsplit(response.url)
                                item = dict(path=part.path, status=response.status,
                                            pagination=pagination_parameters(response.url, response.request.post_data))
                                if response.status == 200 and id(response.request) in finished:
                                    evidence, ids = observe_listing_response(response)
                                    item.update(evidence, **response_id_metrics(ids, seen_ids))
                                    if ids is not None:
                                        seen_ids.update(ids)
                                metadata['responses'].append(item)
                            metadata['final_path'] = urlsplit(page.url).path
                            save()
                        if page.url != case['url']:
                            raise crawl.CrawlError('STREET_MORE_LEFT_STREET_PAGE')
                        if any(r['status'] in (401,403,429) for r in metadata['responses']):
                            raise crawl.CrawlError('ACCESS_RESTRICTION')
                        if any(r['status'] >= 500 for r in metadata['responses']):
                            raise crawl.CrawlError('STREET_MORE_SERVER_ERROR')
                        parsed = parse_street(page.content())
                        urls = {r['url'] for r in parsed['places']}
                        metadata.update(parser_urls=len(urls), new_urls=len(urls-seen),
                                        urls=sorted(urls), card_count=parsed['card_count'])
                        print(json.dumps(dict(street=case['name'], click=click,
                            click_state=metadata['click_state'], parser_urls=len(urls),
                            new_urls=metadata['new_urls'], requests=[dict(path=r['path'],status=r['status'],
                            pagination=r['pagination']) for r in metadata['responses']])), flush=True)
                        save()
                        if not urls-seen:
                            raise crawl.CrawlError('PAGINATION_NO_NEW_URLS')
                        seen.update(urls)
                    else:
                        current['status'] = 'BOUNDED_CHECK_COMPLETE'
                    current['unique_urls'] = len(seen)
                    save()
                report['status'] = 'BOUNDED_CHECK_COMPLETE'
            finally:
                context.close()
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status='STOPPED', error=str(exc) if isinstance(exc,crawl.CrawlError) else type(exc).__name__)
        if report['cases'] and report['cases'][-1]['status'] == 'RUNNING':
            report['cases'][-1]['status'] = 'STOPPED'
    finally:
        save()
    print(json.dumps(dict(status=report['status'],error=report['error'],report=str(out/'summary.json'))), flush=True)
    return 0 if report['status'] == 'BOUNDED_CHECK_COMPLETE' else 1


if __name__ == '__main__':
    raise SystemExit(main())
