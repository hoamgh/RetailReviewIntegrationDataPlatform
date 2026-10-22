"""Explicit local-page UC smoke, one contained session, no source collection."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from .browser_commands import ManagedBrowserSession, CommandFault
from .browser_profile import DedicatedProfile, atomic_json
from .local_browser import HTML, READ, INSTALL
from .local_faults import TIMEOUT_SCRIPT,terminate_owned_worker,durable_fault_state,resolve_terminal_session
from .state import JobStore
from experiments.tiktok_comment_dom.persistence import CommentPersistence


def chrome_identities():
    import psutil
    result = {}
    for process in psutil.process_iter(['name', 'create_time']):
        try:
            if process.info['name'].lower() in {'chrome.exe', 'chromedriver.exe', 'uc_driver.exe'}:
                result[process.pid] = process.info['create_time']
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            continue
    return result


def run(output, *, crash_worker=False, timeout_command=False, startup_timeout=60, terminal_resolution=False):
    if crash_worker and timeout_command:raise ValueError('One fault scenario per session')
    if terminal_resolution and (not crash_worker or timeout_command):
        raise ValueError('Terminal resolution requires exactly the worker-crash scenario')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'status':'FAIL', 'provider':'LOCAL_SELENIUMBASE_UC_V1',
              'source_collection_enabled':False, 'started_monotonic':time.monotonic()}
    before = chrome_identities()
    store = None
    session = None
    start = time.monotonic()
    try:
        page = output/'synthetic.html'
        page.write_bytes(HTML.encode('utf-8'))
        database = output/'local.sqlite3'
        repo = CommentPersistence(database, output/'lake')
        repo.close()
        store = JobStore(database)
        job = store.create_job(source='tiktok', kind='collection', place_id='synthetic-local',
            entity_id='synthetic-local', input_digest=hashlib.sha256(page.read_bytes()).hexdigest(),
            workflow='harness-local-smoke-v1')
        token = store.claim(job, 'local-smoke-controller', lease_seconds=120)
        profile = DedicatedProfile.create(output/'profiles'/'dedicated', database)
        session = ManagedBrowserSession(store, token, profile=profile, evidence=output/'browser',
            local_page=page, startup_timeout=startup_timeout,command_timeout=2 if timeout_command else 8)
        with session:
            report['startup_seconds'] = time.monotonic()-start
            session.driver.get(page.as_uri())
            assert session.driver.current_url == page.as_uri()
            assert session.driver.execute_script(READ) == 'before'
            assert session.driver.execute_script(INSTALL) is True
            button = session.driver.find_element('css selector', '#synthetic-button')
            assert button.is_displayed() and button.text == 'Synthetic button'
            button.click()
            assert session.driver.execute_script(READ) == 'clicked'
            session.driver.save_screenshot(str(output/'browser'/'synthetic.png'))
            report['dom_click'] = 'PASS'
            report['contained_inventory'] = session.owner.inventory()
            # Exercise the actual separate-connection heartbeat, bounded to 2s.
            deadline = time.monotonic()+2
            while not session.heartbeat.renewals and time.monotonic()<deadline:
                time.sleep(.02)
            assert session.heartbeat.renewals > 0
            if crash_worker:
                before_commands=session.commands
                report['terminated_worker']=terminate_owned_worker(session)
                try:
                    _ = session.driver.current_url
                except CommandFault:
                    report['worker_crash_guard'] = 'PASS'
                else:
                    raise AssertionError('Dead worker accepted a command')
                assert session.commands==before_commands and session.inflight is None
                if terminal_resolution:
                    report['terminal_resolution']=resolve_terminal_session(session,output/'lake')
                    atomic_json(output/'terminal-resolution.json',report['terminal_resolution'])
            if timeout_command:
                before_commands=session.commands
                fault_started=time.monotonic()
                try:session.driver.execute_script(TIMEOUT_SCRIPT)
                except CommandFault:pass
                else:raise AssertionError('Expected an unknown timed-out command')
                elapsed=time.monotonic()-fault_started
                assert session.commands==before_commands+1 and session.inflight
                marker=json.loads((session.evidence/('driver-call-'+session.inflight+'.json')).read_text())
                assert marker['command_id']==session.inflight and marker['phase']=='HTTP_REQUEST_SENT'
                assert marker['monotonic']<fault_started+session.command_timeout
                command=json.loads((session.evidence/'commands'/(session.inflight+'.json')).read_text())
                assert command['outcome']=='UNKNOWN' and elapsed>=session.command_timeout
                report['command_timeout']={'outcome':'UNKNOWN','command_id':session.inflight,
                    'deadline_seconds':session.command_timeout,'elapsed_with_cleanup_seconds':elapsed,
                    'transport_request_sent':True,'browser_final_outcome':'UNOBSERVED',
                    'redispatch_count':0}
        proof = session.owner._proof
        assert proof['worker_handle_signaled'] and proof['job_active_processes'] == 0
        report['quiescence'] = proof
        if terminal_resolution:assert proof['durable_resolution'] is True
        end = json.loads((output/'browser'/'session-end.json').read_text())
        assert end['heartbeat']['separate_thread_connection'] and not end['cleanup_error']
        assert end['local_retained_descendants_signaled'] is True
        report['retained_descendants_signaled']=end['local_retained_descendants_signaled']
        report['command_count']=end['command_count']
        report['service_lifecycle']=json.loads((output/'browser'/'real-startup.json').read_text())['service_lifecycle']
        report['heartbeat'] = end['heartbeat']
        assert json.loads((profile/'harness-profile.json').read_text())['active_session'] is None
        if store.get(job)['state']=='RUNNING':
            store.transition(token,'PAUSED_NEEDS_HUMAN',code='LOCAL_SMOKE_ONLY_NO_SOURCE_COVERAGE')
        report['job_state'] = store.get(job)['state']
        if crash_worker or timeout_command:
            durable=durable_fault_state(database,job)
            assert durable['job_state']=='PAUSED_NEEDS_HUMAN' and durable['receipt_count']==0
            assert durable['outbox_count']==0 and durable['attempt_count']==1
            assert durable['intent_count']==session.commands
            if timeout_command:
                assert durable['unknown_count']==1 and durable['unresolved_intents']==[session.inflight]
            else:
                assert durable['unknown_count']==0 and durable['unresolved_intents']==[] and durable['refused_count']==1
            assert durable_fault_state(database,job)==durable  # Reopen, no replay.
            report['durable_state']=durable
        report['status'] = 'PASS'
    except Exception as exc:
        report['error_class'] = type(exc).__name__
    finally:
        if store:store.close()
        after = chrome_identities()
        report['preexisting_browser_count'] = len(before)
        report['preexisting_browser_identities_unchanged'] = all(after.get(pid)==created for pid,created in before.items())
        if not report['preexisting_browser_identities_unchanged']:report['status']='FAIL'
        report['elapsed_seconds'] = time.monotonic()-start
        atomic_json(output/'summary.json',report)
    print(json.dumps(report,indent=2))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--crash-worker',action='store_true')
    parser.add_argument('--timeout-command',action='store_true')
    parser.add_argument('--startup-timeout',type=float,default=60)
    parser.add_argument('--terminal-resolution',action='store_true')
    args=parser.parse_args()
    raise SystemExit(run(args.output,crash_worker=args.crash_worker,timeout_command=args.timeout_command,startup_timeout=args.startup_timeout,terminal_resolution=args.terminal_resolution))
