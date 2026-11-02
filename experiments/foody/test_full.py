"""Offline pagination fixtures and a local-only Chrome DOM integration test."""
import importlib.util
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import crawl
import full


def html(loaded, total, branch='a'):
    return (f'<div class="filter-results"><div class="filter-result-item">'
            f'<a href="/ho-chi-minh/{branch}">Venue</a></div></div>'
            f'<a class="btn-load-more">Xem tiếp {loaded} / {total} (1)</a>')


def test_multiple_pages_until_counter_exhausted():
    pages = [(html(12, 30, 'a'), True), (html(24, 30, 'b'), True), (html(30, 30, 'c'), False)]
    index = [0]; observed = []
    def advance():
        index[0] += 1
    evidence = full.discover(lambda: pages[index[0]], advance,
                             lambda doc, proof: observed.append(proof['loaded']))
    assert observed == [12, 24, 30]
    assert evidence['status'] == 'COUNTER_EXHAUSTED'


def test_no_control_is_not_assumed_complete():
    assert full.pagination_evidence('<html></html>', False)['status'] == 'UNVERIFIED_NO_TERMINAL_EVIDENCE'
    assert full.pagination_evidence('<html></html>', False, brand=True)['status'] == 'UNVERIFIED_STATIC_BRAND'


def test_hidden_control_with_incomplete_count():
    assert full.pagination_evidence(html(12, 10023), False)['status'] == 'INCOMPLETE_COUNTER'


def test_thousands_separator():
    result = full.pagination_evidence(html('10,023', '10,023'), False)
    assert result['total'] == 10023 and result['status'] == 'COUNTER_EXHAUSTED'


def test_repeated_page_stops():
    with pytest.raises(crawl.CrawlError, match='STALLED'):
        full.discover(lambda: (html(12, 30), True), lambda: None, lambda *a: None)


def test_challenge_stops_before_on_page():
    observed = []
    with pytest.raises(crawl.CrawlError, match='ACCESS_RESTRICTION'):
        full.discover(lambda: ('Verify you are human', False), lambda: None,
                      lambda *a: observed.append(1))
    assert observed == []


def test_budget_pauses_without_claiming_completion():
    with pytest.raises(crawl.CrawlError, match='TIME_BUDGET'):
        full.discover(lambda: (html(1, 2), True), lambda: None, lambda *a: None,
                      budget=lambda: False)


def test_counter_completion_does_not_mean_real_world_full_count():
    proof = full.coverage({'pending': [], 'discovery': {'url': {'status': 'COUNTER_EXHAUSTED'}}})
    assert proof['status'] == 'ALL_DISCOVERED_PAGES_EXHAUSTED_WITH_COUNTER_EVIDENCE'
    assert not proof['is_full_district_count']
    proof = full.coverage({'pending': [], 'discovery': {'url': {'status': 'UNVERIFIED_STATIC_BRAND'}}})
    assert proof['status'] == 'PARTIAL_OR_UNVERIFIED'


def test_full_run_does_not_overwrite_previous_static_output(tmp_path):
    path = tmp_path / 'pois.json'; path.write_text('[{"existing":true}]')
    with pytest.raises(ValueError, match='Refusing to mix'):
        full.run('https://www.foody.vn/ho-chi-minh/dia-diem-tai-quan-4', tmp_path)
    assert path.read_text() == '[{"existing":true}]'


def test_stopped_run_cannot_automatically_retry(tmp_path):
    import json
    seed = 'https://www.foody.vn/ho-chi-minh/dia-diem-tai-quan-4'
    (tmp_path / 'full_state.json').write_text(json.dumps(dict(version=full.VERSION,
        seed=seed, review_limit=5, status='STOPPED')))
    with pytest.raises(ValueError, match='manual review'):
        full.run(seed, tmp_path, resume=True)


def test_private_profile_outside_repository(tmp_path, monkeypatch):
    from login_profile import profile_directory
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert profile_directory() == tmp_path / 'FnbFoodyResearch/chrome-profile'


def test_detail_uses_browser_request_and_disposes_without_cookie_export():
    class Response:
        status = 200
        headers = {'content-type': 'text/html'}
        disposed = False
        def body(self):
            return b'<html>Fixture</html>'
        def dispose(self):
            self.disposed = True
    response = Response()
    class Request:
        def get(self, url, **kwargs):
            assert kwargs['max_redirects'] == 0
            assert 'cookies' not in kwargs and 'headers' not in kwargs
            return response
    class Context:
        request = Request()
    text, metadata = full.fetch_detail(Context(), 'https://www.foody.vn/ho-chi-minh/fixture')
    assert response.disposed and metadata['session'] == 'PRIVATE_FOODY_PROFILE'
    assert 'cookie' not in str(metadata).lower() and text.startswith('<html>')


def test_cookie_import_is_scoped_and_does_not_log_value(capsys):
    from login_profile import import_cookie, cookie_payload
    collected = []
    class Context:
        def add_cookies(self, values):
            collected.extend(values)
    import_cookie(Context(), cookie_payload('SYNTHETIC_TEST_VALUE', 'ID_FOODY.AUTH', '.foody.vn', '/'))
    assert collected[0]['domain'] == '.foody.vn'
    assert collected[0]['httpOnly'] and collected[0]['secure']
    assert collected[0]['name'] == 'ID_FOODY.AUTH'
    assert capsys.readouterr().out == ''


def test_cookie_attributes_are_not_guessed():
    from login_profile import cookie_payload
    value = cookie_payload('TEST', 'ID_FOODY.AUTH', 'id.foody.vn', '/account')
    assert value['domain'] == 'id.foody.vn' and value['path'] == '/account'
    assert 'expires' not in value and 'sameSite' not in value
    with pytest.raises(ValueError):
        cookie_payload('TEST', 'ID_FOODY.AUTH', 'evil.test', '/')


def stopped_checkpoint(tmp_path, error='PAGINATION_STALLED_OR_TIMEOUT'):
    import json
    seed = 'https://www.foody.vn/ho-chi-minh/dia-diem-tai-quan-4?categorygroup=food'
    detail = 'https://www.foody.vn/ho-chi-minh/example-branch'
    brand = 'https://www.foody.vn/thuong-hieu/example'
    path = tmp_path / 'full_state.json'
    path.write_text(json.dumps(dict(version=full.VERSION, seed=seed, review_limit=5,
        status='STOPPED', error=error, pending=[seed, detail, brand, detail],
        visited=[], records={}, discovery={seed:{'status':'MORE','loaded':12,'total':30}})), encoding='utf-8')
    return path, seed, detail, brand


def test_recover_discovered_deduplicates_preserves_original_and_no_full_claim(tmp_path):
    path, seed, detail, brand = stopped_checkpoint(tmp_path)
    original = path.read_bytes()
    state = full.recover_discovered(path, seed, 5)
    assert state['pending'] == [detail]
    assert state['deferred_discovery_urls'] == [seed, brand]
    assert state['run_mode'] == 'DISCOVERED_DETAILS_ONLY'
    assert path.read_bytes() == original
    state['pending'] = []
    assert full.coverage(state)['status'] == 'PARTIAL_OR_UNVERIFIED'


@pytest.mark.parametrize('error', ['ACCESS_RESTRICTION', 'LOGIN_REQUIRED', 'PAGINATION_HTTP_ACCESS_RESTRICTION'])
def test_recovery_refuses_access_stop(tmp_path, error):
    path, seed, _, _ = stopped_checkpoint(tmp_path, error)
    with pytest.raises(ValueError, match='never access'):
        full.recover_discovered(path, seed, 5)


@pytest.mark.parametrize('click_timeout,progress,status', [
    (False, False, 403), (True, True, 200), (True, False, 200), (True, True, 403)])
def test_pagination_http_diagnostics_redacted_and_listener_removed(monkeypatch, click_timeout, progress, status):
    monkeypatch.setattr(full.time, 'sleep', lambda _: None)
    class Request:
        resource_type = 'xhr'
        method = 'GET'
        url = 'https://www.foody.vn/api/list?token=NEVER_LOG_THIS'
    class Response:
        request = Request()
        url = 'https://www.foody.vn/api/list?token=NEVER_LOG_THIS'
        headers = {'content-type':'application/json','set-cookie':'NEVER_LOG_THIS'}
    Response.status = status
    class Empty:
        def count(self):
            return 0
    class Control:
        def count(self):
            return 1
        def nth(self, i):
            return self
        def is_visible(self):
            return True
        def evaluate(self, script, **kwargs):
            assert 'scrollIntoView' in script and "behavior:'instant'" in script
        def click(self, **kwargs):
            page.listeners['request'](Response.request)
            page.listeners['response'](Response())
            page.listeners['requestfailed'](Response.request)
            page.clicks += 1
            if click_timeout:
                raise TimeoutError('NEVER_LOG_THIS')
    class Page:
        url = 'https://www.foody.vn/ho-chi-minh/example'
        listener = None
        def __init__(self):
            self.listeners = {}
            self.clicks = 0
        def locator(self, selector):
            return Control() if selector == full.MORE_SELECTOR else Empty()
        def evaluate(self, *args):
            return {'links':['/ho-chi-minh/a'],'counters':['12 / 30']}
        def on(self, event, listener):
            self.listeners[event] = listener
        def remove_listener(self, event, listener):
            del self.listeners[event]
        def wait_for_function(self, *args, **kwargs):
            assert kwargs['timeout'] == 60000
            if not progress:
                raise TimeoutError('Synthetic fixture')
        def wait_for_timeout(self, ms):
            pass
        def content(self):
            return html(12,30, 'b' if self.clicks and progress else 'a')
    page = Page(); listing = full.BrowserListing(page)
    if status == 200 and progress:
        listing.advance()
    else:
        with pytest.raises(crawl.CrawlError, match=('HTTP_ACCESS_RESTRICTION' if status == 403 else 'STALLED_OR_TIMEOUT')):
            listing.advance()
    assert page.clicks == 1
    assert listing.last_diagnostic.get('click_timeout', False) == click_timeout
    assert page.listener is None
    assert not page.listeners
    assert listing.last_diagnostic['requests'][0]['state'] == 'failed'
    assert listing.last_diagnostic['elapsed_seconds'] >= 0
    assert listing.last_diagnostic['last_phase'] == 'wait_for_results'
    assert listing.last_diagnostic['responses'][0]['status'] == status
    assert 'NEVER_LOG_THIS' not in str(listing.last_diagnostic)
    assert listing.last_diagnostic['responses'][0]['path'] == '/api/list'


def test_pagination_metadata_allowlist_and_cursor_redaction():
    data = full.pagination_parameters('https://www.foody.vn/x?page=2&token=SECRET&cursor=SECRET',
        '{"PageSize":12,"Authorization":"SECRET","offset":24,"page":"SECRET"}')
    assert data['page'] == 2 and data['pagesize'] == 12 and data['offset'] == 24
    assert 'cursor_sha256' in data
    assert 'SECRET' not in str(data) and 'Authorization' not in str(data)


def test_response_ids_unknown_is_not_empty_and_duplicate_counts():
    assert full.response_restaurant_ids({'Items':[{'Id':5}]}) is None
    assert full.response_restaurant_ids({'error':'not logged'}) is None
    assert full.response_restaurant_ids({'Restaurants':[{'Id':True}]}) is None
    ids = full.response_restaurant_ids({'Restaurants':[{'Id':1},{'Id':2},{'Id':2}]})
    metrics = full.response_id_metrics(ids, {'1'})
    assert metrics['response_id_count'] == 3
    assert metrics['new_id_count'] == 1 and metrics['duplicate_id_count'] == 2
    assert full.response_id_metrics(None, set())['new_id_count'] is None
    assert full.response_restaurant_ids({'Restaurants':[]}) == []


@pytest.mark.parametrize('length,state', [(None,'MISSING'), ('bad','INVALID'), ('90','VALID')])
def test_observe_finished_json_without_requiring_length(length, state):
    class Response:
        headers = {} if length is None else {'content-length':length}
        def body(self):
            return b'{"Restaurants":[{"Id":1},{"Id":2}],"token":"NEVER_LOG_THIS"}'
    metadata, ids = full.observe_listing_response(Response())
    assert metadata['content_length_state'] == state
    assert metadata['body_observation'] == 'JSON_READ'
    assert ids == ['1','2'] and metadata['response_ids'] == ['1','2']
    assert metadata['decoded_bytes'] > 0 and len(metadata['body_sha256']) == 64
    assert 'NEVER_LOG_THIS' not in str(metadata)


@pytest.mark.parametrize('mode,expected', [
    ('declared_large','DECLARED_TOO_LARGE'), ('decoded_large','DECODED_TOO_LARGE'),
    ('error','READ_ERROR'), ('invalid_json','INVALID_JSON'), ('unknown','JSON_READ')])
def test_observation_failure_modes_are_distinct_and_private(mode, expected):
    class Response:
        headers = {'content-length':'2000001'} if mode == 'declared_large' else {}
        def body(self):
            assert mode != 'declared_large', 'must not read oversized declared body'
            if mode == 'error':
                raise RuntimeError('NEVER_LOG_THIS')
            if mode == 'decoded_large':
                return b'x' * 2_000_001
            if mode == 'invalid_json':
                return b'NEVER_LOG_THIS'
            return b'{"data":{"Items":[{"Id":1,"token":"NEVER_LOG_THIS"}]}}'
    metadata, ids = full.observe_listing_response(Response())
    assert metadata['body_observation'] == expected
    assert ids is None and metadata['response_ids'] is None
    assert 'NEVER_LOG_THIS' not in str(metadata)
    if mode == 'unknown':
        assert metadata['response_shape']['fields']['data']['fields']['Items']['length'] == 1


def test_observed_response_ids_can_be_compared_across_pages():
    class Response:
        headers = {}
        def __init__(self, body):
            self.data = body
        def body(self):
            return self.data
    a, _ = full.observe_listing_response(Response(b'{"Restaurants":[{"Id":2},{"Id":1}]}'))
    b, _ = full.observe_listing_response(Response(b'{"Restaurants":[{"Id":1},{"Id":2}]}'))
    assert a['response_ids'] == b['response_ids']
    assert a['body_sha256'] != b['body_sha256']


def test_counter_only_progress_stops_and_writes_metadata(monkeypatch):
    from playwright.sync_api import sync_playwright
    monkeypatch.setattr(full.time, 'sleep', lambda _: None)
    evidence = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            page.set_content(html(12, 100))
            page.locator('.btn-load-more').evaluate("n => n.onclick=()=>n.innerText='24 / 100'")
            listing = full.BrowserListing(page, metadata_sink=lambda d: evidence.append(dict(d)))
            pending = ['https://www.foody.vn/ho-chi-minh/kept']
            original = list(pending)
            snapshots = []
            with pytest.raises(crawl.CrawlError, match='PAGINATION_NO_NEW_URLS'):
                full.discover(listing.snapshot, listing.advance,
                              lambda doc, proof: snapshots.append(proof))
            assert pending == original and len(snapshots) == 1
            assert evidence[0]['new_parser_url_count'] == 0
            assert evidence[0]['click_state'] == 'RETURNED'
            assert evidence[0]['stop_code'] == 'PAGINATION_NO_NEW_URLS'
        finally:
            browser.close()


@pytest.mark.parametrize('ids,expected_new', [([], 0), ([1, 1], 0), ([2], 1)])
def test_mock_response_id_stop_without_another_click(monkeypatch, ids, expected_new):
    import json
    from playwright.sync_api import sync_playwright
    monkeypatch.setattr(full.time, 'sleep', lambda _: None)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page()
            requests = []
            body = json.dumps({'Restaurants':[{'Id':i} for i in ids], 'private':'NEVER_LOG_THIS'})
            doc = html(12, 100) + '''<script>
            document.querySelector('.btn-load-more').onclick=async function(){
              await (await fetch('/ho-chi-minh/dia-diem?page=2&token=NEVER_LOG_THIS')).json();
              document.querySelector('.filter-results').insertAdjacentHTML('beforeend',
                '<div class="filter-result-item"><a href="/ho-chi-minh/b">B</a></div>');
              this.innerText='24 / 100';
            };</script>'''
            def intercept(route):
                if '/dia-diem?' in route.request.url:
                    requests.append(1)
                    route.fulfill(status=200, body=body, content_type='application/json',
                                  headers={'content-length':str(len(body.encode()))})
                else:
                    route.fulfill(status=200, body=doc, content_type='text/html')
            page.route('**/*', intercept)
            page.goto('https://www.foody.vn/ho-chi-minh/fixture')
            listing = full.BrowserListing(page, seen_ids={'1'})
            if expected_new:
                listing.advance()
            else:
                with pytest.raises(crawl.CrawlError, match='PAGINATION_NO_NEW_IDS'):
                    listing.advance()
            assert len(requests) == 1
            entry = listing.last_diagnostic['responses'][0]
            assert entry['pagination'] == {'page':2}
            assert entry['new_id_count'] == expected_new
            assert entry['response_id_count'] == len(ids)
            assert listing.last_diagnostic['new_parser_url_count'] == 1
            assert 'NEVER_LOG_THIS' not in str(listing.last_diagnostic)
        finally:
            browser.close()


def test_reviewed_bad_navigation_recovery_retains_collected_places(tmp_path):
    import json
    path, seed, detail, brand = stopped_checkpoint(tmp_path, 'EMPTY_OR_UNSUPPORTED_DETAIL')
    state = json.loads(path.read_text(encoding='utf-8'))
    state['pending'] = ['https://www.foody.vn/ho-chi-minh/khu-vuc-quan-4', detail]
    state['records'] = {'existing': {'source_id': 'existing'}}
    state['deferred_discovery_urls'] = [seed, brand]
    path.write_text(json.dumps(state), encoding='utf-8')
    recovered = full.recover_discovered(path, seed, 5)
    assert recovered['pending'] == [detail]
    assert recovered['records'] == state['records']
    assert recovered['deferred_discovery_urls'] == [seed, brand]
    assert len(recovered['skipped_navigation_urls']) == 1


def test_actual_detail_parse_failure_is_not_silently_skipped(tmp_path):
    import json
    path, seed, detail, _ = stopped_checkpoint(tmp_path, 'EMPTY_OR_UNSUPPORTED_DETAIL')
    state=json.loads(path.read_text()); state['pending']=[detail];path.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        full.recover_discovered(path, seed, 5)


def test_primary_pagination_counter_ignores_other_widgets():
    doc = '''<button class="btn-load-more" data-bind="click: handleClickLoadMoreResult">24 / 30</button>
    <button class="btn-load-more">5 / 999</button>'''
    proof = full.pagination_evidence(doc, True)
    assert proof['loaded'] == 24 and proof['total'] == 30


@pytest.mark.parametrize('offset', [0, 30000])
def test_real_primary_load_more_ignores_unrelated_loading_widget(monkeypatch, offset):
    from playwright.sync_api import sync_playwright
    monkeypatch.setattr(full.time, 'sleep', lambda _: None)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            page.set_content('''<div class="filter-results"><a href="/ho-chi-minh/a">A</a></div>
            <button class="btn-load-more">Đang tải...</button>
            <button class="btn-load-more" data-bind="click: handleClickLoadMoreResult"
            onclick="document.querySelector('.filter-results').insertAdjacentHTML('beforeend',
            '<a href=/ho-chi-minh/b>B</a>');this.innerText='2 / 2';this.style.display='none';">1 / 2</button>''')
            page.add_style_tag(content=f'html {{ scroll-behavior:smooth; }} '
                f'[data-bind] {{ margin-top:{offset}px; }}')
            listing = full.BrowserListing(page)
            listing.advance()
            html, visible = listing.snapshot()
            proof = full.pagination_evidence(html, visible)
            assert proof['status'] == 'COUNTER_EXHAUSTED'
            assert listing.last_diagnostic['primary_control']
        finally:
            browser.close()


def test_continue_discovery_preserves_completed_details_and_source(tmp_path):
    import json
    path, seed, detail, brand = stopped_checkpoint(tmp_path)
    state=json.loads(path.read_text());state.update(status='EXHAUSTED_DISCOVERED_URLS',
        error=None,run_mode='DISCOVERED_DETAILS_ONLY',pending=[],visited=[detail],
        records={'existing':{'source_id':'existing'}},deferred_discovery_urls=[seed,brand])
    path.write_text(json.dumps(state));original=path.read_bytes()
    next_state=full.continue_discovery(path,seed,5)
    assert next_state['records']==state['records'] and next_state['visited']==[detail]
    assert next_state['pending']==[seed,brand]
    assert path.read_bytes()==original


def test_discovery_continuation_rejects_failed_checkpoint(tmp_path):
    path,seed,_,_=stopped_checkpoint(tmp_path)
    with pytest.raises(ValueError,match='completed detail-only'):
        full.continue_discovery(path,seed,5)


def test_real_chrome_local_load_more(monkeypatch):
    from playwright.sync_api import sync_playwright
    monkeypatch.setattr(full.time, 'sleep', lambda _: None)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page()
            requests = []
            page.route('**/*', lambda route: route.abort())
            page.on('request', lambda request: requests.append(request.url))
            page.set_content('''<div class="filter-results"><div class="filter-result-item">
              <a href="/ho-chi-minh/a">A</a></div></div>
              <button class="btn-load-more" onclick="
                document.querySelector('.filter-results').insertAdjacentHTML('beforeend',
                '<div class=filter-result-item><a href=/ho-chi-minh/b>B</a></div>');
                this.textContent='2 / 2'; this.style.display='none';">1 / 2</button>''')
            listing = full.BrowserListing(page)
            results = []
            evidence = full.discover(listing.snapshot, listing.advance,
                                     lambda doc, proof: results.append(proof['loaded']))
            assert results == [1, 2]
            assert evidence['status'] == 'COUNTER_EXHAUSTED'
            assert requests == []
        finally:
            browser.close()
