"""Windows managed-only Python worker containment, no browser or retry loop.

Atomic Job Object membership at process creation; no adopt, PID kill, breakaway
or suspended-create/AssignProcessToJobObject fallback. Cooperative control plane,
not an OS security sandbox. Unresolved launch intents require human review.
"""
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
from dataclasses import asdict, dataclass
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from .state import StateError, OwnershipError
from .publication import resource_id, windows_mutex


class ContainmentError(StateError):
    pass


class BasicLimits(c.Structure):
    _fields_ = [('process_time',c.c_int64),('job_time',c.c_int64),('flags',w.DWORD),
        ('minimum',c.c_size_t),('maximum',c.c_size_t),('active',w.DWORD),
        ('affinity',c.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]


class ExtendedLimits(c.Structure):
    _fields_ = [('basic',BasicLimits),('io',c.c_uint64*6),('memory',c.c_size_t*4)]


class Accounting(c.Structure):
    _fields_ = [('times',c.c_int64*4),('faults',w.DWORD),('total',w.DWORD),
                ('active',w.DWORD),('terminated',w.DWORD)]


class Startup(c.Structure):
    _fields_ = [('cb',w.DWORD),('reserved',w.LPWSTR),('desktop',w.LPWSTR),
        ('title',w.LPWSTR),('x',w.DWORD),('y',w.DWORD),('width',w.DWORD),
        ('height',w.DWORD),('xchars',w.DWORD),('ychars',w.DWORD),('fill',w.DWORD),
        ('flags',w.DWORD),('show',w.WORD),('reserved_size',w.WORD),
        ('reserved_bytes',c.c_void_p),('stdin',w.HANDLE),('stdout',w.HANDLE),('stderr',w.HANDLE)]


class StartupEx(c.Structure):
    _fields_ = [('startup',Startup),('attributes',c.c_void_p)]


class ProcessInfo(c.Structure):
    _fields_ = [('process',w.HANDLE),('thread',w.HANDLE),('pid',w.DWORD),('tid',w.DWORD)]


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    creation_filetime: int
    windows_session_id: int


class WindowsAPI:
    def __init__(self):
        if os.name != 'nt':
            raise ContainmentError('Windows 10+ Job Object creation support required')
        k = self.k = c.WinDLL('kernel32',use_last_error=True)
        p = c.c_void_p
        definitions = [
            ('CreateJobObjectW',[p,w.LPCWSTR],w.HANDLE),
            ('SetInformationJobObject',[w.HANDLE,c.c_int,p,w.DWORD],w.BOOL),
            ('QueryInformationJobObject',[w.HANDLE,c.c_int,p,w.DWORD,p],w.BOOL),
            ('InitializeProcThreadAttributeList',[p,w.DWORD,w.DWORD,c.POINTER(c.c_size_t)],w.BOOL),
            ('UpdateProcThreadAttribute',[p,w.DWORD,c.c_size_t,p,c.c_size_t,p,p],w.BOOL),
            ('DeleteProcThreadAttributeList',[p],None),
            ('CreateProcessW',[w.LPCWSTR,w.LPWSTR,p,p,w.BOOL,w.DWORD,p,w.LPCWSTR,p,c.POINTER(ProcessInfo)],w.BOOL),
            ('IsProcessInJob',[w.HANDLE,w.HANDLE,c.POINTER(w.BOOL)],w.BOOL),
            ('GetProcessTimes',[w.HANDLE,*([c.POINTER(w.FILETIME)]*4)],w.BOOL),
            ('ProcessIdToSessionId',[w.DWORD,c.POINTER(w.DWORD)],w.BOOL),
            ('GetProcessId',[w.HANDLE],w.DWORD),
            ('GetHandleInformation',[w.HANDLE,c.POINTER(w.DWORD)],w.BOOL),
            ('OpenProcess',[w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
            ('WaitForSingleObject',[w.HANDLE,w.DWORD],w.DWORD),
            ('GetExitCodeProcess',[w.HANDLE,c.POINTER(w.DWORD)],w.BOOL),
            ('TerminateJobObject',[w.HANDLE,w.UINT],w.BOOL),
            ('ResumeThread',[w.HANDLE],w.DWORD),
            ('CloseHandle',[w.HANDLE],w.BOOL),
        ]
        for name,args,result in definitions:
            f=getattr(k,name);f.argtypes=args;f.restype=result

    @staticmethod
    def require(ok):
        if not ok:
            raise ContainmentError('Windows containment API failure: '+str(c.get_last_error()))

    def wait(self,handle,ms=0):
        value=self.k.WaitForSingleObject(handle,ms)
        if value not in (0,0x102):
            raise ContainmentError('Process handle wait failed')
        return value==0

    def identity(self,handle):
        values=[w.FILETIME() for _ in range(4)]
        self.require(self.k.GetProcessTimes(handle,*[c.byref(v) for v in values]))
        pid=self.k.GetProcessId(handle);self.require(pid)
        sid=w.DWORD();self.require(self.k.ProcessIdToSessionId(pid,c.byref(sid)))
        return ProcessIdentity(pid,(values[0].dwHighDateTime<<32)|values[0].dwLowDateTime,sid.value)

    def noninherited(self,handle):
        flags=w.DWORD();self.require(self.k.GetHandleInformation(handle,c.byref(flags)))
        if flags.value & 1:raise ContainmentError('Inheritable ownership handle refused')

    def member(self,process,job):
        yes=w.BOOL();self.require(self.k.IsProcessInJob(process,job,c.byref(yes)))
        if not yes.value:raise ContainmentError('Process not in owned Job Object')

    def active(self,job):
        value=Accounting()
        self.require(self.k.QueryInformationJobObject(job,1,c.byref(value),c.sizeof(value),None))
        return value.active

    def pids(self,job):
        capacity=16
        for _ in range(9):
            buf=c.create_string_buffer(8+capacity*c.sizeof(c.c_size_t))
            ok=self.k.QueryInformationJobObject(job,3,buf,c.sizeof(buf),None)
            if not ok and c.get_last_error()!=234:self.require(ok)
            assigned,listed=(w.DWORD*2).from_buffer(buf)
            if ok and assigned==listed:
                return list((c.c_size_t*listed).from_buffer(buf,8))
            capacity=max(capacity*2,assigned)
        raise ContainmentError('Descendant inventory exceeds bounded query')

    @contextmanager
    def attributes(self,job):
        size=c.c_size_t()
        self.k.InitializeProcThreadAttributeList(None,1,0,c.byref(size))
        if not size.value:raise ContainmentError('Job-list attributes unavailable')
        storage=c.create_string_buffer(size.value)
        self.require(self.k.InitializeProcThreadAttributeList(storage,1,0,c.byref(size)))
        try:
            jobs=(w.HANDLE*1)(job)
            self.require(self.k.UpdateProcThreadAttribute(storage,0,0x2000D,
                c.cast(jobs,c.c_void_p),c.sizeof(jobs),None,None))
            yield c.cast(storage,c.c_void_p)
        finally:self.k.DeleteProcThreadAttributeList(storage)

    def create(self,job,argv,cwd):
        info=ProcessInfo()
        with self.attributes(job) as attrs:
            startup=StartupEx();startup.startup.cb=c.sizeof(startup);startup.attributes=attrs
            command=c.create_unicode_buffer(subprocess.list2cmdline(argv))
            # CREATE_SUSPENDED is for pre-execution verification only. Membership
            # is atomic via JOB_LIST, never a later AssignProcessToJobObject.
            self.require(self.k.CreateProcessW(argv[0],command,None,None,False,
                0x80000|0x08000000|4,None,str(cwd),c.byref(startup),c.byref(info)))
        return info


def assert_workers_resolved(store,source):
    """Existing durable process facts, reusable by storage-only ownership checks."""
    rows=store.db.execute('''SELECT job_id,attempt_id,generation,event_type,code FROM harness_events
        WHERE source=? AND event_type IN ('worker.launch_intent','worker.quiescent','worker.launch_aborted')''',
        (source,)).fetchall()
    sealed={(r['job_id'],r['attempt_id'],r['generation'],r['code']) for r in rows
            if r['event_type']!='worker.launch_intent'}
    intents=[r for r in rows if r['event_type']=='worker.launch_intent']
    if any((r['job_id'],r['attempt_id'],r['generation'],r['code']) not in sealed for r in intents):
        raise ContainmentError('Unresolved worker ownership; human review required')
    return intents


class ManagedWorker:
    """One Python worker per attempt, one cooperating owner per DB/source.

    Owns kernel handles on the creating supervisor thread. No browser adoption,
    heartbeat, claim/takeover, restriction release, collection retry or C1 replay.
    Existing history launch-intent anchors make uncertain restarts fail-closed.
    """
    def __init__(self,store,token,*,fault=None):
        self.store,self.token,self.fault=store,token,fault
        self.api=WindowsAPI()
        self.session_id=uuid.uuid4().hex
        self.controller_pid=os.getpid();self.controller_thread=threading.get_ident()
        self.database_id=resource_id(store.path,'database')
        self.job=None;self.process=None;self.thread=None;self.controller=None
        self.worker_identity=None;self.controller_identity=None
        self._lock=None;self._entered=False;self._intent=False;self._finished=False
        self._created=False;self._children={};self._proof=None
        self._source_guard=None;self._creation_binding=None

    def __enter__(self):
        if self._entered or self._finished:raise ContainmentError('Owner context cannot be reused')
        self._lock=windows_mutex('Global\\IntegrationRecommendationWorkerV1_'+self.database_id+'_tiktok')
        self._source_guard=self._lock.__enter__()
        try:
            with self.store._transaction(self.token):
                job=self.store._owned(self.token)
                if (job['source'],job['kind'],job['state'])!=('tiktok','collection','RUNNING'):
                    raise OwnershipError('RUNNING TikTok collection claim required')
                self._eligible(job)
            self.controller=self.api.k.OpenProcess(0x100000|0x1000,False,os.getpid())
            self.api.require(self.controller)
            self.controller_identity=self.api.identity(self.controller)
            self.api.noninherited(self.controller)
            self.job=self.api.k.CreateJobObjectW(None,None);self.api.require(self.job)
            self.api.noninherited(self.job)
            limits=ExtendedLimits();limits.basic.flags=0x2000
            self.api.require(self.api.k.SetInformationJobObject(self.job,9,c.byref(limits),c.sizeof(limits)))
            self._limits()
            self._entered=True
            return self
        except BaseException:
            self._dispose()
            raise

    def _eligible(self,job):
        from .restrictions import assert_dispatch
        assert_dispatch(self.store,job['source'],self.session_id)
        if (job['source'],job['kind'],job['state'])!=('tiktok','collection','RUNNING'):
            raise OwnershipError('RUNNING TikTok collection claim required')
        intents=assert_workers_resolved(self.store,job['source'])
        if any(r['attempt_id']==self.token.attempt_id for r in intents):
            raise ContainmentError('Attempt already launched; no implicit worker retry')

    def _owner(self,session_id=None,attempt_id=None):
        if not self._entered or self._finished or os.getpid()!=self.controller_pid or threading.get_ident()!=self.controller_thread:
            raise OwnershipError('Wrong supervisor process/thread or closed owner')
        if session_id is not None and session_id!=self.session_id or attempt_id is not None and attempt_id!=self.token.attempt_id:
            raise OwnershipError('Worker session/attempt mismatch')
        if self.database_id!=resource_id(self.store.path,'database'):
            raise OwnershipError('Database resource changed')
        if self.api.identity(self.controller)!=self.controller_identity:
            raise OwnershipError('Supervisor creation identity changed')

    def _limits(self):
        limits=ExtendedLimits()
        self.api.require(self.api.k.QueryInformationJobObject(self.job,9,c.byref(limits),c.sizeof(limits),None))
        if limits.basic.flags!=0x2000:raise ContainmentError('Unsafe Job Object limits')

    def start(self,argv,*,cwd=None):
        self._owner()
        if self._intent:raise ContainmentError('Only one launch allowed')
        if not isinstance(argv,(list,tuple)) or not argv or any(not isinstance(a,str) or '\0' in a for a in argv):
            raise ContainmentError('Explicit Python argv required; no shell')
        if Path(argv[0]).resolve()!=Path(sys.executable).resolve():
            raise ContainmentError('Managed launcher accepts this Python executable only')
        argv=[str(Path(sys.executable).resolve()),*argv[1:]]
        cwd=Path(cwd or Path.cwd()).resolve()
        if not cwd.is_dir():raise ContainmentError('Existing working directory required')
        with self.store._transaction(self.token):
            job=self.store._owned(self.token);self._eligible(job)
            self.store._event(job,'worker.launch_intent',job['state'],job['state'],self.session_id)
        self._intent=True
        if self.fault:self.fault('intent_committed',self)
        try:
            with self.store._transaction(self.token):
                if self.store._owned(self.token)['state']!='RUNNING':
                    raise OwnershipError('Source already sealed; worker launch refused')
                self._limits()
                info=self.api.create(self.job,argv,cwd)
                self.process,self.thread=info.process,info.thread
                self._created=True
                self.api.noninherited(self.process);self.api.noninherited(self.thread)
                self.api.member(self.process,self.job)
                self.worker_identity=self.api.identity(self.process)
                self._creation_binding=(self.job,self.process,self.worker_identity,self.controller_identity)
                if self.fault:self.fault('created_suspended',self)
                if self.store._owned(self.token)['state']!='RUNNING':
                    raise OwnershipError('Source already sealed; worker resume refused')
                if self.api.k.ResumeThread(self.thread)!=1:
                    raise ContainmentError('Worker resume did not match suspended creation')
            if self.fault:self.fault('resumed',self)
        except BaseException:
            # Kernel-owned tree only. No PID lookup/termination fallback.
            self.shutdown(session_id=self.session_id,attempt_id=self.token.attempt_id)
            raise
        finally:
            if self.thread:self.api.k.CloseHandle(self.thread);self.thread=None
        return self.binding()

    def binding(self):
        self._owner()
        return {'session_id':self.session_id,'job_id':self.token.job_id,
            'attempt_id':self.token.attempt_id,'generation':self.token.generation,
            'database_id':self.database_id,'controller':asdict(self.controller_identity),
            'worker':asdict(self.worker_identity) if self.worker_identity else None}

    def inventory(self):
        self._owner();self._limits()
        for pid in self.api.pids(self.job):
            if pid in self._children:continue
            handle=self.api.k.OpenProcess(0x100000|0x1000,False,pid)
            if not handle:
                # A snapshot can race process exit; never infer quiescence here.
                raise ContainmentError('Descendant inventory changed; retry observation or hold')
            try:
                self.api.member(handle,self.job)
                identity=self.api.identity(handle)
                self._children[pid]=(handle,identity);handle=None
            finally:
                if handle:self.api.k.CloseHandle(handle)
        return [asdict(identity) for _,identity in self._children.values()]

    def quiescence(self):
        self._owner();self._limits()
        if not self._created or not self.api.wait(self.process) or self.api.active(self.job)!=0:
            raise ContainmentError('Worker/tree not proven quiescent')
        return {**self.binding(),'worker_handle_signaled':True,'job_active_processes':0,
                'source_lock_held':True,'scope':'OWNED_PROCESS_TREE_ONLY'}

    def shutdown(self,*,session_id,attempt_id,timeout=5):
        self._owner(session_id,attempt_id);self._limits()
        if type(timeout) not in (float,int) or not math.isfinite(timeout) or not 0<timeout<=30:
            raise ContainmentError('Finite shutdown deadline 0..30s required')
        if self.api.active(self.job):
            self.api.require(self.api.k.TerminateJobObject(self.job,83))
        deadline=time.monotonic()+timeout
        # Bounded native observer; no source/network retry or unbounded babysitting.
        while self.api.active(self.job):
            if time.monotonic()>=deadline:raise ContainmentError('Tree shutdown unverified; human hold')
            time.sleep(.01)
        if self._created:
            if not self.api.wait(self.process,max(0,int((deadline-time.monotonic())*1000))):
                raise ContainmentError('Worker exit unverified')
            self._proof=self.quiescence()
        else:
            self._proof={**self.binding(),'process_creation_not_completed':True,'job_active_processes':0}
        return dict(self._proof)

    def close(self):
        if self._finished:return self._proof
        self._owner()
        try:
            proof=self.shutdown(session_id=self.session_id,attempt_id=self.token.attempt_id)
            if self._intent:
                try:
                    with self.store._transaction(self.token):
                        job=self.store._owned(self.token)
                        event='worker.quiescent' if self._created else 'worker.launch_aborted'
                        self.store._event(job,event,job['state'],job['state'],self.session_id)
                    proof['durable_resolution']=True
                except OwnershipError:
                    # Cleanup of retained owned handles is safe without a live
                    # lease. Stale authority may NOT clear durable launch intent.
                    # An explicit reviewed cleanup operation may already have
                    # resolved this exact historical intent without a Claim.
                    resolved=self.store.db.execute("""SELECT 1 FROM harness_events
                        WHERE job_id=? AND attempt_id=? AND generation=?
                        AND event_type='worker.quiescent' AND code=?""",
                        (self.token.job_id,self.token.attempt_id,self.token.generation,self.session_id)).fetchone()
                    proof['durable_resolution']=resolved is not None
            self._proof=proof
            return dict(proof)
        finally:self._dispose()

    def _dispose(self):
        # Closing the sole noninheritable job handle is kill-on-close as a final
        # fail-safe, not a claim that asynchronous termination has been verified.
        for h,_ in self._children.values():self.api.k.CloseHandle(h)
        self._children.clear()
        for name in ('thread','process','job','controller'):
            h=getattr(self,name,None)
            if h:self.api.k.CloseHandle(h);setattr(self,name,None)
        if self._lock:
            self._lock.__exit__(None,None,None);self._lock=None
        self._source_guard=None
        self._finished=True

    def __exit__(self,*_):
        self.close()
