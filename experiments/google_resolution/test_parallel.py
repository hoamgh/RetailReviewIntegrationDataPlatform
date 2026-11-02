"""Offline coordinator tests; no browser or network."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from threading import Event, Lock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import parallel


def fixtures(tmp_path, count=12):
    jobs = {f'overture:{i}':dict(source='overture', source_id=str(i),
            source_key=f'overture:{i}') for i in range(count)}
    state = dict(status='READY', error=None, results=[], pending=list(jobs), in_flight=[])
    return tmp_path, state, jobs


def test_foody_first_400_sample_is_unique_and_reproducible(tmp_path):
    data = tmp_path/'pois.json'
    data.write_text(json.dumps([dict(source_id=str(i), name=f'Cafe {i}',
        url=f'https://www.foody.vn/ho-chi-minh/cafe-{i}',
        fields={'streetAddress':['Quận 4']}, latitude=10.75, longitude=106.7)
        for i in range(410)]), encoding='utf-8')
    # Real source must be under the repository root for input SHA checking.
    original_root = parallel.batch.ROOT
    try:
        parallel.batch.ROOT = tmp_path
        parallel.prepare_foody_sample(tmp_path/'sample', data)
        plan = json.loads((tmp_path/'sample/plan.json').read_text(encoding='utf-8'))
        state = json.loads((tmp_path/'sample/parallel_state.json').read_text(encoding='utf-8'))
        assert len(plan['jobs']) == len(state['pending']) == 400
        assert len(set(state['pending'])) == 400
        assert plan['jobs'][0]['source_id'] == '0' and plan['jobs'][-1]['source_id'] == '399'
        assert plan['inputs']['pois.json'] == parallel.poc.sha(data)
        assert state['status'] == 'READY' and not state['results']
    finally:
        parallel.batch.ROOT = original_root


def success(job):
    return dict(job, status='CAPTURED_FOR_REVIEW', google_id_kind='data_id',
                google_identity_value=job['source_id'], verified=False, reviews_collected=0)


def test_four_workers_process_each_key_once_and_publish(tmp_path):
    root, state, jobs = fixtures(tmp_path)
    calls, lock = [], Lock()
    def worker(job):
        with lock:
            calls.append(job['source_key'])
        return success(job)
    with ThreadPoolExecutor(max_workers=4) as pool:
        parallel.schedule(root, state, jobs, pool, Event(), worker, spacing=0)
    assert state['status']=='COMPLETED' and not state['pending'] and not state['in_flight']
    assert len(calls)==len(set(calls))==len(jobs)
    assert parallel.batch.pq.read_metadata(root/'overture_mapping.parquet').num_rows==len(jobs)


@pytest.mark.parametrize('reason', ['CHALLENGE','BLOCKED','LIMITED_VIEW','BROWSER_OR_RUNNER_ERROR'])
def test_shared_stop_preserves_pending_and_prevents_further_dispatch(tmp_path, reason):
    root, state, jobs = fixtures(tmp_path)
    stop, calls = Event(), []
    def worker(job):
        calls.append(job['source_key'])
        stop.set()
        return dict(job, status=reason)
    with ThreadPoolExecutor(max_workers=4) as pool:
        parallel.schedule(root, state, jobs, pool, stop, worker, spacing=0)
    assert state['status']=='STOPPED' and state['error']==reason
    assert state['pending']==list(jobs) and not state['in_flight']
    assert len(calls)==1
    assert json.loads((root/'SOURCE_STOP.json').read_text())['reason']==reason


def test_budget_preserves_prior_results(tmp_path):
    root, state, jobs = fixtures(tmp_path)
    row=success(jobs.pop('overture:0'))
    state['results']=[row]
    state['pending'].remove(row['source_key'])
    with ThreadPoolExecutor(max_workers=4) as pool:
        parallel.schedule(root, state, jobs, pool, Event(), success, minutes=0, spacing=0)
    assert state['status']=='PAUSED' and state['results']==[row]
    assert len(state['pending'])==11


def test_navigation_failure_goes_to_dlq_and_other_jobs_complete(tmp_path):
    root,state,jobs=fixtures(tmp_path)
    calls=[]
    def worker(job):
        calls.append(job['source_key'])
        return dict(job,status='NAVIGATION_FAILED') if job['source_id']=='0' else success(job)
    with ThreadPoolExecutor(max_workers=4) as pool:
        parallel.schedule(root,state,jobs,pool,Event(),worker,spacing=0)
    assert state['status']=='COMPLETED' and state['error'] is None
    assert not state['pending'] and not state['in_flight']
    assert len(calls)==len(set(calls))==len(jobs)
    assert [r['source_key'] for r in state['dlq']]==['overture:0']
    assert not (root/'SOURCE_STOP.json').exists()
    assert json.loads((root/'dlq.json').read_text())['retry_policy']=='MANUAL_ONLY'
    summary=json.loads((root/'summary.json').read_text())
    assert summary['counts']['overture']['dlq']==1
    assert summary['counts']['overture']['captured']==11


def test_worker_navigation_failure_does_not_set_global_stop(monkeypatch):
    class Browser:
        driver=object()
    class Stop(Event):
        def wait(self,seconds): return self.is_set()
    stop=Stop()
    monkeypatch.setattr(parallel,'_browser',Browser())
    monkeypatch.setattr(parallel,'_stop',stop,raising=False)
    monkeypatch.setattr(parallel.batch,'capture',lambda job,driver:dict(job,status='NAVIGATION_FAILED'))
    row=parallel.execute(dict(source='foody',source_id='1',source_key='foody:1'))
    assert row['status']=='NAVIGATION_FAILED' and not stop.is_set()


def test_navigation_migration_archives_stop_and_preserves_all_results(tmp_path):
    root,state,jobs=fixtures(tmp_path,2)
    failed=dict(jobs['overture:0'],status='NAVIGATION_FAILED')
    state.update(status='STOPPED',error='NAVIGATION_FAILED',results=[failed])
    parallel.checkpoint(root,state)
    parallel.poc.save(root/'SOURCE_STOP.json',dict(reason='NAVIGATION_FAILED',source_key='overture:0'))
    original=(root/'parallel_state.json').read_bytes()
    parallel.accept_navigation_dlq(root)
    updated=json.loads((root/'parallel_state.json').read_text())
    assert updated['results']==[failed] and updated['pending']==['overture:1']
    assert updated['status']=='PAUSED' and not (root/'SOURCE_STOP.json').exists()
    assert list((root/'stop_reviews').glob('*/parallel_state.json'))[0].read_bytes()==original
    assert len(updated['dlq'])==1


@pytest.mark.parametrize('reason',['CHALLENGE','BLOCKED','LIMITED_VIEW','BROWSER_OR_RUNNER_ERROR'])
def test_migration_never_clears_access_or_unknown_stop(tmp_path,reason):
    root,state,jobs=fixtures(tmp_path,1)
    state.update(status='STOPPED',error=reason,results=[dict(jobs['overture:0'],status=reason)])
    parallel.checkpoint(root,state)
    parallel.poc.save(root/'SOURCE_STOP.json',dict(reason=reason,source_key='overture:0'))
    before=(root/'parallel_state.json').read_bytes()
    with pytest.raises(ValueError): parallel.accept_navigation_dlq(root)
    assert (root/'parallel_state.json').read_bytes()==before
    assert (root/'SOURCE_STOP.json').exists() and not (root/'stop_reviews').exists()


def test_failed_publication_preserves_previous_parquet_and_pending(tmp_path, monkeypatch):
    root,state,jobs=fixtures(tmp_path)
    parallel.checkpoint(root,state)
    target=root/'overture_mapping.parquet'
    before=target.read_bytes()
    def fail(*args,**kwargs):
        raise OSError('offline fixture')
    monkeypatch.setattr(parallel.batch.pq,'write_table',fail)
    with pytest.raises(OSError):
        parallel.checkpoint(root,state)
    assert target.read_bytes()==before and state['pending']==list(jobs)


def test_prepare_refuses_prior_stop_without_mutating_origin(tmp_path):
    origin=tmp_path/'origin'; origin.mkdir()
    stop=origin/'SOURCE_STOP.json'; stop.write_text('{"reason":"BLOCKED"}')
    with pytest.raises(ValueError):
        parallel.prepare(origin,tmp_path/'new')
    assert stop.read_text()=='{"reason":"BLOCKED"}' and not (tmp_path/'new').exists()


def test_four_profiles_are_not_shared_between_spawned_processes():
    # Exercise the same spawn initialization/assignment mechanism without Chrome.
    ctx=parallel.mp.get_context('spawn')
    queue=ctx.Queue(); stop=ctx.Event()
    for i in range(4):
        queue.put(str(i))
    with parallel.ProcessPoolExecutor(max_workers=4,mp_context=ctx,
            initializer=parallel.initialize,initargs=(queue,stop,{})) as pool:
        assert sorted(pool.map(abs,[-1,-2,-3,-4]))==[1,2,3,4]
