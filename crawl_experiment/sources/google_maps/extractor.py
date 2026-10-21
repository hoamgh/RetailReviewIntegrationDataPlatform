import html
import re
from urllib.parse import urlparse

from crawl_experiment.core.errors import ReviewParseError
from crawl_experiment.core.models import Review

from . import selectors


def _text(card, selector: str) -> str | None:
    elements = card.find_elements("css selector", selector)
    if not elements:
        return None
    return (elements[0].text or "").strip() or None


def _review_url(card) -> str | None:
    for element in card.find_elements("css selector", selectors.REVIEW_URL):
        url = (element.get_attribute("href") or "").strip()
        if url and ("/maps/reviews/" in url or "review_id=" in url or "reviewId=" in url):
            return url
    return None


def _reviewer_metadata(text: str | None) -> dict:
    """Parse only explicit English/Vietnamese labels; retain unknown text raw."""
    result = dict(reviewer_metadata_raw=text, reviewer_review_count=None,
                  reviewer_photo_count=None, reviewer_is_local_guide=None)
    if not text:
        return result
    for field, label in (("reviewer_review_count", r"reviews?|bài đánh giá|đánh giá"),
                         ("reviewer_photo_count", r"photos?|ảnh")):
        match = re.search(r"(?<![\w.,])([0-9]+(?:[, .][0-9]{3})*)\s+(?:" + label + r")\b", text, re.I)
        if match:
            result[field] = int(re.sub(r"[, .]", "", match.group(1)))
    if re.search(r"\b(?:Local Guide|Hướng dẫn viên địa phương)\b", text, re.I):
        result["reviewer_is_local_guide"] = True
    return result


def _reviewer_fields(card) -> dict:
    result = _reviewer_metadata(_text(card, selectors.REVIEWER_METADATA))
    result.update(reviewer_profile_url=None, reviewer_id=None, reviewer_avatar_url=None)
    for element in card.find_elements("css selector", selectors.REVIEWER_PROFILE):
        url = html.unescape(element.get_attribute("data-href") or element.get_attribute("href") or "").strip()
        parsed = urlparse(url)
        match = re.fullmatch(r"/maps/contrib/([0-9]+)(?:/.*)?", parsed.path)
        if parsed.scheme in {"http", "https"} and parsed.hostname and (parsed.hostname == "google.com" or parsed.hostname.endswith(".google.com")) and match:
            result.update(reviewer_profile_url=url, reviewer_id=match.group(1))
            break
    for element in card.find_elements("css selector", selectors.REVIEWER_AVATAR):
        url = html.unescape(element.get_attribute("src") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            result["reviewer_avatar_url"] = url
            break
    return result


def _image_urls(card) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for element in card.find_elements("css selector", selectors.REVIEW_MEDIA):
        resolved_src = element.get_attribute("src")
        if resolved_src:
            candidates = [resolved_src]
        else:
            candidates = [
                element.get_attribute("data-src"),
                element.get_attribute("data-lazy-src"),
                element.get_attribute("data-original"),
            ]
            style = element.get_attribute("style") or ""
            candidates.extend(re.findall(r"url\((?:['\"])?(.*?)(?:['\"])?\)", style))
        for candidate in candidates:
            url = html.unescape(candidate or "").strip().strip("'\"")
            parsed = urlparse(url)
            if (
                parsed.scheme in {"http", "https"}
                and parsed.netloc
                and url not in seen
            ):
                seen.add(url)
                urls.append(url)
    return urls


class ReviewExtractor:
    def extract(self, card, store_id: str) -> Review:
        try:
            review_id = (card.get_attribute(selectors.REVIEW_ID_ATTRIBUTE) or "").strip()
            if not review_id:
                raise ReviewParseError("review card has no data-review-id")

            ratings = card.find_elements("css selector", selectors.RATING)
            rating_label = ratings[0].get_attribute("aria-label") if ratings else None
            match = re.search(r"([0-5](?:\.\d+)?)", rating_label or "")
            review = Review(
                source="google_maps",
                source_review_id=review_id,
                store_id=store_id,
                author=_text(card, selectors.AUTHOR),
                rating=float(match.group(1)) if match else None,
                text=_text(card, selectors.TEXT),
                displayed_date=_text(card, selectors.DATE),
                owner_response=_text(card, selectors.OWNER_RESPONSE),
                review_url=_review_url(card),
                image_urls=_image_urls(card),
                **_reviewer_fields(card),
            )
            collector = getattr(self, "timestamp_collector", None)
            if collector is not None:
                try:
                    return collector.enrich(review)
                except Exception:
                    # Timestamp enrichment must never turn a valid DOM review into a parse failure.
                    from dataclasses import replace
                    return replace(review, timestamp_status="PARSE_FAILED", timestamp_source="google_maps_network")
            return review
        except ReviewParseError:
            raise
        except Exception as exc:
            raise ReviewParseError(str(exc)) from exc
