"""Conservative Foody street-search fallback; never infer venue URLs."""
import re
import unicodedata

from bs4 import BeautifulSoup

import crawl


def street_name(entry):
    """Index contains non-street headings as well as street rows."""
    text = entry.get('text') or ''
    label = text.splitlines()[0].strip() if text.splitlines() else ''
    if not label or re.fullmatch(r'\d+\s+địa điểm', label, re.I):
        return None
    return label


def normalize(text):
    normalized = unicodedata.normalize('NFD', text.casefold().replace('đ', 'd'))
    return re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in normalized if not unicodedata.combining(c))).strip()


def street_evidence(address, street):
    if not address:
        return 'STREET_UNVERIFIED'
    haystack, needle = normalize(address), normalize(street)
    return 'STREET_MATCH' if re.search(r'(?<!\w)' + re.escape(needle) + r'(?!\w)', haystack) else 'STREET_MISMATCH'


def parse_results(html):
    """Only anchors in observed search-result cards; never page-wide links."""
    soup = BeautifulSoup(html, 'html.parser')
    if crawl.RESTRICTION.search(soup.get_text(' ', strip=True)):
        raise crawl.CrawlError('ACCESS_RESTRICTION')
    if soup.select('input[type=password]'):
        raise crawl.CrawlError('LOGIN_REQUIRED')
    cards = soup.select('.filter-result-item, .result-item, .search-result-item')
    if not cards:
        return []
    results = {}
    for card in cards:
        for link in card.select('a[href]'):
            try:
                url = crawl.canonical(link['href'])
                if crawl.source_link_kind(url) != 'detail':
                    continue
            except ValueError:
                continue
            results.setdefault(url, dict(url=url, name=crawl.clean(link.get('title') or link.get_text(' ', strip=True)),
                                         discovery_method='FOODY_SEARCH_FALLBACK',
                                         validation_status='PENDING_DETAIL_VALIDATION'))
    return list(results.values())


def search_task(browser_context, street, max_pages=3):
    """Use the site's visible search control; stop on unsupported UI/restriction."""
    from streets import INDEX
    page = browser_context.new_page()
    try:
        response = page.goto(INDEX, wait_until='domcontentloaded', timeout=30000)
        if not response or response.status != 200:
            raise crawl.CrawlError('SEARCH_HTTP_' + str(response.status if response else 'UNKNOWN'))
        if crawl.RESTRICTION.search(BeautifulSoup(page.content(), 'html.parser').get_text(' ', strip=True)):
            raise crawl.CrawlError('ACCESS_RESTRICTION')
        # Resolve a single visible search control rather than guessing endpoints.
        inputs = page.locator('input[type=search]:visible, input[name=search]:visible, input[name=keyword]:visible, input[placeholder*="Tìm kiếm"]:visible')
        if inputs.count() != 1:
            raise crawl.CrawlError('SEARCH_UI_UNVERIFIED')
        inputs.first.fill(f'{street} Quận 4')
        inputs.first.press('Enter')
        page.wait_for_load_state('domcontentloaded', timeout=15000)
        if page.url == INDEX:
            raise crawl.CrawlError('SEARCH_UI_UNVERIFIED')
        results = {}
        observations = []
        for index in range(max_pages):
            page.wait_for_timeout(1000)
            if crawl.RESTRICTION.search(BeautifulSoup(page.content(), 'html.parser').get_text(' ', strip=True)):
                raise crawl.CrawlError('ACCESS_RESTRICTION')
            found = parse_results(page.content())
            if index == 0 and not found and not page.locator('.filter-result-item, .result-item, .search-result-item').count():
                raise crawl.CrawlError('SEARCH_LAYOUT_UNVERIFIED')
            new = 0
            for item in found:
                if item['url'] not in results:
                    results[item['url']] = item
                    new += 1
            observations.append(dict(page=index + 1, result_urls=len(found), new_urls=new))
            if not found or not new:
                break
            next_link = page.locator('.pagination a.next:visible, a[rel=next]:visible')
            if next_link.count() != 1:
                break
            old_url = page.url
            next_link.first.click(timeout=10000)
            if page.url == old_url:
                break
        return dict(outcome='SEARCH_PARTIAL_UNVERIFIED', places=list(results.values()), observations=observations)
    finally:
        page.close()
