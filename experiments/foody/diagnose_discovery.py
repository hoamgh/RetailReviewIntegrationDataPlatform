"""Bounded owner-run load-more check; no details/reviews, no login, no bulk crawl."""
import argparse
import json
from pathlib import Path
import time
import uuid

import crawl
from full import BrowserListing, discover
from profile_access import available_profile
from profile_q4 import ROOT, SEED


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-directory', type=Path, required=True)
    parser.add_argument('--clicks', type=int, default=6)
    parser.add_argument('--max-minutes', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.clicks <= 70 or not 1 <= args.max_minutes <= 60:
        parser.error('Diagnostic limited to 1–70 clicks and 1–60 minutes')
    out = ROOT / 'output' / ('foody-load-more-check-' + uuid.uuid4().hex[:12])
    out.mkdir(parents=True)
    evidence = dict(status='RUNNING', clicks=0, pages=[], discovered_urls=[], error=None)
    seen = set()
    path = out / 'summary.json'
    deadline = time.monotonic() + args.max_minutes * 60
    print(json.dumps(dict(report=str(path), click_limit=args.clicks,
                          max_minutes=args.max_minutes)), flush=True)
    def save():
        evidence['discovered_urls'] = sorted(seen)
        crawl.save(path, evidence)
    try:
        profile = available_profile(args.profile_directory)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(profile), channel='chrome',
                        headless=False, viewport={'width':1280,'height':900})
            try:
                page = context.pages[0] if context.pages else context.new_page()
                def metadata_sink(metadata):
                    row = dict(click=evidence['clicks'], **metadata)
                    with (out / 'pagination.jsonl').open('a', encoding='utf-8') as log:
                        log.write(json.dumps(row, ensure_ascii=False) + '\n')
                        log.flush()
                    print(json.dumps(dict(click=row['click'], click_state=row['click_state'],
                        parser_urls=row['parser_url_count'], new_urls=row['new_parser_url_count'],
                        responses=[{k:r.get(k) for k in ('pagination','status','id_schema',
                            'response_id_count','new_id_count','duplicate_id_count')} for r in row['responses']],
                        stop_code=row.get('stop_code')), ensure_ascii=False), flush=True)
                listing = BrowserListing(page, metadata_sink=metadata_sink)
                listing.open(SEED)
                def on_page(html, proof):
                    parsed = crawl.parse_page(html, SEED)
                    if not parsed['links']:
                        raise crawl.CrawlError('EMPTY_OR_UNSUPPORTED_DISCOVERY')
                    seen.update(parsed['links'])
                    evidence['pages'].append(dict(**proof, unique_urls=len(seen),
                                                   diagnostic=listing.last_diagnostic))
                    save()
                    print(json.dumps(dict(clicks=evidence['clicks'], unique_urls=len(seen),
                                          loaded=proof['loaded'], total=proof['total'])), flush=True)
                def advance():
                    if evidence['clicks'] >= args.clicks:
                        raise crawl.CrawlError('DIAGNOSTIC_CLICK_LIMIT_REACHED')
                    evidence['clicks'] += 1
                    print(json.dumps(dict(click=evidence['clicks'], phase='LOAD_MORE_STARTED')), flush=True)
                    try:
                        listing.advance()
                    finally:
                        evidence['last_diagnostic'] = listing.last_diagnostic
                        save()
                proof = discover(listing.snapshot, advance, on_page,
                                 budget=lambda: time.monotonic()<deadline)
                evidence['status'] = proof['status']
            finally:
                context.close()
    except (Exception, KeyboardInterrupt) as exc:
        reason = ('INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else
                  str(exc) if isinstance(exc, crawl.CrawlError) else type(exc).__name__)
        evidence.update(status='BOUNDED_CHECK_COMPLETE' if reason=='DIAGNOSTIC_CLICK_LIMIT_REACHED'
                        else 'STOPPED', error=reason)
    finally:
        save()
    print(json.dumps(dict(status=evidence['status'], clicks=evidence['clicks'],
                          unique_urls=len(seen), error=evidence['error'], report=str(path))), flush=True)
    return 0 if evidence['status'] in ('COUNTER_EXHAUSTED','BOUNDED_CHECK_COMPLETE') else 1


if __name__ == '__main__':
    raise SystemExit(main())
