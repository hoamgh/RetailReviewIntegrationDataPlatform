"""Owner-run Q4 street observations. No reviews, load-more clicks or publication."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

from bs4 import BeautifulSoup

import crawl
from full import fetch_detail
from profile_access import available_profile

ROOT = Path(__file__).resolve().parents[2]
INDEX = 'https://www.foody.vn/ho-chi-minh/khu-vuc-quan-4'


def street_url(value):
    url = crawl.canonical(value)
    if not re.fullmatch(re.escape(INDEX) + r'/tren-duong-[a-z0-9-]+', url):
        raise ValueError('Only observed Q4 street URLs are accepted')
    return url


def parse_street(html):
    soup = BeautifulSoup(html, 'html.parser')
    if crawl.RESTRICTION.search(soup.get_text(' ', strip=True)):
        raise crawl.CrawlError('ACCESS_RESTRICTION')
    if soup.select('input[type=password]'):
        raise crawl.CrawlError('LOGIN_REQUIRED')
    cards = soup.select('.res-nearby-content-items')
    if not cards:
        raise crawl.CrawlError('EMPTY_OR_UNSUPPORTED_STREET_LAYOUT')
    places = {}
    for card in cards:
        for anchor in card.select('a[href]'):
            try:
                url = crawl.canonical(anchor['href'])
            except ValueError:
                continue
            if crawl.source_link_kind(url) != 'detail':
                continue
            name = crawl.clean(anchor.get('title') or anchor.get_text(' ', strip=True))
            if url not in places or (name and not places[url]['name']):
                places[url] = dict(url=url, name=name)
    if not places:
        raise crawl.CrawlError('NO_VALID_STREET_PLACE_URLS')
    return dict(places=list(places.values()), card_count=len(cards),
                more_present=bool(soup.select('.load-more-result')),
                inclusion='SOURCE_NEARBY_STREET_NOT_EXACT_ADDRESS',
                coverage='INITIAL_VISIBLE_RESULTS_ONLY')


def queue_state(manifest_bytes, expected):
    source = json.loads(manifest_bytes)
    if source.get('source') != INDEX or source.get('status') != 200:
        raise ValueError('Expected verified Q4 street index')
    urls = list(dict.fromkeys(street_url(row['url']) for row in source['streets']))
    if not urls:
        raise ValueError('No verified street links')
    return dict(version=1, manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                street_count=len(urls), expected_street_count=expected,
                street_index_complete=len(urls) == expected,
                pending_streets=urls, streets={}, places={}, pending_details=[],
                records={}, status='READY', error=None, phase='STREETS',
                coverage='PARTIAL_NEARBY_STREET_OBSERVATIONS', reviews_collected=0)


def add_observation(state, url, observation):
    # Called only after a valid page; errors never pop pending work.
    for place in observation['places']:
        entry = state['places'].setdefault(place['url'], dict(**place, observed_streets=[]))
        if url not in entry['observed_streets']:
            entry['observed_streets'].append(url)
    state['streets'][url] = {k:v for k,v in observation.items() if k != 'places'}
    state['pending_streets'].remove(url)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-directory', type=Path, required=True)
    parser.add_argument('--street-links', type=Path,
                        default=ROOT/'output/foody-q4-street-inspection/street-links-scrolled.json')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--rights-confirmed', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--details', action='store_true', help='Collect POI fields with review_limit=0')
    parser.add_argument('--expected-streets', type=int, default=121)
    parser.add_argument('--max-streets', type=int, default=121)
    parser.add_argument('--max-minutes', type=int, default=180)
    args = parser.parse_args()
    if not args.rights_confirmed:
        parser.error('--rights-confirmed is required')
    if not 1 <= args.max_streets <= 121 or not 1 <= args.max_minutes <= 180:
        parser.error('Use 1..121 streets and 1..180 minutes')
    root = args.root.resolve()
    if not root.is_relative_to((ROOT/'output').resolve()):
        parser.error('Use a dedicated root under output/')
    manifest = args.street_links.read_bytes()
    checkpoint = root/'raw/street_state.json'
    if checkpoint.exists():
        if not args.resume:
            parser.error('Existing output; use --resume only for PAUSED runs')
        state = json.loads(checkpoint.read_text(encoding='utf-8'))
        if (state['status'] != 'PAUSED' or state['manifest_sha256'] != hashlib.sha256(manifest).hexdigest()
                or state.get('details_requested') != args.details):
            parser.error('Resume requires PAUSED matching configuration')
    else:
        if args.resume or (root.exists() and any(root.iterdir())):
            parser.error('Use a fresh output directory')
        state = queue_state(manifest, args.expected_streets)
        state['details_requested'] = args.details
    profile = available_profile(args.profile_directory)
    (root/'raw').mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.max_minutes*60
    processed = 0

    def save():
        crawl.save(checkpoint, state)
        crawl.save(root/'raw/pois.json', list(state['records'].values()))
        crawl.save(root/'raw/discovered_places.json', list(state['places'].values()))
        summary = {k:state[k] for k in ('status','error','phase','street_count','expected_street_count',
                                       'street_index_complete','coverage','reviews_collected')}
        summary.update(streets_done=len(state['streets']), pending_streets=len(state['pending_streets']),
                       unique_urls=len(state['places']), records=len(state['records']))
        crawl.save(root/'summary.json', summary)
        line = json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), **summary))
        with (root/'progress.log').open('a', encoding='utf-8') as log:
            log.write(line+'\n')
        print(line, flush=True)

    def budget():
        if time.monotonic() >= deadline:
            raise crawl.CrawlError('TIME_BUDGET')

    state.update(status='RUNNING', error=None)
    save()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(profile), channel='chrome', headless=False,
                                                         viewport={'width':1280,'height':900})
            try:
                page = context.pages[0] if context.pages else context.new_page()
                while state['pending_streets']:
                    budget()
                    if processed >= args.max_streets:
                        raise crawl.CrawlError('STREET_BUDGET')
                    url = state['pending_streets'][0]
                    time.sleep(3)
                    response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    if not response or response.status != 200:
                        raise crawl.CrawlError('STREET_HTTP_'+str(response.status if response else 'UNKNOWN'))
                    if page.url != url:
                        raise crawl.CrawlError('STREET_REDIRECT_REQUIRES_REVIEW')
                    page.wait_for_function('''() => document.querySelector('.res-nearby-content-items') ||
                      document.querySelector('input[type=password]') ||
                      /captcha|access denied|just a moment/i.test(document.body?.innerText||'')''', timeout=10000)
                    observation = parse_street(page.content())
                    add_observation(state, url, observation)
                    processed += 1
                    save()
                if args.details:
                    if state['phase'] == 'STREETS':
                        state['pending_details'] = list(state['places'])
                        state['phase'] = 'DETAILS'
                        save()
                    while state['pending_details']:
                        budget()
                        url = state['pending_details'][0]
                        time.sleep(3)
                        html, provenance = fetch_detail(context, url)
                        record = crawl.parse_page(html, url, review_limit=0)['record']
                        if record is None:
                            raise crawl.CrawlError('EMPTY_OR_UNSUPPORTED_DETAIL')
                        key = record['source_id'] or url
                        if key in state['records'] and state['records'][key]['url'] != url:
                            raise crawl.CrawlError('SOURCE_ID_URL_CONFLICT')
                        record.update(provenance=provenance,
                                      street_observations=state['places'][url]['observed_streets'])
                        state['records'][key] = record
                        state['pending_details'].pop(0)
                        save()
                state['status'] = 'COMPLETED_PARTIAL'
            finally:
                context.close()
    except KeyboardInterrupt:
        state.update(status='PAUSED', error='INTERRUPTED')
    except Exception as exc:
        code = str(exc) if isinstance(exc, crawl.CrawlError) else type(exc).__name__
        state.update(status='PAUSED' if code in ('TIME_BUDGET','STREET_BUDGET') else 'STOPPED', error=code)
    finally:
        save()
    return 0 if state['status'] == 'COMPLETED_PARTIAL' else 1


if __name__ == '__main__':
    raise SystemExit(main())
