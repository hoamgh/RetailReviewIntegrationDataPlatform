"""Bounded reliability measurements around the existing DOM + SQLite runner.

No requests are made unless --live is explicitly supplied. Input MATCH files must
be reviewed in advance and are revalidated by the existing runner.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
import hashlib
import re
from collections import Counter


class Telemetry:
    """Exclusive monotonic stage intervals; persistence is nested and subtracted."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.times = dict.fromkeys(('browser_startup', 'navigation', 'onboarding', 'comments', 'collection', 'persistence'))
        self.observations = []
        self.current = None
        self.started = None
        self.nested = 0

    def add_duration(self, stage, duration):
        self.times[stage] = (self.times[stage] or 0) + duration
        if stage != self.current and self.current is not None:
            self.nested += duration

    def stage(self, name):
        self.finish()
        self.last_stage = name
        self.current, self.started, self.nested = name, self.clock(), 0

    def finish(self):
        if self.current is not None:
            duration = max(0, self.clock()-self.started-self.nested)
            self.times[self.current] = (self.times[self.current] or 0)+duration
            self.current = None

    def persistence(self, duration):
        self.add_duration('persistence', duration)

    def observe(self, snapshot):
        valid = (isinstance(snapshot.get('challenge_present'), bool) and
                 bool(snapshot.get('video_detail_rendered') or snapshot.get('cards')) and
                 not snapshot.get('interstitial_pending'))
        self.observations.append({'stage': self.current, 'fresh': valid,
            'observed_monotonic': self.clock(), 'challenge_present': snapshot.get('challenge_present'),
            'comments_visible': bool(snapshot.get('cards') or snapshot.get('comment_panel_present')),
            'active_challenge': snapshot.get('challenge_present') is True})


class Resources:
    """Sample runner process tree. CPU can exceed 100% across multiple cores."""
    def __init__(self):
        self.stop = threading.Event()
        self.peak_rss = self.peak_cpu = None
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        try:
            import psutil
            root, known = psutil.Process(), {}
            while not self.stop.is_set():
                rss = cpu = 0
                for process in [root]+root.children(recursive=True):
                    try:
                        key = (process.pid, process.create_time())
                        if key not in known:
                            known[key] = process
                            process.cpu_percent()
                        else:
                            cpu += known[key].cpu_percent()
                        rss += process.memory_info().rss
                    except psutil.Error:
                        continue
                self.peak_rss = max(self.peak_rss or 0, rss)
                self.peak_cpu = max(self.peak_cpu or 0, cpu)
                self.stop.wait(.25)
        except (ImportError, OSError):
            pass

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=2)
        return {'peak_rss_bytes': self.peak_rss, 'peak_cpu_percent': self.peak_cpu,
                'scope': 'runner process tree, 250ms samples; pre-existing processes excluded'}


def reconcile(database, lake, video_id):
    """Compare exact event payloads and latest content revisions, scoped by video.

    SQLite UNCHANGED observations update observed_at/run_id/provenance without
    creating a revision event. These observation fields are not current-content
    equality checks; their historical values remain verified against the outbox.
    """
    import pyarrow.parquet as pq
    with sqlite3.connect(f'file:{Path(database).resolve().as_posix()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        comments = [dict(r) for r in db.execute('SELECT * FROM tiktok_comments WHERE video_id=?', (video_id,))]
        events = [dict(r) for r in db.execute('SELECT * FROM tiktok_comment_events')]
    expected = {e['event_id']: json.loads(e['payload']) for e in events
                if json.loads(e['payload'])['video_id'] == video_id}
    actual = [r for f in lake.rglob('*.parquet') for r in pq.read_table(f, partitioning=None).to_pylist()
              if r['video_id'] == video_id]
    counts = Counter(r['event_id'] for r in actual)
    pending = sum(not e['delivered'] for e in events if e['event_id'] in expected)
    verified = (pending == 0 and counts == Counter(expected.keys()) and
                all(expected[r['event_id']] == r for r in actual) and
                all(any(r['storage_key'] == c['storage_key'] and r['revision'] == c['revision'] and
                        all(r[k] == v for k, v in c.items() if k not in
                            ('observed_at', 'run_id', 'provenance')) for r in actual) for c in comments))
    return {'sqlite_records': len(comments), 'parquet_record_count': len(actual),
            'pending_outbox_events': pending, 'duplicate_event_count': sum(n-1 for n in counts.values()),
            'persistence_verified': verified}

BLOCKED = {'CHALLENGE', 'ACCESS_DENIED', 'RATE_LIMITED', 'UNKNOWN_403',
           'REQUIRED_ENDPOINT_ERROR', 'APPLICATION_ERROR', 'CHALLENGE_TIMEOUT',
           'CHALLENGE_ABORTED_BY_USER', 'CHALLENGE_SESSION_CHANGED',
           'CHALLENGE_VIDEO_CHANGED', 'AUTH_GATE', 'LOGIN_OR_CONSENT_GATE'}
REQUIRED = {'document', 'comments_api', 'video_detail_api', 'search_api'}
MAX_BATCH_VIDEOS = 10
VIDEO_LOCAL_STOPS = {'COMMENT_CARDS_TIMEOUT', 'COMMENT_SURFACE_UNAVAILABLE'}
NORMAL_STOPS = {'COMMENT_LIMIT', 'HAS_MORE_ZERO', 'MAX_SCROLLS', 'BOUNDED_NO_GROWTH', 'NO_SAFE_COMMENT_SCROLL_CONTAINER', 'EXPLICIT_END_MARKER', 'EXPLICIT_EMPTY_MARKER'}


def batch_stop(row):
    if row.get('confirmed_access_denial'):
        return 'ACCESS_RESTRICTION'
    if row.get('persistence_verified') is not True:
        return 'PERSISTENCE_UNRECONCILED'
    if row.get('stop_reason') in VIDEO_LOCAL_STOPS:
        return None
    if row.get('stop_reason') not in NORMAL_STOPS:
        return row.get('stop_reason') or 'UNKNOWN_STOP'
    return None


def outcome_interval(successes, total):
    return {'numerator': successes, 'denominator': total,
            'rate': successes/total if total else None, 'wilson_95_interval': wilson(successes, total)}


def load_batch(path):
    """Offline integrity and place-association validation, never browser startup."""
    from experiments.tiktok_place_intelligence.probe import validate_dom_match
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    inputs = data['inputs']
    if not 1 <= len(inputs) <= MAX_BATCH_VIDEOS:
        raise ValueError('One batch must contain 1..10 videos')
    encoded = json.dumps(inputs, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    if data.get('inputs_sha256') != hashlib.sha256(encoded).hexdigest():
        raise ValueError('Batch input digest mismatch; review changed dataset')
    ids = [validate_dom_match(item)[0]['video_id'] for item in inputs]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate video IDs in batch')
    if data.get('batch_id') not in ('batch-01', 'batch-02', 'batch-03'):
        raise ValueError('Invalid study batch ID')
    return data


def prepare_study(output):
    """Review saved public discovery evidence; no discovery or live availability claims."""
    from experiments.tiktok_place_intelligence.probe import validate_dom_match
    from experiments.tiktok_place_discovery.probe import score_video, normalized
    root = Path(__file__).resolve().parents[2]
    source_root = root/'experiments/tiktok_place_discovery/output'
    place_path = source_root/'20261008T032106.973735Z/canonical_place.json'
    place = json.loads(place_path.read_text(encoding='utf-8'))
    sources = [source_root/'20261008T032106.973735Z/unique_videos.json',
               source_root/'20261008T034216.136593Z-pagination/pagination_videos.json']
    selected, exclusions = {}, []
    fields = ('video_id', 'creator_username', 'caption', 'hashtags', 'observed_at', 'published_at', 'comment_count')
    for source in sources:
        for record in json.loads(source.read_text(encoding='utf-8')):
            match = score_video(record, place)
            reason = None
            if match['decision'] != 'MATCH' or 'ADDRESS_MATCH' not in match['signals']:
                reason = 'NOT_ADDRESS_QUALIFIED_MATCH'
            elif any(term in normalized(record.get('caption')) for term in ('go vap', 'quy nhon', 'nam dinh', 'y yen')):
                reason = 'CONFLICTING_LOCATION'
            elif not re.fullmatch(r'[\w.]+', record.get('creator_username') or '') or not str(record.get('video_id', '')).isdigit():
                reason = 'MISSING_PUBLIC_NAVIGATION_IDENTITY'
            if reason:
                exclusions.append({'source': str(source.relative_to(root)), 'video_id': record.get('video_id'), 'reason': reason})
                continue
            video = {key: record.get(key) for key in fields}
            video.update(place_id=place['place_id'], decision='MATCH', matching_signals=match['signals'],
                navigation_target=f"https://www.tiktok.com/@{video['creator_username']}/video/{video['video_id']}",
                video_url_provenance='DERIVED_FROM_OBSERVED_PUBLIC_VIDEO_ID_AND_CREATOR_HANDLE',
                public_availability='UNVERIFIED_CURRENT')
            item = {'selected_video': video, 'place': {key: place[key] for key in
                    ('place_id', 'canonical_name', 'address', 'ward', 'brand', 'search_category')},
                    'review': {'source_file': str(source.relative_to(root)),
                        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                        'place_source': str(place_path.relative_to(root)),
                        'place_source_sha256': hashlib.sha256(place_path.read_bytes()).hexdigest(),
                        'association': 'CATALOG_NAME_ADDRESS_AND_CAPTION_HASHTAG_EVIDENCE',
                        'current_availability': 'NOT_LIVE_CHECKED'}}
            validate_dom_match(item)
            selected[video['video_id']] = item
    seed = root/'output/tiktok-smoke-test/verified_popeyes_match.json'
    original = json.loads(seed.read_text(encoding='utf-8'))
    validate_dom_match(original)
    video = {key: original['selected_video'].get(key) for key in fields}
    video.update(place_id=original['place']['place_id'], decision='MATCH',
        navigation_target=original['selected_video']['navigation_target'],
        video_url_provenance='PREVIOUS_VERIFIED_LIVE_NAVIGATION', public_availability='UNVERIFIED_CURRENT')
    selected[video['video_id']] = {'selected_video': video, 'place': original['place'],
        'review': {'source_file': str(seed.relative_to(root)), 'source_sha256': hashlib.sha256(seed.read_bytes()).hexdigest(),
                  'association': 'CATALOG_NAME_ADDRESS_AND_CAPTION_EVIDENCE', 'current_availability': 'NOT_LIVE_CHECKED'}}
    inputs = sorted(selected.values(), key=lambda item: item['selected_video']['video_id'])[:30]
    if output.exists():
        raise ValueError('Study dataset output must be fresh')
    output.mkdir(parents=True)
    batches = []
    for index in range(3):
        subset = inputs[index*10:(index+1)*10]
        if not subset:
            continue
        batch_id = f'batch-{index+1:02d}'
        encoded = json.dumps(subset, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        manifest = {'study_id': output.name, 'batch_id': batch_id, 'inputs': subset,
            'inputs_sha256': hashlib.sha256(encoded).hexdigest(), 'max_comments': 7,
            'session_policy': 'ONE_HEADED_UC_SESSION_SEQUENTIAL_NO_GATE_RETRY',
            'approval': 'REQUIRES_BATCH_SPECIFIC_USER_APPROVAL', 'status': 'PREPARED_NOT_EXECUTED'}
        path = output/f'{batch_id}.json'
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
        load_batch(path)
        batches.append({'batch_id': batch_id, 'dataset': path.name, 'videos': len(subset), 'inputs_sha256': manifest['inputs_sha256']})
    study = {'study_id': output.name, 'requested_maximum': 30, 'qualified_candidates': len(inputs),
        'shortfall': 30-len(inputs), 'batches': batches, 'exclusions': exclusions,
        'limitations': ['Historical public discovery; current availability is unverified.',
                       '26 candidates from one Leetea creator and one Popeyes video; strong selection and clustering bias.',
                       'Derived Leetea navigation URLs are not observed hrefs or live-verified URLs.',
                       'No fabricated IDs, no duplicate padding, no automatic next batch.']}
    (output/'study_manifest.json').write_text(json.dumps(study, indent=2, ensure_ascii=False), encoding='utf-8')
    with (output/'candidates.csv').open('w', newline='', encoding='utf-8') as handle:
        fields = ('batch_id', 'video_id', 'place_id', 'canonical_name', 'address', 'navigation_target', 'caption', 'url_provenance', 'observed_at')
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for i, item in enumerate(inputs):
            video, poi = item['selected_video'], item['place']
            writer.writerow({'batch_id': f'batch-{i//10+1:02d}', 'video_id': video['video_id'],
                'place_id': poi['place_id'], 'canonical_name': poi['canonical_name'], 'address': poi['address'],
                'navigation_target': video['navigation_target'], 'caption': video['caption'],
                'url_provenance': video['video_url_provenance'], 'observed_at': video['observed_at']})
    return study


def evidence(row, network_entries=()):
    """Missing evidence stays unknown; an absent challenge marker is not proof."""
    reason = str(row.get('stop_reason') or '')
    summary = row.get('crawler_summary') or {}
    actions = summary.get('actions') or []
    signals = summary.get('challenge_details') or summary.get('challenge_markers') or summary.get('challenge_text_signals')
    observations = row.get('access_observations') or []
    valid = [s for s in observations if s.get('fresh') is True and s.get('stage') in
             ('navigation', 'onboarding', 'comments', 'collection') and
             isinstance(s.get('challenge_present'), bool)]
    challenged = bool(reason == 'CHALLENGE' or reason.startswith('CHALLENGE_') or
                      any(s.get('active_challenge') is True for s in observations) or
                      any(s['challenge_present'] for s in valid))
    reached = {s['stage'] for s in valid}
    complete = row.get('comments_extracted', 0) > 0 and {'navigation', 'comments', 'collection'} <= reached
    captcha = ('CONFIRMED_CHALLENGE' if challenged else 'CONFIRMED_CLEAR' if complete and
               all(not s['challenge_present'] for s in valid) else 'UNKNOWN')
    hits = []
    for entry in network_entries:
        if entry.get('event') == 'DOCUMENT_REQUEST':
            continue
        status = entry.get('status')
        if status not in (403, 429) or entry.get('host_scope') not in ('tiktok',):
            continue
        hits.append({'status': status, 'endpoint_category': entry.get('endpoint_kind', 'unknown'),
                     'hostname': entry.get('hostname'), 'pathname': entry.get('pathname'),
                     'retry_after_seconds': entry.get('retry_after_seconds')})
    clicked = any(a.get('target') in ('COMMENT_ICON', 'COMMENTS_TAB') and a.get('ok') for a in actions)
    recovered = sum(a.get('target') == 'MANUAL_CHALLENGE_RECOVERY' for a in actions)
    def action_success(target):
        matches = [a for a in actions if a.get('target') == target]
        return bool(matches[-1].get('ok')) if matches else None
    row.update(comments_icon_success=action_success('COMMENT_ICON'),
        comments_tab_success=action_success('COMMENTS_TAB'),
        browser_backend='seleniumbase-uc', observed_stages=sorted(reached),
        comments_panel_opened=(
        'yes' if row.get('comments_extracted', 0) > 0 or any(o.get('comments_visible') for o in valid) else 'no' if 'collection' in reached else 'unknown'),
        comments_click_attempted=any(a.get('target') in ('COMMENT_ICON', 'COMMENTS_TAB') for a in actions), captcha=captcha, captcha_stage=(
        next((s['stage'] for s in valid if s['challenge_present']), row.get('stage_at_stop', 'unknown'))
        if captcha == 'CONFIRMED_CHALLENGE' else None),
        manual_verification_count=recovered, http_events=hits,
        confirmed_access_denial=(challenged or reason in BLOCKED or any(
            h['status'] == 429 or (h['status'] == 403 and h['endpoint_category'] in REQUIRED | {'unknown'})
            for h in hits)))
    if row['confirmed_access_denial'] and captcha == 'CONFIRMED_CLEAR':
        row['captcha'] = 'UNKNOWN'
    return row


def quantile(values, fraction):
    if not values:
        return None
    sorted_values = sorted(values)
    position = (len(values) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def wilson(successes, total, z=1.96):
    if not total:
        return None
    p = successes / total
    denom = 1 + z*z/total
    centre = (p + z*z/(2*total)) / denom
    margin = z * math.sqrt((p*(1-p) + z*z/(4*total))/total) / denom
    return [max(0, centre-margin), min(1, centre+margin)]


def aggregate(rows):
    known = [r for r in rows if r.get('captcha') in ('CONFIRMED_CHALLENGE', 'CONFIRMED_CLEAR')]
    free = sum(r['captcha'] == 'CONFIRMED_CLEAR' for r in known)
    durations = [r['elapsed_seconds'] for r in rows if isinstance(r.get('elapsed_seconds'), (int, float))]
    total_time = sum(durations)
    persisted = [r for r in rows if r.get('persistence_verified') is not None]
    errors = {}
    for r in rows:
        if r.get('stop_reason') not in NORMAL_STOPS or r.get('persistence_verified') is False:
            key = r.get('stage_at_stop') or 'unknown'
            errors[key] = errors.get(key, 0) + 1
    successful = sum(r.get('comments_extracted', 0) > 0 and r.get('persistence_verified') is True
                     and not r.get('confirmed_access_denial') and r.get('stop_reason') in NORMAL_STOPS for r in rows)
    first_restriction = next((i for i, r in enumerate(rows) if r.get('confirmed_access_denial')), None)
    http_observed = [r for r in rows if r.get('network_observation') == 'OBSERVED']
    return {'stop_reason_counts': dict(Counter(r.get('stop_reason') or 'UNKNOWN' for r in rows)),
            'data_completeness': {'full_top_level_coverage_confirmed': 0,
                'reply_coverage': 'PARTIAL_VISIBLE' if any(r.get('reply_coverage') == 'PARTIAL_VISIBLE' for r in rows) else 'NOT_COLLECTED',
                'replies_collected': sum(r.get('replies_collected',0) for r in rows),
                'bounded_partial_runs': sum(r.get('stop_reason') in ('COMMENT_LIMIT', 'MAX_SCROLLS') for r in rows)},
            'successful_reconciled_collection_runs': successful,
            'collection_success': outcome_interval(successful, len(rows)),
            'comment_access_success': outcome_interval(sum(r.get('comments_panel_opened') == 'yes' for r in rows), len(rows)),
            'completed_videos_before_first_restriction': (sum(batch_stop(r) is None for r in rows[:first_restriction]) if first_restriction is not None else None),
            'first_restriction_video_id': rows[first_restriction].get('video_id') if first_restriction is not None else None,
            'video_local_error_count': sum(r.get('stop_reason') in VIDEO_LOCAL_STOPS for r in rows),
            'empty_comment_responses_observed': sum(r.get('comments_api_empty_response_observed') is True for r in rows),
            'failure_reasons': dict(Counter(r.get('stop_reason') if r.get('stop_reason') in VIDEO_LOCAL_STOPS else batch_stop(r) for r in rows if r.get('stop_reason') in VIDEO_LOCAL_STOPS or batch_stop(r))),
            'network_observed_videos': len(http_observed), 'network_unobserved_videos': len(rows)-len(http_observed),
            'http_incidence': {str(status): outcome_interval(sum(any(e['status'] == status for e in r.get('http_events', [])) for r in http_observed), len(http_observed)) for status in (403, 429)},
            'persistence_reliability': outcome_interval(sum(r['persistence_verified'] is True for r in persisted), len(persisted)),
            'sqlite_records': sum(r.get('sqlite_records', 0) for r in rows),
            'parquet_records': sum(r.get('parquet_record_count', 0) for r in rows),
            'pending_outbox_events': sum(r.get('pending_outbox_events', 0) for r in rows),
            'stage_latency_seconds': {stage: {'p50': quantile([r.get('stage_times_seconds', {}).get(stage) for r in rows if r.get('stage_times_seconds', {}).get(stage) is not None], .5), 'p95': quantile([r.get('stage_times_seconds', {}).get(stage) for r in rows if r.get('stage_times_seconds', {}).get(stage) is not None], .95)} for stage in ('browser_startup', 'navigation', 'onboarding', 'comments', 'collection', 'persistence')},
            'videos_per_hour': len(rows)*3600/total_time if total_time else None,
            'captcha_counts': {kind: sum(r.get('captcha', 'UNKNOWN') == kind for r in rows)
                              for kind in ('CONFIRMED_CLEAR', 'CONFIRMED_CHALLENGE', 'UNKNOWN')},
            'duplicate_event_count': sum(r.get('duplicate_event_count', 0) for r in rows),
            'attempted_videos': len(rows),
            'opened_videos': sum(r.get('video_navigation_success') is True for r in rows),
            'comment_accessible_videos': sum(r.get('comments_panel_opened') == 'yes' for r in rows),
            'successful_collection_runs': sum(r.get('comments_extracted', 0) > 0 for r in rows),
            'confirmed_captcha_events': sum(r.get('captcha') == 'CONFIRMED_CHALLENGE' for r in rows),
            'captcha_events_by_stage': {stage: sum(r.get('captcha') == 'CONFIRMED_CHALLENGE' and
                r.get('captcha_stage') == stage for r in rows) for stage in
                ('navigation', 'onboarding', 'comments', 'collection', 'unknown')},
            'captcha_free': {'numerator': free, 'denominator': len(known),
                             'unknown': len(rows)-len(known), 'rate': free/len(known) if known else None,
                             'wilson_95_interval': wilson(free, len(known))},
            'http_429_events': sum(e['status'] == 429 for r in rows for e in r.get('http_events', [])),
            'http_403_events': sum(e['status'] == 403 for r in rows for e in r.get('http_events', [])),
            'http_errors_by_endpoint': {kind: {str(status): sum(e['status'] == status and
                e['endpoint_category'] == kind for r in rows for e in r.get('http_events', []))
                for status in (403, 429)} for kind in sorted({e['endpoint_category']
                for r in rows for e in r.get('http_events', [])})},
            'comments_per_minute': sum(r.get('comments_extracted', 0) for r in rows)*60/total_time if total_time else None,
            'latency_seconds': {'p50': quantile(durations, .5), 'p95': quantile(durations, .95)},
            'persistence_success_rate': (sum(r['persistence_verified'] for r in persisted)/len(persisted)
                                         if persisted else None),
            'error_distribution_by_stage': errors,
            'observed_captcha_outcomes': len(known), 'unknown_captcha_outcomes': len(rows)-len(known),
            'warning': 'Selected, place/creator-clustered sequential sample; Wilson intervals are descriptive and assume independent trials. Stop-on-restriction censors later videos. Separate browser batches are operationally independent, not statistically independent. Network incidence covers captured evidence only, not all requests. No universal TikTok rate limit or complete coverage inferred.'}


def write_report(output, rows, stop_reason, *, session_authentication=None):
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output/'run_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    planned = manifest.get('planned_videos', len(rows))
    ready = (stop_reason == 'TEST_SET_COMPLETED' and len(rows) == planned and bool(rows) and
             all(batch_stop(row) is None and row.get('captcha') == 'CONFIRMED_CLEAR' for row in rows))
    review = {'decision': 'GO_FOR_HUMAN_REVIEW' if ready else 'NO_GO',
              'next_batch_approval_required': True, 'automatic_next_batch': False,
              'reason': 'Completed and reconciled; a separate approval is still required.' if ready else
                        'Incomplete, restricted, unknown CAPTCHA evidence or unreconciled persistence; inspect artifacts.'}
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'stop_reason': stop_reason,
              'manifest': manifest, 'planned_videos': planned, 'unattempted_videos': max(0, planned-len(rows)),
              'next_batch_review': review, 'videos': rows, 'aggregate': aggregate(rows)}
    if session_authentication is not None:
        report['session_authentication'] = dict(session_authentication)
    temp = output/'reliability_report.json.tmp'
    temp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    temp.replace(output/'reliability_report.json')
    fields = ('video_id', 'place_id', 'video_view_count', 'video_like_count', 'video_stats_observed_at', 'association_status', 'browser_startup_success', 'video_navigation_success',
              'comments_panel_opened', 'comments_icon_success', 'comments_tab_success', 'captcha', 'captcha_stage', 'comments_extracted',
              'sqlite_records', 'parquet_record_count', 'pending_outbox_events',
              'elapsed_seconds', 'stage_at_stop', 'stop_reason', 'stage_times_seconds', 'resources', 'persistence_verified', 'duplicate_event_count', 'observed_stages', 'http_events')
    csv_tmp = output/'per_video.csv.tmp'
    with csv_tmp.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in fields} for row in rows)
    csv_tmp.replace(output/'per_video.csv')
    jsonl_tmp = output/'per_video.jsonl.tmp'
    jsonl_tmp.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows), encoding='utf-8')
    jsonl_tmp.replace(output/'per_video.jsonl')
    a = report['aggregate']
    md = (f'# TikTok reliability — {stop_reason}\n\n'
          f"Attempted: {a['attempted_videos']}; collected: {a['successful_collection_runs']}.\n\n"
          f"Planned: {planned}; unattempted: {report['unattempted_videos']}; reconciled collection success: {a['collection_success']}.\n\n"
          f"Confirmed CAPTCHA-free: {a['captcha_free']['numerator']}/{a['captcha_free']['denominator']}; "
          f"unknown: {a['captcha_free']['unknown']}; 95% Wilson CI: {a['captcha_free']['wilson_95_interval']}.\n\n"
          f"Confirmed CAPTCHA: {a['confirmed_captcha_events']} by stage {a['captcha_events_by_stage']}.\n\n"
          f"HTTP 403/429 by endpoint: {a['http_errors_by_endpoint']}.\n\n"
          f"p50/p95 elapsed seconds: {a['latency_seconds']}; comments/min: {a['comments_per_minute']}.\n\n"
          f"Persistence success: {a['persistence_success_rate']}; duplicate events: {a['duplicate_event_count']}.\n\n"
          f"Persistence interval: {a['persistence_reliability']}; SQLite/Parquet: {a['sqlite_records']}/{a['parquet_records']}; pending: {a['pending_outbox_events']}.\n\n"
          f"HTTP per-video observed incidence: {a['http_incidence']}; network unobserved videos: {a['network_unobserved_videos']}.\n\n"
          f"Stop reasons: {a['stop_reason_counts']}; completeness: {a['data_completeness']}.\n\n"
          f"Completed before first restriction: {a['completed_videos_before_first_restriction']}; failures: {a['failure_reasons']}.\n\n"
          f"Next batch: {review['decision']}. {review['reason']} No automatic next batch.\n\n"
          f"Stage latencies: {a['stage_latency_seconds']}; videos/hour: {a['videos_per_hour']}.\n\n"
          f"Per-video reconciliation and resources: {[(r.get('video_id'), r.get('sqlite_records'), r.get('parquet_record_count'), r.get('pending_outbox_events'), r.get('resources')) for r in rows]}.\n\n{a['warning']}\n"
          'COMMENT_LIMIT is not full coverage. Unobserved requests and challenges are unknown.\n')
    if session_authentication is not None:
        md += '\nAuthentication/startup observations (excluded from per-video CAPTCHA rate): ' + json.dumps(session_authentication, ensure_ascii=False) + '\n'
    md_tmp = output/'report.md.tmp'
    md_tmp.write_text(md, encoding='utf-8')
    md_tmp.replace(output/'report.md')
    checkpoint = {'study_id': manifest.get('study_id'), 'batch_id': manifest.get('batch_id'),
                  'run_id': manifest.get('run_id'), 'inputs_sha256': manifest.get('inputs_sha256'),
                  'attempted_video_ids': [row.get('video_id') for row in rows],
                  'planned_videos': planned, 'stop_reason': stop_reason, 'review': review,
                  'pending_outbox_events': a['pending_outbox_events'], 'updated_at': report['created_at'],
                  'resume_policy': 'Diagnostic checkpoint only; no automatic browser replacement or retry.'}
    tmp = output/'batch_checkpoint.json.tmp'
    tmp.write_text(json.dumps(checkpoint, indent=2), encoding='utf-8')
    tmp.replace(output/'batch_checkpoint.json')
    return report


def run_live(inputs, output, *, run_id, limit=7, driver_path=None, batch_metadata=None, existing_port=None, persistence_root=None, allow_query_candidates=False, comment_scrolls=0, reply_limit=0, reply_expand_limit=0):
    """Single headed UC browser, sequential existing runner calls, no retries."""
    from experiments.tiktok_place_intelligence.probe import validate_dom_match, run_dom_comments
    from experiments.tiktok_access_benchmark.benchmark import BrowserObserver
    from experiments.tiktok_access_benchmark.ui_flow import NetworkCapture
    from experiments.tiktok_comment_dom.probe import SeleniumDOMPort

    if not 1 <= len(inputs) <= MAX_BATCH_VIDEOS or not 1 <= limit <= 50:
        raise ValueError('Limit to 1..10 reviewed videos and 1..50 top-level comments each')
    if type(comment_scrolls) is not int or not 0<=comment_scrolls<=10:
        raise ValueError('Comment scroll budget must be 0..10')
    if not isinstance(reply_limit,int) or isinstance(reply_limit,bool) or not 0<=reply_limit<=50:
        raise ValueError('Reply limit must be 0..50')
    if not isinstance(reply_expand_limit,int) or isinstance(reply_expand_limit,bool) or not 0<=reply_expand_limit<=5 or (reply_expand_limit and not reply_limit):
        raise ValueError('Reply expansion requires reply_limit>0 and expansion limit0..5')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', run_id):
        raise ValueError('Invalid run ID')
    ids = []
    for data in inputs:
        video, _, _ = validate_dom_match(data,allow_query_candidates=True) if allow_query_candidates else validate_dom_match(data)
        ids.append(video['video_id'])
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate video IDs')
    if output.exists():
        raise ValueError('Output path must be fresh')
    output.mkdir(parents=True)
    persistence_root = Path(persistence_root) if persistence_root is not None else output
    manifest = {**(batch_metadata or {}), 'run_id': run_id, 'planned_videos': len(inputs),
                'max_comments_per_video': limit, 'video_ids': ids,
                'session_policy': 'ONE_HEADED_UC_SESSION_SEQUENTIAL_NO_RETRY',
                'inputs_sha256': hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False,
                    separators=(',', ':')).encode()).hexdigest()}
    (output/'run_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    rows = []
    stop = 'TEST_SET_COMPLETED'
    owner = None
    write_report(output, rows, 'STARTING')
    startup = time.monotonic()
    resources = Resources()
    resources.thread.start()
    startup_seconds = None
    try:
        if existing_port is None:
            owner = BrowserObserver(capture_dir=output, driver_path=driver_path)
            startup_seconds = time.monotonic()-startup
            if not getattr(owner.driver, '_is_using_uc', False):
                raise RuntimeError('UC_BACKEND_REQUIRED')
            network = NetworkCapture(owner.driver)
            network.start()
            port = SeleniumDOMPort(driver=owner.driver, network=network)
        else:
            port = existing_port
            if not getattr(port.driver, '_is_using_uc', False):
                raise RuntimeError('UC_BACKEND_REQUIRED')
            network = port.network
            if network is None: raise ValueError('Existing network observer required')
        for index, data in enumerate(inputs):
            result = {}
            video = data['selected_video']
            folder = output/f'video-{index+1}'
            start = time.monotonic()
            prior = len(network.entries)
            telemetry = Telemetry()
            if index == 0: telemetry.times['browser_startup'] = startup_seconds
            row = {'video_id': video['video_id'], 'place_id': data['place']['place_id'],
                   'video_view_count':video.get('view_count'),'video_like_count':video.get('like_count'),
                   'video_stats_observed_at':video.get('observed_at'),'video_stats_source':'DISCOVERY_SEARCH_RESPONSE',
                   'association_status':data.get('association_policy','CATALOG_AND_ADDRESS_MATCH'),
                   'browser_startup_success': True, 'video_navigation_success': None,
                   'stage_times_seconds': {'browser_startup': None, 'navigation': None,
                                           'onboarding': None, 'comments': None, 'collection': None},
                   'stage_at_stop': 'unknown'}
            try:
                result = run_dom_comments(data, output=folder, database=persistence_root/'comments.sqlite3',
                    parquet_root=persistence_root/'lake', run_id=f'{run_id}-{index+1}', limit=limit, port=port, telemetry=telemetry,**({'allow_query_candidates':True} if allow_query_candidates else {}),**({'comment_scrolls':comment_scrolls} if comment_scrolls else {}),**({'reply_limit':reply_limit} if reply_limit else {}),**({'reply_expand_limit':reply_expand_limit} if reply_expand_limit else {}))
                summary_file = folder/'summary.json'
                summary = json.loads(summary_file.read_text(encoding='utf-8')) if summary_file.exists() else {}
                row.update(crawler_summary={'actions': summary.get('actions', []),
                           'challenge_details': summary.get('challenge_details'),
                           'challenge_markers': summary.get('challenge_markers'),
                           'challenge_text_signals': summary.get('challenge_text_signals')},
                           stop_reason=result['stop_reason'], comments_extracted=result['comments_in_collector'],
                           sqlite_records=result['records'], pending_outbox_events=result['pending_events'])
                row['video_navigation_success'] = bool(summary.get('videos_opened'))
                row.update(reconcile(persistence_root/'comments.sqlite3', persistence_root/'lake', video['video_id']))
                row['stage_at_stop'] = telemetry.last_stage if hasattr(telemetry, 'last_stage') else 'unknown'
            except Exception as exc:
                row.update(stop_reason=type(exc).__name__,
                           persistence_verified=False, stage_at_stop='persistence')
                row.setdefault('comments_extracted', 0)
            try:
                network.drain()
            except Exception:
                pass
            telemetry.finish()
            row['stage_times_seconds'] = telemetry.times
            row['access_observations'] = telemetry.observations
            if row['stage_at_stop'] != 'persistence':
                row['stage_at_stop'] = getattr(telemetry, 'last_stage', 'unknown')
            row['network_observation'] = 'OBSERVED' if any(e.get('host_scope') == 'tiktok' and
                e.get('event') != 'DOCUMENT_REQUEST' and e.get('status', 0) > 0 for e in network.entries[prior:]) else 'UNOBSERVED'
            row['elapsed_seconds'] = time.monotonic()-start+((startup_seconds or 0) if index == 0 else 0)
            row['comments_api_empty_response_observed'] = any(
                e.get('endpoint_kind') == 'comments_api' and e.get('status') == 200 and
                e.get('application_status') == 0 and e.get('item_count') == 0
                for e in network.entries[prior:])
            quality = result.get('collection_quality', {})
            row.update(collection_scope='TOP_LEVEL_AND_VISIBLE_REPLIES' if reply_limit else 'TOP_LEVEL_ONLY',
                       top_level_comments=quality.get('top_level_comments'),
                       replies_collected=quality.get('replies_collected',0),
                       reply_coverage='PARTIAL_VISIBLE' if reply_limit else 'NOT_COLLECTED', max_scrolls=comment_scrolls,
                       data_completeness='UNVERIFIED_FULL_COVERAGE')
            evidence(row, network.entries[prior:])
            rows.append(row)
            stop = batch_stop(row) or 'TEST_SET_COMPLETED'
            write_report(output, rows, stop)
            if stop != 'TEST_SET_COMPLETED':
                break
    except Exception as exc:
        stop = ('BROWSER_STARTUP_' if owner is None else 'BENCHMARK_') + type(exc).__name__
        if not rows:
            rows.append(evidence({'video_id': ids[0], 'place_id': inputs[0]['place']['place_id'], 'browser_startup_success': owner is not None, 'video_navigation_success': None, 'comments_extracted': 0, 'elapsed_seconds': time.monotonic()-startup, 'stage_times_seconds': {'browser_startup': startup_seconds or time.monotonic()-startup}, 'stop_reason': stop, 'stage_at_stop': 'browser_startup'}))
        write_report(output, rows, stop)
    finally:
        measured_resources = resources.finish()
        for row in rows: row['resources'] = measured_resources
        if owner is not None:
            owner.lifecycle.close()
    return write_report(output, rows, stop)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample-offline', action='store_true')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--prepare-study', action='store_true')
    parser.add_argument('--preflight', action='store_true', help='Validate dataset offline; no browser')
    parser.add_argument('--batch-file', type=Path, help='Exactly one reviewed 1..10-video manifest')
    parser.add_argument('--match-files', type=Path, nargs='*', default=[])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id')
    parser.add_argument('--max-comments', type=int, default=7)
    args = parser.parse_args()
    if sum((args.sample_offline, args.live, args.prepare_study, args.preflight)) != 1:
        parser.error('Specify exactly one mode: --sample-offline, --live, --prepare-study or --preflight')
    if args.prepare_study:
        study = prepare_study(args.output)
        print(json.dumps({key: study[key] for key in ('qualified_candidates', 'shortfall', 'batches')}, indent=2))
        return
    if args.preflight:
        if not args.batch_file or args.match_files:
            parser.error('Offline preflight requires exactly one --batch-file')
        batch = load_batch(args.batch_file)
        args.output.mkdir(parents=True, exist_ok=False)
        report = {'mode': 'OFFLINE_DATASET_PREFLIGHT', 'status': 'PASS', 'batch_id': batch['batch_id'],
                  'videos': len(batch['inputs']), 'inputs_sha256': batch['inputs_sha256'],
                  'browser_started': False, 'live_requests': False, 'current_public_availability': 'UNVERIFIED'}
        (args.output/'preflight.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
        return
    if args.sample_offline:
        rows = [evidence({'video_id': 'fixture-1', 'place_id': 'fixture-place', 'stop_reason': 'COMMENT_LIMIT',
                          'comments_extracted': 7, 'elapsed_seconds': 12, 'comments_panel_opened': 'yes',
                          'persistence_verified': True, 'access_observations': [{'stage': stage, 'fresh': True, 'challenge_present': False} for stage in ('navigation', 'comments', 'collection')], 'stage_times_seconds': {'navigation': 3, 'onboarding': 1, 'comments': 5, 'collection': 1, 'persistence': 2}, 'sqlite_records': 7, 'parquet_record_count': 7, 'pending_outbox_events': 0}), evidence({'video_id': 'fixture-2', 'stop_reason': 'CHALLENGE', 'stage_at_stop': 'comments', 'comments_extracted': 0, 'elapsed_seconds': 4}), evidence({'video_id': 'fixture-3', 'stop_reason': 'BROWSER_STARTUP_ERROR', 'stage_at_stop': 'browser_startup', 'comments_extracted': 0, 'elapsed_seconds': 2})]
        print(json.dumps(write_report(args.output, rows, 'OFFLINE_FIXTURE')['aggregate'], indent=2))
        return
    if not args.run_id or bool(args.batch_file) == bool(args.match_files):
        parser.error('Live requires --run-id and either one --batch-file or 1..10 --match-files')
    batch = load_batch(args.batch_file) if args.batch_file else None
    inputs = batch['inputs'] if batch else [json.loads(path.read_text(encoding='utf-8')) for path in args.match_files]
    report = run_live(inputs, args.output, run_id=args.run_id, limit=args.max_comments,
                      batch_metadata={key: batch[key] for key in ('study_id', 'batch_id')} if batch else None)
    print(json.dumps(report['aggregate'], indent=2))


if __name__ == '__main__':
    main()
