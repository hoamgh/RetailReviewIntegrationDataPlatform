"""Managed command boundary. Actual driver objects stay in C2a child.

Cooperative Python API, not a security sandbox against reflective/local attackers.
Real provider is local-only; no source access, adoption, replay or release.
"""
from dataclasses import asdict
import json
from multiprocessing.connection import Client
from pathlib import Path
import sys
import threading
import time
import uuid

from .state import OwnershipError,StateError
from .containment import ManagedWorker,ContainmentError
from .containment import assert_workers_resolved
from .publication import JobScopedPublication,windows_mutex
from .browser_profile import DedicatedProfile,atomic_json
from .heartbeat import LeaseHeartbeat
from .browser_protocol import (send,receive,peer_pid,DRIVER_READ,DRIVER_CALL,
                               ELEMENT_READ,ELEMENT_CALL,CDP)


class CommandFault(OwnershipError):
    pass


class DriverFacade:
    __slots__=('_boundary',)
    def __init__(self,boundary):object.__setattr__(self,'_boundary',boundary)
    def __setattr__(self,name,value):return object.__getattribute__(self,'_boundary').deny()
    def __getattribute__(self,name):
        boundary=object.__getattribute__(self,'_boundary')
        if name=='_tiktok_browser_backend':return 'managed-local-uc' if boundary.local_config else 'managed-offline-fixture'
        if name in DRIVER_READ:return boundary.command(name,[])
        if name in DRIVER_CALL:return lambda *args:boundary.command(name,list(args))
        return boundary.deny()


class ElementFacade:
    __slots__=('_boundary','_element_id')
    def __init__(self,boundary,key):
        object.__setattr__(self,'_boundary',boundary);object.__setattr__(self,'_element_id',key)
    def __setattr__(self,name,value):return object.__getattribute__(self,'_boundary').deny()
    def __getattribute__(self,name):
        boundary=object.__getattribute__(self,'_boundary');key=object.__getattribute__(self,'_element_id')
        if name in ELEMENT_READ:return boundary.command(name,[],element=key)
        if name in ELEMENT_CALL:return lambda *args:boundary.command(name,list(args),element=key)
        return boundary.deny()


class ManagedBrowserSession:
    def __init__(self,store,token,*,profile,evidence,fixture_config=None,
                 heartbeat_interval=.5,lease_seconds=30,command_timeout=8,
                 local_page=None, startup_timeout=60):
        if type(command_timeout) not in (int,float) or not 0<command_timeout<=30:
            raise StateError('Bounded command timeout required')
        self.store,self.token=store,token
        self.profile_path=Path(profile).resolve();self.evidence=Path(evidence).resolve()
        self.fixture_config=fixture_config or {};self.command_timeout=command_timeout
        self.local_config=None
        self._local_processes=[]
        self._local_descendants_quiescent=None
        if local_page is not None:
            import hashlib
            from .local_browser import validate_config
            page=Path(local_page).resolve(strict=True)
            self.local_config={'page':str(page),'sha256':hashlib.sha256(page.read_bytes()).hexdigest()}
            validate_config(self.local_config)
            if fixture_config is not None:raise StateError('Local browser cannot use fixture configuration')
        if type(startup_timeout) not in (int,float) or not 0<startup_timeout<=120:
            raise StateError('Bounded startup timeout required')
        self.startup_timeout=startup_timeout
        self.heartbeat=LeaseHeartbeat(store,token,interval=heartbeat_interval,lease_seconds=lease_seconds)
        self.owner=ManagedWorker(store,token,fault=self._bootstrap)
        self.profile=None;self.conn=None;self.driver=None;self.state='NEW'
        self.browser_session=None;self.port=None;self.commands=0;self.inflight=None
        self._screenshot_roots={self.evidence}
        self._serial=threading.Lock();self._entered_owner=False;self._entered_profile=False
        self._created_evidence=False
        from .restrictions import installed, Restrictions
        self.restrictions=Restrictions(store) if installed(store) else None
        if self.local_config is not None and self.restrictions is not None:
            raise StateError('OFFLINE_FIXTURE registry cannot authorize a real browser')
        self._restriction_registered=False;self._restriction_failed=False
        self._access_observation={}
        self._access_observed_monotonic=0
        self._address='\\\\.\\pipe\\IRHarness-'+self.owner.session_id

    def _bootstrap(self,phase,owner):
        if phase=='created_suspended':
            atomic_json(self.evidence/'bootstrap.json',{'binding':owner.binding(),
                'profile_id':self.profile.manifest['profile_id'],'profile_path':str(self.profile_path),
                'restriction_database':str(self.store.path) if self.restrictions else None,
                'claim_owner':self.token.owner,
                'local_config':self.local_config,
                'authority_database':str(self.store.path),
                'fixture_config':self.fixture_config})

    def __enter__(self):
        try:
            if self.local_config is not None:
                with self.store._transaction(self.token):
                    row=self.store._owned(self.token)
                    if (json.loads(row['definition_json'])['workflow']!='harness-local-smoke-v1' or
                            row['input_digest']!=self.local_config['sha256']):
                        raise CommandFault('Dedicated synthetic workflow/input required')
            self.owner.__enter__();self._entered_owner=True
            if self.restrictions:
                self.restrictions.register(self.token,self.owner.session_id)
                self._restriction_registered=True
            with self.store._transaction(self.token):
                rows=self.store.db.execute("SELECT event_type,code FROM harness_events WHERE job_id=? AND event_type IN ('browser.command_intent','browser.command_succeeded')",(self.token.job_id,)).fetchall()
                succeeded={r['code'] for r in rows if r['event_type']=='browser.command_succeeded'}
                if any(r['event_type']=='browser.command_intent' and r['code'] not in succeeded for r in rows):
                    raise CommandFault('Unresolved browser command; human hold required')
            self.evidence.mkdir(parents=True,exist_ok=False)
            self._created_evidence=True
            (self.evidence/'commands').mkdir()
            self.profile=DedicatedProfile(self.profile_path,self.owner)
            self.profile.__enter__();self._entered_profile=True
            if self.local_config is not None:self.heartbeat.start()
            self.owner.start([sys.executable,'-B','-m','crawler_harness.browser_worker',
                '--local-uc' if self.local_config is not None else '--offline-fixture',
                self._address,str(self.evidence/'bootstrap.json')])
            deadline=time.monotonic()+(self.startup_timeout if self.local_config is not None else self.command_timeout)
            while not (self.evidence/'bootstrap.ready').exists():
                if self.heartbeat.lost.is_set() or self.owner.api.wait(self.owner.process) or time.monotonic()>deadline:
                    raise CommandFault('Worker initialization unavailable')
                time.sleep(.01)
            self.conn=Client(self._address,family='AF_PIPE',authkey=None)
            if peer_pid(self.conn,server=True)!=self.owner.worker_identity.pid:
                raise CommandFault('Wrong IPC server process')
            self.owner.api.member(self.owner.process,self.owner.job)
            if not self.conn.poll(self.command_timeout):raise CommandFault('Worker handshake timeout')
            hello=receive(self.conn)
            if hello['session_id']!=self.owner.session_id:raise CommandFault('Worker session mismatch')
            if self.local_config is not None:
                from .local_browser import MODE,retain_owned_processes
                if hello.get('provider')!=MODE:raise CommandFault('Wrong real provider')
                identities=self.owner.inventory()
                atomic_json(self.evidence/'real-handshake.json',{'owned_processes':hello.get('owned_processes'),
                    'inventory':identities})
                retain_owned_processes(self.owner,hello.get('owned_processes',[]))
                self._local_processes=hello['owned_processes']
                atomic_json(self.evidence/'real-startup.json',{'provider':MODE,
                    'owned_processes':hello['owned_processes'],'inventory':identities,
                    'service_lifecycle':hello['service_lifecycle'],
                    'job_id':self.token.job_id,'attempt_id':self.token.attempt_id,
                    'session_id':self.owner.session_id,'profile_id':self.profile.manifest['profile_id']})
            self.browser_session=hello['browser_session']
            if self.local_config is None:self.heartbeat.start()
            self.state='READY';self.driver=DriverFacade(self)
            return self
        except BaseException as exc:
            if self.local_config is not None and self._created_evidence:
                atomic_json(self.evidence/'startup-failure.json',{'error_class':type(exc).__name__,
                    'reason':str(exc) if isinstance(exc,(CommandFault,ContainmentError)) else 'STARTUP_FAILED'})
            self.close()
            raise

    def _validate(self,name):
        if self.state!='READY' or self.heartbeat.lost.is_set():raise CommandFault('Browser dispatch stopped')
        self.owner._owner(self.owner.session_id,self.token.attempt_id)
        self.owner.api.member(self.owner.process,self.owner.job)
        if self.local_config is not None:
            for expected in self._local_processes:
                handle,identity=self.owner._children[expected['pid']]
                if asdict(self.owner.api.identity(handle))!=expected or self.owner.api.wait(handle):
                    raise CommandFault('Pinned browser/service stopped or changed')
                self.owner.api.member(handle,self.owner.job)
        if self.owner.api.wait(self.owner.process):raise CommandFault('Worker stopped')
        self.profile.validate(self.profile.manifest['profile_id'],self.owner.session_id)
        row=self.store._owned(self.token)
        if self.restrictions:
            self._restriction_check()
        if row['state']!='RUNNING' and not (row['state']=='PERSISTING' and name=='save_screenshot'):
            raise CommandFault('Browser action not allowed in current job state')

    def _restriction_check(self,bound=None):
        from .restrictions import _session,assert_dispatch
        ledger=bound or self.store
        def check():
            row=ledger._owned(self.token)
            _session(ledger,self.token,self.owner.session_id)
            assert_dispatch(ledger,row['source'],self.owner.session_id)
        if ledger.db.in_transaction:check()
        else:
            with ledger._transaction(self.token):check()

    def source_gate(self,bound=None):
        if self.local_config is not None:raise CommandFault('Local smoke cannot write source data')
        # Called inside existing M3-B source transaction; no RPC/browser/SQL wait.
        if self.state!='READY' or self.heartbeat.lost.is_set():raise CommandFault('Source commit after unknown browser action refused')
        if self.restrictions:self._restriction_check(bound)

    def observe_access(self,snapshot,entries=()):
        if not self.restrictions:return
        try:
            evidence_id=uuid.uuid4().hex
            # Bounded structured diagnostics, no body/URLs/cookies/raw DOM.
            from .restrictions import REASONS,observation_reason
            reason=observation_reason(snapshot,entries)
            if reason not in REASONS:return
            atomic_json(self.evidence/('access-'+evidence_id+'.json'),{
                'source':'tiktok','session_id':self.owner.session_id,'job_id':self.token.job_id,
                'attempt_id':self.token.attempt_id,'generation':self.token.generation,
                'observed_at':self.store._now(),'classifier_reason':reason,
                'command_count':self.commands,
                'http_status_counts':{str(code):sum(e.get('status')==code for e in entries) for code in (403,429)}})
            return self.restrictions.observe(self.token,self.owner.session_id,snapshot,entries,
                evidence_ref='access:'+evidence_id)
        except BaseException:
            self._restriction_failed=True;self.state='HALTED'
            self.heartbeat.stop_event.set()
            raise

    def _event(self,kind,command_id,operation=None):
        with self.store._transaction(self.token):
            if operation is not None:self._validate(operation)
            row=self.store._owned(self.token)
            self.store._event(row,kind,row['state'],row['state'],command_id)

    def _observe_command_result(self,name,args,result):
        if not self.restrictions:return
        from experiments.tiktok_access_benchmark.session_comments import STATE_JS
        from experiments.tiktok_comment_dom.extractor import access_failure
        from experiments.tiktok_access_benchmark.access import dialog_kinds,automatic_interstitial
        if name=='execute_script' and args and args[0]=='return ('+STATE_JS+')();':
            if automatic_interstitial(result):
                self._access_observation={};self._access_observed_monotonic=0
                return
            self._access_observed_monotonic=time.monotonic()
            self._access_observation={
                'challenge_present':result.get('challenge'),
                'video_detail_rendered':result.get('video_detail'),
                'dialog_kinds':dialog_kinds(result.get('dialog_evidence',[]))}
            if result.get('access_denied'):self._access_observation['network_gate']='ACCESS_DENIED'
            if access_failure(self._access_observation):self.observe_access(self._access_observation)
        elif name=='get_log':
            from experiments.tiktok_access_benchmark.network import diagnostic,decision
            entries=[]
            for event in result:
                try:
                    message=json.loads(event['message'])['message']
                    params=message['params']
                    if message['method']=='Network.responseReceived':
                        entries.append(diagnostic(params['response'],params.get('type','Unknown'),'MANAGED_COMMAND'))
                except (KeyError,TypeError,ValueError):continue
            observed=self._access_observation if time.monotonic()-self._access_observed_monotonic <= 5 else {}
            if decision(entries,rendered=bool(observed.get('video_detail_rendered'))):
                self.observe_access(observed,entries)

    def _evidence(self,cid,name,outcome,start):
        atomic_json(self.evidence/'commands'/(cid+'.json'),{'command_id':cid,'job_id':self.token.job_id,
            'attempt_id':self.token.attempt_id,'generation':self.token.generation,
            'session_id':self.owner.session_id,'operation':name,'outcome':outcome,
            'elapsed_seconds':time.monotonic()-start})

    def _fail(self,cid,name,outcome,start):
        if self.restrictions and outcome=='UNKNOWN':
            try:
                latch=self.restrictions.latch('tiktok')
                if not latch or not latch['active']:
                    self.restrictions.activate(self.token,self.owner.session_id,'UNKNOWN',evidence_ref=cid)
            except Exception:self._restriction_failed=True
        self.state='HALTED'
        self.heartbeat.stop_event.set()
        try:
            if self._entered_owner and self.owner._created:
                self.owner.shutdown(session_id=self.owner.session_id,attempt_id=self.token.attempt_id)
                if self.local_config is not None:
                    stopped=all(self.owner.api.wait(handle,1000) for handle,_ in self.owner._children.values())
                    self.owner._proof['retained_descendants_signaled']=stopped
                    self._local_descendants_quiescent=stopped
                    if not stopped:raise ContainmentError('Owned browser/service handles not quiescent')
        finally:
            self._evidence(cid,name,outcome,start)
        try:self._event('browser.command_unknown' if outcome=='UNKNOWN' else 'browser.command_refused',cid)
        except Exception:pass  # Stale authority never clears/writes ledger facts.
        try:
            with self.store._transaction(self.token):
                row=self.store._owned(self.token)
                if row['state']=='RUNNING':self.store._transition_in_transaction(self.token,'PAUSED_NEEDS_HUMAN',code='BROWSER_COMMAND_UNKNOWN')
        except StateError:pass

    def deny(self):
        cid=uuid.uuid4().hex;start=time.monotonic()
        self._fail(cid,'UNAPPROVED_SURFACE','REFUSED',start)
        raise CommandFault('Driver surface not allowlisted')

    def command(self,name,args,*,element=None):
        if not self._serial.acquire(blocking=False):raise CommandFault('Concurrent/reentrant browser command refused')
        cid=uuid.uuid4().hex;start=time.monotonic();dispatched=False
        try:
            self._validate(name)
            if self.local_config is not None:
                from .local_browser import permit
                permit(self.local_config,name,args,element)
            allowed=(ELEMENT_READ|ELEMENT_CALL) if element else (DRIVER_READ|DRIVER_CALL)
            if name not in allowed or self.commands>=1000:raise CommandFault('Command not permitted')
            if name=='execute_cdp_cmd' and args[0] not in CDP:raise CommandFault('CDP command refused')
            if name=='get_log' and args!=['performance']:raise CommandFault('Log type refused')
            if name=='save_screenshot' and not any(Path(args[0]).resolve().is_relative_to(root) for root in self._screenshot_roots):
                raise CommandFault('Screenshot outside attempt evidence refused')
            request={'command_id':cid,'session_id':self.owner.session_id,'attempt_id':self.token.attempt_id,
                'name':name,'element':element,'args':args}
            json.dumps(request,allow_nan=False)  # No caller objects/callables cross IPC.
            self._event('browser.command_intent',cid,name)
            if name in {'get','click'}:
                self._access_observation={};self._access_observed_monotonic=0
            self.inflight=cid;self.commands+=1
            dispatched=True;send(self.conn,request)
            deadline=start+self.command_timeout
            while not self.conn.poll(.05):
                if self.heartbeat.lost.is_set() or self.owner.api.wait(self.owner.process) or time.monotonic()>deadline:
                    raise CommandFault('Command outcome unknown')
            response=receive(self.conn)
            if (not response.get('ok') or response.get('command_id')!=cid or
                    response.get('session_id')!=self.owner.session_id or
                    response.get('browser_session')!=self.browser_session):
                raise CommandFault('Command response/context unknown')
            if self.local_config is not None and response.get('owned_processes')!=self._local_processes:
                raise CommandFault('Command service ownership proof changed')
            self._validate(name)
            result=self._decode(response['result'])
            self._event('browser.command_succeeded',cid,name)
            self._evidence(cid,name,'SUCCEEDED',start);self.inflight=None
            self._observe_command_result(name,args,result)
            return result
        except BaseException:
            self._fail(cid,name,'UNKNOWN' if dispatched else 'REFUSED',start)
            raise CommandFault('Browser command stopped; no automatic replay') from None
        finally:self._serial.release()

    def _decode(self,node,depth=0):
        if depth>30:raise CommandFault('Result nesting exceeded')
        kind,value=node['type'],node['value']
        if kind=='scalar' and (value is None or type(value) in (str,int,float,bool)):return value
        if kind=='element' and type(value) is str:return ElementFacade(self,value)
        if kind=='list':return [self._decode(v,depth+1) for v in value]
        if kind=='dict':return {k:self._decode(v,depth+1) for k,v in value.items()}
        raise CommandFault('Unapproved result type')

    def run_collection(self,adapter,match_data,**options):
        if self.local_config is not None:raise CommandFault('Real collection disabled pending runtime restrictions review')
        if adapter.store is not self.store or adapter.lake is None:raise CommandFault('Adapter scope mismatch')
        if self.store.get(self.token.job_id)['state']=='PERSISTING':
            raise CommandFault('Sealed source receipt: close session and use storage-only recovery')
        self._screenshot_roots.add(adapter.evidence_root/self.token.job_id/self.token.attempt_id)
        from experiments.tiktok_comment_dom.probe import SeleniumDOMPort
        from experiments.tiktok_access_benchmark.ui_flow import NetworkCapture
        network=NetworkCapture(self.driver);network.start()
        boundary=self
        class ObservedPort(SeleniumDOMPort):
            def snapshot(port):
                snap=super().snapshot()
                boundary.observe_access(snap,network.entries)
                return snap
        self.port=ObservedPort(driver=self.driver,network=network)
        self.port.harness_offline_fixture=True
        result=adapter.execute(self.token,match_data,port=self.port,command_boundary=self,**options)
        self.source_gate()
        return result

    def recover_storage(self,lake):
        proof=self.owner._proof or {}
        if (self.state!='CLOSED' or proof.get('worker_handle_signaled') is not True or
                proof.get('job_active_processes')!=0 or proof.get('durable_resolution') is not True or
                proof.get('job_id')!=self.token.job_id or proof.get('attempt_id')!=self.token.attempt_id):
            raise CommandFault('Verified closed worker/session required for storage replay')
        with windows_mutex('Global\\IntegrationRecommendationWorkerV1_'+self.owner.database_id+'_tiktok'):
            with self.store._transaction():assert_workers_resolved(self.store,'tiktok')
            return JobScopedPublication(self.store,lake).recover(self.token.job_id,self.token,
                verify_quiescent=lambda row:row['job_id']==proof['job_id'],
                evidence_ref='C2B_CLOSED_TREE_AND_EXCLUSIVE_SOURCE')

    def validate_port(self,port,token):
        if token!=self.token or port is not self.port or port.driver is not self.driver or port.lifecycle is not None:
            raise CommandFault('Port/session/attempt mismatch')
        self._validate('current_url')

    def close(self):
        error=None
        try:
            self.heartbeat.close()
            if self._entered_owner:
                self.owner.shutdown(session_id=self.owner.session_id,attempt_id=self.token.attempt_id)
                if self.local_config is not None:
                    stopped=all(self.owner.api.wait(handle,1000) for handle,_ in self.owner._children.values())
                    self.owner._proof['retained_descendants_signaled']=stopped
                    self._local_descendants_quiescent=stopped
                    if not stopped:raise ContainmentError('Owned browser/service handles not quiescent')
                if self._restriction_registered and not self._restriction_failed and self.owner._created:
                    self.restrictions.close_verified(self.token,self.owner.session_id,self.owner)
                    self._restriction_registered=False
                if self._entered_profile:self.profile.release_after_quiescence()
        except Exception as exc:error=exc
        finally:
            self.state='CLOSED'
            if self.conn:self.conn.close();self.conn=None
            try:
                if self._entered_owner:self.owner.close();self._entered_owner=False
            finally:
                if self._entered_profile:self.profile.__exit__(None,None,None);self._entered_profile=False
                if self._created_evidence:
                    atomic_json(self.evidence/'session-end.json',{'session_id':self.owner.session_id,
                        'job_id':self.token.job_id,'attempt_id':self.token.attempt_id,
                        'worker_proof':self.owner._proof,'command_count':self.commands,
                        'unresolved_command_id':self.inflight,
                        'local_retained_descendants_signaled':self._local_descendants_quiescent,
                        'heartbeat':{'renewals':self.heartbeat.renewals,'authority_lost':self.heartbeat.lost.is_set(),
                            'separate_thread_connection':self.heartbeat.connection_thread is not None and
                                self.heartbeat.connection_thread!=threading.get_ident()},
                        'cleanup_error':type(error).__name__ if error else None})
        if error:raise error

    def __exit__(self,*_):self.close()
