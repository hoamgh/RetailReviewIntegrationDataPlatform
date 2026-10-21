"""DOM comments v1. Source IDs stay candidates; fallback keys are session-only."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from experiments.tiktok_access_benchmark.access import dialog_kinds

SCROLL_CONTAINER_JS = Path(__file__).with_name("scroll_container.js").read_text(encoding="utf-8")
SNAPSHOT_JS = Path(__file__).with_name("snapshot.js").read_text(encoding="utf-8").replace(
    '__COMMENT_CONTAINER__', SCROLL_CONTAINER_JS)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalized(value):
    return " ".join(unicodedata.normalize("NFC", str(value or "")).split())


def integer_count(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    if re.fullmatch(r"\d{1,3}([,. ]\d{3})+", text):
        return int(re.sub(r"[,. ]", "", text))
    return None  # Abbreviated/ambiguous counts remain raw, not fabricated integers.


def identity_candidate(attributes, keys):
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value, {"attribute": key, "value": value}
    return None, None


def safe_author_href(href):
    if not isinstance(href, str):
        return None, None
    parsed = urlparse(href)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None, None
    if parsed.netloc and parsed.hostname not in {"www.tiktok.com", "tiktok.com"}:
        return None, None
    match = re.fullmatch(r"/@([\w.]+)", parsed.path)
    if not match or parsed.scheme not in {"", "https"}:
        return None, None
    return href, match[1]


def access_failure(snapshot):
    if snapshot.get("network_gate") in {"ACCESS_DENIED","RATE_LIMITED","CHALLENGE","AUTH_GATE","UNKNOWN_403","REQUIRED_ENDPOINT_ERROR","APPLICATION_ERROR"}:
        return snapshot["network_gate"]
    text = snapshot.get("body_text_snippet", "").casefold()
    if snapshot.get("url", "").startswith("chrome-error://") or "http error 403" in text:
        return "ACCESS_DENIED"
    if snapshot.get("challenge_present") or ('dialog_kinds' not in snapshot and text.strip().startswith('verify you are human')):
        return "CHALLENGE"
    kinds = snapshot.get('dialog_kinds') or dialog_kinds(snapshot.get('dialog_evidence', []))
    if 'CHALLENGE' in kinds:
        return 'CHALLENGE'
    if any(k in kinds for k in ('LOGIN_GATE','CONSENT_GATE')):
        return "LOGIN_OR_CONSENT_GATE"
    # Historical text-only imports have no structural evidence. Only explicit headings.
    if not kinds and any(re.match(r'^(?:log in to tiktok|đăng nhập vào tiktok|accept cookies)(?:\s|$)',
                                 str(d).strip(), re.I) for d in snapshot.get('dialogs', [])):
        return 'LOGIN_OR_CONSENT_GATE'
    if any(t in text for t in ("video unavailable", "video has been removed", "age restricted")):
        return "VIDEO_UNAVAILABLE"
    return None


def normalize_card(card, video_id, observed_at):
    is_reply = card.get('record_type') == 'REPLY'
    comment_id, comment_id_evidence = identity_candidate(
        {**({} if is_reply else card.get("root_attributes", {})), **card.get("item_attributes", {})}, ("data-comment-id", "data-cid"))
    author_id, author_id_evidence = identity_candidate(card.get("author_attributes", {}),
        ("data-user-id", "data-uid", "data-author-id"))
    href, username = safe_author_href(card.get("author_href_raw"))
    # Preserve source whitespace and emoji in stored text. Normalize only the dedup input.
    text = card.get("text") if isinstance(card.get("text"), str) else None
    reply_match = re.search(r"^(?:View|See|Xem)\s+(\d+)\s+(?:repl(?:y|ies)|phản hồi|câu trả lời)",
                            card.get("reply_count_raw") or "", re.I)
    identity = author_id or username or normalized(card.get("author_display_name"))
    fingerprint_input = [str(video_id), identity, normalized(text), card.get("created_at_raw") or ""]
    parent = None
    if is_reply and isinstance(card.get('parent_card'), dict) and normalized(card['parent_card'].get('text')):
        parent_card = {**card['parent_card'], 'record_type': 'TOP_LEVEL'}
        parent = normalize_card(parent_card, video_id, observed_at)
    parent_key = (parent['comment_id_candidate'] or parent['session_dedup_fingerprint']) if parent else None
    if is_reply:
        fingerprint_input.extend(['REPLY', parent_key])
    fingerprint = hashlib.sha256(json.dumps(fingerprint_input, ensure_ascii=False,
                                          separators=(",", ":")).encode()).hexdigest()
    return {"source": "tiktok", "comment_id_candidate": comment_id, "video_id": str(video_id),
        "author_id_candidate": author_id, "author_username": username,
        "author_display_name": card.get("author_display_name"), "text": text,
        "created_at_raw": card.get("created_at_raw") or None,
        "created_at_machine_raw": card.get("date_attributes", {}).get("datetime") or None,
        "first_observed_at": observed_at, "like_count": integer_count(card.get("like_count_raw")),
        "reply_count": int(reply_match[1]) if reply_match else integer_count(card.get('reply_count_raw')),
        "record_type": 'REPLY' if is_reply else 'TOP_LEVEL',
        "parent_comment_id": parent['comment_id_candidate'] if parent else None,
        "parent_session_key": parent_key if parent and not parent['comment_id_candidate'] else None,
        "parent_link_kind": ('DOM_ID_CANDIDATE' if parent['comment_id_candidate'] else 'SESSION_OBSERVATION') if parent else ('UNKNOWN' if is_reply else None),
        "is_reply": is_reply, "observed_at": observed_at, "extraction_source": "DOM",
        "session_dedup_fingerprint": None if comment_id else fingerprint,
        "dedup_key_kind": "SOURCE_ID_CANDIDATE" if comment_id else "SESSION_FINGERPRINT",
        "raw_evidence": {"author_href_raw": href, "author_attributes": card.get("author_attributes", {}),
            "item_attributes": card.get("item_attributes", {}), "root_attributes": card.get("root_attributes", {}),
            "date_attributes": card.get("date_attributes", {}), "comment_id_evidence": comment_id_evidence,
            "author_id_evidence": author_id_evidence, "like_count_raw": card.get("like_count_raw"),
            "like_aria_label": card.get("like_aria_label"), "reply_count_raw": card.get("reply_count_raw"),
            "unsupported_card_shape": card.get("unsupported_card_shape", False)}}


def record_key(row):
    key = row['comment_id_candidate'] or row['session_dedup_fingerprint']
    return 'reply:' + key if row.get('is_reply') else key


class CommentSession:
    def __init__(self, video_id, limit=50, reply_limit=0):
        if not 1 <= limit <= 50:
            raise ValueError("comment limit must be between 1 and 50")
        self.video_id, self.limit = str(video_id), limit
        if not isinstance(reply_limit, int) or isinstance(reply_limit, bool) or not 0 <= reply_limit <= 50:
            raise ValueError('reply limit must be between 0 and 50')
        self.reply_limit = reply_limit
        self.records, self.observations, self.duplicates = {}, 0, 0
        self.pagination = {"snapshots": 0, "no_growth_snapshots": 0,
                           "status": "UNOBSERVED", "exhausted": None, "stop_reason": None}
        self.rejected_cards = 0

    def add(self, snapshot, observed_at):
        if self.pagination["status"] == "STOPPED_GATE":
            return 0  # Terminal within this collector; no implicit gate reset.
        failure = access_failure(snapshot)
        if failure:
            self.pagination.update(status="STOPPED_GATE", stop_reason=failure)
            return 0
        if not urlparse(snapshot.get("url", "")).path.endswith("/video/" + self.video_id):
            raise ValueError("snapshot belongs to another video")
        new = 0
        cards = [(c, False) for c in snapshot.get('cards', [])]
        if self.reply_limit:
            cards += [(c, True) for c in snapshot.get('replies', [])]
        for card, is_reply in cards:
            if not isinstance(card, dict) or not normalized(card.get("text")):
                self.rejected_cards += 1
                continue  # Loading skeletons are not comments.
            row = normalize_card({**card, 'record_type': 'REPLY' if is_reply else 'TOP_LEVEL'}, self.video_id, observed_at)
            key = record_key(row)
            self.observations += 1
            if key in self.records:
                self.duplicates += 1
                previous = self.records[key]
                row["first_observed_at"] = previous.get("first_observed_at", previous["observed_at"])
                # A later virtualized card can omit metadata already observed.
                for field in ("created_at_raw", "created_at_machine_raw", "author_username",
                              "author_display_name", "author_id_candidate", "like_count", "reply_count",
                              "parent_comment_id", "parent_session_key"):
                    if row.get(field) is None:
                        row[field] = previous.get(field)
                if is_reply and row['parent_link_kind'] == 'UNKNOWN' and previous.get('parent_link_kind') != 'UNKNOWN':
                    row['parent_link_kind'] = previous.get('parent_link_kind')
                self.records[key] = row
            elif sum(bool(r.get('is_reply')) == is_reply for r in self.records.values()) < (self.reply_limit if is_reply else self.limit):
                self.records[key] = row
                new += 1
        self.pagination["exhausted"] = None  # Only current explicit end/empty evidence is authoritative.
        self.pagination["snapshots"] += 1
        self.pagination['scroll_container'] = snapshot.get('scroll_container')
        self.pagination['reply_controls_observed'] = snapshot.get('reply_controls_observed')
        self.pagination['unique_top_level_count'] = self.top_level_count
        self.pagination['unique_reply_count'] = len(self.records) - self.top_level_count
        self.pagination["no_growth_snapshots"] = 0 if new else self.pagination["no_growth_snapshots"] + 1
        if self.top_level_count >= self.limit:
            status, reason = "LIMIT_REACHED", "COMMENT_LIMIT"
        elif snapshot.get("wait_timeout"):
            status, reason = "TIMEOUT", "COMMENT_CARDS_TIMEOUT"
        elif snapshot.get("end_of_comments_observed") is True:
            status, reason = "END_OBSERVED", "EXPLICIT_END_MARKER"
            self.pagination["exhausted"] = True
        elif snapshot.get("empty_comments_observed") is True and not self.records:
            status, reason = "EMPTY_CONFIRMED", "EXPLICIT_EMPTY_MARKER"
            self.pagination["exhausted"] = True
        elif new:
            status, reason = "COMMENTS_RENDERED", None
        else:
            status, reason = "NO_GROWTH" if self.records else "EMPTY_UNCONFIRMED", None
        self.pagination.update(status=status, stop_reason=reason)
        return new

    def checkpoint(self):
        """Serializable offline collector state; no browser/session secrets."""
        return {"version": 1, "video_id": self.video_id, "limit": self.limit, 'reply_limit': self.reply_limit,
                "records": list(self.records.values()), "observations": self.observations,
                "duplicates": self.duplicates, "rejected_cards": self.rejected_cards,
                "pagination": dict(self.pagination)}

    @classmethod
    def from_checkpoint(cls, checkpoint):
        if checkpoint.get("version") != 1:
            raise ValueError("Unsupported comment checkpoint version")
        session = cls(checkpoint["video_id"], checkpoint["limit"], checkpoint.get('reply_limit', 0))
        for row in checkpoint["records"]:
            if str(row.get("video_id")) != session.video_id:
                raise ValueError("Checkpoint contains another video")
            identity = row.get('comment_id_candidate') or row.get('session_dedup_fingerprint')
            key = record_key(row) if identity else None
            is_reply = bool(row.get('is_reply'))
            used = sum(bool(r.get('is_reply')) == is_reply for r in session.records.values())
            if not key or key in session.records or used >= (session.reply_limit if is_reply else session.limit):
                raise ValueError("Invalid checkpoint dedup keys or limit")
            session.records[key] = dict(row)
        session.observations = checkpoint["observations"]
        session.duplicates = checkpoint["duplicates"]
        session.rejected_cards = checkpoint.get("rejected_cards", 0)
        session.pagination = dict(checkpoint["pagination"])
        return session

    @property
    def top_level_count(self):
        return sum(not r.get('is_reply') for r in self.records.values())

    def quality(self):
        rows = list(self.records.values())
        return {"raw_card_observations": self.observations, "unique_comments": len(rows),
            'top_level_comments': self.top_level_count,
            'replies_collected': len(rows) - self.top_level_count,
            'reply_coverage': 'PARTIAL_VISIBLE' if self.reply_limit else 'NOT_COLLECTED',
            'replies_without_parent_id': sum(r.get('is_reply') and not r.get('parent_comment_id') for r in rows),
            'replies_without_parent_link': sum(r.get('is_reply') and r.get('parent_link_kind') == 'UNKNOWN' for r in rows),
            'reply_limit': self.reply_limit,
            'reply_expansion_attempts': len(self.pagination.get('reply_expansions', [])),
            'reply_expansion_uncertain': sum(a.get('status') in ('INTENT','CLICKED') for a in self.pagination.get('reply_expansions', [])),
            "rejected_non_comment_cards": self.rejected_cards, "pagination": dict(self.pagination),
            "duplicate_observations_removed": self.duplicates,
            "duplicate_observation_rate": self.duplicates/self.observations if self.observations else 0,
            "missing_text_count": sum(not normalized(r["text"]) for r in rows),
            "missing_author_count": sum(not (r["author_username"] or r["author_display_name"] or r["author_id_candidate"]) for r in rows),
            "missing_time_count": sum(not r["created_at_raw"] for r in rows),
            "missing_like_count": sum(r["like_count"] is None for r in rows),
            "missing_reply_count": sum(r["reply_count"] is None for r in rows),
            "source_comment_id_candidate_count": sum(r["comment_id_candidate"] is not None for r in rows)}


def ui_comment_count(heading):
    match = re.fullmatch(r'(?:([0-9]+)\s+comments|(?:comments|bình luận)\s*\(([0-9]+)\))',
                         (heading or '').strip(), re.IGNORECASE)
    return int(match.group(1) or match.group(2)) if match else None


def write_artifacts(output, videos, snapshots_by_video, stop_reasons=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if len(videos) > 4:
        raise ValueError("maximum four videos per run")
    comments, metrics, evidence = [], [], []
    for video in videos:
        vid = str(video["video_id"])
        session = CommentSession(vid, video.get("limit", 50), video.get('reply_limit', 0))
        snapshots = snapshots_by_video.get(vid, [])
        failure = None
        for entry in snapshots:
            snap = entry["snapshot"]
            failure = access_failure(snap) or failure
            session.add(snap, entry["observed_at"])
            evidence.append({"video_id":vid, **entry})
        rows = list(session.records.values())
        comments.extend(rows)
        metrics.append({"video_id":vid, "video_url":video.get("video_url"),
            "metadata_comment_count":video.get("comment_count"), **session.quality(),
            "ui_comment_count": ui_comment_count(snapshots[-1]["snapshot"].get("comment_heading")) if snapshots else None,
            "successfully_opened":bool(snapshots) and failure is None and any(
                e["snapshot"].get("controls") or e["snapshot"].get("cards") for e in snapshots),
            "comment_surface_opened":any(e["snapshot"].get("comment_panel_present") for e in snapshots),
            "access_failure":failure, "stop_reason":(stop_reasons or {}).get(vid, "SNAPSHOT_VALIDATION")})
    summary = {"extraction_source":"DOM", "videos_attempted":len(videos),
        "videos_successfully_opened":sum(v["successfully_opened"] for v in metrics),
        "comment_surfaces_opened":sum(v["comment_surface_opened"] for v in metrics),
        "unique_comments":len(comments), "videos":metrics,
        "access_render_failures":[{"video_id":v["video_id"],"reason":v["access_failure"] or v["stop_reason"]}
            for v in metrics if v["access_failure"] or v["stop_reason"].startswith(("BROWSER_ERROR", "COMMENT_SURFACE_UNAVAILABLE"))],
        "identity_scope":"source IDs remain candidates; fingerprints deduplicate only this video/session",
        "network_required":False}
    for key in ("raw_card_observations", "duplicate_observations_removed", "missing_text_count",
                "missing_author_count", "missing_time_count", "missing_like_count", "missing_reply_count",
                "source_comment_id_candidate_count"):
        summary[key] = sum(v[key] for v in metrics)
    summary["duplicate_observation_rate"] = summary["duplicate_observations_removed"]/summary["raw_card_observations"] if summary["raw_card_observations"] else 0
    for name, data in (("comments.json",comments),("crawl_summary.json",summary),("dom_evidence.json",evidence)):
        (output/name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
    fields = ["source","comment_id_candidate","video_id","author_id_candidate","author_username",
              "author_display_name","text","created_at_raw","created_at_machine_raw","first_observed_at","like_count","reply_count","parent_comment_id",
              "is_reply","record_type","parent_session_key","parent_link_kind","observed_at","extraction_source","session_dedup_fingerprint","dedup_key_kind"]
    with (output/"comments.csv").open("w",newline="",encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream,fieldnames=fields,extrasaction="ignore")
        writer.writeheader()
        writer.writerows(comments)
    findings = ["# TikTok DOM comments v1", "", f"Videos attempted: {len(videos)}; unique comments: {len(comments)}.", ""]
    for video in metrics:
        findings.append(f"- {video['video_id']}: metadata={video['metadata_comment_count']}, UI={video['ui_comment_count']}, extracted={video['unique_comments']}, stop={video['stop_reason']}, access={video['access_failure']}")
    findings.extend(["", "Dates remain raw. No comment/user IDs are invented. An absent reply-count label is unknown, not zero.",
        "Re-observations during scrolling are session duplicates; output contains one record per session key. Identical anonymous comments can collide without source IDs.",
        "", "MVP connection: canonical place -> high-confidence matched videos -> DOM comments -> normalized text -> future sentiment/topics -> place features.",
        "Keep source observations append-only; never use a session fingerprint as a cross-run TikTok primary key. Network comments remain experimental and optional.",
        "Reading public comments does not establish comprehensive coverage of hidden, moderated or nested replies. A UI count may include replies.",
        "", "No sentiment, creator crawling, request replay or bypass is implemented."])
    (output/"findings.md").write_text("\n".join(findings)+"\n",encoding="utf-8")
    return summary
