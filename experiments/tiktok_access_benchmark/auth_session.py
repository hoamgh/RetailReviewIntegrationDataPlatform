"""Read-only authentication classification; never submits credentials or OAuth requests."""
import json
import re
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

AUTH_JS = r"""return (()=>{
 const visible=e=>!!(e && e.getClientRects().length && getComputedStyle(e).visibility!=='hidden');
 const any=s=>Array.from(document.querySelectorAll(s)).some(visible);
 const text=(document.body?.innerText||'').toLowerCase();
 const google=location.hostname==='accounts.google.com';
 return {google_blocked:google && (text.includes('this browser or app may not be secure') || text.includes('trình duyệt hoặc ứng dụng này có thể không an toàn')),
 authenticated_ui:any('[data-e2e="nav-profile"] a[href^="/@"],a[data-e2e="nav-profile"][href^="/@"],[data-e2e="logout-button"],[data-e2e="logout"]'),
 login_required:any('[data-e2e="login-modal"],#login-modal,[data-e2e="nav-login"],input[type="password"]'),
 challenge:any('#captcha_container,.captcha-verify-container,._wafchallengeid,[id*="captcha-verify"]')};
})();"""


def snapshot(driver):
    """Inspect existing windows and restore selected handle; retain no body text/URLs/cookies."""
    original=driver.current_window_handle
    pages=[]
    try:
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            url=urlsplit(driver.current_url)
            host=url.hostname or ''
            keys=set(parse_qs(url.query))
            evidence=driver.execute_script(AUTH_JS)
            pages.append(dict(hostname=host, provider='GOOGLE' if host=='accounts.google.com' else 'TIKTOK' if host=='www.tiktok.com' else 'OTHER',
                              oauth_navigation=host=='accounts.google.com' and ('oauth' in url.path.lower() or {'client_id','redirect_uri'} <= keys),
                              route_kind='GOOGLE_SIGN_IN' if host=='accounts.google.com' else 'TIKTOK_LOGIN' if host=='www.tiktok.com' and '/login' in url.path else 'OTHER_PAGE',
                              google_blocked=bool(evidence.get('google_blocked')),
                              authenticated_ui=bool(evidence.get('authenticated_ui')) if host=='www.tiktok.com' else False,
                              login_required=bool(evidence.get('login_required')),
                              challenge=bool(evidence.get('challenge'))))
    finally:
        driver.switch_to.window(original)
    return pages


def classify(pages, previously_ready=False):
    if any(p.get('google_blocked') and p.get('provider')=='GOOGLE' for p in pages):
        return {'state':'AUTH_BLOCKED','reason':'GOOGLE_UNSAFE_BROWSER','provider':'GOOGLE'}
    if any(p.get('challenge') for p in pages):
        return {'state':'AUTH_REQUIRED','reason':'MANUAL_VERIFICATION_REQUIRED','provider':'TIKTOK'}
    if len(pages)!=1 or pages[0].get('provider')!='TIKTOK':
        return {'state':'AUTH_REQUIRED','reason':'RETURN_TO_ONE_TIKTOK_TAB','provider':'UNKNOWN'}
    page=pages[0]
    if page.get('authenticated_ui') and not page.get('login_required'):
        return {'state':'AUTH_READY','reason':'SIGNED_IN_UI_OBSERVED','provider':'TIKTOK','validation':'DOM_ONLY_NOT_SERVER_TOKEN_VALIDATION'}
    if previously_ready and page.get('login_required'):
        return {'state':'AUTH_EXPIRED','reason':'PREVIOUS_SIGNED_IN_UI_NOW_REQUIRES_LOGIN','provider':'TIKTOK','validation':'UI_TRANSITION_NOT_EXPIRY_TIME'}
    return {'state':'AUTH_REQUIRED','reason':'NO_VERIFIED_SIGNED_IN_UI','provider':'TIKTOK'}


def browser_info(driver):
    caps=getattr(driver,'capabilities',{})
    chromedriver=caps.get('chrome',{}).get('chromedriverVersion','')
    version=re.match(r'\d+(?:\.\d+)+',chromedriver)
    result={'implementation':'SELENIUM_CHROMEDRIVER','browser_version':caps.get('browserVersion'),
            'chromedriver_version':version.group() if version else None,'launch_arguments':'UNOBSERVED',
            'embedded_browser':False,'oauth_client_implemented':False}
    try:
        args=driver.execute_cdp_cmd('Browser.getBrowserCommandLine',{}).get('arguments',[])
        result['launch_arguments']=[a.split('=',1)[0]+('=[PRIVATE]' if '=' in a else '')
                                    for a in args if isinstance(a,str) and a.startswith('--')]
    except Exception:
        pass
    return result


class AuthMonitor:
    def __init__(self,driver,output=None):
        self.driver=driver
        self.output=Path(output) if output else None
        self.ready=False
        self.info=browser_info(driver)
        self.history=[]
    def check(self):
        try:
            pages=snapshot(self.driver)
            result=classify(pages,self.ready)
        except Exception as exc:
            pages=[]
            result={'state':'AUTH_BLOCKED','reason':'BROWSER_SESSION_UNAVAILABLE','provider':'LOCAL',
                    'error_type':type(exc).__name__}
        self.ready=self.ready or result['state']=='AUTH_READY'
        if result['state']=='AUTH_READY':self.driver._auth_ready_observed=True
        entry=dict(result,captured_at=datetime.now(timezone.utc).isoformat(),contexts=pages)
        self.history.append(entry)
        self.record(result)
        return result
    def record(self,result):
        if self.output:
            self.output.mkdir(parents=True,exist_ok=True)
            tmp=self.output/'auth.json.tmp'
            tmp.write_text(json.dumps({'current':result,'browser':self.info,'history':self.history},indent=2),encoding='utf-8')
            tmp.replace(self.output/'auth.json')


def record_login_gate(driver,output):
    state='AUTH_EXPIRED' if getattr(driver,'_auth_ready_observed',False) else 'AUTH_REQUIRED'
    path=Path(output)/'auth_gate.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'state':state,'reason':'LOGIN_GATE_OR_REQUIRED_ENDPOINT_401','captured_at':datetime.now(timezone.utc).isoformat()}),encoding='utf-8')


def checked_auth(monitor,restriction_check=None):
    """Fresh existing UI classification plus an optional existing access-policy guard."""
    result=monitor.check()
    restriction=restriction_check(result) if restriction_check else None
    if restriction:
        result={'state':'AUTH_BLOCKED','reason':restriction,'provider':'TIKTOK'}
        monitor.history.append(dict(result,captured_at=datetime.now(timezone.utc).isoformat(),contexts=[]))
        monitor.record(result)
    monitor.current_result=result
    return result


def wait_for_auth(monitor,timeout=20,*,interval=.5,clock=None,sleeper=None,restriction_check=None):
    """Bounded DOM observation only: no navigation, refresh, browser restart or login."""
    timeout=float(timeout);interval=float(interval)
    if not math.isfinite(timeout) or not 0<=timeout<=300:raise ValueError('Auth wait must be between 0 and 300 seconds')
    if not math.isfinite(interval) or interval<=0:raise ValueError('Auth observation interval must be positive')
    clock=clock or time.monotonic;sleeper=sleeper or time.sleep
    started=clock();deadline=started+timeout
    while True:
        result=checked_auth(monitor,restriction_check)
        if (result['state'] in ('AUTH_READY','AUTH_BLOCKED','AUTH_EXPIRED') or
                result['reason']=='MANUAL_VERIFICATION_REQUIRED'):
            return result
        remaining=deadline-clock()
        if remaining<=0:
            result=dict(result,wait_timed_out=True,wait_seconds=clock()-started)
            monitor.current_result=result;monitor.record(result)
            return result
        sleeper(min(interval,remaining))


def manual_auth(browser,input_fn=None,output=None,*,navigate=True,allow_public=True,monitor=None,restriction_check=None):
    """Explicit human input on one owner; optionally navigate once, never submit credentials."""
    driver=browser.driver
    if navigate:driver.get('https://www.tiktok.com/')
    print(f'Login browser retained. ChromeDriver port: {urlsplit(driver.service.service_url).port}',flush=True)
    print('Use a TikTok login method offered by its UI (QR/phone/email if available). Do not retry Google refusal. READY requires signed-in UI.',flush=True)
    if allow_public:print('PUBLIC allows anonymous browsing only.',flush=True)
    monitor=monitor or AuthMonitor(driver,output)
    read=input_fn or input
    while True:
        result=checked_auth(monitor,restriction_check)
        print(f"Authentication: {result['state']} ({result['reason']})",flush=True)
        if result['state'] in ('AUTH_BLOCKED','AUTH_EXPIRED'):
            print('Authentication stopped; no automatic retry or crawl. See auth.json.',flush=True)
            return False
        prompt='Manual auth [READY/PUBLIC/CLOSE]: ' if allow_public else 'Manual auth [READY/CLOSE]: '
        try:action=read(prompt).strip().upper()
        except (EOFError,KeyboardInterrupt):return False
        if action=='CLOSE':return False
        if action not in ('READY','PUBLIC') or (action=='PUBLIC' and not allow_public):continue
        result=checked_auth(monitor,restriction_check)  # Fresh revalidation after human input.
        if result['state'] in ('AUTH_BLOCKED','AUTH_EXPIRED'):return False
        if action=='READY' and result['state']=='AUTH_READY':return True
        if allow_public and action=='PUBLIC' and result['state']=='AUTH_REQUIRED' and result['reason']=='NO_VERIFIED_SIGNED_IN_UI':
            monitor.record(dict(result,anonymous_access_requested=True))
            return True  # Crawl still checks visible access gates; never marks anonymous as AUTH_READY.
        print('No eligible state observed. Complete supported manual login, or use PUBLIC only if TikTok permits anonymous access.',flush=True)
