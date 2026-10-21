"""M3-C1 offline job-scoped publication using the existing TikTok flush writer.

Windows global mutexes exclude cooperating publishers by physical DB/lake IDs.
No browser/process supervision, implicit takeover, collection or network calls.
"""
from contextlib import contextmanager, ExitStack
import ctypes
import json
import os
from pathlib import Path
import threading
import sqlite3

from .state import OwnershipError, StateError, _identifier
from .recovery import OfflineRecovery, IntegrityError, verify
from .event_scope import indexed_events, sealed_manifest
from experiments.tiktok_comment_dom.persistence import CommentPersistence, digest


class PublisherBusy(StateError):
    pass


_local_lock = threading.Lock()
_held_names = set()


def resource_id(path, kind):
    stat = Path(path).resolve().stat()
    if not stat.st_ino:
        raise StateError('Filesystem does not expose a stable resource identity')
    return digest([kind,stat.st_dev,stat.st_ino])


@contextmanager
def windows_mutex(name):
    if os.name != 'nt':
        raise StateError('M3-C1 publisher ownership requires Windows')
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    with _local_lock:
        if name in _held_names:
            raise PublisherBusy('Publisher resource already owned')
        _held_names.add(name)
    handle = None
    acquired = False
    try:
        handle = kernel.CreateMutexW(None,False,name)
        if not handle:
            raise OSError(ctypes.get_last_error(),'Publisher mutex unavailable')
        status = kernel.WaitForSingleObject(handle,0)
        if status == 0x102:
            raise PublisherBusy('Publisher resource already owned')
        if status not in (0,0x80):  # WAIT_OBJECT_0 or WAIT_ABANDONED
            raise OSError(ctypes.get_last_error(),'Publisher mutex wait failed')
        acquired = True
        yield
    finally:
        if handle:
            if acquired:kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
        with _local_lock:
            _held_names.discard(name)


@contextmanager
def publisher_ownership(database, lake):
    # Separate resource locks also exclude different DBs targeting the same lake,
    # or one DB being published to two lakes. Physical IDs handle path aliases.
    database, lake = Path(database).resolve(),Path(lake).resolve()
    lake.mkdir(parents=True,exist_ok=True)
    identities = (resource_id(database,'database'),resource_id(lake,'lake'))
    names = sorted('Global\\IntegrationRecommendationPublisherV1_'+i for i in identities)
    with ExitStack() as stack:
        for name in names:stack.enter_context(windows_mutex(name))
        def unchanged():
            if identities != (resource_id(database,'database'),resource_id(lake,'lake')):
                raise IntegrityError('PUBLISHER_RESOURCE_CHANGED')
        unchanged()
        yield unchanged


class JobScopedPublication(OfflineRecovery):
    """One bounded step; explicit current Claim + quiescence, no takeover.

    Reuses M2 policy/backoff/attempt tables and result recording. Calling the
    constructor explicitly enables the existing M2 extension if absent; no new
    schema, implicit metadata backfill or source/Parquet migration is introduced.
    """
    def __init__(self, store, lake, *, max_attempts=3, backoff_seconds=(5,30), fault=None):
        super().__init__(store,lake,max_attempts=max_attempts,backoff_seconds=backoff_seconds)
        self.fault = fault

    def _running_attempts(self, job_id):
        return self.store.db.execute('''SELECT * FROM harness_recovery_attempts
            WHERE status='STARTED' AND job_id=?''',(job_id,)).fetchall()

    @contextmanager
    def _guard(self, token, repo, eid, unchanged):
        with self.store.source_transaction(repo.db,token) as bound:
            job = bound._owned(token)
            if job['state'] != 'PERSISTING':
                raise StateError('Publication requires a sealed PERSISTING job')
            unchanged()
            receipt = bound._receipt(job)
            manifest = sealed_manifest(bound,job,receipt,self.lake)
            events = indexed_events(bound,job)
            if (set(events) != set(manifest['payload_hashes']) or eid not in events or
                    digest(events[eid]['payload']) != manifest['payload_hashes'][eid]):
                raise IntegrityError('SEALED_PAYLOAD_MISMATCH')
            def revalidate():
                bound._owned(token)
                unchanged()
            yield revalidate
            unchanged()

    def recover(self, job_id, token=None, *, verify_quiescent, evidence_ref):
        store = self.store
        _identifier(evidence_ref)
        job = store.get(job_id)
        if (job['source'],job['kind']) != ('tiktok','collection'):
            raise StateError('Scoped publication requires a TikTok collection job')
        if job['state'] in {'ACCESS_RESTRICTED','PAUSED_NEEDS_HUMAN','FAILED'}:
            return {'status':'HELD','job_state':job['state']}
        if verify_quiescent(dict(job)) is not True:
            return {'status':'QUIESCENCE_REQUIRED'}
        terminal = job['state'] in {'COMPLETE','PARTIAL'}
        if not terminal:
            if token is None or token.job_id != job_id:
                raise OwnershipError('Current Claim required; no automatic takeover')
            # Early rejection only. _reserve, publication/ACK and finalization
            # each validate again inside their own atomic write transaction.
            if store._owned(token)['state'] != 'PERSISTING':
                raise StateError('Sealed source receipt required before publication')
        try:
            with publisher_ownership(store.path,self.lake) as unchanged:
                if terminal:
                    try:
                        with store._transaction():
                            job = store.get(job_id)
                            receipt = store._receipt(job)
                            sealed_manifest(store,job,receipt,self.lake)
                            facts = verify(store,self.lake,job,receipt)
                        return {'status':job['state'],'already_finalized':True,**facts}
                    except StateError as exc:
                        return {'status':'INTEGRITY_ERROR','error_code':str(exc)}
                recovery_id,blocked = self._reserve(token)
                if blocked:return {'status':blocked}
                try:
                    with store._transaction(token):
                        job = store._owned(token);receipt = store._receipt(job)
                        sealed_manifest(store,job,receipt,self.lake)
                        verify(store,self.lake,job,receipt,allow_unpublished=True)
                        store._event(job,'recovery.quiescence_attested',job['state'],job['state'],evidence_ref)
                    repo = CommentPersistence(store.path,self.lake)
                    try:
                        repo.flush(event_ids=json.loads(receipt['event_ids_json']),
                            publication_guard=lambda r,eid:self._guard(token,r,eid,unchanged),fault=self.fault)
                    finally:repo.close()
                    if self.fault:self.fault('before_finalize')
                    with store.source_transaction(store.db,token) as bound:
                        job = bound._owned(token);receipt = bound._receipt(job)
                        unchanged()
                        facts = verify(bound,self.lake,job,receipt)
                        bound.record_receipt(token,source_complete=bool(receipt['source_complete']),
                            completion_evidence=receipt['completion_evidence'],stop_reason=receipt['stop_reason'],
                            event_ids=json.loads(receipt['event_ids_json']),checkpoint_ref=receipt['checkpoint_ref'],
                            publication_verified=True,reconciliation_passed=True)
                        final = 'COMPLETE' if receipt['source_complete'] else 'PARTIAL'
                        bound._transition_in_transaction(token,final)
                        bound.db.execute('''UPDATE harness_recovery_attempts SET status='SUCCEEDED',ended_at=?
                            WHERE recovery_id=? AND attempt_id=? AND status='STARTED' ''',
                            (store._now(),recovery_id,token.attempt_id))
                        bound._event(job,'persistence.reconciled','PERSISTING',final,'EXACT_PAYLOAD_AND_MEMBERSHIP')
                    return {'status':final,'already_finalized':False,**facts}
                except OwnershipError:
                    raise
                except IntegrityError as exc:
                    return self._failure(token,recovery_id,str(exc),retryable=False)
                except StateError:
                    return self._failure(token,recovery_id,'RECEIPT_OR_STATE_INVALID',retryable=False)
                except (OSError,sqlite3.OperationalError):
                    return self._failure(token,recovery_id,'STORAGE_INTERRUPTION',retryable=True)
                except Exception:
                    return self._failure(token,recovery_id,'UNKNOWN_PUBLICATION_ERROR',retryable=False)
        except PublisherBusy:
            return {'status':'PUBLISHER_BUSY'}
