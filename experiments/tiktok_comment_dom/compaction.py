"""Read-only outbox/lake compaction to a separate sealed analysis snapshot.

This does not change the live writer or acknowledge any events. Never read source
and compacted lakes as a union. Single offline writer; source run must be stopped.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from .persistence import CommentPersistence, digest


def publish_json(path, value):
    temporary = path.with_suffix('.json.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def filesystem_path(path):
    path = Path(path).resolve()
    if os.name == 'nt' and not str(path).startswith('\\\\?\\'):
        value = str(path)
        return Path('\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value)
    return path


def read_parquet(path):
    import pyarrow.parquet as pq
    # Python file handles support long Windows paths that Arrow's native
    # path-based filesystem may reject. No Hive inference from parent folders.
    with filesystem_path(path).open('rb') as handle:
        return pq.read_table(handle)


def compact(database, source, output, *, fault=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    database, source, output = (Path(p).resolve() for p in (database, source, output))
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('Compacted output must be separate from the source lake')
    if 'google_maps_reviews' in source.parts or 'google_maps_reviews' in output.parts:
        raise ValueError('Google lake is protected')
    if not database.is_file() or not source.is_dir():
        raise ValueError('Existing SQLite and source lake required')
    output = filesystem_path(output)
    with sqlite3.connect(f'file:{database.as_posix()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        events = [dict(row) for row in db.execute('SELECT * FROM tiktok_comment_events ORDER BY event_id')]
        current = [dict(row) for row in db.execute('SELECT storage_key,revision,content_hash FROM tiktok_comments')]
        checkpoints = {(r['run_id'], r['place_id'], r['video_id']): dict(r)
                       for r in db.execute('SELECT * FROM tiktok_comment_checkpoints')}
    if any(not e['delivered'] for e in events):
        raise RuntimeError('Pending outbox events; finish/reconcile source first')
    expected = {e['event_id']: json.loads(e['payload']) for e in events}
    if any(not any(all(row[k] == comment[k] for k in ('storage_key', 'revision', 'content_hash'))
                       for row in expected.values()) for comment in current):
        raise RuntimeError('Current SQLite revision has no matching outbox event')
    schema = CommentPersistence.schema()
    source_files = sorted(source.rglob('*.parquet'))
    actual = []
    for path in source_files:
        table = read_parquet(path)
        if not table.schema.equals(schema, check_metadata=True):
            raise RuntimeError('Source schema mismatch')
        actual.extend(table.to_pylist())
    if Counter(row['event_id'] for row in actual) != Counter(expected.keys()):
        raise RuntimeError('Source event membership mismatch or duplicate event')
    if any(expected[row['event_id']] != row for row in actual):
        raise RuntimeError('Source event payload mismatch')
    groups = defaultdict(list)
    for row in expected.values():
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', row['run_id']) or not str(row['video_id']).isdigit():
            raise ValueError('Unsafe run/video identity')
        groups[(row['run_id'], row['place_id'], row['video_id'])].append(row)
    descriptions = []
    payloads = {}
    for key, rows in sorted(groups.items()):
        rows.sort(key=lambda row: row['event_id'])
        identity = digest([key, rows])
        run, place, video = key
        filename = f'run_id={run}/video_id={video}/group-{identity}.parquet'
        checkpoint = checkpoints.get(key, {})
        descriptions.append({'run_id': run, 'place_id': place, 'video_id': video,
            'file': filename, 'group_id': identity, 'event_ids': [r['event_id'] for r in rows],
            'payload_sha256': digest(rows), 'checkpoint_status': checkpoint.get('status', 'UNKNOWN'),
            'coverage': 'COMPLETE_ONLY_IF_SOURCE_CHECKPOINT_CONFIRMS; otherwise partial/unknown'})
        payloads[filename] = rows
    plan = {'version': 1, 'mode': 'SEALED_EVENT_HISTORY_SNAPSHOT', 'source_database': str(database),
            'source_lake': str(source), 'source_event_count': len(expected), 'groups': descriptions,
            'logical_identity_limit': 'SESSION_FALLBACK cannot guarantee cross-run logical deduplication.',
            'read_policy': 'Only consume when summary status is VERIFIED. Read this lake OR the original, never both; all event revisions retained.'}
    output.mkdir(parents=True, exist_ok=True)
    manifest = output/'manifest.json'
    if manifest.exists():
        if json.loads(manifest.read_text(encoding='utf-8')) != plan:
            raise RuntimeError('Sealed membership changed; use a fresh output snapshot')
    else:
        if any(output.iterdir()):
            raise ValueError('Unmanaged output is not empty')
        publish_json(manifest, plan)
    publish_json(output/'summary.json', {'status': 'IN_PROGRESS', 'readable': False})
    def checkpoint(stage):
        if fault: fault(stage)
    checkpoint('before_write')
    expected_paths = {output/description['file'] for description in descriptions}
    if any(path not in expected_paths for path in output.rglob('*.parquet')):
        raise RuntimeError('Unexpected compacted file')
    for description in descriptions:
        target = output/description['file']
        rows = payloads[description['file']]
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix('.parquet.tmp')
            with temporary.open('wb') as handle:
                pq.write_table(pa.Table.from_pylist(rows, schema=schema), handle, compression='snappy')
            checkpoint('during_write')
            with temporary.open('rb+') as handle:
                os.fsync(handle.fileno())
            if read_parquet(temporary).to_pylist() != rows:
                raise RuntimeError('Temporary Parquet verification failed')
            temporary.replace(target)
            checkpoint('after_rename')
        table = read_parquet(target)
        if not table.schema.equals(schema, check_metadata=True) or table.to_pylist() != rows:
            raise RuntimeError('Published group does not match sealed manifest')
    checkpoint('before_complete')
    report = {'status': 'VERIFIED', 'source_files': len(source_files), 'compacted_files': len(descriptions),
        'event_rows': len(expected), 'duplicate_event_ids': 0, 'outbox_acknowledgements_changed': 0,
        'partial_or_unknown_groups': sum(g['checkpoint_status'] != 'COMPLETE' for g in descriptions),
        'manifest': 'manifest.json', 'publication': 'file fsync + same-directory atomic rename; process-crash recovery tested, power-loss durability not certified'}
    publish_json(output/'summary.json', report)
    return report


def finalize_analysis(run_output):
    """Seal the stopped runner's analysis lake; never hide a crawler failure.

    Call only after all source writers are closed. Raw files remain outbox
    publication evidence. Consumers must use the VERIFIED lake alone.
    """
    root = Path(run_output)
    database, source = root/'comments.sqlite3', root/'lake'
    result = {'status': 'NOT_CREATED', 'readable': False,
              'reason': 'NO_PERSISTENCE_OUTPUT', 'raw_retained': True}
    if database.is_file() or source.exists():
        try:
            report = compact(database, source, root/'analysis_lake')
            result = {**report, 'readable': True, 'lake': 'analysis_lake',
                      'raw_retained': True,
                      'read_policy': 'Read analysis_lake only; never union with lake.'}
        except Exception as exc:
            # Exception strings may contain source payloads or private paths.
            result = {'status': 'FAILED', 'readable': False,
                      'error_type': type(exc).__name__, 'raw_retained': True,
                      'next_action': 'Reconcile source and inspect manifest; rerun compaction offline. Do not recollect.'}
    publish_json(root/'analysis_output.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(compact(args.database, args.source, args.output), indent=2))


if __name__ == '__main__':
    main()
