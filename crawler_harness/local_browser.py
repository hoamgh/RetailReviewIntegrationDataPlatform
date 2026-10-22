"""C2d-A local-only UC provider. Not a source access verification provider.

The real driver lives exclusively inside the contained worker. Only a fixed
synthetic document and reviewed operations are supported; collection is disabled.
"""
from pathlib import Path
import hashlib
from dataclasses import asdict

from .containment import ContainmentError, WindowsAPI
from .local_faults import TIMEOUT_SCRIPT

MODE = 'LOCAL_SELENIUMBASE_UC_V1'
HTML = '''<!doctype html><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; form-action 'none'; base-uri 'none'">
<title>Harness synthetic local smoke</title>
<button id="synthetic-button" type="button">Synthetic button</button>
<p id="synthetic-result">before</p>'''
READ = "return document.querySelector('#synthetic-result').textContent;"
INSTALL = "document.querySelector('#synthetic-button').onclick = () => { document.querySelector('#synthetic-result').textContent = 'clicked'; }; return true;"
READY = "return document.readyState;"
SCRIPTS = {READ, INSTALL, READY, TIMEOUT_SCRIPT}


def retain_owned_processes(owner, identities):
    """Prove exact Job membership using retained handles, not a PID-list guess."""
    if len(identities)!=2 or len({p['pid'] for p in identities})!=2:
        raise ContainmentError('Two distinct owned process identities required')
    for identity in identities:
        handle=owner.api.k.OpenProcess(0x100000|0x1000,False,identity['pid'])
        owner.api.require(handle)
        try:
            actual=owner.api.identity(handle)
            if asdict(actual)!=identity or owner.api.wait(handle):
                raise ContainmentError('Browser/service creation identity changed or stopped')
            owner.api.member(handle,owner.job)
            if identity['pid'] not in owner._children:
                owner._children[identity['pid']]=(handle,actual);handle=None
        finally:
            if handle:owner.api.k.CloseHandle(handle)


def validate_config(config):
    if type(config) is not dict or set(config) != {'page', 'sha256'}:
        raise ContainmentError('Exact local page configuration required')
    page = Path(config['page']).resolve(strict=True)
    if not page.is_file() or page.read_bytes() != HTML.encode('utf-8'):
        raise ContainmentError('Only the fixed synthetic local document is allowed')
    if hashlib.sha256(page.read_bytes()).hexdigest() != config['sha256']:
        raise ContainmentError('Synthetic document digest mismatch')
    return page


def permit(config, name, args, element=None):
    page = validate_config(config)
    if element:
        # Only the synthetic button is ever returned as an element.
        allowed = (name in {'click', 'is_displayed', 'text', 'tag_name', 'size'} and not args)
    else:
        allowed = (
            name in {'current_url', 'window_handles', 'current_window_handle'} and not args or
            name == 'get' and args == [page.as_uri()] or
            name == 'execute_script' and len(args) == 1 and args[0] in SCRIPTS or
            name == 'find_element' and args == ['css selector', '#synthetic-button'] or
            name == 'save_screenshot' and len(args) == 1)
    if not allowed:
        raise ContainmentError('Operation unsupported by local-only real provider')


class LocalUCProvider:
    def __init__(self, config, profile, *, api=None):
        validate_config(config)
        from experiments.tiktok_access_benchmark.session_manager import standard_browser_factory
        from selenium.webdriver.remote.webelement import WebElement
        self.element_type = WebElement
        self.api=api or WindowsAPI()
        from .uc_service import startup_service,endpoint_matches
        # Reuse the reviewed TikTok -> BrowserFactory -> SeleniumBaseSession path.
        with startup_service(self.api) as owned_type:
            self.session = standard_browser_factory(headless=False, profile_dir=profile).create('managed-local')
            self.driver = self.session.start()
        if not isinstance(self.driver.service,owned_type):
            raise ContainmentError('UC did not use the managed service creation hook')
        self.service=self.driver.service
        self.service.seal()
        if not endpoint_matches(self.driver,self.service):
            raise ContainmentError('Driver executor/service endpoint mismatch')
        if not getattr(self.driver, '_is_using_uc', False):
            raise ContainmentError('UC initialization not confirmed')
        self.profile_dir = str(Path(self.driver.user_data_dir).resolve())
        if self.profile_dir != str(Path(profile).resolve()):
            raise ContainmentError('UC did not use the dedicated profile')
        self.driver.set_page_load_timeout(10)
        self.driver.set_script_timeout(5)
        self.browser_process=self.driver.browser_process
        self.browser_identity=self.api.identity(int(self.browser_process._handle))
        self.pids = [self.browser_identity.pid, self.service._managed_identity.pid]
        if len(set(self.pids)) != 2:
            raise ContainmentError('Distinct browser/service ownership required')
        self.validate()

    def validate(self):
        from .uc_service import endpoint_matches
        self.service.validate_owned()
        handle=int(self.browser_process._handle)
        if (self.driver.service is not self.service or
                self.driver.browser_process is not self.browser_process or
                self.driver.browser_pid!=self.browser_identity.pid or
                self.api.identity(handle)!=self.browser_identity or self.api.wait(handle) or
                not endpoint_matches(self.driver,self.service)):
            raise ContainmentError('Pinned browser/service/endpoint changed')
        self.api.member(handle,None)

    def identities(self):
        self.validate()
        return [asdict(self.browser_identity),asdict(self.service._managed_identity)]
