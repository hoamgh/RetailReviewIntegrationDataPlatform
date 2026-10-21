"""Bounded, public TikTok place candidates; no production integration."""
from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from crawl_experiment.storage.place_catalog import DEFAULT_CATALOG, load_catalog
from experiments.tiktok_search_probe import tiktok_search_probe as search


def normalized(value):
    value = unicodedata.normalize("NFD", str(value or "").lower().replace("đ", "d"))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", "".join(c for c in value if not unicodedata.combining(c))).split())


def contains(text, phrase):
    return bool(phrase) and f" {phrase} " in f" {text} "


def load_place(catalog):
    matches = [p for p in load_catalog(catalog).places if normalized(p.name) == "tra sua leetea"]
    if len(matches) != 1:
        raise ValueError(f"Expected one canonical Leetea, found {len(matches)}")
    p = matches[0]
    return dict(place_id=p.place_key, source_place_id=p.source_place_id, canonical_name=p.name,
                alternate_names=[], address=p.address, admin_area=p.admin_area_name,
                ward="Khánh Hội" if "khanh hoi" in normalized(p.admin_area_name) else None,
                district=None, category=p.primary_category, categories=p.categories,
                product_category_group=p.product_category_group, lat=p.lat, lng=p.lng,
                brand="Leetea", search_category="trà sữa", catalog_path=str(catalog))


def generate_queries(place):
    values = [place["canonical_name"], place["brand"]]
    if place.get("ward"):
        values.append(f'{place["brand"]} {place["ward"]}')
    values.extend([f'{place["brand"]} {place["search_category"]}',
                   "#" + normalized(place["brand"]).replace(" ", "")])
    if place.get("address"):
        values.append(f'{place["brand"]} {place["address"]}')
    return list(dict.fromkeys(values))[:8]


def normalize_payload(payload, query, place, observed_at, limit):
    """Use only the proven data[*].item schema, never recursive detection."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    videos = []
    seen = set()
    for result in payload["data"]:
        item = result.get("item") if isinstance(result, dict) else None
        if not isinstance(item, dict):
            continue
        video = search.normalize_video(item, business_name=place["canonical_name"], query=query, observed_at=observed_at)
        if video is None or video["video_id"] in seen:
            continue
        seen.add(video["video_id"])
        extra = item.get("textExtra") or item.get("text_extra") or []
        mentions = [str(e.get("userUniqueId") or e.get("user_unique_id")) for e in extra
                    if isinstance(e, dict) and (e.get("userUniqueId") or e.get("user_unique_id"))] if isinstance(extra, list) else []
        mentions.extend(re.findall(r"@([\w.]+)", video.get("caption") or ""))
        location = {}
        # Explicit, bounded projections only; source values are not inferred.
        for key in ("poi", "poiInfo", "locationCreated", "location"):
            value = item.get(key)
            if isinstance(value, dict):
                location[key] = {k: v for k, v in value.items() if k in
                    {"id", "poiId", "name", "poiName", "address", "city", "district", "latitude", "longitude"}
                    and (v is None or isinstance(v, (str, int, float, bool)))}
            elif isinstance(value, (str, int, float)):
                location[key] = value
        video.update(created_at=video["published_at"], author_id=video["creator_id"],
                     author_username=video["creator_username"], mentions=list(dict.fromkeys(mentions)),
                     location_metadata=location or None, query_used=query)
        videos.append(video)
        if len(videos) >= limit:
            break
    return videos


def deduplicate(records):
    unique = {}
    for record in records:
        identity = record["video_id"]
        if identity not in unique:
            unique[identity] = {**record, "all_queries": [], "first_seen_query": record["query_used"]}
        row = unique[identity]
        if record["query_used"] not in row["all_queries"]:
            row["all_queries"].append(record["query_used"])
        row["matched_query_count"] = len(row["all_queries"])
    return list(unique.values())


def score_video(video, place):
    text = normalized(" ".join([video.get("caption") or "", *(video.get("hashtags") or [])]))
    tags = {normalized(tag).replace(" ", "") for tag in (video.get("hashtags") or [])}
    def matches(value):
        phrase = normalized(value)
        return contains(text, phrase) or bool(phrase) and phrase.replace(" ", "") in tags
    signals = []
    score = 0
    if matches(place["canonical_name"]):
        signals.append("EXACT_NAME"); score += .55
    if matches(place["brand"]):
        score += .1 if "EXACT_NAME" in signals else .25
        signals.append("BRAND_MATCH")
    if matches(place.get("address")):
        signals.append("ADDRESS_MATCH"); score += .4
    if matches(place.get("ward")):
        signals.append("AREA_MATCH"); score += .2
    if matches(place["search_category"]):
        signals.append("CATEGORY_MATCH"); score += .1
    branded = "BRAND_MATCH" in signals or "EXACT_NAME" in signals
    local = "ADDRESS_MATCH" in signals or "AREA_MATCH" in signals
    score = round(min(score, 1), 3)
    # A heuristic recommendation, not a verified identity or calibrated probability.
    decision = "MATCH" if branded and local and score >= .65 else "REVIEW" if branded else "REJECT"
    return dict(video_id=video["video_id"], candidate_place_id=place["place_id"], match_score=score,
                signals=signals, decision=decision, evidence_text=text[:1000])


def write(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def summary(place, queries, records, diagnostics, mode, errors, stopped_reason):
    videos = deduplicate(records)
    candidates = [score_video(v, place) for v in videos]
    by_query = []
    for query in queries:
        subset = [r for r in records if r["query_used"] == query]
        decisions = [score_video(v, place)["decision"] for v in subset]
        by_query.append(dict(query=query, returned=len(subset), unique=len({v["video_id"] for v in subset}),
                             **{s: decisions.count(s) for s in ("MATCH", "REVIEW", "REJECT")}))
    result = dict(mode=mode, place=place, number_of_queries=len(queries), queries_attempted=len(diagnostics),
                  raw_videos_found=len(records), unique_video_ids=len(videos), duplicates_removed=len(records)-len(videos),
                  **{s: sum(c["decision"] == s for c in candidates) for s in ("MATCH", "REVIEW", "REJECT")},
                  by_query=by_query, query_diagnostics=diagnostics, errors=errors, stopped_reason=stopped_reason,
                  top_candidates=sorted(candidates, key=lambda c: (-c["match_score"], c["video_id"]))[:5],
                  limitations=["MATCH is text evidence only, not verified store identity.",
                               "No OCR, transcription, comments or pagination.",
                               "No geographic claims from missing location metadata.",
                               "Missing canonical aliases/district remain empty/null."])
    return videos, candidates, result


def discovery_access_evidence(events):
    # Reuse the existing sanitized network classifier, without a second log consumer.
    from experiments.tiktok_access_benchmark.network import diagnostic
    evidence=[]
    for event in events:
        try:
            message=json.loads(event['message'])['message'];params=message.get('params',{})
            if message['method']=='Network.responseReceived':
                response=params['response']
            elif message['method']=='Network.requestWillBeSent' and 'redirectResponse' in params:
                response=params['redirectResponse']
            else:continue
            entry=diagnostic(response,params.get('type','Unknown'),'DISCOVERY_SEARCH')
            if entry['status']>=400 or entry['endpoint_kind'] in ('document','search_api'):
                evidence.append(entry)
        except (ValueError,KeyError,TypeError):continue
    return evidence


def run(place, queries, output, *, live=False, replay=None, limit=10, driver=None):
    records, diagnostics, errors = [], [], []
    mode = "live_tiktok" if live else "offline_replay"
    stopped = "COMPLETED"
    def checkpoint():
        videos, candidates, report = summary(place, queries, records, diagnostics, mode, errors, stopped)
        for name, data in (("generated_queries.json", queries), ("raw_discovery.json", records),
                           ("unique_videos.json", videos), ("place_match_candidates.json", candidates),
                           ("discovery_summary.json", report)):
            write(output / name, data)
        return report
    output.mkdir(parents=True, exist_ok=False)
    progress_handler = logging.FileHandler(output / "progress.log", encoding="utf-8")
    progress_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(progress_handler)
    write(output / "canonical_place.json", place)
    checkpoint()
    session = None
    try:
        if live:
            if driver is None:
                session = search.browser_session()
                driver = session.start()
            warmup = search.warmup_browser(driver)
            write(output / "browser_warmup.json", warmup)
            if not warmup["network_enabled"] or warmup["errors"]:
                raise RuntimeError("Browser warm-up/Network setup failed")
        else:
            replay_data = json.loads(Path(replay).read_text(encoding="utf-8"))
        for query in queries:
            logging.info("Query %s", query)
            diagnostic = dict(query=query, method="browser_network_json" if live else "offline_replay")
            diagnostics.append(diagnostic)
            if live:
                driver.get_log("performance")
                driver.get(search.search_url(query))
                local_errors = []
                search.readiness(driver, time.sleep, local_errors)
                search.detect_access_block(200, driver.current_url, driver.page_source)
                time.sleep(search.NETWORK_OBSERVATION_SECONDS)
                search.detect_access_block(200, driver.current_url, driver.page_source)
                observed_events = driver.get_log("performance")
                diagnostic['network_evidence'] = discovery_access_evidence(observed_events)
                from experiments.tiktok_access_benchmark.network import decision
                from experiments.tiktok_access_benchmark.benchmark import STATE_JS
                from experiments.tiktok_access_benchmark.access import dialog_kinds
                access=driver.execute_script('return ('+STATE_JS+')();')
                kinds=dialog_kinds(access.get('dialog_evidence',[]))
                gate=('CHALLENGE' if access.get('challenge') or 'CHALLENGE' in kinds else
                      'ACCESS_DENIED' if access.get('access_denied') else
                      'AUTH_GATE' if any(k in kinds for k in ('LOGIN_GATE','CONSENT_GATE')) else
                      decision(diagnostic['network_evidence'],bool(access.get('search') or access.get('video_detail'))))
                if gate:
                    diagnostic['access_stop_reason']=gate
                    stopped=gate;checkpoint();break
                matches = search.parse_network_response_events(observed_events)
                if not matches:
                    diagnostic["error"] = "TARGET_RESPONSE_NOT_OBSERVED"
                    stopped = "CAPTURE_UNAVAILABLE"; checkpoint(); break
                response = matches[0]
                diagnostic.update(status=response.get("status"), mime_type=response.get("mime_type"))
                if response.get("status") in (401, 403, 429):
                    raise search.ProbeStopped("Search response access blocked", authentication=response.get("status")==401,
                                              challenge=response.get("status") in (403,429))
                if response.get("status") != 200:
                    raise RuntimeError("Non-200 search response")
                body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": response["requestId"]})
                content = body["body"]
                diagnostic["response_body_chars"] = len(content)
                if len(content) > 2_000_000:
                    raise RuntimeError("Response exceeds bounded body limit")
                if body.get("base64Encoded"):
                    content = base64.b64decode(content, validate=True).decode("utf-8")
                payload = json.loads(content)
                diagnostic["readiness_errors"] = local_errors
            else:
                payload = replay_data.get(query, {})
            diagnostic["application_status"] = search.bounded_scalar(payload.get("status_code", 0))
            if payload.get("status_code", 0) not in (0, "0"):
                raise RuntimeError("TikTok application status is not successful")
            found = normalize_payload(payload, query, place, search.utc_now(), limit)
            records.extend(found)
            diagnostic.update(videos_found=len(found), cursor=search.bounded_scalar(payload.get("cursor")),
                              has_more=search.bounded_scalar(payload.get("has_more")))
            checkpoint()
    except search.ProbeStopped as exc:
        stopped = "ACCESS_BLOCKED"
        errors.append(dict(type=type(exc).__name__, authentication_required=exc.authentication,
                           challenge_detected=exc.challenge))
    except Exception as exc:
        stopped = "ERROR"
        # Exception messages may contain signed request URLs; record type only.
        known_errors={"TikTok application status is not successful":"APPLICATION_STATUS_ERROR",
                      "Response exceeds bounded body limit":"RESPONSE_BODY_LIMIT",
                      "Non-200 search response":"SEARCH_HTTP_ERROR",
                      "Browser warm-up/Network setup failed":"BROWSER_WARMUP_FAILED"}
        errors.append(dict(type=type(exc).__name__, stage="search_or_capture",
                           error_code=known_errors.get(str(exc),"UNCLASSIFIED_DISCOVERY_ERROR")))
    finally:
        if session:
            session.close()
        report = checkpoint()
        logging.getLogger().removeHandler(progress_handler)
        progress_handler.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--live", action="store_true")
    modes.add_argument("--replay", type=Path, help="Offline JSON mapping queries to response payloads")
    parser.add_argument("--query-limit", type=int, default=6)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.query_limit <= 8 or not 1 <= args.limit <= 10:
        parser.error("query-limit must be 1..8 and limit 1..10")
    place = load_place(args.catalog)
    queries = generate_queries(place)[:args.query_limit]
    output = Path(__file__).parent / "output" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(dict(place=place, queries=queries), ensure_ascii=False, indent=2), flush=True)
    report = run(place, queries, output, live=args.live, replay=args.replay, limit=args.limit)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print("Results:", output.resolve())
    return 0 if report["stopped_reason"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
