from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from .statuses import CrawlStatus


@dataclass(frozen=True)
class Store:
    id: str
    name: str
    query: str
    retailer: str | None = None
    expected_address: str | None = None


# Existing Store is the immutable per-place input job. An alias avoids changing
# constructors, JSON serialization, or callers while making that role explicit.
CrawlJob = Store


class ResolutionResult(TypedDict, total=False):
    """Existing dictionary/CSV contract, including incomplete error evidence."""
    place_key: str
    overture_name: str | None
    google_name: str | None
    google_place_id: str | None
    google_id_kind: str | None
    coordinate_distance_m: float | None
    name_similarity: float | None
    address_similarity: float | None
    match_score: float | None
    status: Literal["RESOLVED", "AMBIGUOUS", "NOT_FOUND", "ERROR"]
    resolved_url: str | None
    google_address: str | None
    elapsed_seconds: float
    error: str | None


@dataclass(frozen=True)
class Review:
    source: str
    source_review_id: str
    store_id: str
    author: str | None = None
    rating: float | None = None
    text: str | None = None
    displayed_date: str | None = None
    owner_response: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False)
    review_url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    published_at: datetime | None = None
    last_modified_at: datetime | None = None
    timestamp_source: str | None = None
    timestamp_status: str = "MISSING"
    reviewer_profile_url: str | None = None
    reviewer_id: str | None = None
    reviewer_avatar_url: str | None = None
    reviewer_metadata_raw: str | None = None
    reviewer_review_count: int | None = None
    reviewer_photo_count: int | None = None
    reviewer_is_local_guide: bool | None = None

    def __post_init__(self):
        for name in ("published_at", "last_modified_at"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, datetime) or value.utcoffset() is None:
                    raise ValueError(f"{name} must be a timezone-aware datetime")
                object.__setattr__(self, name, value.astimezone(timezone.utc))
        if self.timestamp_status not in {"VERIFIED", "MISSING", "PARSE_FAILED", "AMBIGUOUS"}:
            raise ValueError("Unsupported timestamp_status")
        if self.timestamp_status == "VERIFIED" and (
            self.published_at is None or self.last_modified_at is None
            or self.published_at > self.last_modified_at
        ):
            raise ValueError("VERIFIED timestamps require published_at <= last_modified_at")

    @property
    def identity(self) -> tuple[str, str]:
        return self.source, self.source_review_id


@dataclass
class CrawlResult:
    store_id: str
    status: CrawlStatus
    reviews_seen: int = 0
    reviews_written: int = 0
    scroll_count: int = 0
    stop_reason: str | None = None
    configured_max_scrolls: int | None = None
    parse_errors: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_stage: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0
