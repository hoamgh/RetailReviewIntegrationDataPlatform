"""Offline reliability telemetry and stop-policy tests."""
from experiments.tiktok_access_benchmark.reliability import aggregate, evidence, wilson


def test_unknown_not_captcha_free():
    row = evidence({'stop_reason': 'COMMENT_LIMIT', 'comments_extracted': 7})
    assert row['captcha'] == 'UNKNOWN'
    result = aggregate([row])
    assert result['captcha_free']['denominator'] == 0
    assert result['captcha_free']['rate'] is None


def test_captcha_and_http_denial():
    row = evidence({'stop_reason': 'CHALLENGE', 'stage_at_stop': 'comments'}, [
        {'status': 429, 'host_scope': 'tiktok', 'endpoint_kind': 'comments_api', 'retry_after_seconds': 60},
        {'status': 403, 'host_scope': 'other', 'endpoint_kind': 'ancillary'}])
    assert row['captcha'] == 'CONFIRMED_CHALLENGE'
    assert row['captcha_stage'] == 'comments'
    assert row['confirmed_access_denial']
    assert len(row['http_events']) == 1
    assert row['http_events'][0]['retry_after_seconds'] == 60


def test_aggregate_latency_and_persistence():
    rows = [dict(captcha='CONFIRMED_CHALLENGE', elapsed_seconds=10, comments_extracted=5,
                 persistence_verified=True, stop_reason='CHALLENGE', stage_at_stop='comments'),
            dict(captcha='UNKNOWN', elapsed_seconds=30, comments_extracted=0,
                 persistence_verified=False, stop_reason='NAVIGATION_ERROR', stage_at_stop='navigation')]
    summary = aggregate(rows)
    assert summary['latency_seconds'] == {'p50': 20, 'p95': 29}
    assert summary['comments_per_minute'] == 7.5
    assert summary['persistence_success_rate'] == .5
    assert summary['error_distribution_by_stage'] == {'comments': 1, 'navigation': 1}
    assert summary['captcha_free']['denominator'] == 1
    assert summary['captcha_free']['unknown'] == 1


def test_wilson_small_sample():
    low, high = wilson(0, 7)
    assert low == 0 and 0 < high < 1
    assert wilson(0, 0) is None


def test_an_unrelated_error_is_not_a_confirmed_access_denial():
    row = evidence({'stop_reason': 'TimeoutException'}, [
        {'status': 403, 'host_scope': 'other', 'endpoint_kind': 'ancillary'}])
    assert not row['confirmed_access_denial']
    assert row['captcha'] == 'UNKNOWN'


def test_fresh_clear_and_stale_missing_observations():
    observations = [{'stage': s, 'fresh': True, 'challenge_present': False}
                    for s in ('navigation', 'onboarding', 'comments', 'collection')]
    row = evidence({'comments_extracted': 7, 'stop_reason': 'COMMENT_LIMIT',
                    'access_observations': observations})
    assert row['captcha'] == 'CONFIRMED_CLEAR'
    assert row['observed_stages'] == ['collection', 'comments', 'navigation', 'onboarding']
    observations[-1]['fresh'] = False
    assert evidence({'comments_extracted': 7, 'access_observations': observations})['captcha'] == 'UNKNOWN'


def test_active_challenge_without_rendered_video():
    row = evidence({'access_observations': [{'stage': 'navigation', 'fresh': False,
        'active_challenge': True, 'challenge_present': True}], 'stage_at_stop': 'navigation'})
    assert row['captcha'] == 'CONFIRMED_CHALLENGE'


def test_exclusive_monotonic_timing_and_unreached_stages():
    from experiments.tiktok_access_benchmark.reliability import Telemetry
    now = [10.0]
    t = Telemetry(clock=lambda: now[0])
    t.stage('navigation')
    now[0] += 5
    t.persistence(2)
    t.stage('comments')
    now[0] += 4
    t.finish()
    assert t.times['navigation'] == 3
    assert t.times['persistence'] == 2
    assert t.times['comments'] == 4
    assert t.times['onboarding'] is None


def test_observation_requires_rendered_access_snapshot():
    from experiments.tiktok_access_benchmark.reliability import Telemetry
    t = Telemetry()
    t.stage('navigation')
    t.observe({'challenge_present': False})
    t.observe({'challenge_present': False, 'video_detail_rendered': True})
    assert [o['fresh'] for o in t.observations] == [False, True]


def test_persistence_failure_is_separate_from_collection():
    row = evidence({'comments_extracted': 7, 'persistence_verified': False,
                    'stop_reason': 'PersistenceError', 'stage_at_stop': 'persistence'})
    a = aggregate([row])
    assert a['successful_collection_runs'] == 1
    assert a['persistence_success_rate'] == 0
    assert a['error_distribution_by_stage'] == {'persistence': 1}


def test_startup_failure_is_recorded_without_false_clear(tmp_path, monkeypatch):
    from experiments.tiktok_access_benchmark import reliability, benchmark
    from experiments.tiktok_place_intelligence import probe
    monkeypatch.setattr(probe, 'validate_dom_match', lambda data: ({'video_id': '1'}, {}, {}))
    def fail(**kwargs):
        raise RuntimeError('fixture')
    monkeypatch.setattr(benchmark, 'BrowserObserver', fail)
    report = reliability.run_live([{'place': {'place_id': 'fixture'}}], tmp_path/'run', run_id='fixture')
    assert len(report['videos']) == 1
    row = report['videos'][0]
    assert row['browser_startup_success'] is False
    assert row['captcha'] == 'UNKNOWN'
    assert report['stop_reason'] == 'BROWSER_STARTUP_RuntimeError'


def test_report_outputs_and_mixed_outcomes(tmp_path):
    import json
    from experiments.tiktok_access_benchmark.reliability import write_report
    rows = [evidence({'comments_extracted': 0, 'stop_reason': 'CHALLENGE',
                      'stage_at_stop': 'comments', 'elapsed_seconds': 2}),
            evidence({'comments_extracted': 0, 'stop_reason': 'RATE_LIMITED',
                      'elapsed_seconds': 3}, [{'status': 429, 'host_scope': 'tiktok',
                      'endpoint_kind': 'comments_api', 'retry_after_seconds': 120}])]
    report = write_report(tmp_path, rows, 'OFFLINE_FIXTURE')
    assert report['aggregate']['http_429_events'] == 1
    assert report['aggregate']['captcha_free']['unknown'] == 1
    assert len((tmp_path/'per_video.jsonl').read_text().splitlines()) == 2
    assert json.loads((tmp_path/'reliability_report.json').read_text())['stop_reason'] == 'OFFLINE_FIXTURE'
    assert (tmp_path/'per_video.csv').exists() and (tmp_path/'report.md').exists()


def test_record_reconciliation_and_duplicate_detection(tmp_path):
    import json
    import sqlite3
    import pyarrow as pa
    import pyarrow.parquet as pq
    from experiments.tiktok_access_benchmark.reliability import reconcile
    database, lake = tmp_path/'state.sqlite3', tmp_path/'lake'
    lake.mkdir()
    row = {'event_id': 'e', 'video_id': 'v', 'storage_key': 'k', 'revision': 1}
    with sqlite3.connect(database) as db:
        db.executescript('CREATE TABLE tiktok_comments(video_id TEXT, storage_key TEXT, revision INTEGER); CREATE TABLE tiktok_comment_events(event_id TEXT,payload TEXT,delivered INTEGER);')
        db.execute('INSERT INTO tiktok_comments VALUES(?,?,?)', ('v', 'k', 1))
        db.execute('INSERT INTO tiktok_comment_events VALUES(?,?,?)', ('e', json.dumps(row), 1))
    pq.write_table(pa.Table.from_pylist([row]), lake/'one.parquet')
    assert reconcile(database, lake, 'v')['persistence_verified'] is True
    pq.write_table(pa.Table.from_pylist([row]), lake/'two.parquet')
    result = reconcile(database, lake, 'v')
    assert result['duplicate_event_count'] == 1 and not result['persistence_verified']


def test_same_session_stops_after_observed_429(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from experiments.tiktok_access_benchmark import reliability, benchmark, ui_flow
    from experiments.tiktok_place_intelligence import probe
    calls = []
    driver = SimpleNamespace(_is_using_uc=True, set_page_load_timeout=lambda _: None, set_script_timeout=lambda _: None)
    monkeypatch.setattr(probe, 'validate_dom_match', lambda data: (data['selected_video'], {}, {}))
    monkeypatch.setattr(benchmark, 'BrowserObserver', lambda **kw: SimpleNamespace(
        driver=driver, lifecycle=SimpleNamespace(close=lambda: calls.append('close'))))
    network = SimpleNamespace(entries=[], start=lambda: None, drain=lambda: None)
    monkeypatch.setattr(ui_flow, 'NetworkCapture', lambda d: network)
    def run(data, **kwargs):
        calls.append(data['selected_video']['video_id'])
        network.entries.append({'status': 429, 'host_scope': 'tiktok', 'endpoint_kind': 'comments_api', 'retry_after_seconds': 90})
        return dict(stop_reason='COMMENT_LIMIT', comments_in_collector=0, records=0, pending_events=0)
    monkeypatch.setattr(probe, 'run_dom_comments', run)
    monkeypatch.setattr(reliability, 'reconcile', lambda *a: {'persistence_verified': True})
    inputs = [{'selected_video': {'video_id': str(i)}, 'place': {'place_id': 'p'}} for i in range(3)]
    report = reliability.run_live(inputs, tmp_path/'run', run_id='fixture')
    assert calls == ['0', 'close']
    assert report['stop_reason'] == 'ACCESS_RESTRICTION'
    assert report['aggregate']['http_429_events'] == 1


from experiments.tiktok_place_intelligence.test_live_dom_persistence import match


def test_existing_collector_emits_measured_stages(match, tmp_path):
    from experiments.tiktok_place_intelligence.test_live_dom_persistence import Port, snapshot, card, run
    from experiments.tiktok_access_benchmark.reliability import Telemetry, reconcile
    class ObservedPort(Port):
        def snapshot(self):
            return snapshot([card()], challenge_present=False, video_detail_rendered=True)
    t = Telemetry()
    port = ObservedPort([snapshot(challenge_present=False, video_detail_rendered=True),
                         snapshot(challenge_present=False, video_detail_rendered=True),
                         snapshot([card()], challenge_present=False, video_detail_rendered=True)])
    result = run(tmp_path, match, port, telemetry=t)
    assert result['records'] == 1
    assert all(t.times[s] is not None and t.times[s] >= 0 for s in
               ('navigation', 'onboarding', 'comments', 'collection', 'persistence'))
    assert evidence({'comments_extracted': 1, 'access_observations': t.observations})['captcha'] == 'CONFIRMED_CLEAR'
    assert reconcile(tmp_path/'comments.sqlite3', tmp_path/'lake', result['video_id'])['persistence_verified']


def test_batch_integrity_and_catalog_revalidation(match, tmp_path):
    import hashlib
    import json
    import pytest
    from experiments.tiktok_access_benchmark.reliability import load_batch
    inputs = [match]
    manifest = {'batch_id': 'batch-01', 'inputs': inputs, 'inputs_sha256': hashlib.sha256(
        json.dumps(inputs, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()}
    path = tmp_path/'batch.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    assert load_batch(path)['batch_id'] == 'batch-01'
    manifest['inputs'][0]['selected_video']['caption'] = 'unreviewed'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='digest mismatch'):
        load_batch(path)


def install_study_runner(monkeypatch, *, restriction=None, at=2, persistence_ok=True):
    import json
    from types import SimpleNamespace
    from experiments.tiktok_access_benchmark import reliability, benchmark, ui_flow
    from experiments.tiktok_place_intelligence import probe
    calls, owners = [], []
    driver = SimpleNamespace(_is_using_uc=True, set_page_load_timeout=lambda _: None, set_script_timeout=lambda _: None)
    network = SimpleNamespace(entries=[], start=lambda: None, drain=lambda: None)
    monkeypatch.setattr(probe, 'validate_dom_match', lambda data: (data['selected_video'], {}, {}))
    def owner(**kw):
        owners.append('start')
        return SimpleNamespace(driver=driver, lifecycle=SimpleNamespace(close=lambda: owners.append('close')))
    monkeypatch.setattr(benchmark, 'BrowserObserver', owner)
    monkeypatch.setattr(ui_flow, 'NetworkCapture', lambda d: network)
    def run(data, **kwargs):
        index = len(calls)
        calls.append(data['selected_video']['video_id'])
        t = kwargs['telemetry']
        for stage in ('navigation', 'comments', 'collection'):
            t.stage(stage)
            t.observe({'video_detail_rendered': True, 'challenge_present': restriction == 'CHALLENGE' and index == at})
        t.finish()
        status = restriction if index == at and restriction in ('CHALLENGE', 'AUTH_GATE', 'ACCESS_DENIED') else 'COMMENT_LIMIT'
        if index == at and restriction in (403, 429):
            network.entries.append({'event': 'RESPONSE', 'host_scope': 'tiktok', 'status': restriction,
                                    'endpoint_kind': 'comments_api', 'retry_after_seconds': 120})
        output = kwargs['output']
        output.mkdir()
        (output/'summary.json').write_text(json.dumps({'videos_opened': 1, 'actions': [
            {'target': 'COMMENT_ICON', 'ok': True}, {'target': 'COMMENTS_TAB', 'ok': True}]}))
        return dict(stop_reason=status, comments_in_collector=7, records=(index+1)*7, pending_events=0)
    monkeypatch.setattr(probe, 'run_dom_comments', run)
    monkeypatch.setattr(reliability, 'reconcile', lambda *a: {'persistence_verified': persistence_ok or len(calls)-1 != at,
        'sqlite_records': 7, 'parquet_record_count': 7, 'pending_outbox_events': 0, 'duplicate_event_count': 0})
    inputs = [{'selected_video': {'video_id': str(i)}, 'place': {'place_id': 'p'}} for i in range(10)]
    return calls, owners, inputs


def test_ten_video_batch_single_session_and_report(tmp_path, monkeypatch):
    from experiments.tiktok_access_benchmark.reliability import run_live
    calls, owners, inputs = install_study_runner(monkeypatch)
    r = run_live(inputs, tmp_path/'live', run_id='offline-ten', batch_metadata={'batch_id': 'batch-01'})
    assert calls == [str(i) for i in range(10)] and owners == ['start', 'close']
    assert r['aggregate']['sqlite_records'] == r['aggregate']['parquet_records'] == 70
    assert r['aggregate']['collection_success']['numerator'] == 10
    assert r['aggregate']['captcha_free']['denominator'] == 10
    assert r['next_batch_review']['decision'] == 'GO_FOR_HUMAN_REVIEW'
    assert r['next_batch_review']['next_batch_approval_required']
    assert (tmp_path/'live/batch_checkpoint.json').exists()
    assert r['videos'][0]['comments_icon_success'] and r['videos'][0]['comments_tab_success']


import pytest


@pytest.mark.parametrize('restriction', ['CHALLENGE', 'AUTH_GATE', 'ACCESS_DENIED', 403, 429])
def test_ten_video_batch_stops_on_first_restriction(tmp_path, monkeypatch, restriction):
    from experiments.tiktok_access_benchmark.reliability import run_live
    calls, owners, inputs = install_study_runner(monkeypatch, restriction=restriction)
    r = run_live(inputs, tmp_path/'live', run_id='offline-stop')
    assert len(calls) == 3 and owners == ['start', 'close']
    assert r['stop_reason'] == 'ACCESS_RESTRICTION'
    assert r['unattempted_videos'] == 7
    assert r['aggregate']['completed_videos_before_first_restriction'] == 2
    assert r['next_batch_review']['decision'] == 'NO_GO'
    if restriction == 429:
        assert r['videos'][-1]['http_events'][0]['retry_after_seconds'] == 120


def test_ten_video_batch_stops_on_persistence_mismatch(tmp_path, monkeypatch):
    from experiments.tiktok_access_benchmark.reliability import run_live
    calls, owners, inputs = install_study_runner(monkeypatch, persistence_ok=False)
    r = run_live(inputs, tmp_path/'live', run_id='offline-persistence')
    assert len(calls) == 3 and owners == ['start', 'close']
    assert r['stop_reason'] == 'PERSISTENCE_UNRECONCILED'
    assert r['next_batch_review']['decision'] == 'NO_GO'
    assert r['aggregate']['persistence_reliability']['numerator'] == 2


def test_eleven_video_batch_rejected_before_browser(tmp_path, monkeypatch):
    from experiments.tiktok_access_benchmark.reliability import run_live
    calls, owners, inputs = install_study_runner(monkeypatch)
    with pytest.raises(ValueError, match='1..10'):
        run_live(inputs+[inputs[0]], tmp_path/'live', run_id='offline-limit')
    assert calls == owners == []


def test_max_scrolls_is_bounded_partial_not_access_restriction(tmp_path,monkeypatch):
    from experiments.tiktok_access_benchmark import reliability
    from experiments.tiktok_place_intelligence import probe
    calls,owners,inputs=install_study_runner(monkeypatch)
    original=probe.run_dom_comments
    def bounded(data,**kw):
        result=original(data,**kw)
        result['stop_reason']='MAX_SCROLLS'
        return result
    monkeypatch.setattr(probe,'run_dom_comments',bounded)
    report=reliability.run_live(inputs,tmp_path/'batch',run_id='offline-bounded')
    assert len(calls)==10
    assert report['aggregate']['stop_reason_counts']['MAX_SCROLLS']==10
    assert report['aggregate']['data_completeness']['full_top_level_coverage_confirmed']==0
    assert report['aggregate']['data_completeness']['reply_coverage']=='NOT_COLLECTED'
    assert all(row['max_scrolls']==0 for row in report['videos'])


def test_reconciliation_latest_observation_is_not_a_new_content_revision(tmp_path):
    import json
    import sqlite3
    import pyarrow as pa
    import pyarrow.parquet as pq
    from experiments.tiktok_access_benchmark.reliability import reconcile
    database=tmp_path/'state.sqlite3';lake=tmp_path/'lake';lake.mkdir()
    row={'event_id':'e','video_id':'v','storage_key':'k','revision':1,'content_hash':'hash',
         'text':'same content','observed_at':'2026-10-12T08:07:22+00:00','run_id':'run-1','provenance':'old'}
    with sqlite3.connect(database) as db:
        db.executescript('CREATE TABLE tiktok_comments(video_id TEXT,storage_key TEXT,revision INTEGER,content_hash TEXT,text TEXT,observed_at TEXT,run_id TEXT,provenance TEXT); CREATE TABLE tiktok_comment_events(event_id TEXT,payload TEXT,delivered INTEGER);')
        db.execute('INSERT INTO tiktok_comments VALUES(?,?,?,?,?,?,?,?)',
                   ('v','k',1,'hash','same content','2026-10-12T08:07:29+00:00','run-2','new'))
        db.execute('INSERT INTO tiktok_comment_events VALUES(?,?,?)',('e',json.dumps(row),1))
    pq.write_table(pa.Table.from_pylist([row]),lake/'event.parquet')
    assert reconcile(database,lake,'v')['persistence_verified'] is True
    with sqlite3.connect(database) as db:db.execute("UPDATE tiktok_comments SET text='corrupted content'")
    assert reconcile(database,lake,'v')['persistence_verified'] is False


def test_empty_comment_timeout_is_local_but_restrictions_and_persistence_still_stop():
    from experiments.tiktok_access_benchmark.reliability import batch_stop
    row={'stop_reason':'COMMENT_CARDS_TIMEOUT','persistence_verified':True,'confirmed_access_denial':False}
    assert batch_stop(row) is None
    assert batch_stop({**row,'confirmed_access_denial':True})=='ACCESS_RESTRICTION'
    assert batch_stop({**row,'persistence_verified':False})=='PERSISTENCE_UNRECONCILED'
    assert batch_stop({**row,'stop_reason':'UNKNOWN_403','confirmed_access_denial':True})=='ACCESS_RESTRICTION'


def test_local_empty_result_stays_unknown_and_visible_in_failure_metrics():
    row=evidence({'stop_reason':'COMMENT_CARDS_TIMEOUT','persistence_verified':True,
        'comments_extracted':0,'comments_api_empty_response_observed':True,'stage_at_stop':'comments'})
    result=aggregate([row])
    assert row['captcha']=='UNKNOWN'
    assert result['captcha_free']['denominator']==0
    assert result['collection_success']['numerator']==0
    assert result['failure_reasons']=={'COMMENT_CARDS_TIMEOUT':1}
    assert result['empty_comment_responses_observed']==1 and result['video_local_error_count']==1


def test_startup_challenge_is_reported_without_fabricating_video_trials(tmp_path):
    from experiments.tiktok_access_benchmark.reliability import write_report
    import json
    auth={'stage':'AUTHENTICATION','state':'AUTH_REQUIRED','reason':'MANUAL_VERIFICATION_REQUIRED',
          'challenge_signal_observed':True,'manual_verification_waits':0,
          'rate_scope':'EXCLUDED_FROM_PER_VIDEO_DENOMINATOR'}
    report=write_report(tmp_path,[],'CHALLENGE',session_authentication=auth)
    assert report['session_authentication']==auth
    assert report['aggregate']['attempted_videos']==0
    assert report['aggregate']['captcha_free']['denominator']==0
    assert report['aggregate']['captcha_free']['rate'] is None
    assert json.loads((tmp_path/'reliability_report.json').read_text(encoding='utf-8'))['session_authentication']==auth
    assert 'Authentication/startup observations' in (tmp_path/'report.md').read_text(encoding='utf-8')


@pytest.mark.parametrize('kwargs', [{'limit':51},{'comment_scrolls':11}])
def test_reliability_rejects_out_of_bounds_before_browser(tmp_path,kwargs):
    from experiments.tiktok_access_benchmark.reliability import run_live
    with pytest.raises(ValueError):run_live([{}],tmp_path/'run',run_id='fixture',**kwargs)


def test_reliability_to_real_dom_runner_accepts_expanded_bounds(match,tmp_path):
    from types import SimpleNamespace
    from experiments.tiktok_access_benchmark.reliability import run_live
    from experiments.tiktok_place_intelligence.test_live_dom_persistence import Port,snapshot,card
    class ExistingPort(Port):
        def snapshot(self):return snapshot([card(f'Synthetic {i}',f'wide-{i}') for i in range(8)])
    port=ExistingPort([snapshot([card(f'Synthetic {i}',f'wide-{i}') for i in range(8)])])
    port.driver._is_using_uc=True
    port.network=SimpleNamespace(entries=[],request_count=0,drain=lambda:None)
    result=run_live([match],tmp_path/'run',run_id='expanded-fixture',existing_port=port,
                    limit=50,comment_scrolls=10,reply_limit=20,reply_expand_limit=5)
    assert result['stop_reason']=='TEST_SET_COMPLETED'
    assert result['aggregate']['sqlite_records']==8
    assert result['aggregate']['parquet_records']==8
    assert result['aggregate']['pending_outbox_events']==0
