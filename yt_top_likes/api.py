"""HTTP client helpers for contacting the YouTube Data API."""

from __future__ import annotations

import time
from typing import Sequence

import requests

from .config import SEARCH_URL, VIDEOS_URL
from .utils import build_video_row, chunked, snippet_matches_keywords


class YoutubeAPIError(Exception):
    """Raised when the YouTube Data API returns an error response."""


def parse_yt_error(resp: requests.Response) -> tuple[str | None, list[str], dict | None]:
    """Return the error message, reasons, and full payload for a failed response."""
    try:
        payload = resp.json()
    except ValueError:
        return None, [], None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or ""
        reasons: list[str] = []
        for entry in error.get("errors", []):
            reason = entry.get("reason")
            if reason and reason not in reasons:
                reasons.append(reason)
        return message, reasons, payload
    return None, [], payload if isinstance(payload, dict) else None


def describe_yt_error(resp: requests.Response) -> str:
    """Return a concise string description for a failed YouTube response."""
    message, reasons, payload = parse_yt_error(resp)
    if message:
        if reasons:
            return f"{message} (reason: {', '.join(reasons)})"
        return message
    if payload is not None:
        return str(payload)
    return f"{resp.status_code} {resp.reason}"


def interpret_yt_http_error(resp: requests.Response, context: str) -> str:
    """Build a friendly error message for failed YouTube Data API requests."""
    message, reasons, payload = parse_yt_error(resp)
    if message:
        description = f"{message} (reason: {', '.join(reasons)})" if reasons else message
    elif payload is not None:
        description = str(payload)
    else:
        description = f"{resp.status_code} {resp.reason}"

    reason_set = set(reasons)
    if resp.status_code == 403:
        if reason_set.intersection({"quotaExceeded", "dailyLimitExceeded"}):
            return (
                f"YouTube API quota exceeded while {context}. Wait for the daily reset or use a different API key."
            )
        if reason_set.intersection({"rateLimitExceeded", "userRateLimitExceeded"}):
            return (
                f"YouTube API rate limit hit while {context}. Reduce request volume or retry later."
            )
    return f"YouTube API request failed while {context}: {description}"


def _raise_for_status(resp: requests.Response, context: str) -> None:
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        error_response = exc.response or resp
        raise YoutubeAPIError(interpret_yt_http_error(error_response, context)) from exc


def yt_search(
    api_key: str,
    query: str,
    published_after: str,
    published_before: str,
    *,
    max_total: int = 300,
    sleep_sec: float = 0.2,
) -> list[str]:
    """Use search.list to collect video IDs for a keyword within a publish window."""
    got = 0
    page_token: str | None = None
    ids: list[str] = []

    while True:
        params = {
            "key": api_key,
            "part": "snippet",
            "type": "video",
            "q": query,
            "maxResults": 50,
            "publishedAfter": published_after,
            "publishedBefore": published_before,
        }
        if page_token:
            params["pageToken"] = page_token

        try:
            resp = requests.get(SEARCH_URL, params=params, timeout=30)
        except requests.RequestException as exc:
            raise YoutubeAPIError(
                f"Network error during YouTube search for query '{query}': {exc}"
            ) from exc
        _raise_for_status(resp, f"searching for query '{query}'")
        data = resp.json()

        items = data.get("items", [])
        for it in items:
            idobj = it.get("id") or {}
            if idobj.get("kind") == "youtube#video" and idobj.get("videoId"):
                ids.append(idobj["videoId"])
        got += len(items)

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        if got >= max_total:
            break

        time.sleep(sleep_sec)
    return ids


def yt_videos_stats(api_key: str, video_ids: Sequence[str]) -> dict[str, dict[str, object]]:
    """Retrieve snippet/statistics for video ids via videos.list."""
    results: dict[str, dict[str, object]] = {}
    for batch in chunked(list(video_ids), 50):
        if not batch:
            continue
        params = {
            "key": api_key,
            "part": "snippet,statistics",
            "id": ",".join(batch),
        }
        try:
            resp = requests.get(VIDEOS_URL, params=params, timeout=30)
        except requests.RequestException as exc:
            first_id = batch[0] if batch else ""
            raise YoutubeAPIError(
                f"Network error while fetching video stats for batch starting with {first_id}: {exc}"
            ) from exc
        context = (
            f"fetching video stats (batch starting with {batch[0]})" if batch else "fetching video stats"
        )
        _raise_for_status(resp, context)
        data = resp.json()
        for it in data.get("items", []):
            vid = it.get("id")
            if not vid:
                continue
            snippet = it.get("snippet", {})
            stats = it.get("statistics", {})
            results[vid] = build_video_row(vid, snippet, stats)
    return results


def fetch_most_liked_videos(
    api_key: str,
    *,
    region_code: str | None,
    pool_limit: int,
    keywords_lower: Sequence[str] | None = None,
    video_category_id: str | None = None,
    sleep_sec: float = 0.2,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Pull a slice of the mostPopular feed and keep entries that match the keyword filter."""
    if pool_limit <= 0:
        return [], {
            "requests": 0,
            "examined": 0,
            "region": (region_code or "").upper(),
            "pool_limit": 0,
            "categoryId": video_category_id,
        }

    pool_limit = min(max(pool_limit, 1), 200)
    lowered_keywords = list(keywords_lower or [])

    region = (region_code or "").strip()
    if not region and not video_category_id:
        raise YoutubeAPIError(
            "Region code or video category id must be provided for most-liked mode."
        )

    results: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    page_token: str | None = None
    requests_made = 0

    while len(seen_ids) < pool_limit:
        remaining = pool_limit - len(seen_ids)
        batch_size = min(50, remaining)
        params = {
            "key": api_key,
            "part": "snippet,statistics",
            "chart": "mostPopular",
            "maxResults": batch_size,
        }
        if region:
            params["regionCode"] = region
        if video_category_id:
            params["videoCategoryId"] = video_category_id
        if page_token:
            params["pageToken"] = page_token

        try:
            resp = requests.get(VIDEOS_URL, params=params, timeout=30)
        except requests.RequestException as exc:
            raise YoutubeAPIError(
                f"Network error while retrieving most liked videos: {exc}"
            ) from exc

        context_bits = ["fetching most liked videos"]
        if region:
            context_bits.append(f"region {region.upper()}")
        if video_category_id:
            context_bits.append(f"category {video_category_id}")
        _raise_for_status(resp, " ".join(context_bits))

        requests_made += 1
        data = resp.json()
        for item in data.get("items", []):
            vid = item.get("id")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)
            snippet = item.get("snippet", {})
            if lowered_keywords and not snippet_matches_keywords(snippet, lowered_keywords):
                continue
            stats = item.get("statistics", {})
            results.append(build_video_row(vid, snippet, stats))

        page_token = data.get("nextPageToken")
        if not page_token:
            break
        time.sleep(sleep_sec)

    meta = {
        "requests": requests_made,
        "examined": len(seen_ids),
        "region": region.upper() if region else "",
        "pool_limit": pool_limit,
        "categoryId": video_category_id,
    }
    return results, meta
