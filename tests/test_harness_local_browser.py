"""C2d-A offline provider contracts. These tests never create a real browser."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext

import pytest

from crawler_harness.browser_commands import ManagedBrowserSession, CommandFault
from crawler_harness.containment import ContainmentError
from crawler_harness.local_browser import HTML, READ, INSTALL, LocalUCProvider, permit, validate_config, retain_owned_processes
from crawler_harness.state import JobStore, StateError
from crawler_harness.restrictions import Restrictions
from experiments.tiktok_comment_dom.persistence import CommentPersistence


@pytest.fixture
def page(tmp_path):
    path=tmp_path/'synthetic.html';path.write_bytes(HTML.encode())
    return {'page':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.mark.parametrize('name,args,element',[
    ('get',None,None),('execute_script',[READ],None),('execute_script',[INSTALL],None),
    ('find_element',['css selector','#synthetic-button'],None),
    ('current_url',[],None),('click',[],'opaque-key'),('text',[],'opaque-key')])
def test_allow_only_local_smoke_operations(page,name,args,element):
    permit(page,name,[Path(page['page']).as_uri()] if args is None else args,element)


@pytest.mark.parametrize('name,args,element',[
    ('get',['https://www.tiktok.com/'],None),('get',['http://localhost:80/'],None),
    ('get',['file:///other.html'],None),('execute_script',["window.location='https://example.com'"],None),
    ('execute_script',[READ,'unexpected-arg'],None),('execute_cdp_cmd',['Network.enable',{}],None),
    ('find_element',['css selector','a'],None),('quit',[],None),('uc_open',[],None),
    ('get_attribute',['href'],'opaque-key'),('find_element',['xpath','..'],'opaque-key')])
def test_reject_external_and_unreviewed_surfaces(page,name,args,element):
    with pytest.raises(ContainmentError):permit(page,name,args,element)


def test_page_tampering_rejected_after_initial_validation(page):
    validate_config(page)
    Path(page['page']).write_text('<script>fetch("https://example.com")</script>')
    with pytest.raises(ContainmentError):permit(page,'current_url',[])


def test_wrong_digest_or_extra_config_rejected(page):
    with pytest.raises(ContainmentError):validate_config(dict(page,sha256='wrong'))
    with pytest.raises(ContainmentError):validate_config(dict(page,url='https://example.com'))


@pytest.mark.parametrize('defect',[None,'not-uc','wrong-profile','same-process'])
def test_existing_factory_and_profile_contract_without_real_browser(page,tmp_path,monkeypatch,defect):
    from experiments.tiktok_access_benchmark import session_manager
    profile=tmp_path/'dedicated'
    from crawler_harness.containment import ProcessIdentity
    service=SimpleNamespace(process=SimpleNamespace(pid=200),seal=lambda:None,validate_owned=lambda:None,
        _managed_identity=ProcessIdentity(100 if defect=='same-process' else 200,22,2))
    driver=SimpleNamespace(_is_using_uc=defect!='not-uc',user_data_dir=str(profile if defect!='wrong-profile' else tmp_path),
        browser_pid=100,service=SimpleNamespace(process=SimpleNamespace(pid=100 if defect=='same-process' else 200)),
        browser_process=SimpleNamespace(_handle=100),
        set_page_load_timeout=lambda _:None,set_script_timeout=lambda _:None)
    driver.service=service
    from crawler_harness import uc_service
    monkeypatch.setattr(uc_service,'startup_service',lambda api:nullcontext(SimpleNamespace))
    monkeypatch.setattr(uc_service,'endpoint_matches',lambda *args:True)
    api=SimpleNamespace(identity=lambda h:ProcessIdentity(h,11,2),wait=lambda h:False,member=lambda *a:None)
    calls=[]
    def factory(**options):
        calls.append(options)
        return SimpleNamespace(create=lambda _:SimpleNamespace(start=lambda:driver))
    monkeypatch.setattr(session_manager,'standard_browser_factory',factory)
    if defect:
        with pytest.raises(ContainmentError):LocalUCProvider(page,profile,api=api)
    else:
        provider=LocalUCProvider(page,profile,api=api)
        assert provider.pids==[100,200]
    assert calls==[{'headless':False,'profile_dir':profile}]


@pytest.fixture
def ledger(tmp_path):
    database=tmp_path/'local.sqlite3'
    repo=CommentPersistence(database,tmp_path/'lake');repo.close()
    store=JobStore(database)
    yield store
    store.close()


def local_session(ledger,page,tmp_path,**kw):
    job=ledger.create_job(source='tiktok',kind='collection',place_id='synthetic',entity_id='synthetic',
        input_digest=page['sha256'],workflow=kw.pop('workflow','harness-local-smoke-v1'))
    token=ledger.claim(job,'synthetic-owner',lease_seconds=120)
    return ManagedBrowserSession(ledger,token,profile=tmp_path/'not-yet-profile',evidence=tmp_path/'evidence',
        local_page=page['page'],**kw)


def test_fixture_registry_cannot_enable_real_driver(ledger,page,tmp_path):
    Restrictions.install(ledger,offline_fixture=True)
    with pytest.raises(StateError,match='cannot authorize'):local_session(ledger,page,tmp_path)


def test_source_collection_and_source_writes_disabled(ledger,page,tmp_path):
    session=local_session(ledger,page,tmp_path)
    with pytest.raises(CommandFault,match='collection disabled'):session.run_collection(None,None)
    with pytest.raises(CommandFault,match='cannot write'):session.source_gate()
    assert session.owner.process is None


def test_wrong_workflow_fails_before_process_creation(ledger,page,tmp_path):
    session=local_session(ledger,page,tmp_path,workflow='ordinary-source-job')
    with pytest.raises(CommandFault,match='synthetic workflow'):session.__enter__()
    assert session.owner.process is None and not (tmp_path/'evidence').exists()


def test_expired_claim_fails_before_browser_start(ledger,page,tmp_path):
    session=local_session(ledger,page,tmp_path)
    ledger.clock=lambda:ledger.get(session.token.job_id)['lease_until']+1
    from crawler_harness.state import OwnershipError
    with pytest.raises(OwnershipError):session.__enter__()
    assert session.owner.process is None


@pytest.mark.parametrize('fault',[None,'stopped','identity','wrong-job','duplicate'])
def test_process_proof_requires_retained_identity_and_exact_job(fault):
    from crawler_harness.containment import ProcessIdentity
    identities=[{'pid':1,'creation_filetime':11,'windows_session_id':2},
                {'pid':2,'creation_filetime':22,'windows_session_id':2}]
    if fault=='duplicate':identities[1]=identities[0]
    closed=[];members=[]
    def member(handle,job):
        if fault=='wrong-job':raise ContainmentError('wrong Job')
        members.append((handle,job))
    api=SimpleNamespace(k=SimpleNamespace(OpenProcess=lambda rights,inherit,pid:pid,CloseHandle=closed.append),
        require=lambda h:None,wait=lambda h:fault=='stopped',
        identity=lambda h:ProcessIdentity(h,h*11+(1 if fault=='identity' else 0),2),member=member)
    owner=SimpleNamespace(api=api,job='exact-owned-job',_children={})
    if fault:
        with pytest.raises(ContainmentError):retain_owned_processes(owner,identities)
        assert not owner._children
    else:
        retain_owned_processes(owner,identities)
        assert members==[(1,'exact-owned-job'),(2,'exact-owned-job')]
        assert set(owner._children)=={1,2} and closed==[]


def test_installed_uc_connect_starts_even_an_already_running_service():
    from seleniumbase.undetected import Chrome
    from selenium.webdriver.common.service import Service
    class SimulatedService(Service):
        def __init__(self):
            super().__init__('synthetic.exe',port=1234)
            self.process=SimpleNamespace(pid=1,poll=lambda:None);self.created=0
        def _start_process(self,path):
            self.created+=1
            self.process=SimpleNamespace(pid=2,poll=lambda:None)
        def is_connectable(self):return True  # Original listener still answers.
        def command_line_args(self):return []
        def stop(self):pass
    service=SimulatedService();original=service.process
    driver=SimpleNamespace(service=service,start_session=lambda:None,window_handles=[])
    Chrome.connect(driver)  # Actual installed UC implementation, no browser/HTTP.
    assert service.created==1 and service.process is not original and driver._is_connected


@pytest.fixture
def owned_service():
    from crawler_harness.uc_service import service_class
    from crawler_harness.containment import ProcessIdentity
    state={'stopped':False,'wrong_identity':False,'listener':True,'job':True}
    class Base:
        port=1234;service_url='http://localhost:1234'
        def __init__(self):self.process=None;self.created=0
        def _start_process(self,path):
            self.created+=1;self.process=SimpleNamespace(pid=200,_handle=200)
        def start(self):self._start_process('synthetic')
    def member(*args):
        if not state['job']:raise ContainmentError('Wrong Job')
    api=SimpleNamespace(identity=lambda h:ProcessIdentity(h,22+int(state['wrong_identity']),2),
        wait=lambda h:state['stopped'],member=member)
    service=service_class(Base,api,lambda identity,port:state['listener'])()
    return service,state


def test_guarded_uc_startup_connect_reuses_exact_owned_service(owned_service):
    from seleniumbase.undetected import Chrome
    service,_=owned_service;service.start();original=service.process
    driver=SimpleNamespace(service=service,start_session=lambda:None,window_handles=[])
    Chrome.connect(driver)
    assert service.created==1 and service.process is original and service._managed_reuses==1
    service.seal();service.validate_owned()


@pytest.mark.parametrize('fault',['stopped','wrong_identity','listener','job','process_object'])
def test_service_transition_ownership_failure_is_closed(owned_service,fault):
    service,state=owned_service;service.start()
    if fault=='process_object':service.process=SimpleNamespace(pid=200,_handle=200)
    else:state[fault]=fault in {'stopped','wrong_identity'}
    with pytest.raises(ContainmentError):service.start()
    assert service.created==1


def test_post_handshake_restart_is_poisoned_even_when_uc_swallows_error(owned_service):
    from seleniumbase.undetected import Chrome
    service,_=owned_service;service.start();service.seal()
    driver=SimpleNamespace(service=service,start_session=lambda:None,window_handles=[])
    Chrome.connect(driver)  # UC suppresses the refused service.start exception.
    with pytest.raises(ContainmentError):service.validate_owned()
    assert service.created==1


def test_second_service_instance_cannot_replace_first(owned_service):
    service,_=owned_service;service.start()
    with pytest.raises(ContainmentError):type(service)().start()
    assert service.created==1


@pytest.mark.parametrize('url',['http://localhost:1234','http://localhost:9999','https://localhost:1234','http://example.com:1234'])
def test_executor_endpoint_must_match_owned_service(url):
    from crawler_harness.uc_service import endpoint_matches
    driver=SimpleNamespace(command_executor=SimpleNamespace(_client_config=SimpleNamespace(remote_server_addr=url)))
    assert endpoint_matches(driver,SimpleNamespace(port=1234,service_url='http://localhost:1234'))==(url=='http://localhost:1234')
