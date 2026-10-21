"""Trusted managed event routing in existing ledger/checkpoint fields, no DDL.

Legacy/unindexed events are NOT guessed from possibly corrupt payload JSON.
Only cooperating source-commit hooks produce these atomic routing facts.
"""
import json
from pathlib import Path

from .recovery import IntegrityError
from experiments.tiktok_comment_dom.persistence import FIELDS, digest


def checkpoint(store, job):
    row = store.db.execute('''SELECT state_json FROM tiktok_comment_checkpoints
        WHERE run_id=? AND place_id=? AND video_id=?''',
        (job['data_run_id'], job['place_id'], job['entity_id'])).fetchone()
    if row is None:
        return {}
    try:
        result = json.loads(row[0])
        if not isinstance(result, dict): raise ValueError()
        return result
    except (ValueError, TypeError) as exc:
        raise IntegrityError('INVALID_CHECKPOINT') from exc


def is_managed(store, job):
    return bool(store.db.execute('''SELECT 1 FROM harness_events WHERE job_id=?
        AND event_type IN ('source.event_committed','job.publication_sealed') LIMIT 1''', (job['job_id'],)).fetchone()
        or 'managed_publication_manifest' in checkpoint(store, job))


def indexed_events(store, job):
    # Read event identities and trusted routing FIRST. Never parse B's payload.
    all_ids = {r[0] for r in store.db.execute('SELECT event_id FROM tiktok_comment_events')}
    routes = store.db.execute('''SELECT job_id,code,source,place_id,entity_id
        FROM harness_events WHERE event_type='source.event_committed' ''').fetchall()
    mapped = {r['code'] for r in routes}
    if all_ids - mapped:
        raise IntegrityError('UNINDEXED_OUTBOX_EVENT')
    own = [r for r in routes if r['job_id'] == job['job_id']]
    ids = [r['code'] for r in own]
    if len(ids) != len(set(ids)) or any(
            sum(r['code'] == eid for r in routes) != 1 for eid in ids):
        raise IntegrityError('AMBIGUOUS_EVENT_SCOPE')
    if any((r['source'],r['place_id'],r['entity_id']) != (
            job['source'],job['place_id'],job['entity_id']) for r in own):
        raise IntegrityError('INVALID_SCOPE_METADATA')
    events = {}
    for eid in ids:
        row = store.db.execute('SELECT payload,delivered FROM tiktok_comment_events WHERE event_id=?', (eid,)).fetchone()
        if row is None:
            raise IntegrityError('INDEXED_EVENT_MISSING')
        try:
            payload = json.loads(row['payload'])
        except (ValueError,TypeError) as exc:
            raise IntegrityError('MALFORMED_OUTBOX') from exc
        if (not isinstance(payload,dict) or not set(FIELDS).issubset(payload) or
                payload.get('event_id') != eid or
                (payload.get('place_id'),payload.get('video_id'),payload.get('run_id')) !=
                (job['place_id'],job['entity_id'],job['data_run_id']) or
                digest([payload['storage_key'],payload['revision']]) != eid):
            raise IntegrityError('INVALID_EVENT_IDENTITY')
        events[eid] = {'payload':payload,'delivered':row['delivered']}
    return events


def sealed_manifest(store, job, receipt, lake=None):
    cp = checkpoint(store, job)
    manifest = cp.get('managed_publication_manifest')
    observation = cp.get('managed_collection_observation', {})
    if not isinstance(manifest,dict) or manifest.get('version') != 1:
        raise IntegrityError('MANAGED_SEAL_MISSING')
    expected = (job['job_id'],job['place_id'],job['entity_id'],job['data_run_id'])
    if tuple(manifest.get(k) for k in ('job_id','place_id','video_id','data_run_id')) != expected:
        raise IntegrityError('INVALID_SCOPE_METADATA')
    seals = store.db.execute('''SELECT code,generation,attempt_id FROM harness_events
        WHERE job_id=? AND event_type='job.publication_sealed' ''', (job['job_id'],)).fetchall()
    if (len(seals) != 1 or seals[0]['code'] != digest(manifest) or
            (seals[0]['generation'],seals[0]['attempt_id']) != (receipt['generation'],receipt['attempt_id']) or
            (observation.get('generation'),observation.get('attempt_id')) != (receipt['generation'],receipt['attempt_id']) or
            manifest.get('source_complete') is not bool(receipt['source_complete']) or
            manifest.get('stop_reason') != receipt['stop_reason']):
        raise IntegrityError('MANAGED_SEAL_MISMATCH')
    hashes = manifest.get('payload_hashes')
    if not isinstance(hashes,dict) or sorted(hashes) != json.loads(receipt['event_ids_json']):
        raise IntegrityError('EVENT_MEMBERSHIP_MISMATCH')
    if lake is not None and Path(manifest.get('lake_root','')).resolve() != Path(lake).resolve():
        raise IntegrityError('SEALED_LAKE_MISMATCH')
    return manifest
