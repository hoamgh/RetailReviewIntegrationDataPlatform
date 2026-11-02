from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import storage_session as session


def test_only_foody_credentials_are_exported():
    state = dict(cookies=[{'domain': '.foody.vn', 'value': 'FIXTURE'},
                          {'domain': 'id.foody.vn', 'value': 'FIXTURE'},
                          {'domain': 'google.com', 'value': 'UNRELATED'},
                          {'domain': 'evilfoody.vn', 'value': 'UNRELATED'}],
                 origins=[{'origin': 'https://www.foody.vn', 'localStorage': []},
                          {'origin': 'https://example.com', 'localStorage': []}])
    result = session.scoped_state(state)
    assert len(result['cookies']) == 2 and len(result['origins']) == 1
    assert state['cookies'][2]['value'] == 'UNRELATED'


def test_unauthenticated_state_missing_does_not_launch_browser(tmp_path):
    result = session.probe(tmp_path / 'missing.json')
    assert result['status'] == 'BLOCKED_STATE_MISSING'


def test_export_only_reads_existing_tab_and_never_closes_chrome(monkeypatch):
    import playwright.sync_api
    exported = []
    class Page:
        url = 'https://www.foody.vn/ho-chi-minh/dia-diem-tai-quan-4'
        def evaluate(self, expression):
            assert expression == session.AUTH_JS
            return {'logged_in': True}
        def close(self):
            raise AssertionError('Do not close personal tab')
    class Context:
        pages = [Page()]
        def storage_state(self):
            return {'cookies': [], 'origins': []}
        def close(self):
            raise AssertionError('Do not close personal context')
    class Browser:
        contexts = [Context()]
        def close(self):
            raise AssertionError('Do not close personal Chrome')
    class Chromium:
        def connect_over_cdp(self, endpoint, **kwargs):
            return Browser()
    class Playwright:
        chromium = Chromium()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(playwright.sync_api, 'sync_playwright', Playwright)
    monkeypatch.setattr(session, 'save_state', lambda path, state: exported.append(path))
    result = session.export('http://127.0.0.1:9222')
    assert result['exported'] and exported == [session.DEFAULT_STATE]


def test_real_auth_detector_syntax_and_avatar_without_logging_account():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            page.set_content('<header><a href="javascript:void(0)"><img alt="Fixture"><span>Fixture User</span></a></header>')
            assert page.evaluate(session.AUTH_JS)['logged_in']
            page.set_content('<header><a href="/login">Đăng nhập</a></header>')
            assert not page.evaluate(session.AUTH_JS)['logged_in']
        finally:
            browser.close()
