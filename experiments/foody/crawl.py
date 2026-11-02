"""Bounded public Foody POI probe with optional small review samples.

Run --help for usage. Only follow links actually supplied by Foody; dynamic
pagination is deliberately unsupported and is reported as incomplete coverage.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

VERSION = 1
RESTRICTION = re.compile(
    r"verify you are human|access denied|just a moment|unusual traffic|"
    r"captcha|truy cập bị từ chối", re.I)


class CrawlError(RuntimeError):
    pass


def canonical(url):
    parsed = urlsplit(urljoin('https://www.foody.vn', url))
    if (parsed.scheme != 'https' or parsed.hostname != 'www.foody.vn'
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or not parsed.path.startswith(('/ho-chi-minh/', '/thuong-hieu/'))):
        raise ValueError('Only public Foody HCMC / brand URLs are supported')
    return urlunsplit(('https', 'www.foody.vn', parsed.path, parsed.query, ''))


def clean(value):
    return re.sub(r'\s+', ' ', value).strip()


def source_link_kind(url):
    parsed = urlsplit(canonical(url))
    if parsed.path.startswith('/thuong-hieu/'):
        return 'brand'
    slug = parsed.path.removeprefix('/ho-chi-minh/')
    if slug.startswith('dia-diem-tai-'):
        return 'listing'
    navigation = {'food', 'o-dau', 'cafe', 'nha-hang', 'quan-an', 'hinh-anh',
                  'bo-suu-tap', 'bo-suu-tap-hinh-anh', 'coupon', 'binh-luan',
                  'khuyen-mai', 'su-kien', 'top-thanh-vien', 'dia-diem'}
    if ('/' in slug or parsed.query or slug in navigation or slug.startswith('khu-vuc-')
            or '-phong-cach-' in slug or '{{' in slug):
        return 'navigation'
    return 'detail'


def parse_page(html, url, review_limit=0):
    soup = BeautifulSoup(html, 'html.parser')
    visible = soup.get_text(' ', strip=True)
    if RESTRICTION.search(visible):
        raise CrawlError('ACCESS_RESTRICTION')
    # Limit link traversal to rendered result/branch containers, never navigation
    # or recommended venues from a detail page.
    ids = sorted(set(re.findall(r'\brestaurantId\s*[:=]\s*["\x27]?(\d+)', html)))
    heading = soup.find('h1')
    is_detail = bool(heading and ids and soup.select_one('[itemprop="streetAddress"]'))
    scopes = [] if is_detail else soup.select('.filter-results, .branch-list, .brand-list, .restaurant-list')
    if not scopes and '/thuong-hieu/' in url:
        scopes = [soup]  # Brand pages expose branch links outside list containers.
    links = set()
    for scope in scopes:
        cards = scope.select('.filter-result-item')
        if cards:
            anchors = []
            for card in cards:
                anchors.extend(card.select('.result-name a[href], .result-image a[href], .ri-avatar a[href]')
                               or card.select('a[href]'))
        else:
            anchors = scope.select('a[href]')
        for anchor in anchors:
            href = anchor.get('href', '')
            if '{{' in href:
                continue
            try:
                target = canonical(href)
            except ValueError:
                continue
            path = urlsplit(target).path
            if '/thuong-hieu/' in url:
                brand = clean(soup.title.get_text().split('|')[0]).casefold() if soup.title else ''
                label = clean(anchor.get('title') or anchor.get_text(' ', strip=True)).casefold()
                # Avoid unrelated navigation/recommendations from brand pages.
                if not brand or brand not in label or '/thuong-hieu/' in path:
                    continue
            # Address links point to district/neighborhood navigation, not venues.
            # A real venue slug beginning nha-hang-/cafe- remains valid.
            if source_link_kind(target) in ('brand', 'detail'):
                if target != url:
                    links.add(target)
    record = None
    if is_detail:
        fields = {}
        for key in ('streetAddress', 'addressLocality', 'addressRegion',
                    'latitude', 'longitude', 'priceRange', 'openingHours', 'servesCuisine'):
            values = []
            for node in soup.select('[itemprop="' + key + '"]'):
                value = clean(node.get('content') or node.get_text(' ', strip=True))
                if value and '{{' not in value and value not in values:
                    values.append(value)
            fields[key] = values
        # Do not mix user-review ratings into the venue aggregate.
        sections = {}
        for label in ('Thể loại', 'Phong cách ẩm thực', 'Phục vụ các món'):
            for node in soup.find_all(string=lambda x: x and clean(x) == label):
                parent = node.parent
                for _ in range(3):
                    if parent is None:
                        break
                    tags = [clean(a.get_text(' ', strip=True)).strip(' ,') for a in parent.select('a')]
                    tags = list(dict.fromkeys(x for x in tags if x and '{{' not in x))
                    if tags and len(tags) <= 15:
                        sections[label] = tags
                        break
                    parent = parent.parent
                if label in sections:
                    break
        latitude = longitude = None
        geometry_status = 'MISSING'
        if fields['latitude'] and fields['longitude']:
            try:
                latitude, longitude = float(fields['latitude'][0]), float(fields['longitude'][0])
                if not (math.isfinite(latitude) and math.isfinite(longitude)
                        and -90 <= latitude <= 90 and -180 <= longitude <= 180):
                    raise ValueError()
                geometry_status = 'VALID'
            except ValueError:
                latitude = longitude = None
                geometry_status = 'INVALID'
        record = dict(source='foody', source_id=ids[0] if len(ids) == 1 else None,
                      source_id_candidates=ids, url=url, name=clean(heading.get_text(' ', strip=True)),
                      fields=fields, categories=sections, latitude=latitude, longitude=longitude,
                      geometry_status=geometry_status,
                      review_dom_blocks=len(soup.select('.review-item')),
                      reviews_collected=0)
        count_node = soup.select_one('[itemprop="reviewCount"]')
        count_text = (count_node.get('content') or count_node.get_text()) if count_node else ''
        count = int(count_text.strip()) if count_text.strip().isdigit() else None
        record['review_count_displayed'] = count
        # Never request review pagination. Leave at least one uncollected if the
        # source supplies a reliable small count; unknown counts remain unverified.
        limit = min(max(0, review_limit), 5)
        if count is not None:
            limit = min(limit, max(0, count - 1))
        samples = []
        for node in soup.select('.review-item .review-des'):
            if len(samples) >= limit:
                break
            body = clean(node.get_text(' ', strip=True))
            if not body or '{{' in body:
                continue
            # No author, profile URL, cookies or tokens are collected. Redact
            # obvious contact details from free text before writing local output.
            body = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL]', body)
            body = re.sub(r'(?<!\d)(?:\+?84|0)(?:[ .-]?\d){8,10}(?!\d)', '[PHONE]', body)
            fingerprint = hashlib.sha256(body.encode('utf-8')).hexdigest()
            if any(x['text_sha256'] == fingerprint for x in samples):
                continue
            samples.append(dict(text=body, text_sha256=fingerprint))
        record['review_samples'] = samples
        record['reviews_collected'] = len(samples)
    return dict(record=record, links=sorted(links),
                load_more_present=bool(soup.select('.btn-load-more')),
                title=clean(soup.title.get_text()) if soup.title else None)


def fetch(client, url):
    start = time.monotonic()
    with client.stream('GET', url) as response:
        if response.is_redirect:
            raise CrawlError('REDIRECT_REQUIRES_REVIEW')
        if response.status_code != 200:
            raise CrawlError('HTTP_' + str(response.status_code))
        if 'text/html' not in response.headers.get('content-type', '').lower():
            raise CrawlError('UNEXPECTED_CONTENT_TYPE')
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > 2_000_000:
                raise CrawlError('RESPONSE_SIZE_LIMIT')
            if time.monotonic() - start > 30:
                raise CrawlError('REQUEST_DEADLINE')
    html = bytes(data).decode('utf-8', errors='replace')
    return html, dict(status=200, bytes=len(data), elapsed_seconds=round(time.monotonic() - start, 3),
                      response_sha256=hashlib.sha256(data).hexdigest(),
                      retrieved_at=datetime.now(timezone.utc).isoformat())


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def run(seed, output, *, max_places=100, max_pages=200, delay=3, resume=False,
        review_limit=5, client=None):
    seed = canonical(seed)
    if not (1 <= max_places <= 1000 and 1 <= max_pages <= 2000 and delay >= 2
            and 0 <= review_limit <= 5):
        raise ValueError('Limits: 1–1000 POIs, 1–2000 pages, delay >= 2 seconds, 0–5 reviews')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = output / 'state.json'
    if checkpoint.exists() and not resume:
        raise ValueError('Output already exists; use --resume or a new directory')
    state = (json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else
             dict(version=VERSION, seed=seed, review_limit=review_limit,
                  pending=[seed], visited=[], records={}, pages=[],
                  status='READY', error=None))
    if (state['seed'] != seed or state['version'] != VERSION
            or state.get('review_limit') != review_limit):
        raise ValueError('Checkpoint seed/version mismatch')
    if state['status'] == 'STOPPED':
        raise ValueError('Stopped checkpoint requires manual review; no automatic retry')
    owned = client is None
    if owned:
        client = httpx.Client(timeout=httpx.Timeout(20, connect=10), follow_redirects=False,
                              trust_env=False, headers={'User-Agent': 'FnbFoodyResearchProbe/1.0'})
    state['status'] = 'RUNNING'
    started = time.monotonic()
    try:
        while state['pending'] and len(state['visited']) < max_pages and len(state['records']) < max_places:
            if time.monotonic() - started > 2700:
                break
            url = state['pending'][0]
            time.sleep(delay)
            html, provenance = fetch(client, url)
            parsed = parse_page(html, url, review_limit)
            if not parsed['record'] and not parsed['links']:
                raise CrawlError('EMPTY_OR_UNSUPPORTED_PAGE')
            record = parsed['record']
            if record:
                record['provenance'] = provenance
                # URL is retained when source ID is ambiguous; no branch merging by location.
                key = record['source_id'] or url
                if key in state['records'] and state['records'][key]['url'] != url:
                    raise CrawlError('SOURCE_ID_URL_CONFLICT')
                state['records'][key] = record
            state['pending'].pop(0)
            state['visited'].append(url)
            state['pending'].extend(x for x in parsed['links']
                                    if x not in state['visited'] and x not in state['pending'])
            state['pages'].append(dict(url=url, **provenance,
                                       load_more_present=parsed['load_more_present']))
            save(checkpoint, state)
            progress = f"Pages={len(state['visited'])} POIs={len(state['records'])} pending={len(state['pending'])}"
            with (output / 'progress.log').open('a', encoding='utf-8') as log:
                log.write(datetime.now(timezone.utc).isoformat() + ' ' + progress + '\n')
            print(progress, flush=True)
        state['status'] = 'BOUNDED_STOP' if state['pending'] else 'EXHAUSTED_STATIC_LINKS'
    except (Exception, KeyboardInterrupt) as exc:
        state['status'] = 'STOPPED'
        state['error'] = str(exc) if isinstance(exc, CrawlError) else type(exc).__name__
    finally:
        if owned:
            client.close()
        save(checkpoint, state)
        with (output / 'progress.log').open('a', encoding='utf-8') as log:
            log.write(datetime.now(timezone.utc).isoformat() + ' status=' + state['status']
                      + ' error=' + str(state['error']) + '\n')
        save(output / 'pois.json', list(state['records'].values()))
        save(output / 'summary.json', dict(status=state['status'], error=state['error'],
             pages=len(state['visited']), pois=len(state['records']), pending=len(state['pending']),
             geometry_counts=dict(Counter(x['geometry_status'] for x in state['records'].values())),
             missing_address=sum(not x['fields']['streetAddress'] for x in state['records'].values()),
             missing_categories=sum(not x['categories'] for x in state['records'].values()),
             category_counts=dict(Counter(label for x in state['records'].values()
                                          for label in x['categories'].get('Thể loại', []))),
             coverage='UNVERIFIED', reviews_collected=sum(
                 x['reviews_collected'] for x in state['records'].values()),
             limitations=['Static linked pages only; no dynamic or review pagination.',
                          'Review samples are DOM order, not random/representative; may be truncated.',
                          'Free text may still contain personal information; keep output private.',
                          'No claim of complete brand/district coverage; no analytical publication.']))
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--max-places', type=int, default=100)
    parser.add_argument('--max-pages', type=int, default=200)
    parser.add_argument('--review-limit', type=int, default=5)
    parser.add_argument('--delay', type=float, default=3)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--rights-confirmed', action='store_true',
                        help='Operator has verified permission to collect and store these POI fields')
    args = parser.parse_args()
    if not args.rights_confirmed:
        parser.error('Collection/storage rights must be verified before running (--rights-confirmed)')
    result = run(args.url, args.output, max_places=args.max_places, max_pages=args.max_pages,
                 delay=args.delay, resume=args.resume, review_limit=args.review_limit)
    return 1 if result['status'] == 'STOPPED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
