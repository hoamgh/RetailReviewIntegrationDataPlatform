"""Owner-run, four-profile capture. One coordinator owns all checkpoints."""
import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import json
import multiprocessing as mp
from multiprocessing.util import Finalize
import os
from pathlib import Path
import shutil
import time

import batch
import poc

_browser = None
DLQ_STATUSES = {'NAVIGATION_FAILED'}


def initialize(profiles, stop, environment):
    global _profile, _stop, _environment
    _profile, _stop, _environment = profiles.get(), stop, environment


def execute(job):
    global _browser
    if _stop.wait(3):
        return None
    try:
        if _browser is None:
            from experiments.browser_framework.adapters import SeleniumBaseBrowser
            _browser = SeleniumBaseBrowser(_environment['chrome_path'],
                _environment['driver_version'], _environment['driver_path'], Path(_profile))
            Finalize(None, _browser.close, exitpriority=10)
            _browser.start()
        if _stop.is_set():
            return None
        row = batch.capture(job, _browser.driver)
    except Exception:
        row = dict(source=job['source'], source_key=job['source_key'],
                   source_id=job['source_id'], status='BROWSER_OR_RUNNER_ERROR',
                   verified=False, reviews_collected=0)
    if row['status'] not in batch.SUCCESS | DLQ_STATUSES:
        _stop.set()
    return row


def prepare(origin, root):
    if root.exists():
        raise ValueError('Fresh parallel root required')
    if (origin/'running.lock').exists() or (origin/'SOURCE_STOP.json').exists():
        raise ValueError('Origin active or stopped; review required')
    plan = json.loads((origin/'plan.json').read_text(encoding='utf-8'))
    states = {}
    for source in ('foody', 'overture'):
        state = json.loads((origin/(source+'_state.json')).read_text(encoding='utf-8'))
        if state['status'] not in ('READY', 'PAUSED', 'COMPLETED') or state.get('in_flight'):
            raise ValueError('Origin requires review')
        if state['plan_sha256'] != poc.sha(origin/'plan.json'):
            raise ValueError('Plan changed')
        results = state['results']
        keys = {j['source_key'] for j in plan['jobs'] if j['source'] == source}
        done = {r['source_key'] for r in results}
        if len(done) != len(results) or not done <= keys or any(r['status'] not in batch.SUCCESS for r in results):
            raise ValueError('Unexpected prior observations')
        if len(state['pending']) != len(set(state['pending'])) or set(state['pending']) != keys-done:
            raise ValueError('Pending partition mismatch')
        states[source] = state
    root.mkdir()
    shutil.copyfile(origin/'plan.json', root/'plan.json')
    rows = [r for state in states.values() for r in state['results']]
    # Finish the small Foody side first, then remaining Overture jobs.
    pending = [k for source in ('foody', 'overture') for k in states[source]['pending']]
    state = dict(status='READY', results=rows, pending=pending, in_flight=[], error=None,
                 plan_sha256=poc.sha(root/'plan.json'))
    poc.save(root/'continuation.json', dict(origin=str(origin),
        state_hashes={s:poc.sha(origin/(s+'_state.json')) for s in states}, concurrency=4))
    checkpoint(root, state)
    sql = (Path(__file__).with_name('join_mappings.sql')).read_text(encoding='utf-8')
    sql = sql.replace(str((batch.ROOT/'output/google-resolution-full-20261030-v2').as_posix()), root.as_posix())
    (root/'join_mappings.sql').write_text(sql, encoding='utf-8')


def prepare_foody_sample(root, source_path, limit=400):
    """Create an isolated, deterministic first-N detail sample for Google ID capture."""
    if root.exists():
        raise ValueError('Fresh sample root required')
    rows = json.loads(source_path.read_text(encoding='utf-8'))
    if not isinstance(rows, list) or len(rows) < limit:
        raise ValueError('Insufficient detailed Foody input')
    jobs = []
    for row in rows[:limit]:
        source_id = str(row['source_id'])
        fields = row.get('fields') or {}
        street = fields.get('streetAddress') or []
        address = ' '.join(street) if isinstance(street, list) else str(street)
        if not source_id or not row.get('name') or not row.get('url') or not isinstance(row.get('latitude'), (int, float)) or not isinstance(row.get('longitude'), (int, float)):
            raise ValueError('Missing required Foody detail fields')
        jobs.append(dict(source='foody', source_id=source_id, source_key='foody:'+source_id,
                         name=row['name'], address=address, lat=row['latitude'],
                         lon=row['longitude'], source_url=row['url'],
                         snapshot_sha256=poc.sha(source_path), cached_url=None))
    keys = [j['source_key'] for j in jobs]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate Foody source identities in sample')
    plan = dict(version=2, policy='CAPTURE_ONLY_MATCH_BY_TYPED_GOOGLE_ID_PROVISIONAL',
                mode='FOODY_FIRST_DETAIL_SAMPLE', limit=limit,
                inputs={str(source_path.relative_to(batch.ROOT)).replace('\\', '/'):poc.sha(source_path)},
                jobs=jobs, seeds=[], counts={'foody':limit},
                reviews=False, concurrency=4, retries=0)
    root.mkdir(parents=True)
    poc.save(root/'plan.json', plan)
    state = dict(status='READY', results=[], pending=keys, in_flight=[], error=None,
                 plan_sha256=poc.sha(root/'plan.json'))
    checkpoint(root, state)
    print(json.dumps(dict(status='PREPARED', source='foody', jobs=limit,
                          unique_keys=len(set(keys)), root=str(root))), flush=True)


def checkpoint(root, state):
    poc.save(root/'parallel_state.json', state)
    poc.save(root/'dlq.json', dict(version=1, retry_policy='MANUAL_ONLY',
                                  items=state.get('dlq', [])))
    counts = {}
    for source in ('foody', 'overture'):
        rows = [r for r in state['results'] if r['source'] == source]
        batch.write_mapping(root, source, dict(results=rows))
        counts[source] = dict(observations=len(rows), captured=sum(
            r['status']=='CAPTURED_FOR_REVIEW' for r in rows),
            pending=sum(k.startswith(source+':') for k in state['pending']),
            dlq=sum(r['source']==source for r in state.get('dlq', [])))
    summary = dict(status=state['status'], error=state['error'], counts=counts,
                   in_flight=len(state['in_flight']), verified=False, reviews_collected=0)
    poc.save(root/'summary.json', summary)
    with (root/'progress.jsonl').open('a', encoding='utf-8') as log:
        log.write(json.dumps(summary)+'\n')
    print(json.dumps(summary), flush=True)


def move_to_dlq(state, row):
    key = row['source_key']
    if row['status'] not in DLQ_STATUSES or key not in state['pending']:
        raise ValueError('Not an eligible pending navigation failure')
    items = state.setdefault('dlq', [])
    if any(item['source_key']==key for item in items):
        raise ValueError('Duplicate DLQ key')
    items.append(dict(source=row['source'], source_id=row['source_id'], source_key=key,
                      status=row['status'], failed_at=row.get('retrieved_at'),
                      recorded_at=datetime.now(timezone.utc).isoformat(), retry='MANUAL_ONLY'))
    state['pending'].remove(key)


def accept_navigation_dlq(root):
    """Explicit migration only; archive original checkpoint and stop before changes."""
    state = json.loads((root/'parallel_state.json').read_text(encoding='utf-8'))
    stop = json.loads((root/'SOURCE_STOP.json').read_text(encoding='utf-8'))
    failures = [r for r in state['results'] if r['status'] not in batch.SUCCESS]
    if ((root/'running.lock').exists() or state['in_flight'] or state['status']!='STOPPED'
            or state['error']!='NAVIGATION_FAILED' or stop.get('reason')!='NAVIGATION_FAILED'
            or not failures or any(r['status'] not in DLQ_STATUSES for r in failures)
            or stop.get('source_key') not in {r['source_key'] for r in failures}):
        raise ValueError('Only stopped navigation-only failures can move to DLQ')
    # Validate all transitions before touching files.
    for row in failures:
        move_to_dlq(state, row)
    history = root/'stop_reviews'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f-navigation-dlq')
    history.mkdir(parents=True)
    for name in ('parallel_state.json','SOURCE_STOP.json'):
        shutil.copyfile(root/name, history/name)
    state.update(status='PAUSED', error=None)
    checkpoint(root, state)
    os.replace(root/'SOURCE_STOP.json', history/'accepted-original-stop.json')
    print('NAVIGATION_FAILURES_MOVED_TO_DLQ; no failed job retried', flush=True)


def schedule(root, state, jobs, pool, stop, worker=execute, *, minutes=180, spacing=3):
    active = {}
    deadline = time.monotonic()+minutes*60
    last_submit = float('-inf')
    state['status'] = 'RUNNING'
    checkpoint(root, state)
    try:
        while active or (state['pending'] and not stop.is_set() and time.monotonic()<deadline):
            ready = [k for k in state['pending'] if k not in state['in_flight']]
            if ready and len(active)<4 and not stop.is_set() and time.monotonic()<deadline:
                if not stop.wait(max(0, spacing-(time.monotonic()-last_submit))):
                    key = ready[0]
                    state['in_flight'].append(key)
                    checkpoint(root, state)  # Durable before submission.
                    active[pool.submit(worker, jobs[key])] = key
                    last_submit = time.monotonic()
            if not active:
                continue
            finished, _ = wait(active, timeout=.2, return_when=FIRST_COMPLETED)
            for future in finished:
                key = active.pop(future)
                row = future.result()
                state['in_flight'].remove(key)
                if row is not None:
                    if row['source_key'] != key or row['source'] != jobs[key]['source']:
                        raise ValueError('Worker identity mismatch')
                    state['results'].append(row)
                    if row['status'] in batch.SUCCESS:
                        state['pending'].remove(key)
                    elif row['status'] in DLQ_STATUSES:
                        move_to_dlq(state, row)
                    else:
                        stop.set()
                        state['error'] = row['status']
                        poc.save(root/'SOURCE_STOP.json', dict(reason=row['status'], source_key=key))
                checkpoint(root, state)
        state['status'] = 'STOPPED' if stop.is_set() else ('PAUSED' if state['pending'] else 'COMPLETED')
    except BaseException:
        stop.set()
        state.update(status='STOPPED', error='INTERRUPTED_OR_RUNNER_FAILURE')
        poc.save(root/'SOURCE_STOP.json', dict(reason=state['error']))
        raise
    finally:
        checkpoint(root, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--origin', type=Path, default=Path('output/google-resolution-continuation-20261031T111306Z'))
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--prepare-foody-sample', action='store_true')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--accept-navigation-dlq', action='store_true',
                        help='Archive an existing navigation-only stop and quarantine failed jobs; no live requests')
    parser.add_argument('--max-minutes', type=int, default=180)
    args = parser.parse_args()
    root, origin = args.root.resolve(), args.origin.resolve()
    allowed = (batch.ROOT/'output').resolve()
    if root == allowed or not root.is_relative_to(allowed) or not origin.is_relative_to(allowed):
        raise ValueError('Dedicated output directories required')
    if not 1 <= args.max_minutes <= 180:
        raise ValueError('Use 1..180 minutes')
    if args.accept_navigation_dlq and (args.prepare or args.prepare_foody_sample or args.live or args.resume):
        raise ValueError('DLQ migration must run separately from prepare/live/resume')
    if args.prepare_foody_sample and (args.prepare or args.live or args.resume):
        raise ValueError('Sample preparation must run separately')
    # These four task-approved crawler profiles only; never personal Chrome.
    profiles = [allowed/('google-crawler-login'+suffix) for suffix in ('', '-02', '-03', '-04')]
    for profile in profiles:
        if not (profile/'Local State').is_file() or not (profile/'Default/Preferences').is_file():
            raise ValueError('Dedicated profile missing')
    from experiments.browser_framework.profiles import assert_available
    from experiments.browser_framework.run import environment
    assert_available(profiles)
    env = environment(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
    # Preserve the originating stop gate even after preparing this new root.
    sample_mode = args.prepare_foody_sample or (root/'plan.json').exists() and json.loads((root/'plan.json').read_text(encoding='utf-8')).get('mode') == 'FOODY_FIRST_DETAIL_SAMPLE'
    for gate in (() if sample_mode else (origin, batch.ROOT/'output/google-resolution-full-20261030-v2')):
        if (gate/'SOURCE_STOP.json').exists() or (gate/'running.lock').exists():
            raise ValueError('Prior source stop or active runner requires review')
    if args.prepare_foody_sample:
        prepare_foody_sample(root, batch.ROOT/'output/foody-q4-detail-first1000-v1/raw/pois.json')
    elif args.prepare:
        prepare(origin, root)
    state = json.loads((root/'parallel_state.json').read_text(encoding='utf-8'))
    plan = json.loads((root/'plan.json').read_text(encoding='utf-8'))
    if state['plan_sha256'] != poc.sha(root/'plan.json'):
        raise ValueError('Plan changed')
    for name, digest in plan['inputs'].items():
        if poc.sha(batch.ROOT/name) != digest:
            raise ValueError('Input changed')
    if args.accept_navigation_dlq:
        accept_navigation_dlq(root)
        return
    if (root/'SOURCE_STOP.json').exists() or state['in_flight'] or state['status'] not in ('READY', 'PAUSED'):
        raise ValueError('Stop/in-flight state requires review; no automatic profile switching')
    if (state['status']=='PAUSED') != args.resume:
        raise ValueError('Use --resume only for PAUSED')
    print('PREFLIGHT_PASS: four profiles; no reviews; global stop gate', flush=True)
    if not args.live:
        return
    lock = root/'running.lock'
    fd = os.open(lock, os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    os.close(fd)
    try:
        ctx = mp.get_context('spawn')
        stop, assignments = ctx.Event(), ctx.Queue()
        for profile in profiles:
            assignments.put(str(profile))
        poc.save(root/'run_environment.json', dict(chrome=env['chrome'],
            driver=env['driver_version'], runner_sha256=poc.sha(Path(__file__)),
            batch_sha256=poc.sha(Path(batch.__file__)), concurrency=4,
            started_at=datetime.now(timezone.utc).isoformat()))
        with ProcessPoolExecutor(max_workers=4, mp_context=ctx, initializer=initialize,
                                 initargs=(assignments, stop, env)) as pool:
            schedule(root, state, {j['source_key']:j for j in plan['jobs']},
                     pool, stop, minutes=args.max_minutes)
    finally:
        lock.unlink()  # Only the lock owned by this invocation.


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Avoid raw WebDriver errors, URLs, or authentication information.
        print(json.dumps(dict(status='BLOCKED', error_type=type(exc).__name__)), flush=True)
        raise SystemExit(1)
