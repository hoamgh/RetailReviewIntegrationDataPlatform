"""M3-B offline managed collection. Publication/browser recovery belong to M3-C.

Uses the existing runner and writer; no network/browser/provider initialization.
Guards cover cooperative managed source transactions, not external SQL or legacy
writers, browser actions, JSON evidence files, or filesystem publication.
"""
from contextlib import contextmanager
import json
from pathlib import Path

from .state import OwnershipError, RESTRICTED_STOP_REASONS, StateError
from experiments.tiktok_comment_dom.persistence import CommentPersistence, SourceRunConflict, digest
from experiments.tiktok_place_intelligence.probe import (
    dom_input_digest, run_dom_comments, validate_dom_match,
)


PARTIAL_STOPS = {
    'COMMENT_LIMIT', 'MAX_SCROLLS', 'BOUNDED_NO_GROWTH',
    'NO_SAFE_COMMENT_SCROLL_CONTAINER', 'COMMENT_CARDS_TIMEOUT',
    'COMMENT_SURFACE_UNAVAILABLE', 'COMMENT_BUTTON_TIMEOUT',
    'REPLY_EXPANSION_NO_GROWTH',
}
END_STOPS = {'EXPLICIT_END_MARKER', 'EXPLICIT_EMPTY_MARKER'}
AMBIGUOUS_STOPS = {'UNKNOWN_403', 'REQUIRED_ENDPOINT_ERROR', 'APPLICATION_ERROR'}


class TikTokManagedAdapter:
    """Borrow an existing JobStore and a fake port. No schema migration.

    Limits/match are pinned by the runner's own digest. Data-run is stable across
    attempts; evidence directories use unique attempt IDs. Successful collection
    stops in PERSISTING, with pending outbox and an unverified durable receipt.
    """
    def __init__(self, store, lake, evidence_root, *, fault=None):
        self.store = store
        self.lake = Path(lake).resolve()
        self.evidence_root = Path(evidence_root).resolve()
        self.fault = fault
        if 'google_maps_reviews' in self.lake.parts:
            raise StateError('Google lake is protected')

    def prepare(self, match_data, *, workflow='tiktok-managed-v1', catalog=None,
                limit=7, comment_scrolls=0, reply_limit=0, reply_expand_limit=0):
        # Strict reviewed matching: query-only candidates are not upgraded.
        video, place, _ = validate_dom_match(match_data, catalog)
        options = dict(limit=limit, comment_scrolls=comment_scrolls,
                       reply_limit=reply_limit, reply_expand_limit=reply_expand_limit)
        if (type(limit) is not int or not 1 <= limit <= 50 or
                type(comment_scrolls) is not int or not 0 <= comment_scrolls <= 10 or
                type(reply_limit) is not int or not 0 <= reply_limit <= 50 or
                type(reply_expand_limit) is not int or not 0 <= reply_expand_limit <= 5 or
                (reply_expand_limit and not reply_limit)):
            raise StateError('Invalid bounded collector options')
        identity = dom_input_digest(video, place, **options)
        job_id = self.store.create_job(source='tiktok', kind='collection',
            place_id=place['place_id'], entity_id=video['video_id'],
            input_digest=identity, workflow=workflow)
        return job_id

    @contextmanager
    def guard(self, token, repo, context, source_gate=None):
        # source_transaction verifies actual main-DB path, begins on repo.db,
        # validates Claim and rolls back if the original deadline expires.
        with self.store.source_transaction(repo.db, token) as bound:
            if source_gate is not None:source_gate(bound)
            job = bound._owned(token)
            from .restrictions import assert_dispatch,installed
            # C2c managed collection requires its session hook. Legacy fixtures
            # remain unchanged only on databases without the extension.
            if source_gate is None:
                if installed(bound):
                    raise StateError('C2c source commit requires guarded session hook')
                assert_dispatch(bound, job['source'])
            if (job['source'], job['kind'], job['state']) != ('tiktok', 'collection', 'RUNNING'):
                raise StateError('Managed collection requires a RUNNING TikTok job')
            if (context['place_id'], context['video_id'], context['run_id'], context['input_digest']) != (
                    job['place_id'], job['entity_id'], job['data_run_id'], job['input_digest']):
                raise StateError('Source write does not belong to job scope')
            if type(context['next_page']) is not int or context['next_page'] < 0:
                raise StateError('Checkpoint progress must be a nonnegative integer')
            final = context['final_observation']
            if final is not None:
                reason = final['stop_reason']
                if not isinstance(reason, str) or not reason or len(reason) > 100:
                    raise StateError('Bounded final outcome required')
                checkpoint = context['checkpoint']
                complete = (reason in END_STOPS and
                            checkpoint['pagination'].get('exhausted') is True and
                            not checkpoint.get('reply_limit', 0))
                checkpoint['pagination']['stop_reason'] = reason
                if not complete:
                    checkpoint['pagination']['exhausted'] = None
                # Atomic final observation in existing checkpoint, no new table.
                checkpoint['managed_collection_observation'] = {
                    'version': 1, 'job_id': job['job_id'], 'attempt_id': token.attempt_id,
                    'generation': token.generation, 'stop_reason': reason,
                    'source_complete': complete,
                }
            yield bound
        if context['final_observation'] is not None and self.fault:
            self.fault('after_commit')

    def observe(self, token, repo, context, bound):
        job = bound._owned(token)
        for eid in context['committed_event_ids']:
            bound._event(job,'source.event_committed',job['state'],job['state'],eid)
        if context['final_observation'] is None:
            return
        final = context['checkpoint']['managed_collection_observation']
        reason = final['stop_reason']
        from .event_scope import indexed_events
        events = indexed_events(bound, job)
        manifest = {'version':1,'job_id':job['job_id'],'place_id':job['place_id'],
                    'video_id':job['entity_id'],'data_run_id':job['data_run_id'],
                    'lake_root':str(self.lake),'source_complete':final['source_complete'],
                    'stop_reason':reason,
                    'payload_hashes':{eid:digest(event['payload']) for eid,event in events.items()}}
        context['checkpoint']['managed_publication_manifest'] = manifest
        repo.db.execute('''UPDATE tiktok_comment_checkpoints SET state_json=?
            WHERE run_id=? AND place_id=? AND video_id=?''',
            (json.dumps(context['checkpoint'],ensure_ascii=False),job['data_run_id'],job['place_id'],job['entity_id']))
        bound._event(job,'job.publication_sealed',job['state'],job['state'],digest(manifest))
        bound.record_receipt(token, source_complete=final['source_complete'],
            completion_evidence=reason if final['source_complete'] else None,
            stop_reason=reason, event_ids=sorted(events),
            checkpoint_ref='source:' + job['data_run_id'] + ':' + job['entity_id'])
        if reason in AMBIGUOUS_STOPS:
            target = 'PAUSED_NEEDS_HUMAN'
        elif reason in RESTRICTED_STOP_REASONS:
            target = 'ACCESS_RESTRICTED'
        elif reason in PARTIAL_STOPS or reason in END_STOPS:
            target = 'PERSISTING'
        else:
            target = 'PAUSED_NEEDS_HUMAN'
        bound.transition(token, target, code=reason)
        if self.fault:
            self.fault('before_commit')

    def repository(self, token, database=None, lake=None, *, source_gate=None):
        database = self.store.path if database is None else Path(database).resolve()
        lake = self.lake if lake is None else Path(lake).resolve()
        if Path(database).resolve() != self.store.path or lake != self.lake:
            raise StateError('Managed repository path mismatch')
        return CommentPersistence(database, lake,
            commit_guard=lambda repo, ctx: self.guard(token, repo, ctx, source_gate),
            commit_observer=lambda repo, ctx, bound: self.observe(token, repo, ctx, bound),
            publication_enabled=False)

    def execute(self, token, match_data, *, port, catalog=None, resume=False,
                limit=7, comment_scrolls=0, reply_limit=0, reply_expand_limit=0, command_boundary=None):
        if getattr(port, 'harness_offline_fixture', False) is not True:
            raise StateError('M3-B accepts offline fixture ports only')
        from .restrictions import installed, assert_dispatch
        if installed(self.store) and command_boundary is None:
            raise StateError('C2c requires guarded session; direct adapter dispatch refused')
        if command_boundary is not None:command_boundary.validate_port(port,token)
        # Detach caller-owned mutable input and reject changed config before UI.
        pinned = json.loads(json.dumps(match_data))
        with self.store._transaction(token):
            job = self.store._owned(token)
            if command_boundary is None:
                assert_dispatch(self.store, job['source'])
            if job['state'] != 'RUNNING':
                raise StateError('Source already sealed; use publication recovery')
        job_id = token.job_id
        video, place, _ = validate_dom_match(pinned, catalog)
        identity = dom_input_digest(video, place, limit=limit, comment_scrolls=comment_scrolls,
                                    reply_limit=reply_limit, reply_expand_limit=reply_expand_limit)
        if (job['source'],job['kind'],job['place_id'],job['entity_id'],job['input_digest']) != (
                'tiktok','collection',place['place_id'],video['video_id'],identity):
            raise StateError('Pinned match/config changed')
        blocked = None
        with self.store._transaction(token):
            current = self.store._owned(token)
            if current['state'] != 'RUNNING':
                raise StateError('Source already sealed; use publication recovery')
            saved = self.store.db.execute('''SELECT status FROM tiktok_comment_checkpoints
                WHERE run_id=? AND place_id=? AND video_id=?''',
                (job['data_run_id'],job['place_id'],job['entity_id'])).fetchone()
            if saved and saved['status'] in {'ACCESS_DENIED','RATE_LIMITED','COMPLETE','FAILED'}:
                restricted = saved['status'] in {'ACCESS_DENIED','RATE_LIMITED'}
                blocked = saved['status'] if restricted else 'FINAL_OBSERVATION_MISSING'
                self.store._transition_in_transaction(token,
                    'ACCESS_RESTRICTED' if restricted else 'PAUSED_NEEDS_HUMAN',code=blocked)
        if blocked:
            raise StateError('Terminal source checkpoint requires operator review: '+blocked)
        output = self.evidence_root / job_id / token.attempt_id
        if output.exists():
            raise StateError('Attempt evidence already exists; use a new verified attempt')
        try:
            result = run_dom_comments(pinned, output=output, database=self.store.path,
                parquet_root=self.lake, run_id=job['data_run_id'], port=port,
                catalog=catalog, resume=resume, limit=limit, comment_scrolls=comment_scrolls,
                reply_limit=reply_limit, reply_expand_limit=reply_expand_limit,
                repository_factory=lambda db, lake: self.repository(token, db, lake,
                    source_gate=command_boundary.source_gate if command_boundary is not None else None),
                defer_publication=True)
        except OwnershipError:
            raise  # A stale worker cannot record a hold or change any state.
        except SourceRunConflict:
            # The source batch rolled back before any DML. Only this current
            # owner may record the explicit unsupported-refresh hold.
            self.store.transition(token, 'PAUSED_NEEDS_HUMAN', code='CROSS_RUN_SOURCE_ID_CONFLICT')
            raise
        except Exception:
            # Unknown collection exceptions may lack a final observation. Hold
            # only an owned RUNNING job; never fabricate a receipt or replace an
            # already committed PERSISTING/restricted outcome.
            try:
                with self.store._transaction(token):
                    if self.store._owned(token)['state'] == 'RUNNING':
                        self.store._transition_in_transaction(token, 'PAUSED_NEEDS_HUMAN',
                                                              code='COLLECTION_EXCEPTION')
            except OwnershipError:
                pass
            raise
        return {'job_id': job_id, 'attempt_id': token.attempt_id,
                'data_run_id': job['data_run_id'], 'state': self.store.get(job_id)['state'],
                'collection_complete': bool(self.store._receipt(self.store.get(job_id))['source_complete']),
                'source_stop_reason': result['stop_reason'], 'evidence': str(output),
                'publication': 'DEFERRED_M3_C'}
