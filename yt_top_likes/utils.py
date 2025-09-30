"""Utility helpers for keyword filtering and result shaping."""

from __future__ import annotations

from typing import Iterator, Sequence, TypeVar

T = TypeVar("T")


def safe_int(value: object) -> int:
    """Safely convert a value to int, returning 0 on failure."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def prepare_keyword_filters(raw_keywords: Sequence[str] | None) -> tuple[list[str], list[str]]:
    """Normalise keyword input and return original plus lower-cased variants."""
    normalized: list[str] = []
    lowered: list[str] = []
    if not raw_keywords:
        return normalized, lowered
    for keyword in raw_keywords:
        if keyword is None:
            continue
        value = keyword.strip()
        if not value:
            continue
        normalized.append(value)
        lowered.append(value.casefold())
    return normalized, lowered


def snippet_matches_keywords(snippet: dict, lowered_keywords: Sequence[str]) -> bool:
    """Return True when snippet contains any keyword in title, description, or tags."""
    if not lowered_keywords:
        return True
    text_parts: list[str] = []
    title = snippet.get("title")
    if title:
        text_parts.append(title)
    description = snippet.get("description")
    if description:
        text_parts.append(description)
    tags = snippet.get("tags")
    if isinstance(tags, list):
        text_parts.extend(tag for tag in tags if tag)
    if not text_parts:
        return False
    haystack = " ".join(text_parts).casefold()
    return any(keyword in haystack for keyword in lowered_keywords)


def build_video_row(video_id: str, snippet: dict, statistics: dict) -> dict[str, object]:
    """Shape the API payload into a flat dictionary we can print or export."""
    return {
        "videoId": video_id or "",
        "title": snippet.get("title", ""),
        "channelTitle": snippet.get("channelTitle", ""),
        "publishedAt": snippet.get("publishedAt", ""),
        "likeCount": safe_int(statistics.get("likeCount")),
        "viewCount": safe_int(statistics.get("viewCount")),
        "commentCount": safe_int(statistics.get("commentCount")),
    }


def rfc3339_day_start(day_text: str) -> str:
    return f"{day_text}T00:00:00Z"


def rfc3339_day_end(day_text: str) -> str:
    return f"{day_text}T23:59:59Z"


def chunked(seq: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """Yield slices of the given sequence in blocks of *size*."""
    if size <= 0:
        raise ValueError("chunk size must be positive")
    for idx in range(0, len(seq), size):
        yield seq[idx : idx + size]
