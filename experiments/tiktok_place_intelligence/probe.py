"""Place-only MVP; bounded public browser observation, never request replay."""
from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import sys
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.tiktok_place_discovery.probe import score_video, write, normalized
from experiments.tiktok_place_discovery.pagination import decode_body
from experiments.tiktok_search_probe import tiktok_search_probe as search

DEFAULT_SOURCE = ROOT / "experiments/tiktok_place_discovery/output/20261008T034216.136593Z-pagination"
DEFAULT_PLACE = ROOT / "experiments/tiktok_place_discovery/output/20261008T032106.973735Z/canonical_place.json"
COUNTS = ("view_count", "like_count", "comment_count", "share_count", "favorite_count")


def count(value):
    if isinstance(value, bool):
        return None
    try:
        result = int(str(value))
        return result if result >= 0 else None
    except (ValueError, TypeError):
        return None


def clean_text(value):
    return " ".join(unicodedata.normalize("NFC", str(value or "")).split())


def select_videos(records, place, limit):
    """MATCH plus exact address is mandatory; no REVIEW promotion in this phase."""
    unique = {}
    for record in records:
        match = score_video(record, place)
        if match["decision"] != "MATCH" or "ADDRESS_MATCH" not in match["signals"]:
            continue
        text = normalized(record.get("caption"))
        if any(phrase in text for phrase in ("go vap", "quy nhon", "nam dinh", "y yen")):
            continue
        vid = record["video_id"]
        previous = unique.get(vid)
        if previous is None or str(record.get("observed_at") or "") > str(previous[0].get("observed_at") or ""):
            unique[vid] = (record, match)
    rows = sorted(unique.values(), key=lambda pair: (
        -int("EXACT_NAME" in pair[1]["signals"]), -pair[1]["match_score"], pair[0]["video_id"]))
    return rows[:limit], len(rows)


def normalize_video(record, match, place):
    return dict(video_id=record["video_id"], video_url=record.get("video_url"), place_id=place["place_id"],
                author_id=record.get("author_id", record.get("creator_id")),
                author_username=record.get("author_username", record.get("creator_username")),
                author_display_name=record.get("creator_display_name"), caption=record.get("caption"),
                hashtags=record.get("hashtags") or [], created_at=record.get("created_at", record.get("published_at")),
                duration=record.get("duration"), **{k: count(record.get(k)) for k in COUNTS},
                matching_signals=match["signals"], match_score=match["match_score"],
                discovered_at=record.get("observed_at"), observed_at=record.get("observed_at"),
                metadata_source="saved_normalized_discovery", decision="MATCH")


def normalize_comment(item, video_id, observed_at):
    if not isinstance(item, dict) or not item.get("cid"):
        return None
    if str(item.get("aweme_id", video_id)) != video_id:
        return None
    parent = item.get("reply_id") or item.get("parent_comment_id")
    if parent not in (None, "", 0, "0"):
        return None  # Never normalize reply records as top-level comments.
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    text = item.get("text") if isinstance(item.get("text"), str) else None
    return dict(comment_id=str(item["cid"]), video_id=video_id,
                author_id=user.get("uid", user.get("id")),
                author_username=user.get("unique_id", user.get("uniqueId")),
                text=text, normalized_text=clean_text(text), created_at=search.timestamp(item.get("create_time")),
                like_count=count(item.get("digg_count")), reply_count=count(item.get("reply_comment_total")),
                parent_comment_id=None, observed_at=observed_at,
                language=item.get("language") if isinstance(item.get("language"), str) else None)


def raw_comment_projection(item):
    result = {k: item[k] for k in ("cid", "aweme_id", "text", "create_time", "digg_count",
              "reply_comment_total", "reply_id", "language") if k in item}
    user = item.get("user")
    if isinstance(user, dict):
        result["user"] = {k: user[k] for k in ("uid", "id", "unique_id", "uniqueId", "nickname") if k in user}
    return result


def snapshot(video):
    return {"video_id": video["video_id"], "observed_at": video["observed_at"],
            "source": video["metadata_source"], **{k: video[k] for k in COUNTS}}


def features(videos, place, as_of):
    now = datetime.fromisoformat(as_of)
    recent = {7: [], 30: []}
    for video in videos:
        try:
            created = datetime.fromisoformat(video["created_at"])
            if created.tzinfo is None:
                continue
            for days in recent:
                if now - timedelta(days=days) <= created <= now:
                    recent[days].append(video)
        except (TypeError, ValueError):
            pass
    result = dict(place_id=place["place_id"], scope="selected_strong_MATCH_videos_only", as_of=as_of,
                  matched_video_count=len(videos), unique_creator_count=len({v["author_id"] or v["author_username"]
                    for v in videos if v["author_id"] or v["author_username"]}),
                  videos_last_7d=len(recent[7]), videos_last_30d=len(recent[30]),
                  creators_last_30d=len({v["author_id"] or v["author_username"] for v in recent[30]
                                       if v["author_id"] or v["author_username"]}),
                  observations_may_have_different_times=True, engagement_rates=[])
    for key in COUNTS[:4]:
        values = [v[key] for v in videos if v[key] is not None]
        result["total_" + {"view_count":"views", "like_count":"likes", "comment_count":"comments",
                          "share_count":"shares"}[key]] = sum(values) if values else None
        result[key + "_known_video_count"] = len(values)
    views = [v["view_count"] for v in videos if v["view_count"] is not None]
    result.update(median_views=statistics.median(views) if views else None, max_views=max(views) if views else None)
    for video in videos:
        values = [video[k] for k in COUNTS[:4]]
        rate = sum(values[1:]) / values[0] if all(v is not None for v in values) and values[0] > 0 else None
        result["engagement_rates"].append(dict(video_id=video["video_id"], rate=rate))
    complete = [v for v in videos if all(v[k] is not None for k in COUNTS[:4])]
    denominator = sum(v["view_count"] for v in complete)
    result["aggregate_engagement_rate_complete_records"] = (
        sum(v["like_count"]+v["comment_count"]+v["share_count"] for v in complete) / denominator
        if denominator > 0 else None)
    return result


def comment_analysis(comments, duplicate_count, place):
    nonempty = [c for c in comments if c["normalized_text"]]
    languages = {}
    examples = []
    noise = {"url_present": 0, "repeated_normalized_text": 0}
    seen_text = set()
    for c in nonempty:
        languages[c["language"] or "UNKNOWN"] = languages.get(c["language"] or "UNKNOWN", 0) + 1
        text = c["normalized_text"]
        noise["url_present"] += bool(re.search(r"https?://|www\.", text, re.I))
        noise["repeated_normalized_text"] += text in seen_text
        seen_text.add(text)
        if any(term in normalized(text) for term in ("leetea", "ton dan", "dia chi", "quan", "tra sua")):
            if len(examples) < 5:
                examples.append(dict(comment_id=c["comment_id"], text=text))
    denominator = len(comments)+duplicate_count
    return dict(non_empty_comment_count=len(nonempty), duplicate_comments_removed=duplicate_count,
                duplicate_rate=duplicate_count/denominator if denominator else None,
                language_distribution=languages, language_method="explicit_source_label_or_UNKNOWN",
                place_related_text_candidates=examples, noise_indicators=noise,
                no_sentiment_inferred=True, noise_indicators_are_not_spam_verdicts=True)


class CommentCollector:
    """Transient IDs/URLs only in memory; persist safe cursor metadata, never signed URLs."""
    def __init__(self, driver, video_id):
        self.driver, self.video_id = driver, video_id
        self.requests, self.completed = {}, []

    def target(self, url):
        p = urlparse(url)
        params = parse_qs(p.query)
        return (p.scheme == "https" and p.hostname == "www.tiktok.com" and p.path == "/api/comment/list/"
                and params.get("aweme_id", [None])[0] == self.video_id)

    def feed(self, entries):
        for entry in entries:
            try:
                event = json.loads(entry["message"])["message"]
                p, method = event.get("params", {}), event.get("method")
                rid = p.get("requestId")
                if method in ("Network.requestWillBeSent", "Network.responseReceived"):
                    obj = p.get("request", {}) if method.endswith("requestWillBeSent") else p.get("response", {})
                    url = obj.get("url", "")
                    if self.target(url):
                        params = parse_qs(urlparse(url).query)
                        row = self.requests.setdefault(rid, {})
                        row.update(endpoint_path="/api/comment/list/", parameter_names=sorted(params),
                                   cursor_in=params.get("cursor", [None])[0], count_requested=params.get("count", [None])[0])
                        if method.endswith("responseReceived"):
                            row.update(http_status=obj.get("status"), mime_type=obj.get("mimeType"))
                elif method in ("Network.loadingFinished", "Network.loadingFailed") and rid in self.requests:
                    self.completed.append((rid, self.requests.pop(rid), method.endswith("loadingFailed")))
            except (ValueError, KeyError, TypeError):
                continue

    def next(self, timeout=10):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            self.feed(self.driver.get_log("performance"))
            if self.completed:
                rid, evidence, failed = self.completed.pop(0)
                if failed:
                    raise RuntimeError("COMMENT_LOADING_FAILED")
                status = evidence.get("http_status")
                if status in (401, 403, 429):
                    raise search.ProbeStopped("Comment access blocked", authentication=status == 401,
                                              challenge=status in (403, 429))
                if status != 200:
                    raise RuntimeError("COMMENT_HTTP_ERROR")
                body = self.driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": rid})
                return decode_body(body), evidence
            time.sleep(.2)
        return None


def scroll_comments(driver):
    return driver.execute_script("""
    const marker = document.querySelector('[data-e2e="comment-list"], [data-e2e="comment-item"]');
    if (!marker) return {scrolled:false, reason:'COMMENT_CONTAINER_NOT_FOUND'};
    let e=marker;
    while(e && e!==document.body) {
      if(e.scrollHeight>e.clientHeight+20 && ['auto','scroll'].includes(getComputedStyle(e).overflowY)) {
        const before=e.scrollTop; e.scrollTop=e.scrollHeight;
        return {scrolled:e.scrollTop>before, tag:e.tagName};
      } e=e.parentElement;
    } return {scrolled:false, reason:'NO_COMMENT_SCROLL_SURFACE'};
    """)


def detail_item(driver, video_id):
    # Immutable script snapshot, one explicit schema; no recursive video detection.
    scripts = driver.execute_script("return Array.from(document.scripts).filter(s=>s.id==='__UNIVERSAL_DATA_FOR_REHYDRATION__').map(s=>s.textContent)")
    for text in scripts:
        if len(text) > 2_000_000:
            continue
        try:
            state = json.loads(text)
            item = state["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
            if isinstance(item, dict) and str(item.get("id")) == video_id:
                return item
        except (TypeError, ValueError, KeyError):
            continue
    return None


def navigation_url(video):
    if not re.fullmatch(r"\d+", video["video_id"]) or not re.fullmatch(r"[\w.]+", video["author_username"] or ""):
        raise ValueError("INVALID_NAVIGATION_IDENTITY")
    # Derived public navigation target, NOT claimed as a source-provided video_url.
    return "https://www.tiktok.com/@"+quote(video["author_username"])+"/video/"+video["video_id"]


def run(source, place_path, output, live=False, max_videos=5, max_comments=50):
    records = json.loads((source/"pagination_videos.json").read_text(encoding="utf-8"))
    place = json.loads(place_path.read_text(encoding="utf-8"))
    selected, eligible = select_videos(records, place, max_videos)
    output.mkdir(parents=True, exist_ok=False)
    videos = [normalize_video(r, m, place) for r, m in selected]
    comments, raw_comments, diagnostics, errors = [], [], [], []
    raw_sources = [dict(video_id=r["video_id"], provenance="saved_normalized_discovery_NOT_original_API_payload",
                        source_record=r) for r, _ in selected]
    duplicate_count = 0
    global_comment_ids = set()
    snapshots = [] if live else [snapshot(v) for v in videos]
    session = None
    finished = False
    warmup = None
    handler = logging.FileHandler(output/"progress.log", encoding="utf-8")
    logging.getLogger().addHandler(handler)
    def checkpoint():
        now = search.utc_now()
        summary = dict(mode="live_comments" if live else "cached_prepare_no_live_requests", finished=finished,
                       source_run=str(source), selected_MATCH_video_count=len(videos), eligible_strong_MATCH_count=eligible,
                       videos_normalized=len(videos), comment_videos_attempted=len(diagnostics),
                       comment_videos_successful=sum(d.get("successful", False) for d in diagnostics),
                       comment_crawl_success_rate=(sum(d.get("successful", False) for d in diagnostics)/len(diagnostics)
                                                  if diagnostics else None), unique_comments=len(comments),
                       duplicate_comments_removed=duplicate_count, diagnostics=diagnostics, errors=errors,
                       limits=dict(max_videos=max_videos, max_top_level_comments_per_video=max_comments,
                                   max_comment_pages_per_video=5, page_load_timeout_seconds=30,
                                   per_page_capture_timeout_seconds=10, retries=0),
                       missing_video_fields={k:sum(v.get(k) is None for v in videos) for k in
                         ("video_url", "duration", "favorite_count", "author_display_name", "created_at")},
                       comment_text_analysis=comment_analysis(comments, duplicate_count, place),
                       browser_warmup=warmup, timestamp=now, limitations=["Automated MATCH is not manual verification.",
                         "Metrics cover selected videos, not all videos for the place, and are not trends.",
                         "Old API payloads unavailable; saved discovery provenance is explicit.",
                         "Live raw evidence is a privacy-safe field projection, not a complete response archive.",
                         "Missing comment responses are not proof of zero comments.",
                         "Comment schema and cursor behavior require the manual live run for validation."])
        for name, value in (("videos.json", videos), ("comments.json", comments),
                            ("engagement_snapshots.json", snapshots),
                            ("place_features.json", features(videos, place, now)),
                            ("raw_source_payloads.json", raw_sources), ("raw_comments.json", raw_comments),
                            ("crawl_summary.json", summary)):
            write(output/name, value)
        (output/"findings.md").write_text(
            "# Leetea place intelligence\n\nMode: "+summary["mode"]+"\n\n"
            +f"Selected {len(videos)} address-qualified MATCH videos; unique comments: {len(comments)}.\n\n"
            +"See crawl_summary.json for actual per-page cursor, has_more, counts, errors and success status. "
            +"No trends or sentiment are inferred. Cached observations retain their original observation time. "
            +"No menu/OCR, creator history, replies, graph expansion or recommender ranking.\n", encoding="utf-8")
    checkpoint()
    try:
        if live and videos:
            session = search.browser_session()
            driver = session.start()
            driver.execute_cdp_cmd("Network.enable", {})
            # Prevent common media binary URL fetches; never save media or follow download links.
            driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls":["*.mp4*", "*.m3u8*"]})
            warmup = search.warmup_browser(driver)
            if warmup["errors"] or not warmup["network_enabled"]:
                raise RuntimeError("BROWSER_WARMUP_FAILED")
            for video in videos:
                logging.warning("Inspect video_id=%s", video["video_id"])
                diagnostic = dict(video_id=video["video_id"], pages=[], successful=False)
                diagnostics.append(diagnostic)
                try:
                    driver.get_log("performance")
                    collector = CommentCollector(driver, video["video_id"])
                    driver.get(navigation_url(video))
                    ready_errors = []
                    search.readiness(driver, time.sleep, ready_errors)
                    search.detect_access_block(200, driver.current_url, driver.page_source)
                    if driver.execute_script("return !!document.querySelector('[role=dialog] input[type=password]')"):
                        raise search.ProbeStopped("Login wall", authentication=True)
                    item = detail_item(driver, video["video_id"])
                    if item:
                        fresh = search.normalize_video(item, business_name=place["canonical_name"],
                                                       query="video_detail", observed_at=search.utc_now())
                        if fresh:
                            fresh_match = score_video(fresh, place)
                            if fresh_match["decision"] != "MATCH" or "ADDRESS_MATCH" not in fresh_match["signals"]:
                                diagnostic["stop_reason"] = "CURRENT_METADATA_NOT_STRONG_MATCH"
                                checkpoint(); continue
                            raw_sources.append(dict(video_id=video["video_id"], provenance="public_embedded_video_detail",
                                item={k:item[k] for k in ("id", "desc", "createTime", "stats", "textExtra") if k in item},
                                author={k:item.get("author", {}).get(k) for k in ("id", "uniqueId", "nickname")},
                                video={"duration":item.get("video", {}).get("duration")}))
                            discovered_at = video["discovered_at"]
                            video.update(normalize_video(fresh, fresh_match, place))
                            video["discovered_at"] = discovered_at
                            video.update(duration=count(item.get("video", {}).get("duration")),
                                         favorite_count=count(item.get("stats", {}).get("collectCount")),
                                         metadata_source="public_embedded_video_detail")
                    final = urlparse(driver.current_url)
                    if final.hostname == "www.tiktok.com" and final.path.endswith("/video/"+video["video_id"]):
                        video["video_url"] = final._replace(query="", fragment="").geturl()
                    # One observation per selected video; preserve its true source time.
                    # New runs have new directories, never replace previous snapshots.
                    snapshots.append(snapshot(video))
                    seen, cursors = set(), set()
                    for page in range(1, 6):
                        if page > 1:
                            move = scroll_comments(driver)
                            if not move.get("scrolled"):
                                diagnostic["stop_reason"] = "NO_COMMENT_SCROLL_SURFACE"; break
                        capture = collector.next()
                        search.detect_access_block(200, driver.current_url, driver.page_source)
                        if capture is None:
                            diagnostic["stop_reason"] = "COMMENT_RESPONSE_NOT_OBSERVED"; break
                        payload, evidence = capture
                        evidence.update(page_number=page, cursor_out=search.bounded_scalar(payload.get("cursor")),
                                        has_more=search.bounded_scalar(payload.get("has_more")))
                        diagnostic["pages"].append(evidence)
                        if payload.get("status_code", 0) != 0:
                            raise RuntimeError("COMMENT_APPLICATION_ERROR")
                        if not isinstance(payload.get("comments"), list):
                            raise ValueError("UNSUPPORTED_COMMENT_SCHEMA")
                        evidence.update(raw_comments=len(payload["comments"]), accepted_new=0, rejected_records=0,
                                        duplicate_records=0)
                        if len(payload["comments"]) > 200:
                            raise ValueError("COMMENT_PAGE_LIMIT")
                        for raw in payload["comments"]:
                            comment = normalize_comment(raw, video["video_id"], search.utc_now())
                            if comment is None:
                                evidence["rejected_records"] += 1
                                continue
                            if comment["comment_id"] in global_comment_ids:
                                duplicate_count += 1
                                evidence["duplicate_records"] += 1
                                continue
                            if len(seen) >= max_comments:
                                break
                            seen.add(comment["comment_id"])
                            global_comment_ids.add(comment["comment_id"])
                            comments.append(comment)
                            raw_comments.append(dict(video_id=video["video_id"], source=raw_comment_projection(raw)))
                            evidence["accepted_new"] += 1
                        diagnostic["successful"] = True
                        checkpoint()
                        if len(seen) >= max_comments:
                            diagnostic["stop_reason"] = "COMMENT_LIMIT"; break
                        if payload.get("has_more") in (0, False, "0"):
                            diagnostic["stop_reason"] = "HAS_MORE_ZERO"; break
                        if evidence["accepted_new"] == 0 or str(payload.get("cursor")) in cursors:
                            diagnostic["stop_reason"] = "NO_GROWTH_OR_REPEATED_CURSOR"; break
                        cursors.add(str(payload.get("cursor")))
                    else:
                        diagnostic["stop_reason"] = "PAGE_LIMIT"
                except search.ProbeStopped:
                    raise
                except Exception as exc:
                    diagnostic.update(error_type=type(exc).__name__, stop_reason="VIDEO_CAPTURE_ERROR")
                    known_errors = {"COMMENT_LOADING_FAILED", "COMMENT_HTTP_ERROR", "COMMENT_APPLICATION_ERROR",
                                    "UNSUPPORTED_COMMENT_SCHEMA", "COMMENT_PAGE_LIMIT", "INVALID_NAVIGATION_IDENTITY"}
                    diagnostic["error_code"] = str(exc) if str(exc) in known_errors else "UNCLASSIFIED_CAPTURE_FAILURE"
                checkpoint()
        finished = True
    except search.ProbeStopped as exc:
        errors.append(dict(type=type(exc).__name__, authentication_required=exc.authentication, challenge_detected=exc.challenge))
    except Exception as exc:
        errors.append(dict(type=type(exc).__name__, stage="browser_setup_or_run"))
    finally:
        try:
            if session:
                session.close()
        finally:
            checkpoint()
            logging.getLogger().removeHandler(handler)
            handler.close()
    return output


def validate_dom_match(data, catalog=None, *, allow_query_candidates=False):
    """Check saved MATCH against persisted canonical place and caption/address evidence."""
    from crawl_experiment.storage.place_catalog import DEFAULT_CATALOG, load_catalog
    selected=dict(data['selected_video']);place=dict(data['place'])
    matches=[p for p in load_catalog(catalog or DEFAULT_CATALOG).places if p.place_key==place.get('place_id')]
    if len(matches)!=1:raise ValueError('Canonical place not found in persisted catalog')
    canonical=matches[0]
    if normalized(canonical.name)!=normalized(place.get('canonical_name')) or normalized(canonical.address)!=normalized(place.get('address')):
        raise ValueError('Canonical place evidence mismatch')
    match=score_video(selected,place)
    query_candidate=allow_query_candidates and data.get('association_policy')=='QUERY_CANDIDATE_UNVERIFIED'
    if query_candidate and (selected.get('query_used') or selected.get('query')) not in place.get('queries',[]):
        raise ValueError('Query candidate must originate from the planned store query')
    if not query_candidate and (selected.get('decision')!='MATCH' or match['decision']!='MATCH' or 'ADDRESS_MATCH' not in match['signals']):
        raise ValueError('Verified address-qualified MATCH required')
    if selected.get('place_id')!=place['place_id']:raise ValueError('Video place association mismatch')
    url=selected.get('navigation_target') or selected.get('video_url')
    parsed=urlparse(url or '')
    if parsed.scheme!='https' or parsed.hostname!='www.tiktok.com' or parsed.query or parsed.fragment or parsed.username or parsed.password or not re.fullmatch(r'/@[\w.]+/video/'+re.escape(str(selected['video_id'])),parsed.path):
        raise ValueError('Observed clean video navigation target required')
    selected['video_url']=url
    return selected,place,match

def dom_input_digest(video, place, *, limit=7, comment_scrolls=0, reply_limit=0, reply_expand_limit=0):
    """One identity formula for legacy runner and managed adapter; no IO."""
    from experiments.tiktok_comment_dom.persistence import digest
    return digest({'video':video,'place':place,'limit':limit,
                   **({'comment_scrolls':comment_scrolls} if comment_scrolls else {}),
                   **({'reply_limit':reply_limit} if reply_limit else {}),
                   **({'reply_expand_limit':reply_expand_limit} if reply_expand_limit else {})})


def run_dom_comments(match_data, *, output, database, parquet_root, run_id, limit=7,
                     port=None, driver_path=None, resume=False, catalog=None, clock=time.monotonic,
                     challenge_resolver=None, telemetry=None, allow_query_candidates=False, comment_scrolls=0, reply_limit=0, reply_expand_limit=0,
                     repository_factory=None, defer_publication=False):
    """One existing DOM collector wired to Storage/SQLite/outbox/Parquet; no login/retries."""
    from types import SimpleNamespace
    from experiments.tiktok_comment_dom.probe import collect_video, SeleniumDOMPort
    from experiments.tiktok_comment_dom.extractor import CommentSession
    from experiments.tiktok_comment_dom.persistence import CommentPersistence, digest
    from experiments.tiktok_access_benchmark.crawler import Storage, output_record
    if type(limit) is not int or not 1<=limit<=50:raise ValueError('Single-video DOM integration permits 1..50 top-level comments')
    if type(comment_scrolls) is not int or not 0<=comment_scrolls<=10:raise ValueError('Comment scroll budget must be 0..10')
    video,place,match=validate_dom_match(match_data,catalog,allow_query_candidates=allow_query_candidates)
    if not isinstance(reply_limit,int) or isinstance(reply_limit,bool) or not 0<=reply_limit<=50:raise ValueError('Reply limit must be 0..50')
    if not isinstance(reply_expand_limit,int) or isinstance(reply_expand_limit,bool) or not 0<=reply_expand_limit<=5 or (reply_expand_limit and not reply_limit):raise ValueError('Reply expansion requires reply_limit>0 and expansion limit0..5')
    output=Path(output);identity=dom_input_digest(video,place,limit=limit,comment_scrolls=comment_scrolls,reply_limit=reply_limit,reply_expand_limit=reply_expand_limit)
    if (output/'summary.json').exists() and not resume:raise ValueError('Output run already exists; no overwrite')
    if resume and port is None:raise ValueError('Resume requires an existing browser port; no session replacement')
    if resume and getattr(getattr(port,'driver',None),'current_url',None)!=video['video_url']:
        raise ValueError('Keep checkpoint video open in the same browser')
    persistence_started=clock()
    repo=(repository_factory or CommentPersistence)(database,parquet_root)
    if telemetry is not None: telemetry.persistence(clock()-persistence_started)
    store=Storage(output,place['canonical_name'],{'max_comments_per_video':limit,'max_comment_scrolls':comment_scrolls})
    store.videos=[{'video_id':video['video_id'],'video_url':video['video_url'],'place_id':place['place_id'],'opened':False,'outcome':'IN_PROGRESS'}]
    store.comment_repository=repo;session=CommentSession(video['video_id'],limit,reply_limit)
    owner=None;started=clock();page=0;status='PARTIAL';reason=None;network=None
    def save(state, count, snapshot=None, evidence=None, final_stop=None):
        nonlocal page,status
        page=count
        if snapshot and urlparse(snapshot.get('url','')).hostname=='www.tiktok.com' and urlparse(snapshot.get('url','')).path==urlparse(video['video_url']).path and (snapshot.get('video_detail_rendered') or snapshot.get('cards')):
            store.videos[0]['opened']=True
        store.videos[0]['comments_count']=len(state.records)
        reason=(snapshot or {}).get('network_gate')
        evidence=dict(evidence or {})
        if reason in ('UNKNOWN_403','REQUIRED_ENDPOINT_ERROR','APPLICATION_ERROR'):evidence['failed']=True
        persistence_started=clock()
        try:
            result=store.persist_collector(state,run_id=run_id,place_id=place['place_id'],input_digest=identity,
                next_page=count,provenance={'mode':'BROWSER_DOM','place_association':('QUERY_CANDIDATE_UNVERIFIED' if allow_query_candidates and match_data.get('association_policy')=='QUERY_CANDIDATE_UNVERIFIED' else 'CATALOG_AND_ADDRESS_MATCH'),
                    'match_digest':digest(match_data),'matching_signals':match['signals'],
                    **({'integration_mapping':match_data['integration_mapping']} if match_data.get('integration_mapping') else {})},evidence=evidence,
                **({'final_observation':{'stop_reason':final_stop}} if repository_factory is not None and final_stop is not None else {}))
            status=result['status']
            store.add([output_record(r,video,place['canonical_name']) for r in state.records.values()])
            store.summary.update(status=status,place_id=place['place_id'],run_id=run_id,collector_stop=state.pagination.get('stop_reason'))
            store.checkpoint(network or SimpleNamespace(request_count=0,entries=[]))
            if not defer_publication:repo.flush()
        finally:
            if telemetry is not None: telemetry.persistence(clock()-persistence_started)
    try:
        saved=repo.load_checkpoint(run_id,place['place_id'],video['video_id'])
        if saved:
            if not resume:raise ValueError('Run checkpoint already exists; do not reset it')
            if saved['input_digest']!=identity:raise ValueError('Checkpoint MATCH/config changed')
            session=CommentSession.from_checkpoint(json.loads(saved['state_json']));page=saved['next_page']
            if saved['status'] in ('ACCESS_DENIED','RATE_LIMITED','COMPLETE'):
                status=saved['status'];reason='CHECKPOINT_TERMINAL';return {**repo.summary(),'status':status,'stop_reason':reason,'live_comments_this_attempt':0}
        elif resume:raise ValueError('Checkpoint not found')
        if not defer_publication:repo.flush()
        if port is None:
            from experiments.tiktok_access_benchmark.benchmark import BrowserObserver
            from experiments.tiktok_access_benchmark.ui_flow import NetworkCapture
            owner=BrowserObserver(driver_path=driver_path,capture_dir=output)
            network=NetworkCapture(owner.driver);network.start()
            port=SeleniumDOMPort(driver=owner.driver,network=network)
        else:network=getattr(port,'network',None)
        if len(port.driver.window_handles)!=1:raise ValueError('Exactly one browser tab required')
        base=page
        snapshots,actions,reason=collect_video(port,video,limit=limit,max_scrolls=comment_scrolls,collector=session,
            on_collect=lambda state,count,snapshot:save(state,base+count,snapshot),resume_in_place=resume,
            challenge_resolver=challenge_resolver,telemetry=telemetry,reply_expand_limit=reply_expand_limit)
        if telemetry is not None: telemetry.finish()
        save(session,page,final_stop=reason)
        store.summary.update(collection_stop=reason,actions=actions)
        if snapshots:
            last_snap = snapshots[-1]
            diagnostics = [{'observed_at':item['observed_at'],
                'before_onboarding_dismissal_at':item.get('before_onboarding_dismissal_at'),
                'dismissed_at':item.get('dismissed_at'),
                'fresh_after_dismissal':item.get('fresh_after_dismissal',False),
                'comment_panel_present':item['snapshot'].get('comment_panel_present'),
                'comments_loading':item['snapshot'].get('comments_loading'),
                'visible_comment_cards':len(item['snapshot'].get('cards',[])),
                'wait_timeout':item['snapshot'].get('wait_timeout',False),
                'dialogs':item['snapshot'].get('dialog_diagnostics',[])} for item in snapshots]
            (output/'dialog_diagnostics.json').write_text(json.dumps({
                'snapshots':diagnostics,'actions':actions,'safe_stop_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                'stop_reason':reason},indent=2),encoding='utf-8')
            for k in ('challenge_details','challenge_markers','challenge_text_signals','challenge_reason'):
                if last_snap.get(k): store.summary[k] = last_snap[k]
    except BaseException as exc:
        reason='INTERRUPTED' if isinstance(exc,KeyboardInterrupt) else type(exc).__name__
        from experiments.tiktok_access_benchmark.crawler import Gate, UIFailure
        if isinstance(exc,(Gate,UIFailure)) and re.fullmatch(r'[A-Z][A-Z0-9_]{1,99}',str(exc)):
            reason=str(exc)
        # Keep every committed observation and preserve collector checkpoint on interruption.
        if not isinstance(exc,ValueError):
            try:save(session,page,evidence={'failed':not isinstance(exc,KeyboardInterrupt)},final_stop=reason)
            except Exception:status='FAILED'
        else:status='FAILED'
        store.summary.update(error_type=type(exc).__name__,collection_stop=reason)
        import traceback
        (output/'error.json').write_text(json.dumps({'error_type':type(exc).__name__,
            'stop_reason':reason,'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
            for f in traceback.extract_tb(exc.__traceback__)]},indent=2),encoding='utf-8')
        if repository_factory is not None:
            raise  # Managed ownership/commit failures must reach the supervisor.
    finally:
        if telemetry is not None: telemetry.finish()
        if port is not None and getattr(port,'driver',None) is not None:
            try:port.driver.save_screenshot(str(output/'final.png'))
            except Exception:pass
        if 'snapshots' in locals() and snapshots:
            last_snap = snapshots[-1]
            for k in ('challenge_details','challenge_markers','challenge_text_signals','challenge_reason'):
                if last_snap.get(k): store.summary[k] = last_snap[k]
        report={**repo.summary(),'status':status,'stop_reason':reason,'video_id':video['video_id'],
            'collection_quality':session.quality(),
            'place_id':place['place_id'],'comments_in_collector':len(session.records),
            'latency_seconds':clock()-started,'database':str(Path(database).resolve()),
            'parquet_root':str(Path(parquet_root).resolve()),'network_observation':'OBSERVED' if network and network.entries else 'UNOBSERVED'}
        store.videos[0].update(outcome=status,collection_stop=reason,comments_count=len(session.records))
        store.summary.update(report)
        try:store.checkpoint(network or SimpleNamespace(request_count=0,entries=[]))
        finally:
            if owner:owner.lifecycle.close()
            store.comment_repository=None;repo.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--source-run", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--place-file", type=Path, default=DEFAULT_PLACE)
    parser.add_argument("--max-videos", type=int, default=5)
    parser.add_argument("--max-comments", type=int, default=50)
    parser.add_argument('--dom-replay',type=Path,help='Offline rendered DOM snapshots; no browser/login')
    parser.add_argument('--state-db',type=Path)
    parser.add_argument('--parquet-root',type=Path)
    parser.add_argument('--run-id')
    parser.add_argument('--place-id',help='Explicit video-to-place association for offline import')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--max-replay-pages',type=int,help='Bounded interruption simulation; resume same run')
    parser.add_argument('--dom-live-match',type=Path,help='Single verified MATCH artifact, DOM collector + persistence')
    parser.add_argument('--chromedriver',type=Path)
    parser.add_argument('--manual-captcha',action='store_true',help='Pause session on active CAPTCHA to allow human operator manual verification')
    parser.add_argument('--manual-captcha-timeout',type=int,default=120,help='Max seconds to wait for manual CAPTCHA verification')
    args = parser.parse_args()
    if args.dom_live_match:
        if not args.live or args.dom_replay or args.place_id or args.max_replay_pages:
            parser.error('DOM live needs --live, and forbids replay or fixture place mapping')
        if not all((args.state_db,args.parquet_root,args.run_id,args.output)):
            parser.error('DOM live needs --state-db --parquet-root --run-id --output')
        if args.resume:parser.error('Live resume requires an existing same-page port via run_dom_comments; no new browser on resume')
        resolver = None
        if args.manual_captcha:
            from experiments.tiktok_comment_dom.probe import InteractiveChallengeResolver
            resolver = InteractiveChallengeResolver(timeout=args.manual_captcha_timeout)
        result=run_dom_comments(json.loads(args.dom_live_match.read_text(encoding='utf-8')),
            output=args.output,database=args.state_db,parquet_root=args.parquet_root,run_id=args.run_id,
            limit=min(args.max_comments,7),driver_path=args.chromedriver,challenge_resolver=resolver)
        print(json.dumps(result,ensure_ascii=False));return
    if args.chromedriver:parser.error('--chromedriver requires --dom-live-match')
    if args.dom_replay:
        if args.live:parser.error('DOM replay cannot run live')
        if not all((args.state_db,args.parquet_root,args.run_id,args.place_id,args.output)):
            parser.error('DOM replay requires --state-db --parquet-root --run-id --place-id --output')
        if args.max_replay_pages is not None and args.max_replay_pages<1:parser.error('Replay page limit must be positive')
        from experiments.tiktok_access_benchmark.crawler import Storage
        from experiments.tiktok_comment_dom.persistence import replay_comments
        storage=Storage(args.output,'OFFLINE_DOM_REPLAY',{'max_comments_per_video':50})
        existing=args.output/'summary.json'
        if existing.exists():storage.restore(json.loads(existing.read_text(encoding='utf-8')))
        result=replay_comments(json.loads(args.dom_replay.read_text(encoding='utf-8')),storage=storage,
            database=args.state_db,parquet_root=args.parquet_root,run_id=args.run_id,
            place_id=args.place_id,resume=args.resume,max_pages=args.max_replay_pages)
        print(json.dumps(result,ensure_ascii=False));return
    if any((args.resume,args.state_db,args.parquet_root,args.run_id,args.place_id,args.output,args.max_replay_pages)):
        parser.error('Persistence options require --dom-replay')
    if not 1 <= args.max_videos <= 5 or not 1 <= args.max_comments <= 50:
        parser.error("max-videos must be 1..5; max-comments must be 1..50")
    output = Path(__file__).parent/"output"/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"))
    print(run(args.source_run, args.place_file, output, args.live, args.max_videos, args.max_comments))


if __name__ == "__main__":
    main()
