"""Offline protection for the captured Google working-tree baseline."""
import hashlib
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def test_frozen_google_source_manifest():
    manifest=json.loads((ROOT/'docs/baselines/google_reviews_manifest.json').read_text(encoding='utf-8'))
    drift=[]
    for name, expected in manifest['files'].items():
        path=ROOT/name
        actual=hashlib.sha256(path.read_text(encoding='utf-8').replace('\r\n','\n').encode()).hexdigest() if path.is_file() else None
        if actual != expected: drift.append(name)
    assert not drift, 'Frozen Google baseline changed; review authorization and regression before updating manifest: '+', '.join(drift)
def test_canonical_google_entrypoint_and_storage_contract():
    from scripts import coles_small_set_full_crawl as cli
    from crawl_experiment.runners import full_crawl_runner as runner
    from crawl_experiment.sources.google_maps.crawler import GoogleMapsCrawler
    from crawl_experiment.sources.google_maps.extractor import ReviewExtractor
    assert cli.run_full_crawl is runner.run_full_crawl
    assert runner.GoogleMapsCrawler is GoogleMapsCrawler
    assert str(runner.DEFAULT_STATE_DATABASE).replace('\\','/') == 'data/state/crawler_state.sqlite3'
    assert str(runner.DEFAULT_PARQUET_ROOT).replace('\\','/') == 'data/lake/google_maps_reviews'
    args=cli.build_parser().parse_args(['--crawl-mode','REVIEW_INCREMENTAL','--known-streak-threshold','5'])
    assert args.crawl_mode=='REVIEW_INCREMENTAL' and args.known_streak_threshold==5
    assert callable(ReviewExtractor.extract)
