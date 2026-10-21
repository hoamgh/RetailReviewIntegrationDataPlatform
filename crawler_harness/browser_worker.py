"""Contained offline driver host. No SeleniumBase/browser creation implementation.

Only the reviewed tests/fixtures_harness_browser.py provider is loaded. JSON IPC
has one verified controller peer and explicit operations; no pickle or eval.
"""
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
from multiprocessing.connection import Listener
import sys
import uuid

from .browser_protocol import (send,receive,peer_pid,DRIVER_READ,DRIVER_CALL,
                               ELEMENT_READ,ELEMENT_CALL,CDP)
from .browser_profile import atomic_json
from .containment import WindowsAPI,ContainmentError


def serve(address,bootstrap_file):
    config=json.loads(Path(bootstrap_file).read_text())
    binding=config['binding'];api=WindowsAPI()
    self_handle=api.k.OpenProcess(0x100000|0x1000,False,os.getpid());api.require(self_handle)
    try:
        if asdict(api.identity(self_handle))!=binding['worker']:
            raise ContainmentError('Worker creation identity mismatch')
        # Parent proves membership against its private Job handle; it is never
        # inherited by the child (which would defeat kill-on-supervisor-close).
        api.member(self_handle,None)
        fixture=Path(__file__).resolve().parents[1]/'tests'/'fixtures_harness_browser.py'
        spec=importlib.util.spec_from_file_location('offline_browser_fixture',fixture)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        driver=module.FakeDriver(dict(config.get('fixture_config',{}),_profile_path=config['profile_path']))
        browser_session=driver.session_id;elements={};seen=set()
        restrictions=None;ledger=None
        if config.get('restriction_database'):
            from .state import JobStore,Claim
            from .restrictions import Restrictions
            ledger=JobStore(config['restriction_database'])
            restrictions=Restrictions(ledger)
            token=Claim(binding['job_id'],config['claim_owner'],binding['generation'],binding['attempt_id'])
        def profile_valid():
            marker=json.loads((Path(config['profile_path'])/'harness-profile.json').read_text())
            if (Path(driver.profile_dir).resolve()!=Path(config['profile_path']).resolve() or
                    marker.get('profile_id')!=config['profile_id'] or
                    marker.get('active_session')!=binding['session_id'] or
                    marker.get('database')!=binding['database_id']):
                raise ContainmentError('Worker profile/session changed')
        profile_valid()
        def encode(value,depth=0):
            if depth>30:raise ContainmentError('Result nesting exceeded')
            if isinstance(value,module.FakeElement):
                key=uuid.uuid4().hex;elements[key]=value
                return {'type':'element','value':key}
            if value is None or type(value) in (str,int,float,bool):return {'type':'scalar','value':value}
            if type(value) in (list,tuple):return {'type':'list','value':[encode(v,depth+1) for v in value]}
            if type(value) is dict:return {'type':'dict','value':{k:encode(v,depth+1) for k,v in value.items()}}
            raise ContainmentError('Unapproved remote result object')
        with Listener(address,family='AF_PIPE',authkey=None) as listener:
            atomic_json(Path(bootstrap_file).with_suffix('.ready'),{'ready':True})
            with listener.accept() as conn:
                pid=peer_pid(conn,server=False)
                controller=api.k.OpenProcess(0x100000|0x1000,False,pid);api.require(controller)
                try:
                    if asdict(api.identity(controller))!=binding['controller']:
                        raise ContainmentError('IPC controller identity mismatch')
                    send(conn,{'session_id':binding['session_id'],'browser_session':browser_session})
                    while not api.wait(controller):
                        request=receive(conn)
                        cid=request['command_id'];name=request['name'];target=request.get('element')
                        if (cid in seen or request['session_id']!=binding['session_id'] or
                                request['attempt_id']!=binding['attempt_id']):
                            raise ContainmentError('Duplicate command or session mismatch')
                        seen.add(cid)
                        if len(seen)>1000:raise ContainmentError('Command budget exceeded')
                        allowed=(ELEMENT_READ|ELEMENT_CALL) if target else (DRIVER_READ|DRIVER_CALL)
                        if name not in allowed:raise ContainmentError('Operation not allowlisted')
                        if name=='get_log' and request['args']!=['performance']:raise ContainmentError('Log type refused')
                        if name=='execute_cdp_cmd' and request['args'][0] not in CDP:raise ContainmentError('CDP operation refused')
                        if driver.session_id!=browser_session:raise ContainmentError('Browser session changed')
                        profile_valid()
                        if restrictions:restrictions.admit(token,binding['session_id'],cid)
                        receiver=elements[target] if target else driver
                        try:
                            value=getattr(receiver,name)
                            result=value if name in (ELEMENT_READ if target else DRIVER_READ) else value(*request['args'])
                            if driver.session_id!=browser_session:raise ContainmentError('Browser session changed')
                            profile_valid()
                            send(conn,{'command_id':cid,'session_id':binding['session_id'],
                                       'browser_session':browser_session,'ok':True,'result':encode(result)})
                        except Exception:
                            send(conn,{'command_id':cid,'ok':False,'error':'UNKNOWN_DRIVER_OUTCOME'})
                            return  # No retry/replay after an ambiguous result.
                finally:api.k.CloseHandle(controller)
    finally:api.k.CloseHandle(self_handle)


if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1]!='--offline-fixture':raise SystemExit('Offline fixture host only')
    serve(sys.argv[2],sys.argv[3])
