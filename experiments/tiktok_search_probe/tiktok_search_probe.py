"""Bounded public TikTok keyword-search metadata experiment."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from selenium.common.exceptions import StaleElementReferenceException


MAX_QUERIES = 1
MAX_VIDEOS = 10
HTTP_TIMEOUT_SECONDS = 15
MAX_HTTP_ATTEMPTS = 2
PAGE_LOAD_TIMEOUT_SECONDS = 30
READINESS_RECHECK_SECONDS = 2
BROWSER_WARMUP_DELAY_SECONDS = 3
NETWORK_OBSERVATION_SECONDS = 10
MAX_STRUCTURAL_PATHS = 100
MAX_VIDEO_PATHS = 50
MAX_CURSOR_VALUES = 20
MAX_NETWORK_CANDIDATES = 20
TARGET_SEARCH_ENDPOINT = "/api/search/general/full/"
OUTPUT_DIRECTORY = Path(__file__).resolve().parent / "output"
DEFAULT_QUERIES = (
    "No.1 Malatang",
)
STRUCTURED_IDS = ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE")
INTERESTING_KEYS = {
    "itemlist",
    "item_list",
    "search_item_list",
    "aweme_list",
    "aweme_info",
    "itemstruct",
    "video",
    "author",
    "stats",
    "statistics",
    "playcount",
    "play_count",
    "diggcount",
    "commentcount",
    "sharecount",
    "cursor",
    "maxcursor",
    "max_cursor",
    "hasmore",
    "has_more",
}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
CHALLENGE_MARKERS = (
    "verify to continue",
    "security verification",
    'id="captcha_container"',
    'class="captcha-verify-container"',
)

logger = logging.getLogger("tiktok_search_probe")


class ProbeStopped(RuntimeError):
    def __init__(self, message: str, *, authentication=False, challenge=False):
        super().__init__(message)
        self.authentication = authentication
        self.challenge = challenge


class PerformanceLogUnavailableError(RuntimeError):
    def __init__(self, available_log_types: list[str]):
        super().__init__(
            "plain Chrome does not expose the required performance log; "
            f"available log types: {available_log_types}"
        )
        self.available_log_types = available_log_types


class PlainChromeSession:
    """Compatibility name for the standard SeleniumBase lifecycle owner."""

    def __init__(self):
        self.driver = None
        self.available_log_types: list[str] = []
        self.network_domain_enabled = False
        self.lifecycle = None
        self.backend = "seleniumbase-standard"

    def start(self):
        if self.driver is not None:
            return self.driver
        from experiments.tiktok_access_benchmark.session_manager import TikTokSessionManager, standard_browser_factory
        if self.lifecycle is None:
            self.lifecycle = TikTokSessionManager(standard_browser_factory())
        try:
            self.driver = self.lifecycle.acquire('tiktok-search', 1).driver
            self.available_log_types = require_performance_log(self.driver.log_types)
            self.driver.execute_cdp_cmd("Network.enable", {})
            self.network_domain_enabled = True
            return self.driver
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        driver, self.driver = self.driver, None
        if self.lifecycle is not None:
            self.lifecycle.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def search_url(query: str) -> str:
    return "https://www.tiktok.com/search?" + urllib.parse.urlencode({"q": query})


def request_html(url: str) -> tuple[str, int, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    last_error: Exception | None = None
    for attempt in range(1, MAX_HTTP_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8", errors="replace")
                logger.info(
                    "HTTP query_url=%s status=%s final_url=%s attempt=%s",
                    url,
                    response.status,
                    response.geturl(),
                    attempt,
                )
                return body, response.status, response.geturl()
        except urllib.error.HTTPError as exc:
            last_error = exc
            logger.info(
                "HTTP query_url=%s status=%s final_url=%s attempt=%s",
                url,
                exc.code,
                exc.geturl(),
                attempt,
            )
            if exc.code in {401, 403, 429}:
                raise ProbeStopped(
                    f"TikTok returned HTTP {exc.code}",
                    authentication=exc.code == 401,
                    challenge=exc.code in {403, 429},
                ) from exc
            if exc.code < 500 or attempt == MAX_HTTP_ATTEMPTS:
                raise
        except (TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            logger.warning("HTTP attempt=%s failed: %s", attempt, type(exc).__name__)
            if attempt == MAX_HTTP_ATTEMPTS:
                raise
    raise RuntimeError(f"request failed: {last_error}")


def detect_access_block(status: int, final_url: str, html: str) -> None:
    path = urllib.parse.urlparse(final_url).path.lower()
    lowered = html.lower()
    if status == 401 or path.startswith("/login"):
        raise ProbeStopped("TikTok authentication is required", authentication=True)
    if (
        status in {403, 429}
        or path.startswith(("/verify", "/captcha"))
        or any(marker in lowered for marker in CHALLENGE_MARKERS)
    ):
        raise ProbeStopped("TikTok challenge or access block detected", challenge=True)


def script_snapshots_from_html(html: str) -> list[dict[str, Any]]:
    scripts: list[dict[str, Any]] = []
    pattern = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
    for index, match in enumerate(pattern.finditer(html)):
        attributes, text = match.groups()
        id_match = re.search(r"\bid=[\"']([^\"']+)[\"']", attributes, re.IGNORECASE)
        type_match = re.search(r"\btype=[\"']([^\"']+)[\"']", attributes, re.IGNORECASE)
        src_match = re.search(r"\bsrc=[\"']([^\"']+)[\"']", attributes, re.IGNORECASE)
        scripts.append(
            {
                "index": index,
                "id": id_match.group(1) if id_match else None,
                "type": type_match.group(1) if type_match else None,
                "src": src_match.group(1) if src_match else None,
                "text": text,
            }
        )
    return scripts


def payloads_from_scripts(
    scripts: list[Any],
    errors: list[str],
) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, Any]]]:
    payloads: list[tuple[str, dict[str, Any]]] = []
    containers: list[dict[str, Any]] = []
    for index, script in enumerate(scripts):
        if not isinstance(script, dict):
            errors.append(f"script[{index}]: expected object")
            continue
        script_id = str(script.get("id") or "").strip()
        script_type = str(script.get("type") or "").strip().lower()
        source = str(script.get("src") or "").strip()
        if source:
            parsed = urllib.parse.urlparse(source)
            source = urllib.parse.urlunparse(
                (parsed.scheme, parsed.netloc, parsed.path, "", "", "")
            )
        if script_id or script_type:
            containers.append(
                {
                    "index": script.get("index", index),
                    "id": script_id or None,
                    "type": script_type or None,
                    "src": source or None,
                }
            )
        relevant = (
            script_id in STRUCTURED_IDS
            or script_type == "application/json"
            or any(token in script_id.casefold() for token in ("state", "data", "universal"))
        )
        if not relevant:
            continue
        try:
            value = json.loads(str(script.get("text") or ""))
        except (json.JSONDecodeError, TypeError) as exc:
            errors.append(f"script[{index}] {script_id or script_type}: {type(exc).__name__}")
            continue
        if isinstance(value, dict):
            payloads.append((script_id or f"application-json-{index}", value))
    return payloads, containers


def json_path(parent: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{parent}[{key}]" if parent else f"[{key}]"
    return f"{parent}.{key}" if parent else str(key)


def walk_values(value: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    """Walk an immutable JSON-compatible value and retain its structural path."""
    yield path or "$", value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk_values(child, json_path(path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_values(child, json_path(path, index))


def video_identity(item: dict[str, Any]) -> Any:
    return first_value(item, "aweme_id", "id", "itemId", "video_id")


def is_video_like(item: dict[str, Any]) -> bool:
    return video_identity(item) is not None and any(
        key in item
        for key in ("author", "author_info", "stats", "statistics", "desc", "caption", "video")
    )


def bounded_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:200]
    return f"<{type(value).__name__}>"


def structural_summary(
    script: dict[str, Any],
    *,
    fallback_index: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    text = str(script.get("text") or "")
    script_id = str(script.get("id") or "").strip() or None
    script_type = str(script.get("type") or "").strip() or None
    source = str(script.get("src") or "").strip()
    if source:
        parsed_source = urllib.parse.urlparse(source)
        source = urllib.parse.urlunparse(
            (parsed_source.scheme, parsed_source.netloc, parsed_source.path, "", "", "")
        )
    record: dict[str, Any] = {
        "index": script.get("index", fallback_index),
        "script_id": script_id,
        "type": script_type,
        "src": source or None,
        "text_length": len(text),
        "json_parseable": False,
        "top_level_keys": [],
        "interesting_paths": [],
        "video_like_object_paths": [],
        "candidate_video_count": 0,
        "cursor_like_values": [],
        "has_more_like_values": [],
    }
    if not text.strip():
        return record, None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return record, None
    record["json_parseable"] = True
    if isinstance(payload, dict):
        record["top_level_keys"] = list(payload)[:50]
    elif isinstance(payload, list):
        record["top_level_keys"] = ["<list>"]
    for path, value in walk_values(payload):
        leaf = path.rsplit(".", 1)[-1].split("[", 1)[0].casefold()
        if leaf in INTERESTING_KEYS and len(record["interesting_paths"]) < MAX_STRUCTURAL_PATHS:
            record["interesting_paths"].append(path)
        if isinstance(value, dict) and is_video_like(value):
            record["candidate_video_count"] += 1
            if len(record["video_like_object_paths"]) < MAX_VIDEO_PATHS:
                record["video_like_object_paths"].append(path)
        if leaf in {"cursor", "maxcursor", "max_cursor"} and len(record["cursor_like_values"]) < MAX_CURSOR_VALUES:
            record["cursor_like_values"].append({"path": path, "value": bounded_scalar(value)})
        if leaf in {"hasmore", "has_more"} and len(record["has_more_like_values"]) < MAX_CURSOR_VALUES:
            record["has_more_like_values"].append({"path": path, "value": bounded_scalar(value)})
    return record, payload if isinstance(payload, dict) else None


def diagnose_structured_scripts(
    scripts: list[Any],
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    payloads: list[tuple[str, dict[str, Any]]] = []
    records: list[dict[str, Any]] = []
    for index, raw_script in enumerate(scripts):
        script = raw_script if isinstance(raw_script, dict) else {"text": ""}
        record, payload = structural_summary(script, fallback_index=index)
        records.append(record)
        if payload is not None:
            payloads.append((record["script_id"] or f"json-script-{index}", payload))
    return payloads, {
        "script_containers": records,
        "parseable_json_script_count": sum(record["json_parseable"] for record in records),
        "top_level_keys": list(
            dict.fromkeys(key for record in records for key in record["top_level_keys"])
        )[:100],
        "interesting_key_paths": [
            path for record in records for path in record["interesting_paths"]
        ][:MAX_STRUCTURAL_PATHS],
        "video_like_object_paths": [
            path for record in records for path in record["video_like_object_paths"]
        ][:MAX_VIDEO_PATHS],
        "candidate_video_count": sum(record["candidate_video_count"] for record in records),
        "cursor_like_values": [
            value for record in records for value in record["cursor_like_values"]
        ][:MAX_CURSOR_VALUES],
        "has_more_like_values": [
            value for record in records for value in record["has_more_like_values"]
        ][:MAX_CURSOR_VALUES],
    }


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def nested(mapping: dict[str, Any], *path: str) -> Any:
    value: Any = mapping
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def count_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMB])?", text, re.IGNORECASE)
    if not match:
        return None
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return int(float(match.group(1)) * multiplier[(match.group(2) or "").upper()])


def timestamp(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def hashtags(item: dict[str, Any], caption: str | None) -> list[str]:
    values: list[str] = []
    extras = item.get("textExtra") or item.get("text_extra") or []
    if isinstance(extras, list):
        for extra in extras:
            if isinstance(extra, dict):
                tag = first_value(extra, "hashtagName", "hashtag_name", "name")
                if tag:
                    values.append(str(tag).lstrip("#"))
    if caption:
        values.extend(re.findall(r"(?<!\w)#([\w]+)", caption, re.UNICODE))
    return list(dict.fromkeys(value for value in values if value))


def normalize_video(
    item: dict[str, Any],
    *,
    business_name: str,
    query: str,
    observed_at: str,
) -> dict[str, Any] | None:
    video_id_value = first_value(item, "id", "aweme_id", "itemId", "video_id")
    author = item.get("author")
    stats = item.get("stats")
    if video_id_value is None or not isinstance(author, dict) or not isinstance(stats, dict):
        return None
    video_id = str(video_id_value).strip()
    creator_username = first_value(author, "uniqueId", "unique_id")
    creator_username = str(creator_username).strip() if creator_username is not None else ""
    published_at = timestamp(item.get("createTime"))
    if not video_id or not creator_username or published_at is None:
        return None
    caption = first_value(item, "desc", "caption", "description")
    video_url = first_value(item, "shareUrl", "share_url", "videoUrl", "video_url")
    if video_url is None:
        video_url = (
            nested(item, "shareInfo", "shareUrl")
            or nested(item, "share_info", "share_url")
            or nested(item, "shareMeta", "href")
        )
    return {
        "business_name": business_name,
        "query": query,
        "video_id": video_id,
        "creator_id": first_value(author, "id", "uid"),
        "creator_username": creator_username,
        "creator_display_name": author.get("nickname"),
        "published_at": published_at,
        "caption": caption,
        "hashtags": hashtags(item, str(caption) if caption is not None else None),
        "video_url": video_url,
        "view_count": count_value(stats.get("playCount")),
        "like_count": count_value(stats.get("diggCount")),
        "comment_count": count_value(stats.get("commentCount")),
        "share_count": count_value(stats.get("shareCount")),
        "query_that_found_it": query,
        "observed_at": observed_at,
    }


def videos_from_payloads(
    payloads: list[tuple[str, dict[str, Any]]],
    *,
    business_name: str,
    query: str,
    limit: int,
    observed_at: str,
) -> list[dict[str, Any]]:
    videos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, payload in payloads:
        data = payload.get("data")
        if not isinstance(data, list):
            continue
        for result in data:
            if not isinstance(result, dict) or not isinstance(result.get("item"), dict):
                continue
            item = result["item"]
            video = normalize_video(
                item,
                business_name=business_name,
                query=query,
                observed_at=observed_at,
            )
            if video is None or video["video_id"] in seen:
                continue
            seen.add(video["video_id"])
            videos.append(video)
            if len(videos) >= limit:
                return videos
    return videos


def pagination_evidence(payloads: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    evidence = {"cursor_found": False, "has_more_found": False}
    for _, payload in payloads:
        for mapping in walk_dicts(payload):
            if any(key in mapping for key in ("cursor", "maxCursor", "max_cursor")):
                evidence["cursor_found"] = True
            if any(key in mapping for key in ("hasMore", "has_more")):
                evidence["has_more_found"] = True
    return evidence


def plain_chrome_options():
    from selenium import webdriver

    options = webdriver.ChromeOptions()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return options


def require_performance_log(log_types: Any) -> list[str]:
    available = list(log_types or [])
    if "performance" not in available:
        raise PerformanceLogUnavailableError(available)
    return available


def browser_session() -> PlainChromeSession:
    return PlainChromeSession()


def diagnostic_read(label: str, reader, errors: list[str], default=None):
    for attempt in (1, 2):
        try:
            return reader()
        except StaleElementReferenceException as exc:
            errors.append(f"{label}: stale attempt={attempt}: {exc}")
            if attempt == 1:
                continue
            return default
        except Exception as exc:  # noqa: BLE001 - best-effort diagnostics
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            return default
    return default


def readiness(driver, sleeper, errors: list[str]) -> str | None:
    state = diagnostic_read(
        "ready_state", lambda: driver.execute_script("return document.readyState;"), errors
    )
    if state not in {"interactive", "complete"}:
        sleeper(READINESS_RECHECK_SECONDS)
        state = diagnostic_read(
            "ready_state_recheck",
            lambda: driver.execute_script("return document.readyState;"),
            errors,
        )
    return state


def dom_snapshot(driver, errors: list[str]) -> dict[str, Any]:
    javascript = """
        return {
            readyState: document.readyState,
            scripts: Array.from(document.scripts).map((s, i) => ({
                index: i, id: s.id || null, type: s.type || null,
                src: s.src || null, text: s.textContent || ""
            })),
            resources: performance.getEntriesByType('resource').map(entry => ({
                name: entry.name, initiatorType: entry.initiatorType || null
            }))
        };
    """
    value = diagnostic_read(
        "dom_snapshot", lambda: driver.execute_script(javascript), errors, {}
    )
    return value if isinstance(value, dict) else {}


def warmup_browser(driver, sleeper=time.sleep) -> dict[str, Any]:
    """Prime one browser session without counting or issuing a search query."""
    errors: list[str] = []
    diagnostic_read(
        "warmup_page_load_timeout",
        lambda: driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS),
        errors,
    )
    try:
        driver.get("https://www.tiktok.com/")
    except Exception as exc:  # noqa: BLE001 - preserve bounded diagnostics
        errors.append(f"warmup_navigation: {type(exc).__name__}: {exc}")
    ready_state = readiness(driver, sleeper, errors)
    sleeper(BROWSER_WARMUP_DELAY_SECONDS)
    final_url = diagnostic_read(
        "warmup_final_url", lambda: driver.current_url, errors, ""
    )
    page_title = diagnostic_read("warmup_page_title", lambda: driver.title, errors)
    page_source = diagnostic_read(
        "warmup_page_source", lambda: driver.page_source, errors, ""
    ) or ""
    detect_access_block(200, final_url, page_source)
    diagnostic_read(
        "warmup_clear_performance_log",
        lambda: driver.get_log("performance"),
        errors,
        [],
    )
    network_enabled = True
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception as exc:  # noqa: BLE001 - caller records and exits cleanly
        network_enabled = False
        errors.append(f"Network.enable: {type(exc).__name__}: {exc}")
    return {
        "final_url": final_url,
        "page_title": page_title,
        "document_ready_state": ready_state,
        "network_enabled": network_enabled,
        "errors": errors,
    }


def should_reload_first_query(
    *,
    is_first_query: bool,
    diagnostic: dict[str, Any],
    matching_responses: list[dict[str, Any]],
) -> bool:
    return (
        is_first_query
        and not matching_responses
        and diagnostic.get("document_ready_state") in {"interactive", "complete"}
    )


def relevant_network_url(url: str) -> bool:
    lowered = url.casefold()
    return any(token in lowered for token in ("search", "item", "video", "aweme"))


def sanitized_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def redacted_path(url: str) -> str:
    return urllib.parse.urlparse(url).path


def parse_network_response_events(entries: Any) -> list[dict[str, Any]]:
    """Parse only matching response metadata from a performance-log snapshot."""
    matches: list[dict[str, Any]] = []
    request_methods: dict[str, Any] = {}
    if not isinstance(entries, list):
        return matches
    for entry in entries:
        try:
            envelope = json.loads(entry.get("message", ""))
            message = envelope["message"]
            params = message.get("params") or {}
            request_id = str(params.get("requestId") or "")
            if message.get("method") == "Network.requestWillBeSent":
                request_methods[request_id] = (params.get("request") or {}).get("method")
                continue
            if message.get("method") != "Network.responseReceived":
                continue
            response = params.get("response") or {}
            url = str(response.get("url") or "")
            if TARGET_SEARCH_ENDPOINT not in url:
                continue
            matches.append(
                {
                    "path": redacted_path(url),
                    "status": response.get("status"),
                    "mime_type": response.get("mimeType"),
                    "resource_type": params.get("type"),
                    "requestId": request_id,
                    "method": request_methods.get(request_id),
                }
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return matches


def minimal_video_sample(video: dict[str, Any]) -> dict[str, Any]:
    return {
        "video_id": video.get("video_id"),
        "creator_id": video.get("creator_id"),
        "creator_username": video.get("creator_username"),
        "creator_display_name": video.get("creator_display_name"),
        "caption": video.get("caption"),
        "stats": {
            "view_count": video.get("view_count"),
            "like_count": video.get("like_count"),
            "comment_count": video.get("comment_count"),
            "share_count": video.get("share_count"),
        },
    }


def analyze_network_body(
    body_result: Any,
    *,
    response: dict[str, Any],
    business_name: str,
    query: str,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostic = dict(response)
    diagnostic.update(
        body_captured=False,
        body_length=0,
        json_parseable=False,
        top_level_keys=[],
        interesting_paths=[],
        video_like_object_paths=[],
        video_like_object_count=0,
        cursor_like_values=[],
        has_more_like_values=[],
        cursor=None,
        has_more=None,
        minimal_video_sample=None,
        error=None,
    )
    if not isinstance(body_result, dict) or not isinstance(body_result.get("body"), str):
        diagnostic["error"] = "Network.getResponseBody returned no text body"
        return [], diagnostic
    raw_body = body_result["body"]
    diagnostic["body_captured"] = True
    if body_result.get("base64Encoded"):
        try:
            body_bytes = base64.b64decode(raw_body, validate=True)
            body_text = body_bytes.decode("utf-8", errors="replace")
            diagnostic["body_length"] = len(body_bytes)
        except (ValueError, UnicodeError) as exc:
            diagnostic["error"] = f"base64 body decode failed: {type(exc).__name__}"
            return [], diagnostic
    else:
        body_text = raw_body
        diagnostic["body_length"] = len(body_text.encode("utf-8"))
    record, payload = structural_summary(
        {
            "index": 0,
            "id": "network-search-response",
            "type": diagnostic.get("mime_type"),
            "text": body_text,
        },
        fallback_index=0,
    )
    diagnostic.update(
        json_parseable=record["json_parseable"],
        top_level_keys=record["top_level_keys"],
        interesting_paths=record["interesting_paths"],
        video_like_object_paths=record["video_like_object_paths"],
        video_like_object_count=record["candidate_video_count"],
        cursor_like_values=record["cursor_like_values"],
        has_more_like_values=record["has_more_like_values"],
    )
    if payload is None:
        diagnostic["error"] = "response body is not a JSON object"
        return [], diagnostic
    diagnostic["cursor"] = bounded_scalar(payload.get("cursor"))
    diagnostic["has_more"] = bounded_scalar(payload.get("has_more"))
    videos = videos_from_payloads(
        [("network-search-response", payload)],
        business_name=business_name,
        query=query,
        limit=limit,
        observed_at=utc_now(),
    )
    if videos:
        diagnostic["minimal_video_sample"] = minimal_video_sample(videos[0])
    return videos, diagnostic


def capture_search_response(
    driver,
    *,
    business_name: str,
    query: str,
    limit: int,
    errors: list[str],
    sleeper=time.sleep,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sleeper(NETWORK_OBSERVATION_SECONDS)
    entries = diagnostic_read(
        "performance_log", lambda: driver.get_log("performance"), errors, []
    )
    matches = parse_network_response_events(entries)
    if not matches:
        errors.append(f"network_capture: endpoint not observed: {TARGET_SEARCH_ENDPOINT}")
        return [], []
    response = matches[0]
    try:
        body_result = driver.execute_cdp_cmd(
            "Network.getResponseBody", {"requestId": response["requestId"]}
        )
    except Exception as exc:  # noqa: BLE001 - bounded observation must fail cleanly
        failed = dict(response)
        failed.update(
            body_captured=False,
            body_length=0,
            json_parseable=False,
            error=f"Network.getResponseBody failed: {type(exc).__name__}: {exc}",
        )
        errors.append(failed["error"])
        return [], [failed]
    videos, diagnostic = analyze_network_body(
        body_result,
        response=response,
        business_name=business_name,
        query=query,
        limit=limit,
    )
    if diagnostic.get("error"):
        errors.append(f"network_capture: {diagnostic['error']}")
    return videos, [diagnostic]


def resource_network_candidates(resources: list[Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for value in resources:
        resource = value if isinstance(value, dict) else {"name": value}
        url = str(resource.get("name") or "")
        if not relevant_network_url(url):
            continue
        candidate = {
            "url": sanitized_url(url),
            "method": None,
            "status": None,
            "resource_type": resource.get("initiatorType"),
            "content_type": None,
        }
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates[:MAX_NETWORK_CANDIDATES]


def browser_query(
    driver,
    *,
    url: str,
    business_name: str,
    query: str,
    limit: int,
    sleeper=time.sleep,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
]:
    errors: list[str] = []
    diagnostic_read(
        "page_load_timeout",
        lambda: driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS),
        errors,
    )
    try:
        driver.get(url)
    except Exception as exc:  # noqa: BLE001 - source can remain available after timeout
        errors.append(f"navigation: {type(exc).__name__}: {exc}")
    ready_state = readiness(driver, sleeper, errors)
    final_url = diagnostic_read("final_url", lambda: driver.current_url, errors, url)
    title = diagnostic_read("page_title", lambda: driver.title, errors)
    page_source = diagnostic_read("page_source", lambda: driver.page_source, errors, "") or ""
    detect_access_block(200, final_url, page_source)
    snapshot = dom_snapshot(driver, errors)
    scripts = snapshot.get("scripts") if isinstance(snapshot.get("scripts"), list) else []
    resources = snapshot.get("resources") if isinstance(snapshot.get("resources"), list) else []
    if not scripts:
        scripts = script_snapshots_from_html(page_source)
    payloads, state_diagnostic = diagnose_structured_scripts(scripts)
    containers = [
        {
            "index": record["index"],
            "id": record["script_id"],
            "type": record["type"],
            "src": record["src"],
            "text_length": record["text_length"],
            "json_parseable": record["json_parseable"],
        }
        for record in state_diagnostic["script_containers"]
        if record["script_id"] or record["type"] or record["src"]
    ]
    observed_at = utc_now()
    videos = videos_from_payloads(
        payloads,
        business_name=business_name,
        query=query,
        limit=limit,
        observed_at=observed_at,
    )
    pagination = pagination_evidence(payloads)
    network = resource_network_candidates(resources)
    matching_responses: list[dict[str, Any]] = []
    if state_diagnostic["candidate_video_count"] == 0:
        captured_videos, matching_responses = capture_search_response(
            driver,
            business_name=business_name,
            query=query,
            limit=limit,
            errors=errors,
        )
        if captured_videos:
            videos = captured_videos
    state_diagnostic.update(
        {
            "query": query,
            "network_candidates": network,
        }
    )
    return videos, {
        "query": query,
        "method": "browser_rendered_html",
        "page_title": title,
        "final_url": final_url,
        "rendered_html_length": len(page_source),
        "document_ready_state": ready_state or snapshot.get("readyState"),
        "script_count": len(scripts),
        "structured_data_found": bool(payloads),
        "script_containers": containers,
        "network_candidates": network,
        **pagination,
        "videos_found": len(videos),
        "errors": errors,
    }, state_diagnostic, matching_responses


def run(business_name: str, queries: list[str], limit: int) -> dict[str, Any]:
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    diagnostics: list[dict[str, Any]] = []
    state_diagnostics: list[dict[str, Any]] = []
    matching_responses: list[dict[str, Any]] = []
    errors: list[str] = []
    http_statuses: list[int] = []
    summary: dict[str, Any] = {
        "business_name": business_name,
        "queries_attempted": 0,
        "queries_succeeded": 0,
        "unique_videos_collected": 0,
        "method_used": None,
        "browser_fallback_used": False,
        "structured_data_found": False,
        "challenge_detected": False,
        "authentication_required": False,
        "http_statuses": http_statuses,
        "page_titles": [],
        "final_urls": [],
        "rendered_html_lengths": [],
        "script_state_containers_found": False,
        "cursor_found": False,
        "has_more_found": False,
        "cursor": None,
        "has_more": None,
        "network_response_observed": False,
        "network_body_captured": False,
        "browser_warmup_used": False,
        "warmup_final_url": None,
        "warmup_page_title": None,
        "first_query_reloaded": False,
        "first_query_target_xhr_observed": False,
        "browser_backend": "seleniumbase-standard",
        "available_log_types": [],
        "network_domain_enabled": False,
        "target_endpoint_observed": False,
        "target_status": None,
        "target_mime_type": None,
        "target_resource_type": None,
        "response_body_captured": False,
        "response_body_length": 0,
        "errors": errors,
    }
    session = None
    driver = None
    stop = False
    try:
        session = browser_session()
        try:
            driver = session.start()
        except PerformanceLogUnavailableError as exc:
            summary["available_log_types"] = exc.available_log_types
            errors.append(f"browser_backend: {exc}")
            stop = True
        except Exception as exc:  # noqa: BLE001 - do not navigate after setup failure
            errors.append(f"browser_backend: {type(exc).__name__}: {exc}")
            stop = True
        if driver is not None:
            summary["available_log_types"] = session.available_log_types
            summary["network_domain_enabled"] = session.network_domain_enabled
            summary["browser_warmup_used"] = True
            try:
                warmup = warmup_browser(driver)
                summary["warmup_final_url"] = warmup["final_url"]
                summary["warmup_page_title"] = warmup["page_title"]
                errors.extend(f"browser_warmup: {error}" for error in warmup["errors"])
                if not warmup["network_enabled"]:
                    summary["network_domain_enabled"] = False
                    stop = True
            except ProbeStopped as exc:
                errors.append(f"browser_warmup: {exc}")
                summary["authentication_required"] = exc.authentication
                summary["challenge_detected"] = exc.challenge
                stop = True
            except Exception as exc:  # noqa: BLE001 - write a clean failure summary
                errors.append(f"browser_warmup: {type(exc).__name__}: {exc}")
                stop = True
        for query in queries[:MAX_QUERIES]:
            if len(results) >= limit or stop:
                break
            summary["queries_attempted"] += 1
            url = search_url(query)
            query_videos: list[dict[str, Any]] = []
            query_diagnostic: dict[str, Any] = {"query": query, "method": "http_html", "errors": []}
            try:
                html, status, final_url = request_html(url)
                http_statuses.append(status)
                detect_access_block(status, final_url, html)
                scripts = script_snapshots_from_html(html)
                payloads, containers = payloads_from_scripts(scripts, query_diagnostic["errors"])
                query_videos = videos_from_payloads(
                    payloads,
                    business_name=business_name,
                    query=query,
                    limit=limit - len(results),
                    observed_at=utc_now(),
                )
                page_evidence = pagination_evidence(payloads)
                query_diagnostic.update(
                    final_url=final_url,
                    html_length=len(html),
                    structured_data_found=bool(payloads),
                    script_containers=containers,
                    **page_evidence,
                    videos_found=len(query_videos),
                )
                summary["structured_data_found"] |= bool(payloads)
                if not query_videos:
                    summary["browser_fallback_used"] = True
                    (
                        query_videos,
                        query_diagnostic,
                        state_diagnostic,
                        query_responses,
                    ) = browser_query(
                        driver,
                        url=url,
                        business_name=business_name,
                        query=query,
                        limit=limit - len(results),
                    )
                    state_diagnostic["attempt"] = "initial"
                    state_diagnostics.append(state_diagnostic)
                    is_first_query = summary["queries_attempted"] == 1
                    if should_reload_first_query(
                        is_first_query=is_first_query,
                        diagnostic=query_diagnostic,
                        matching_responses=query_responses,
                    ):
                        summary["first_query_reloaded"] = True
                        initial_diagnostic = {
                            "final_url": query_diagnostic.get("final_url"),
                            "page_title": query_diagnostic.get("page_title"),
                            "document_ready_state": query_diagnostic.get(
                                "document_ready_state"
                            ),
                            "target_xhr_observed": False,
                            "errors": query_diagnostic.get("errors", []),
                        }
                        (
                            query_videos,
                            query_diagnostic,
                            state_diagnostic,
                            query_responses,
                        ) = browser_query(
                            driver,
                            url=url,
                            business_name=business_name,
                            query=query,
                            limit=limit - len(results),
                        )
                        query_diagnostic["initial_attempt"] = initial_diagnostic
                        query_diagnostic["reloaded"] = True
                        state_diagnostic["attempt"] = "reload"
                        state_diagnostics.append(state_diagnostic)
                    if is_first_query:
                        summary["first_query_target_xhr_observed"] = bool(
                            query_responses
                        )
                    for response in query_responses:
                        response["query"] = query
                        matching_responses.append(response)
                    if query_responses and not summary["target_endpoint_observed"]:
                        target = query_responses[0]
                        summary["target_endpoint_observed"] = True
                        summary["target_status"] = target.get("status")
                        summary["target_mime_type"] = target.get("mime_type")
                        summary["target_resource_type"] = target.get("resource_type")
                        summary["response_body_captured"] = bool(
                            target.get("body_captured")
                        )
                        summary["response_body_length"] = target.get(
                            "body_length", 0
                        )
                        summary["cursor"] = target.get("cursor")
                        summary["has_more"] = target.get("has_more")
                        summary["cursor_found"] |= target.get("cursor") is not None
                        summary["has_more_found"] |= target.get("has_more") is not None
                    summary["network_response_observed"] |= bool(query_responses)
                    summary["network_body_captured"] |= any(
                        response.get("body_captured") for response in query_responses
                    )
                    for diagnostic_error in query_diagnostic.get("errors", []):
                        if any(
                            token in diagnostic_error
                            for token in (
                                "network_capture",
                                "performance_log",
                                "Network.getResponseBody",
                            )
                        ):
                            errors.append(f"{query}: {diagnostic_error}")
                    summary["structured_data_found"] |= query_diagnostic["structured_data_found"]
                    summary["method_used"] = (
                        "browser_network_json"
                        if query_videos
                        and any(
                            response.get("body_captured")
                            and response.get("json_parseable")
                            for response in query_responses
                        )
                        else "browser_rendered_html"
                    )
                elif summary["method_used"] is None:
                    summary["method_used"] = "http_html"
            except ProbeStopped as exc:
                errors.append(f"{query}: {exc}")
                summary["authentication_required"] = exc.authentication
                summary["challenge_detected"] = exc.challenge
                query_diagnostic["errors"].append(str(exc))
                stop = True
            except Exception as exc:  # noqa: BLE001 - record and continue to next bounded query
                message = f"{query}: {type(exc).__name__}: {exc}"
                errors.append(message)
                query_diagnostic["errors"].append(message)

            added = 0
            for video in query_videos:
                if video["video_id"] in seen_ids:
                    continue
                seen_ids.add(video["video_id"])
                results.append(video)
                added += 1
                if len(results) >= limit:
                    break
            if added:
                summary["queries_succeeded"] += 1
            diagnostics.append(query_diagnostic)
            summary["page_titles"].append(query_diagnostic.get("page_title"))
            summary["final_urls"].append(query_diagnostic.get("final_url"))
            summary["rendered_html_lengths"].append(
                query_diagnostic.get("rendered_html_length", query_diagnostic.get("html_length", 0))
            )
            summary["script_state_containers_found"] |= bool(
                query_diagnostic.get("script_containers")
            )
            summary["cursor_found"] |= bool(query_diagnostic.get("cursor_found"))
            summary["has_more_found"] |= bool(query_diagnostic.get("has_more_found"))
    finally:
        if session is not None:
            session.close()

    summary["unique_videos_collected"] = len(results)
    summary["method_used"] = summary["method_used"] or "http_html"
    write_json(OUTPUT_DIRECTORY / "search_results.json", results)
    write_json(OUTPUT_DIRECTORY / "search_summary.json", summary)
    write_json(OUTPUT_DIRECTORY / "diagnostics.json", diagnostics)
    write_json(OUTPUT_DIRECTORY / "state_diagnostics.json", state_diagnostics)
    write_json(
        OUTPUT_DIRECTORY / "network_capture.json",
        {"matching_responses": matching_responses},
    )
    return summary


def self_test() -> None:
    capabilities = plain_chrome_options().to_capabilities()
    assert capabilities["goog:loggingPrefs"] == {"performance": "ALL"}
    assert require_performance_log(["browser", "performance"]) == [
        "browser",
        "performance",
    ]
    try:
        require_performance_log(["browser", "driver"])
    except PerformanceLogUnavailableError as exc:
        assert exc.available_log_types == ["browser", "driver"]
    else:
        raise AssertionError("performance-log gate did not reject missing log type")

    class FakeWarmupDriver:
        current_url = "https://www.tiktok.com/"
        title = "TikTok"
        page_source = "<html><title>TikTok</title></html>"

        def __init__(self):
            self.events: list[str] = []

        def set_page_load_timeout(self, seconds):
            self.events.append(f"timeout:{seconds}")

        def get(self, url):
            self.events.append(f"get:{url}")

        def execute_script(self, script):
            self.events.append("ready_state")
            return "complete"

        def get_log(self, log_type):
            self.events.append(f"clear_log:{log_type}")
            return []

        def execute_cdp_cmd(self, method, params):
            self.events.append(f"cdp:{method}")
            return {}

    fake_driver = FakeWarmupDriver()
    sleeps: list[int] = []
    warmup = warmup_browser(fake_driver, sleeper=sleeps.append)
    assert warmup["document_ready_state"] == "complete"
    assert warmup["network_enabled"] is True
    assert sleeps == [BROWSER_WARMUP_DELAY_SECONDS]
    assert fake_driver.events.index("get:https://www.tiktok.com/") < fake_driver.events.index(
        "clear_log:performance"
    ) < fake_driver.events.index("cdp:Network.enable")
    assert should_reload_first_query(
        is_first_query=True,
        diagnostic={"document_ready_state": "complete"},
        matching_responses=[],
    )
    assert not should_reload_first_query(
        is_first_query=False,
        diagnostic={"document_ready_state": "complete"},
        matching_responses=[],
    )
    assert not should_reload_first_query(
        is_first_query=True,
        diagnostic={"document_ready_state": "complete"},
        matching_responses=[{"requestId": "observed"}],
    )
    item = {
        "id": "video-1",
        "author": {"id": "creator-1", "uniqueId": "creator", "nickname": "Creator"},
        "createTime": 1_700_000_000,
        "desc": "Great #malatang #sydney.",
        "shareUrl": "https://www.tiktok.com/@creator/video/video-1",
        "textExtra": [{"hashtagName": "malatang"}],
        "stats": {"playCount": "1.2K", "diggCount": "50", "commentCount": 2, "shareCount": 1},
        "challenges": [
            {"id": "not-a-video", "desc": "hashtag metadata", "stats": {}}
        ],
    }
    payload = {
        "data": [
            {"item": item},
            {"item": dict(item)},
            {"item": {"id": "missing-author", "stats": {}, "createTime": 1_700_000_000}},
            {"item": {"id": "missing-stats", "author": {"uniqueId": "creator"}, "createTime": 1_700_000_000}},
            {"item": {"id": "bad-time", "author": {"uniqueId": "creator"}, "stats": {}, "createTime": "bad"}},
        ],
        "cursor": 12,
        "has_more": 1,
    }
    html = (
        '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps(payload)
        + "</script>"
    )
    errors: list[str] = []
    payloads, _ = payloads_from_scripts(script_snapshots_from_html(html), errors)
    videos = videos_from_payloads(
        payloads,
        business_name="Business",
        query="query",
        limit=10,
        observed_at="now",
    )
    assert errors == []
    assert len(videos) == 1
    assert videos[0]["hashtags"] == ["malatang", "sydney"]
    assert videos[0]["view_count"] == 1_200
    assert videos[0]["like_count"] == 50
    assert videos[0]["video_id"] != "not-a-video"
    assert normalize_video(
        {"id": "hashtag-1", "desc": "hashtag metadata"},
        business_name="Business",
        query="query",
        observed_at="now",
    ) is None
    assert normalize_video(
        {"id": "missing-author", "stats": {}, "createTime": 1_700_000_000},
        business_name="Business",
        query="query",
        observed_at="now",
    ) is None
    assert normalize_video(
        {
            "id": "missing-stats",
            "author": {"uniqueId": "creator"},
            "createTime": 1_700_000_000,
        },
        business_name="Business",
        query="query",
        observed_at="now",
    ) is None
    assert normalize_video(
        {
            "id": "bad-time",
            "author": {"uniqueId": "creator"},
            "stats": {},
            "createTime": "bad",
        },
        business_name="Business",
        query="query",
        observed_at="now",
    ) is None
    assert normalize_video(
        {
            "id": "missing-username",
            "author": {"id": "creator"},
            "stats": {},
            "createTime": 1_700_000_000,
        },
        business_name="Business",
        query="query",
        observed_at="now",
    ) is None
    state_payload = {
        "search": {
            "aweme_list": [
                {
                    "aweme_id": "video-2",
                    "author_info": {
                        "uid": "creator-2",
                        "unique_id": "alternate_creator",
                        "display_name": "Alternate Creator",
                    },
                    "create_time": 1_700_000_001,
                    "caption": "Alternate #schema",
                    "share_url": "https://www.tiktok.com/@alternate_creator/video/video-2",
                    "statistics": {
                        "play_count": "2K",
                        "digg_count": 75,
                        "comment_count": 3,
                        "share_count": 2,
                    },
                }
            ],
            "cursor": 10,
            "has_more": True,
        }
    }
    state_scripts = [
        {
            "index": 0,
            "id": "state",
            "type": "application/json",
            "src": None,
            "text": json.dumps(state_payload),
        },
        {"index": 1, "id": "broken", "type": "application/json", "text": "{"},
    ]
    state_payloads, state_diagnostic = diagnose_structured_scripts(state_scripts)
    alternate = videos_from_payloads(
        state_payloads,
        business_name="Business",
        query="alternate",
        limit=10,
        observed_at="now",
    )
    assert state_diagnostic["parseable_json_script_count"] == 1
    assert state_diagnostic["candidate_video_count"] == 1
    assert state_diagnostic["video_like_object_paths"] == ["search.aweme_list[0]"]
    assert "search.aweme_list" in state_diagnostic["interesting_key_paths"]
    assert state_diagnostic["cursor_like_values"][0]["value"] == 10
    assert state_diagnostic["has_more_like_values"][0]["value"] is True
    assert alternate == []
    request_event = {
        "message": json.dumps(
            {
                "message": {
                    "method": "Network.requestWillBeSent",
                    "params": {
                        "requestId": "request-1",
                        "request": {
                            "url": "https://www.tiktok.com/api/search/general/full/?signed=redacted",
                            "method": "GET",
                        },
                    },
                }
            }
        )
    }
    response_event = {
        "message": json.dumps(
            {
                "message": {
                    "method": "Network.responseReceived",
                    "params": {
                        "requestId": "request-1",
                        "type": "Fetch",
                        "response": {
                            "url": "https://www.tiktok.com/api/search/general/full/?signed=secret",
                            "status": 200,
                            "mimeType": "application/json",
                        },
                    },
                }
            }
        )
    }
    ignored_event = {
        "message": json.dumps(
            {
                "message": {
                    "method": "Network.responseReceived",
                    "params": {
                        "requestId": "request-2",
                        "type": "Fetch",
                        "response": {
                            "url": "https://www.tiktok.com/api/unrelated/?secret=value",
                            "status": 200,
                            "mimeType": "application/json",
                        },
                    },
                }
            }
        )
    }
    network_events = parse_network_response_events(
        [request_event, response_event, ignored_event, {"message": "not-json"}]
    )
    assert len(network_events) == 1
    assert network_events[0]["path"] == TARGET_SEARCH_ENDPOINT
    assert network_events[0]["method"] == "GET"
    assert "secret" not in json.dumps(network_events[0])
    network_payload = {
        "data": [{"item": item}, {"item": dict(item)}],
        "cursor": 24,
        "has_more": 1,
    }
    network_videos, network_diagnostic = analyze_network_body(
        {"body": json.dumps(network_payload), "base64Encoded": False},
        response=network_events[0],
        business_name="Business",
        query="network",
        limit=10,
    )
    assert len(network_videos) == 1
    assert network_diagnostic["json_parseable"] is True
    assert network_diagnostic["video_like_object_count"] >= 2
    assert network_diagnostic["minimal_video_sample"]["video_id"] == "video-1"
    assert network_diagnostic["cursor"] == 24
    assert network_diagnostic["has_more"] == 1
    malformed_videos, malformed_diagnostic = analyze_network_body(
        {"body": "not-json", "base64Encoded": False},
        response=network_events[0],
        business_name="Business",
        query="network",
        limit=10,
    )
    assert malformed_videos == []
    assert malformed_diagnostic["json_parseable"] is False
    assert malformed_diagnostic["error"] == "response body is not a JSON object"
    assert normalize_video(
        {"unexpected": True},
        business_name="Business",
        query="query",
        observed_at="now",
    ) is None
    print("offline synthetic validation: PASS")


def bounded_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= MAX_VIDEOS:
        raise argparse.ArgumentTypeError(f"limit must be between 1 and {MAX_VIDEOS}")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-name")
    parser.add_argument("--query", action="append")
    parser.add_argument("--limit", type=bounded_limit, default=MAX_VIDEOS)
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if not args.business_name:
        parser.error("--business-name is required")
    queries = args.query or list(DEFAULT_QUERIES)
    if len(queries) > MAX_QUERIES:
        parser.error(f"at most {MAX_QUERIES} queries are allowed")
    if any(not query.strip() for query in queries):
        parser.error("queries must not be empty")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run(args.business_name.strip(), [query.strip() for query in queries], args.limit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
