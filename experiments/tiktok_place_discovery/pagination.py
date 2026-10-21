"""Observe up to three UI-generated search pages in one logged-out session."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.tiktok_place_discovery.probe import normalize_payload, score_video, write
from experiments.tiktok_search_probe import tiktok_search_probe as search

QUERY = "Leetea 169 Tôn Đản"


def identity(value):
    """Comparable fingerprint, never persist raw session tokens."""
    return hashlib.sha256(str(value).encode()).hexdigest()[:20] if value not in (None, "") else None


def target_request(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "www.tiktok.com" and parsed.path == search.TARGET_SEARCH_ENDPOINT


def request_projection(url):
    params = parse_qs(urlparse(url).query, keep_blank_values=True)
    def first(name): return params.get(name, [None])[0]
    session = {k: identity(first(k)) for k in ("search_id", "searchId", "search_session_id") if first(k)}
    extra = first("search_extra")
    if extra:
        try:
            decoded = json.loads(extra)
            if isinstance(decoded, dict):
                for key in ("search_id", "searchId", "search_session_id"):
                    if decoded.get(key): session["search_extra." + key] = identity(decoded[key])
        except (TypeError, ValueError):
            pass
    return dict(parameter_names=sorted(params), keyword=first("keyword"), cursor_in=first("cursor"),
                offset_in=first("offset"), count_requested=first("count"), search_session_identifiers=session)


def response_projection(payload):
    result = dict(top_level_keys=sorted(payload), cursor_out=search.bounded_scalar(payload.get("cursor")),
                  has_more=search.bounded_scalar(payload.get("has_more")),
                  application_status=search.bounded_scalar(payload.get("status_code")),
                  response_identifiers={})
    for key in ("search_id", "searchId", "search_session_id"):
        if payload.get(key): result["response_identifiers"][key] = identity(payload[key])
    log_pb = payload.get("log_pb")
    if isinstance(log_pb, dict):
        result["log_pb_keys"] = sorted(log_pb)
        if log_pb.get("impr_id"):
            result["response_identifiers"]["log_pb.impr_id"] = identity(log_pb["impr_id"])
    return result


class UIResponseCollector:
    def __init__(self, driver):
        self.driver = driver
        self.requests = {}
        self.completed = []
        self.sequence = 0

    def observe(self, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            entries = self.driver.get_log("performance")
            for entry in entries:
                try:
                    event = json.loads(entry["message"])["message"]
                    params = event.get("params", {})
                    rid = params.get("requestId")
                    method = event.get("method")
                    if method == "Network.requestWillBeSent":
                        request = params.get("request", {})
                        if target_request(request.get("url", "")):
                            self.sequence += 1
                            self.requests[rid] = dict(sequence=self.sequence, method=request.get("method"),
                                                      **request_projection(request["url"]))
                    elif method == "Network.responseReceived":
                        response = params.get("response", {})
                        if target_request(response.get("url", "")):
                            record = self.requests.setdefault(rid, dict(sequence=None, **request_projection(response["url"])))
                            record.update(http_status=response.get("status"), mime_type=response.get("mimeType"),
                                          resource_type=params.get("type"))
                    elif method in ("Network.loadingFinished", "Network.loadingFailed") and rid in self.requests:
                        record = self.requests.pop(rid)
                        self.completed.append(dict(requestId=rid, metadata=record, loading_failed=method.endswith("loadingFailed")))
                except (KeyError, TypeError, ValueError):
                    continue
            if self.completed:
                return self.completed.pop(0)
            time.sleep(.25)
        return None


def decode_body(body):
    content = body.get("body")
    if not isinstance(content, str) or len(content) > 2_000_000:
        raise ValueError("BODY_LIMIT_OR_MISSING")
    if body.get("base64Encoded"):
        content = base64.b64decode(content, validate=True).decode("utf-8")
    payload = json.loads(content)
    if not isinstance(payload, dict): raise ValueError("NON_OBJECT_RESPONSE")
    return payload


def page_metrics(videos, seen, place):
    ids = {v["video_id"] for v in videos}
    new = ids - seen
    scores = [score_video(v, place) for v in videos]
    return dict(unique_new_video_ids=len(new), cumulative_unique_ids=len(seen | ids),
                normalized_videos=len(videos), duplicate_ids_vs_previous=len(ids & seen),
                **{s: sum(c["decision"] == s for c in scores) for s in ("MATCH", "REVIEW", "REJECT")},
                marginal_match=sum(c["decision"] == "MATCH" and c["video_id"] in new for c in scores)), ids


def scroll_once(driver):
    # Snapshot selection of the actual rendered scroll surface; no fabricated API cursor.
    return driver.execute_script("""
    const root = document.scrollingElement;
    const nodes = [root, ...Array.from(document.querySelectorAll('main, div'))]
      .filter(e => e && e.clientHeight > 250 && e.scrollHeight > e.clientHeight + 100
        && (e === root || ['auto','scroll'].includes(getComputedStyle(e).overflowY)));
    nodes.sort((a,b) => (b.scrollHeight-b.clientHeight) - (a.scrollHeight-a.clientHeight));
    const e = nodes[0];
    if (!e) return {scrollable: false};
    const before = e.scrollTop; e.scrollTop = e.scrollHeight;
    return {scrollable:true, tag:e.tagName, test_id:e.getAttribute('data-e2e'),
      before:before, after:e.scrollTop, client_height:e.clientHeight, scroll_height:e.scrollHeight};
    """)


def run(source, output, max_pages=3):
    place = json.loads((source / "canonical_place.json").read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=False)
    handler = logging.FileHandler(output / "progress.log", encoding="utf-8")
    logging.getLogger().addHandler(handler)
    report = dict(query=QUERY, canonical_place_id=place["place_id"], source_run=str(source.resolve()),
                  pages=[], errors=[], stopped_reason="STARTED", mode="live_ui_generated_pagination",
                  limitations=["Session identifiers are hashed, not raw tokens.",
                               "No request replay or constructed page-2 URL.",
                               "A single query cannot prove independent per-query session semantics.",
                               "Parameter observation does not establish which parameters are mandatory."])
    observations = []
    seen, fingerprints = set(), set()
    def checkpoint():
        report["cumulative_unique_videos"] = len(seen)
        unique = {v["video_id"]: v for v in observations}
        report["cumulative_match_candidates"] = sum(score_video(v, place)["decision"] == "MATCH" for v in unique.values())
        write(output / "pagination_summary.json", report)
        write(output / "pagination_videos.json", observations)
    session = None
    checkpoint()
    try:
        session = search.browser_session()
        driver = session.start()
        warmup = search.warmup_browser(driver)
        if warmup["errors"] or not warmup["network_enabled"]:
            raise RuntimeError("WARMUP_FAILED")
        collector = UIResponseCollector(driver)
        for number in range(1, max_pages + 1):
            page = dict(page_number=number, errors=[])
            report["pages"].append(page)
            started = time.monotonic()
            if number == 1:
                driver.get(search.search_url(QUERY))
                errors = []
                search.readiness(driver, time.sleep, errors)
                page["readiness_errors"] = errors
            else:
                page["scroll"] = scroll_once(driver)
                if not page["scroll"]["scrollable"]:
                    report["stopped_reason"] = "NO_UI_SCROLL_SURFACE"; break
            search.detect_access_block(200, driver.current_url, driver.page_source)
            captured = collector.observe()
            search.detect_access_block(200, driver.current_url, driver.page_source)
            page["latency_seconds"] = round(time.monotonic()-started, 3)
            if captured is None:
                report["stopped_reason"] = "NO_PAGINATION_RESPONSE_OBSERVED"
                page["errors"].append("CAPTURE_TIMEOUT"); break
            page.update(captured["metadata"])
            if captured["loading_failed"]:
                page["errors"].append("NETWORK_LOADING_FAILED")
                report["stopped_reason"] = "NETWORK_LOADING_FAILED"; break
            status = page.get("http_status")
            if status in (401, 403, 429):
                raise search.ProbeStopped("ACCESS_BLOCKED", authentication=status==401, challenge=status in (403,429))
            if status != 200: raise RuntimeError("HTTP_ERROR")
            body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": captured["requestId"]})
            payload = decode_body(body)
            page.update(response_projection(payload))
            if payload.get("status_code", 0) != 0:
                report["stopped_reason"] = "APPLICATION_STATUS_ERROR"
                page["errors"].append("NONZERO_STATUS_NOT_ASSUMED_TOKEN_INVALID"); break
            data = payload.get("data")
            if not isinstance(data, list): raise RuntimeError("UNSUPPORTED_DATA_SCHEMA")
            page["raw_videos_returned"] = sum(isinstance(v, dict) and isinstance(v.get("item"), dict) for v in data)
            if page["raw_videos_returned"] > 100: raise RuntimeError("RECORD_LIMIT")
            videos = normalize_payload(payload, QUERY, place, search.utc_now(), 100)
            metrics, ids = page_metrics(videos, seen, place)
            page.update(metrics, video_ids=sorted(ids))
            if number > 1:
                prior = report["pages"][-2].get("cursor_out")
                actual = page.get("cursor_in") or page.get("offset_in")
                page["previous_cursor_used"] = str(prior) == str(actual) if prior is not None and actual is not None else None
            page["latency_seconds"] = round(time.monotonic()-started, 3)
            observations.extend({**v, "page_number":number} for v in videos)
            fingerprint = tuple(sorted(ids))
            repeated = fingerprint in fingerprints
            fingerprints.add(fingerprint)
            seen.update(ids)
            checkpoint()
            if repeated:
                report["stopped_reason"] = "REPEATED_PAGE_IDS"; break
            if page["unique_new_video_ids"] == 0:
                report["stopped_reason"] = "NO_GROWTH"; break
            if page.get("has_more") in (0, False, "0"):
                report["stopped_reason"] = "HAS_MORE_ZERO"; break
        else:
            report["stopped_reason"] = "MAX_PAGES"
    except search.ProbeStopped as exc:
        report["stopped_reason"] = "ACCESS_BLOCKED"
        report["errors"].append(dict(type=type(exc).__name__, authentication_required=exc.authentication, challenge_detected=exc.challenge))
    except Exception as exc:
        report["stopped_reason"] = "ERROR"
        # Never retain arbitrary WebDriver exception messages containing signed URLs.
        report["errors"].append(dict(type=type(exc).__name__, stage="UI_PAGINATION_OR_CAPTURE"))
    finally:
        if session: session.close()
        checkpoint()
        logging.getLogger().removeHandler(handler)
        handler.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, choices=(1,2,3), default=3)
    args = parser.parse_args()
    from datetime import datetime, timezone
    output = Path(__file__).parent / "output" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-pagination")
    logging.basicConfig(level=logging.INFO)
    result = run(args.source_run, output, args.max_pages)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Results:", output.resolve())
