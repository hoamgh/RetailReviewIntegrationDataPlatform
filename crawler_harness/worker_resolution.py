"""Explicit retained-controller cleanup authority, never a collection Claim.

No schema, launch, operator verification, restriction release or source replay.
The controller must still hold original C2a handles and its source lock.
"""
from contextlib import ExitStack
from dataclasses import asdict
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import threading
import os

from .containment import ManagedWorker,ContainmentError
from .publication import publisher_ownership,resource_id
from .state import OwnershipError,_identifier,_digest


class TerminalWorkerResolution:
    """One in-process cleanup capability scoped to exact original worker intent.

    actor/evidence_ref are audit labels, not credentials or proof. Authority
    comes from retained creation handles + original controller + native locks.
    """
    def __init__(self,worker,lake,*,actor,evidence_ref):
        if type(worker) is not ManagedWorker:raise ContainmentError('Original retained ManagedWorker required')
        self.worker=worker;self.store=worker.store;self.lake=Path(lake).resolve()
        self.actor=_identifier(actor);self.evidence_ref=_identifier(evidence_ref)
        self.active=False;self.stack=None

    def _native(self):
        w=self.worker
        w._owner(w.session_id,w.token.attempt_id)
        if w._source_guard is None:raise ContainmentError('Retained source mutex ownership required')
        w._source_guard()
        if (not w._created or not w._intent or w._creation_binding!=
                (w.job,w.process,w.worker_identity,w.controller_identity)):
            raise ContainmentError('Original creation binding not retained')
        w._limits()
        def verify(handle,identity):
            # GetProcessTimes/GetProcessId operate on the retained creation
            # handle even after exit; no PID->session lookup after process death.
            values=[wintypes.FILETIME() for _ in range(4)]
            w.api.require(w.api.k.GetProcessTimes(handle,*[ctypes.byref(v) for v in values]))
            created=(values[0].dwHighDateTime<<32)|values[0].dwLowDateTime
            if w.api.k.GetProcessId(handle)!=identity.pid or created!=identity.creation_filetime:
                raise ContainmentError('Retained native process creation identity mismatch')
            if not w.api.wait(handle):raise ContainmentError('Retained worker/descendant still active')
        verify(w.process,w.worker_identity)
        for handle,identity in w._children.values():verify(handle,identity)
        if w.api.active(w.job)!=0:raise ContainmentError('Owned Job not quiescent')
        return {**w.binding(),'worker_handle_signaled':True,'job_active_processes':0,
            'retained_descendants':[asdict(i) for _,i in w._children.values()],
            'source_lock_held':True,'scope':'RETAINED_CONTROLLER_TERMINAL_CLEANUP_ONLY'}

    def __enter__(self):
        if self.stack is not None:raise ContainmentError('Cleanup authority cannot be reused')
        self._native()
        self.stack=ExitStack()
        try:
            self.publisher_guard=self.stack.enter_context(publisher_ownership(self.store.path,self.lake))
            self.controller=(os.getpid(),threading.get_ident())
            self.database_id=resource_id(self.store.path,'database')
            self.active=True
            return self
        except BaseException:
            self.stack.close();raise

    def resolve(self,*,job_id,attempt_id,generation,session_id):
        if not self.active or self.controller!=(os.getpid(),threading.get_ident()):
            raise OwnershipError('Exclusive cleanup authority not active on original controller')
        w=self.worker;t=w.token
        if (job_id,attempt_id,generation,session_id)!=(t.job_id,t.attempt_id,t.generation,w.session_id):
            raise OwnershipError('Resolution scope differs from original intent')
        if resource_id(self.store.path,'database')!=self.database_id:
            raise OwnershipError('Cleanup database identity changed')
        with self.store._transaction():
            self.publisher_guard();proof=self._native()
            row=self.store.get(job_id)
            if ((row['owner'],row['attempt_id'],row['generation'])!=(t.owner,attempt_id,generation) or
                    row['source']!='tiktok' or row['kind']!='collection' or
                    row['state'] not in {'PAUSED_NEEDS_HUMAN','ACCESS_RESTRICTED','FAILED'}):
                raise OwnershipError('Only the exact ended collection attempt may be resolved')
            attempt=self.store.db.execute('SELECT * FROM harness_attempts WHERE attempt_id=?',(attempt_id,)).fetchone()
            if (attempt is None or (attempt['job_id'],attempt['generation'],attempt['owner'])!=(job_id,generation,t.owner)
                    or attempt['ended_at'] is None):
                raise OwnershipError('Ended attempt provenance mismatch')
            params=(job_id,attempt_id,generation,session_id)
            intents=self.store.db.execute("SELECT 1 FROM harness_events WHERE job_id=? AND attempt_id=? AND generation=? AND code=? AND event_type='worker.launch_intent'",params).fetchall()
            if len(intents)!=1:raise ContainmentError('Exact single launch intent required')
            # Never lock an arbitrary lake in place of a sealed publication's lake.
            for checkpoint in self.store.db.execute('SELECT state_json FROM tiktok_comment_checkpoints WHERE place_id=? AND video_id=? AND run_id=?',
                    (row['place_id'],row['entity_id'],row['data_run_id'])):
                manifest=json.loads(checkpoint[0]).get('managed_publication_manifest')
                if manifest and Path(manifest['lake_root']).resolve()!=self.lake:
                    raise ContainmentError('Publication lake mismatch')
            prior=self.store.db.execute("SELECT code FROM harness_events WHERE job_id=? AND attempt_id=? AND generation=? AND event_type='worker.terminal_resolution'",(job_id,attempt_id,generation)).fetchall()
            quiescent=self.store.db.execute("SELECT 1 FROM harness_events WHERE job_id=? AND attempt_id=? AND generation=? AND code=? AND event_type='worker.quiescent'",params).fetchall()
            if prior:
                if len(prior)!=1 or not quiescent:raise ContainmentError('Inconsistent resolution history')
                audit=json.loads(prior[0][0])
                if (audit.get('authority')!='RETAINED_CONTROLLER_CLEANUP_V1' or
                        audit.get('proof_digest')!=_digest(proof) or
                        _digest(audit.get('native_proof'))!=audit['proof_digest'] or
                        audit.get('database_id')!=self.database_id or
                        audit.get('lake_id')!=resource_id(self.lake,'lake')):
                    raise ContainmentError('Resolution native proof/resource binding changed')
                return {'resolved':True,'already_resolved':True,'proof':proof}
            audit={'authority':'RETAINED_CONTROLLER_CLEANUP_V1','actor':self.actor,
                'evidence_ref':self.evidence_ref,'native_proof':proof,'proof_digest':_digest(proof),
                'database_id':self.database_id,'lake_id':resource_id(self.lake,'lake')}
            self.store._event(row,'worker.terminal_resolution',row['state'],row['state'],json.dumps(audit,sort_keys=True))
            if not quiescent:self.store._event(row,'worker.quiescent',row['state'],row['state'],session_id)
            self.publisher_guard();self._native()
        return {'resolved':True,'already_resolved':False,'proof':proof}

    def __exit__(self,*args):
        self.active=False
        if self.stack:self.stack.close()
