"""Export Foody-only state through existing CDP; test one page without bulk crawl.

Never closes/disconnects the user's Chrome explicitly or changes existing tabs.
Only the Playwright transport is stopped when attaching. No credentials in output.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit
import uuid

import crawl

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = ROOT / '.auth/foody-state.json'
SEED = 'https://www.foody.vn/ho-chi-minh/dia-diem-tai-quan-4?categorygroup=food'


def foody_host(host):
    host = (host or '').lstrip('.').lower()
    return host == 'foody.vn' or host.endswith('.foody.vn')


def scoped_state(state):
    # Personal Chrome may hold unrelated Google/banking/etc. sessions. Never write
    # those into this project's state file.
    return dict(cookies=[c for c in state.get('cookies', []) if foody_host(c.get('domain'))],
                origins=[o for o in state.get('origins', [])
                         if foody_host(urlsplit(o.get('origin', '')).hostname)])


AUTH_JS = '''() => {
 const visible=e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
 const anchors=[...document.querySelectorAll('a')];
 const logout=anchors.filter(a=>/^(đăng xuất|logout|log out)$/i.test(a.innerText.trim())
   || /(?:logout|logoff|dang-xuat)(?:[/?#]|$)/i.test(a.getAttribute('href')||''));
 const account=[...document.querySelectorAll('header .user-name,header .user-avatar,.header .user-name,.header .user-avatar,.account-name,.user-account .avatar')]
   .some(e=>visible(e) && !(e.innerText||'').includes('{{'));
 const avatarAccount=anchors.some(a=>a.closest('header,[role="banner"]')&&visible(a)&&
   a.querySelector('img')&&/^javascript:/i.test(a.getAttribute('href')||'')&&
   a.innerText.trim()&&!/đăng nhập|login|{{/i.test(a.innerText));
 const loggedIn=logout.some(visible)||(account&&logout.length>0)||avatarAccount;
 const loginVisible=anchors.some(a=>visible(a)&&/^(đăng nhập|login|log in)$/i.test(a.innerText.trim()));
 const restriction=/verify you are human|access denied|just a moment|unusual traffic/i.test(document.body?.innerText||'');
 return {logged_in:loggedIn&&!restriction,login_visible:loginVisible,
   restriction,positive_account_signal:account||avatarAccount,positive_logout_signal:logout.length>0};
}'''


def protect_directory(folder):
    folder.mkdir(parents=True, exist_ok=True)
    ignored = subprocess.run(['git', 'check-ignore', '--quiet', str(folder / 'foody-state.json')],
                             cwd=ROOT, capture_output=True, timeout=5)
    tracked = subprocess.run(['git', 'ls-files', '--', '.auth'], cwd=ROOT,
                             capture_output=True, timeout=5, check=True)
    if ignored.returncode != 0 or tracked.stdout.strip():
        raise ValueError('AUTH_DIRECTORY_NOT_PRIVATE_FROM_GIT')
    if os.name == 'nt':
        identity = subprocess.run(['whoami'], capture_output=True, text=True,
                                  check=True, timeout=5).stdout.strip()
        subprocess.run(['icacls', str(folder), '/inheritance:r', '/grant:r',
                        identity + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'],
                       capture_output=True, check=True, timeout=10)
    else:
        folder.chmod(0o700)


def save_state(path, state):
    if path.resolve() != DEFAULT_STATE.resolve():
        raise ValueError('Use only the designated ignored .auth/foody-state.json')
    protect_directory(path.parent)
    temporary = path.with_name('foody-state.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(scoped_state(state), stream, ensure_ascii=False)
        stream.flush(); os.fsync(stream.fileno())
    if os.name != 'nt':
        temporary.chmod(0o600)
    os.replace(temporary, path)


def export(endpoint):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint, timeout=10000)
        # Do NOT use browser.close(), context.close(), page.close(), navigation,
        # reload, logout or cookie mutation on this attached browser.
        candidates = [(context, page) for context in browser.contexts for page in context.pages
                      if foody_host(urlsplit(page.url).hostname)]
        if not candidates:
            return dict(status='BLOCKED_NO_EXISTING_FOODY_TAB', exported=False)
        for context, page in candidates:
            signals = page.evaluate(AUTH_JS)
            if signals['logged_in']:
                state = context.storage_state()
                save_state(DEFAULT_STATE, state)
                del state
                return dict(status='EXPORTED', exported=True, account_recognized=True,
                            state_path='.auth/foody-state.json', scope='Foody domains only')
        return dict(status='BLOCKED_SESSION_UNVERIFIED', exported=False,
                    note='No positive authenticated UI signal on existing Foody tabs')


def probe(path):
    from playwright.sync_api import sync_playwright
    if not path.exists():
        return dict(status='BLOCKED_STATE_MISSING', account_recognized=False)
    with sync_playwright() as p:
        # This owns a NEW isolated browser, never the attached personal browser.
        browser = None
        phase = 'BROWSER_LAUNCH'
        try:
            browser = p.chromium.launch(channel='chrome', headless=True)
            phase = 'LOAD_STATE'
            context = browser.new_context(storage_state=str(path))
            page = context.new_page()
            phase = 'ONE_PAGE_NAVIGATION'
            response = page.goto(SEED, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_function("() => document.readyState === 'complete'", timeout=15000)
            phase = 'ACCOUNT_DETECTION'
            try:
                page.wait_for_function('''() => [...document.querySelectorAll('header a,[role="banner"] a')]
                    .some(a=>/đăng nhập|login/i.test(a.innerText)||
                        (a.querySelector('img')&&/^javascript:/i.test(a.getAttribute('href')||'')))''', timeout=10000)
            except Exception:
                pass  # Report unverified/empty rendering, never assume a valid session.
            signals = page.evaluate(AUTH_JS)
            html = page.content()
            phase = 'CRAWLER_PARSER'
            parsed = crawl.parse_page(html, SEED, review_limit=0)
            # Reuse experimental crawler parser; return counts/booleans only.
            return dict(status='PASS' if signals['logged_in'] else 'SESSION_NOT_RECOGNIZED',
                        account_recognized=signals['logged_in'], http_status=response.status if response else None,
                        restriction=signals['restriction'], parsed_discovery_links=len(parsed['links']),
                        rendered_body_characters=len(page.locator('body').inner_text()),
                        bulk_crawl=False)
        except Exception as exc:
            return dict(status='BLOCKED', phase=phase, error_type=type(exc).__name__,
                        account_recognized=False, bulk_crawl=False)
        finally:
            if browser:
                browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    command = sub.add_parser('export'); command.add_argument('--cdp-endpoint', required=True)
    sub.add_parser('test')
    args = parser.parse_args()
    try:
        result = export(args.cdp_endpoint) if args.mode == 'export' else probe(DEFAULT_STATE)
    except Exception as exc:
        # Never log Playwright error strings, stack traces or state contents: they
        # can contain URLs, headers, user data or credentials.
        result = dict(status='BLOCKED', error_type=type(exc).__name__, exported=False)
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if result['status'] in ('EXPORTED', 'PASS') else 1


if __name__ == '__main__':
    raise SystemExit(main())
