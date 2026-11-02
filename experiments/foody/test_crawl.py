"""Synthetic offline fixtures; no Foody review text or personal data."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location('foody_probe', Path(__file__).with_name('crawl.py'))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
URL = 'https://www.foody.vn/ho-chi-minh/example-branch'
DETAIL = '''<html><h1>Example Cafe</h1><script>restaurantId = 123;</script>
<span itemprop="streetAddress">42 Example</span>
<meta itemprop="latitude" content="10.77"><meta itemprop="longitude" content="106.70">
<div><label>Thể loại</label><a>Café/Dessert</a></div>
<div><label>Phong cách ẩm thực</label><a>Món Nhật</a><a>Ý</a></div>
<span itemprop="servesCuisine">Món Nhật, Ý</span>
<div class="review-item"><span itemprop="ratingValue">10</span>PRIVATE REVIEW</div>
<a href="/ho-chi-minh/unrelated">Recommendation</a></html>'''


def test_detail_fields_and_no_personal_data():
    page = probe.parse_page(DETAIL, URL)
    row = page['record']
    assert row['source_id'] == '123'
    assert row['latitude'] == 10.77
    assert row['categories']['Thể loại'] == ['Café/Dessert']
    assert row['categories']['Phong cách ẩm thực'] == ['Món Nhật', 'Ý']
    assert row['review_dom_blocks'] == 1
    assert 'PRIVATE REVIEW' not in json.dumps(row)
    assert 'ratingValue' not in row['fields']
    assert page['links'] == []


@pytest.mark.parametrize('value', ['NaN', '999', 'bad'])
def test_invalid_coordinates(value):
    row = probe.parse_page(DETAIL.replace('content="10.77"', f'content="{value}"'), URL)['record']
    assert row['geometry_status'] == 'INVALID'
    assert row['latitude'] is None


def test_missing_coordinates():
    row = probe.parse_page(DETAIL.replace('<meta itemprop="latitude" content="10.77">', ''), URL)['record']
    assert row['geometry_status'] == 'MISSING'


def test_list_dedup_and_limits():
    html = '''<div class="filter-results"><a href="/ho-chi-minh/example-branch">A</a>
    <a href="/ho-chi-minh/example-branch">A</a><a href="https://evil.test/a">B</a>
    <a href="/ho-chi-minh/{{slug}}">T</a></div><a class="btn-load-more">More</a>'''
    page = probe.parse_page(html, 'https://www.foody.vn/ho-chi-minh/dia-diem-tai-quan-1')
    assert page['links'] == [URL]
    assert page['load_more_present']


@pytest.mark.parametrize('url', ['http://www.foody.vn/ho-chi-minh/x',
                                 'https://evil.test/ho-chi-minh/x',
                                 'https://user:secret@www.foody.vn/ho-chi-minh/x'])
def test_url_boundary(url):
    with pytest.raises(ValueError):
        probe.canonical(url)


def test_challenge():
    with pytest.raises(probe.CrawlError, match='ACCESS_RESTRICTION'):
        probe.parse_page('<body>Verify you are human</body>', URL)


def test_resume_no_redownload(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.time, 'sleep', lambda _: None)
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=DETAIL, headers={'content-type': 'text/html'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        state = probe.run(URL, tmp_path, client=client)
        assert len(state['records']) == 1
        probe.run(URL, tmp_path, client=client, resume=True)
    assert calls == [URL]
    assert json.loads((tmp_path / 'summary.json').read_text())['coverage'] == 'UNVERIFIED'


def test_failure_keeps_partial_and_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.time, 'sleep', lambda _: None)
    seed = 'https://www.foody.vn/thuong-hieu/example'
    second = URL + '-2'
    calls = []
    def respond(request):
        url = str(request.url)
        calls.append(url)
        if url == seed:
            html = f'<title>Example | Foody.vn</title><a href="{URL}">Example A</a><a href="{second}">Example B</a>'
            return httpx.Response(200, text=html, headers={'content-type': 'text/html'})
        if url == URL:
            return httpx.Response(200, text=DETAIL, headers={'content-type': 'text/html'})
        return httpx.Response(429)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        state = probe.run(seed, tmp_path, client=client)
        assert state['status'] == 'STOPPED'
        assert state['error'] == 'HTTP_429'
        assert len(state['records']) == 1
        with pytest.raises(ValueError, match='manual review'):
            probe.run(seed, tmp_path, client=client, resume=True)
    assert len(calls) == 3
    assert len(json.loads((tmp_path / 'pois.json').read_text(encoding='utf-8'))) == 1


def test_redirect_not_followed():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
            302, headers={'location': 'https://evil.test'}))) as client:
        with pytest.raises(probe.CrawlError, match='REDIRECT'):
            probe.fetch(client, URL)


def test_review_sample_limit_redaction_and_dedup():
    html = DETAIL.replace('PRIVATE REVIEW', '<div class="review-des">Contact 0912345678 a@b.com</div>')
    html += '<meta itemprop="reviewCount" content="2">'
    html += '<div class="review-item"><div class="review-des">Another review</div></div>'
    record = probe.parse_page(html, URL, review_limit=5)['record']
    assert record['reviews_collected'] == 1
    assert record['review_samples'][0]['text'] == 'Contact [PHONE] [EMAIL]'
    assert 'author' not in record['review_samples'][0]


def test_single_review_not_collected_in_full():
    html = DETAIL + '<meta itemprop="reviewCount" content="1">'
    assert probe.parse_page(html, URL, 5)['record']['reviews_collected'] == 0


def test_brand_does_not_follow_unrelated_links():
    html = f'''<title>Example | Foody.vn</title><a href="{URL}">Example branch</a>
    <a href="/ho-chi-minh/unrelated">Other venue</a>'''
    assert probe.parse_page(html, 'https://www.foody.vn/thuong-hieu/example')['links'] == [URL]


def test_card_address_navigation_excluded_and_restaurant_slug_kept():
    html = '''<div class="filter-results"><div class="filter-result-item">
    <div class="result-name"><a href="/ho-chi-minh/nha-hang-example">Example</a></div>
    <div class="result-address"><a href="/ho-chi-minh/khu-vuc-quan-4">Quận 4</a></div>
    </div></div>'''
    assert probe.parse_page(html, 'https://www.foody.vn/ho-chi-minh/dia-diem-tai-quan-4')['links'] == [
        'https://www.foody.vn/ho-chi-minh/nha-hang-example']


def test_location_links_not_places():
    assert probe.source_link_kind('/ho-chi-minh/khu-vuc-quan-4') == 'navigation'
    assert probe.source_link_kind('/ho-chi-minh/cafe-phong-cach-nhat-ban') == 'navigation'
    assert probe.source_link_kind('/ho-chi-minh/cafe-example') == 'detail'
