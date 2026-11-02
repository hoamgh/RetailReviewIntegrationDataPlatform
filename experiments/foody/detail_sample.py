"""Bounded Foody detail sample from an existing discovery checkpoint; no reviews."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import copy
import json
import multiprocessing as mp
import os
from pathlib import Path

from shapely.geometry import shape

import parallel_q4 as q

DEFAULT_SOURCE = q.ROOT / 'output/foody-q4-search-fallback-20261031-v1/state.json'
PROFILES = ('manual-foody-20261029-135111', 'manual-foody-20261031-173744')


def prepare(source, limit):
    original = json.loads(source.read_text(encoding='utf-8'))
    if original.get('status') != 'BLOCKED' or not isinstance(original.get('places'), dict):
        raise ValueError('Expected preserved blocked discovery checkpoint')
    if not 1 <= limit <= 1000:
        raise ValueError('Detail sample limit must be between 1 and 1000')
    # The discovery state preserves insertion order, but this order is not a
    # Foody ranking. Keep it stable across resumes and never crawl the full set.
    selected = list(original['places'])[:limit]
    state = copy.deepcopy(original)
    state['places'] = {url: original['places'][url] for url in selected}
    state['phase'] = 'DETAILS'
    state['status'] = 'READY'
    state['error'] = None
    state['in_flight'] = []
    state['pending'] = [url for url in selected if url not in set(original['detail_done'])]
    state['dlq'] = []
    state['detail_done'] = [url for url in selected if url in set(original['detail_done'])]
    state['records'] = {key: copy.deepcopy(record) for key, record in original['records'].items()
                        if record.get('url') in state['detail_done']}
    state['source_checkpoint_sha256'] = q.digest(source)
    state['sample_size'] = len(selected)
    state['initial_detailed'] = len(state['detail_done'])
    state['coverage'] = 'FIRST_DISCOVERED_URLS_SAMPLE_NOT_DISTRICT_COMPLETE'
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--root', type=Path, default=q.ROOT / 'output/foody-q4-detail-first1000-v1')
    parser.add_argument('--limit', type=int, default=1000)
    parser.add_argument('--max-minutes', type=int, default=180)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--rights-confirmed', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    allowed = (q.ROOT / 'output').resolve()
    if root == allowed or not root.is_relative_to(allowed) or not 1 <= args.max_minutes <= 180:
        raise ValueError('Output must be a dedicated directory within output/; max 180 minutes')
    if args.live and not args.rights_confirmed:
        raise ValueError('Operator must confirm rights to collect/store Foody data')
    source = args.source.resolve()
    if not source.is_file():
        raise ValueError('Discovery checkpoint missing')
    expected = prepare(source, args.limit)
    if args.resume:
        state = json.loads((root / 'state.json').read_text(encoding='utf-8'))
        if (state.get('status') != 'PAUSED' or state.get('in_flight') or
                state.get('source_checkpoint_sha256') != expected['source_checkpoint_sha256'] or
                state.get('sample_size') != expected['sample_size']):
            raise ValueError('Only PAUSED unchanged sample may resume')
    else:
        if root.exists():
            raise ValueError('New output directory required; never overwrite an existing run')
        state = expected
    profiles = [Path(os.environ['LOCALAPPDATA']) / 'FnbFoodyResearch' / name for name in PROFILES]
    for profile in profiles:
        q.available_profile(profile)
    features = json.loads(q.BOUNDARY.read_text(encoding='utf-8'))['features']
    selected = [f for f in features if f['properties'].get('shapeID') == '81297802B29563104023098']
    if len(selected) != 1:
        raise ValueError('Historical Q4 boundary unavailable')
    polygon = shape(selected[0]['geometry'])
    if not polygon.is_valid:
        raise ValueError('Invalid district polygon')
    if args.check or not args.live:
        print(json.dumps(dict(status='PREFLIGHT_PASS', selected=state['sample_size'],
                              already_detailed=state['initial_detailed'], pending=len(state['pending']),
                              profiles=len(profiles), google_maps_matched=False)))
        return
    if not args.resume:
        (root / 'raw').mkdir(parents=True)
    lock = root / 'running.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    try:
        ctx = mp.get_context('spawn')
        stop = ctx.Event()
        assignments = ctx.Queue()
        for profile in profiles:
            assignments.put(str(profile))
        with ctx.Manager() as manager:
            events = manager.Queue()
            with ProcessPoolExecutor(max_workers=2, mp_context=ctx,
                                     initializer=q.initialize,
                                     initargs=(assignments, stop, events)) as pool:
                q.run(root, state, polygon, pool, stop, events, args.max_minutes, 1)
    finally:
        lock.unlink()


if __name__ == '__main__':
    main()
