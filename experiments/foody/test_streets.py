"""Offline fixtures for street observations; no live access."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import crawl
import streets
from street_load_more_probe import select_cases

STREET = streets.INDEX + '/tren-duong-ben-van-don'
HTML = '''<div class="suggestion-box"><a href="/ho-chi-minh/not-a-result">Recent</a></div>
<div class="res-nearby-content-items"><div class="img"><a href="/ho-chi-minh/one"></a></div>
<div class="name"><a href="/ho-chi-minh/one">One</a></div></div>
<div class="res-nearby-content-items"><a href="/ho-chi-minh/two">Two</a></div>
<a class="load-more-result" href="javascript:void(0)">Xem thêm</a>'''


def state():
    return streets.queue_state(json.dumps(dict(source=streets.INDEX, status=200,
        streets=[dict(url=STREET), dict(url=STREET)])).encode(), 121)


def test_parse_only_street_cards_deduplicates_and_never_follows_more():
    result = streets.parse_street(HTML)
    assert len(result['places']) == 2
    assert result['places'][0]['name'] == 'One'
    assert result['more_present'] is True
    assert result['coverage'] == 'INITIAL_VISIBLE_RESULTS_ONLY'
    assert all('not-a-result' not in p['url'] for p in result['places'])
    assert not any('reviews' in p for p in result['places'])


@pytest.mark.parametrize('html,error', [('<h1>Nothing</h1>', 'UNSUPPORTED_STREET'),
    ('<div>captcha</div>', 'ACCESS_RESTRICTION'), ('<input type=password>', 'LOGIN_REQUIRED')])
def test_errors_preserve_pending(html, error):
    s = state()
    before = json.dumps(s, sort_keys=True)
    with pytest.raises(crawl.CrawlError, match=error):
        result = streets.parse_street(html)
        streets.add_observation(s, STREET, result)
    assert json.dumps(s, sort_keys=True) == before


def test_index_dedup_and_street_count_discrepancy_explicit():
    s = state()
    assert s['pending_streets'] == [STREET]
    assert not s['street_index_complete']
    assert s['expected_street_count'] == 121


def test_cross_street_urls_keep_all_observation_provenance():
    s = state()
    other = streets.INDEX + '/tren-duong-hoang-dieu'
    s['pending_streets'].append(other)
    result = streets.parse_street(HTML)
    streets.add_observation(s, STREET, result)
    streets.add_observation(s, other, result)
    assert len(s['places']) == 2
    assert all(p['observed_streets'] == [STREET, other] for p in s['places'].values())
    assert s['records'] == {} and s['reviews_collected'] == 0


@pytest.mark.parametrize('url', ['https://www.foody.vn/ho-chi-minh/place',
    STREET+'?page=2', 'https://evil.test/ho-chi-minh/khu-vuc-quan-4/tren-duong-a'])
def test_no_invented_or_other_listing_routes(url):
    with pytest.raises(ValueError):
        streets.street_url(url)


def test_probe_chooses_high_count_observed_routes_only():
    rows = [dict(text='Missing\n900 địa điểm', href=None),
            dict(text='Small\n100 địa điểm', href=STREET),
            dict(text='Large\n706 địa điểm', href=STREET),
            dict(text='Other\n696 địa điểm', href=streets.INDEX+'/tren-duong-doan-van-bo')]
    selected = select_cases(rows, 2)
    assert [r['source_displayed_count'] for r in selected] == [706, 696]
    assert all(r['url'].startswith(streets.INDEX+'/tren-duong-') for r in selected)
    other = streets.INDEX+'/tren-duong-doan-van-bo'
    assert [r['url'] for r in select_cases(rows, 2, other)] == [other]
    with pytest.raises(ValueError, match='observed'):
        select_cases(rows, 2, streets.INDEX+'/tren-duong-not-observed')
