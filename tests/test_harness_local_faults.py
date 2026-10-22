"""A.2 deterministic offline faults: contained Python fake worker, never Chrome."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from crawler_harness.browser_commands import CommandFault
from crawler_harness.local_faults import TIMEOUT_SCRIPT,terminate_owned_worker,observe_timeout_request,durable_fault_state
from crawler_harness.containment import ContainmentError
from tests.test_harness_browser_commands import work,session
from tests.test_harness_local_browser import page


def test_worker_crash_is_refused_without_new_dispatch_and_no_receipt(work):
    root,store,_,_,token,_,_=work
    with session(work) as s:
        assert s.driver.current_url=='about:blank'
        count=s.commands
        identity=terminate_owned_worker(s)
        assert identity['pid']==s.owner.worker_identity.pid
        with pytest.raises(CommandFault):_ = s.driver.current_url
        assert s.commands==count and s.owner.api.active(s.owner.job)==0
    state=durable_fault_state(store.path,token.job_id)
    assert state['job_state']=='PAUSED_NEEDS_HUMAN' and state['receipt_count']==0
    assert state['intent_count']==count and state['unresolved_intents']==[] and state['refused_count']==1
    assert durable_fault_state(store.path,token.job_id)==state


def test_real_ipc_deadline_with_fake_driver_is_unknown_and_not_replayed(work):
    root,store,_,_,token,_,_=work
    with session(work,fixture_config={'control':str(root)}) as s:
        assert s.driver.current_url=='about:blank'
        s.command_timeout=.3
        before=s.commands
        with pytest.raises(CommandFault):s.driver.execute_script('wait')
        assert (root/'inflight').exists() and s.commands==before+1 and s.inflight
        assert s.owner.api.active(s.owner.job)==0
        records=[json.loads(p.read_text()) for p in (s.evidence/'commands').glob('*.json')]
        assert sum(r['outcome']=='UNKNOWN' for r in records)==1
    state=durable_fault_state(store.path,token.job_id)
    assert state['unresolved_intents']==[s.inflight] and state['unknown_count']==1
    assert state['job_state']=='PAUSED_NEEDS_HUMAN' and state['receipt_count']==0
    assert state['intent_count']==s.commands and state['attempt_count']==1


@pytest.mark.parametrize('fault',['identity','dead','job'])
def test_native_fault_target_must_be_exact_live_owned_handle(fault):
    from crawler_harness.containment import ProcessIdentity
    identity=ProcessIdentity(100,1234,2)
    def refuse(*a):raise AssertionError('TerminateProcess must not be reached')
    def member(*a):
        if fault=='job':raise ContainmentError('wrong Job')
    api=SimpleNamespace(k=SimpleNamespace(TerminateProcess=refuse),
        identity=lambda h:ProcessIdentity(100,9999,2) if fault=='identity' else identity,
        wait=lambda h:fault=='dead',member=member)
    owner=SimpleNamespace(api=api,process='retained-handle',worker_identity=identity,job='owned-job',
        session_id='synthetic',_owner=lambda *a:None)
    s=SimpleNamespace(owner=owner,token=SimpleNamespace(attempt_id='a'),_validate=lambda _:None)
    with pytest.raises(ContainmentError):terminate_owned_worker(s)


def test_timeout_is_one_fixed_script_with_no_dynamic_arguments(page):
    from crawler_harness.local_browser import permit
    permit(page,'execute_script',[TIMEOUT_SCRIPT])
    with pytest.raises(ContainmentError):permit(page,'execute_script',[TIMEOUT_SCRIPT,1])
    with pytest.raises(ContainmentError):permit(page,'execute_script',[TIMEOUT_SCRIPT.replace('6000','60000')])


def test_timeout_send_marker_and_resend_refusal(tmp_path,monkeypatch):
    from urllib3.connection import HTTPConnection
    calls=[]
    monkeypatch.setattr(HTTPConnection,'request',lambda *a,**kw:calls.append(True))
    driver=SimpleNamespace(session_id='synthetic',service=SimpleNamespace(port=1234))
    conn=SimpleNamespace(host='localhost',port=1234)
    with observe_timeout_request(driver,tmp_path,'abc'):
        HTTPConnection.request(conn,'POST','/session/synthetic/execute/sync')
        with pytest.raises(ContainmentError):HTTPConnection.request(conn,'POST','/session/synthetic/execute/sync')
    marker=json.loads((tmp_path/'driver-call-abc.json').read_text())
    assert marker['phase']=='HTTP_REQUEST_SENT' and marker['browser_outcome']=='UNOBSERVED'
    assert calls==[True] and 'body' not in marker


@pytest.mark.parametrize('host,port,method,url',[
    ('example.com',1234,'POST','/session/synthetic/execute/sync'),
    ('localhost',9999,'POST','/session/synthetic/execute/sync'),
    ('localhost',1234,'GET','/session/synthetic/execute/sync'),
    ('localhost',1234,'POST','/session/other/execute/sync')])
def test_timeout_observer_refuses_wrong_endpoint_before_send(tmp_path,monkeypatch,host,port,method,url):
    from urllib3.connection import HTTPConnection
    def refuse(*a,**kw):raise AssertionError('No transport call permitted')
    monkeypatch.setattr(HTTPConnection,'request',refuse)
    driver=SimpleNamespace(session_id='synthetic',service=SimpleNamespace(port=1234))
    with observe_timeout_request(driver,tmp_path,'abc'):
        with pytest.raises(ContainmentError):HTTPConnection.request(SimpleNamespace(host=host,port=port),method,url)
    assert not (tmp_path/'driver-call-abc.json').exists()
