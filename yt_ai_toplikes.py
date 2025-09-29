#!/usr/bin/env python3
import argparse
import csv
import os
import sys
import time

import requests

API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# 預設的 AI 關鍵字（英文/中文）
AI_QUERIES = [
    "AI",
    "artificial intelligence",
    "generative AI",
    "LLM",
    "machine learning",
    "deep learning",
    "人工智慧",
    "生成式AI",
    "機器學習",
    "深度學習",
]


class YoutubeAPIError(Exception):
    """Raised when the YouTube Data API returns an error response."""


def configure_output_streams():
    """Allow printing of non-ASCII characters even on narrow Windows code pages."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        reconfig = getattr(stream, "reconfigure", None)
        if callable(reconfig):
            try:
                stream.reconfigure(errors="backslashreplace")
            except Exception:
                pass


configure_output_streams()


def parse_yt_error(resp):
    """Return (message, reasons, payload_dict) for a YouTube API error response."""
    try:
        payload = resp.json()
    except ValueError:
        return None, [], None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or ""
        reasons = []
        for entry in error.get("errors", []):
            reason = entry.get("reason")
            if reason and reason not in reasons:
                reasons.append(reason)
        return message, reasons, payload
    return None, [], payload


def describe_yt_error(resp):
    message, reasons, payload = parse_yt_error(resp)
    if message:
        if reasons:
            return f"{message} (reason: {', '.join(reasons)})"
        return message
    if payload is not None:
        return str(payload)
    return f"{resp.status_code} {resp.reason}"


def interpret_yt_http_error(resp, context):
    """Build a friendly error message for failed YouTube Data API requests."""
    message, reasons, payload = parse_yt_error(resp)
    if message:
        if reasons:
            description = f"{message} (reason: {', '.join(reasons)})"
        else:
            description = message
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


def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def prepare_keyword_filters(raw_keywords):
    normalized = []
    lowered = []
    if not raw_keywords:
        return normalized, lowered
    for kw in raw_keywords:
        if kw is None:
            continue
        value = kw.strip()
        if not value:
            continue
        normalized.append(value)
        lowered.append(value.casefold())
    return normalized, lowered


def snippet_matches_keywords(snippet, lowered_keywords):
    if not lowered_keywords:
        return True
    text_parts = []
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
    return any(kw in haystack for kw in lowered_keywords)


def build_video_row(video_id, snippet, statistics):
    return {
        "videoId": video_id or "",
        "title": snippet.get("title", ""),
        "channelTitle": snippet.get("channelTitle", ""),
        "publishedAt": snippet.get("publishedAt", ""),
        "likeCount": safe_int(statistics.get("likeCount")),
        "viewCount": safe_int(statistics.get("viewCount")),
        "commentCount": safe_int(statistics.get("commentCount")),
    }


def rfc3339_day_start(day_text):
    return f"{day_text}T00:00:00Z"


def rfc3339_day_end(day_text):
    return f"{day_text}T23:59:59Z"


def yt_search(api_key, query, published_after, published_before, max_total=300, sleep_sec=0.2):
    """
    Use search.list to collect video IDs (type=video) for a keyword within a publish window.
    """
    got = 0
    page_token = None
    ids = []

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
            resp.raise_for_status()
        except requests.HTTPError as exc:
            error_msg = interpret_yt_http_error(resp, f"searching for query '{query}'")
            raise YoutubeAPIError(error_msg) from exc
        except requests.RequestException as exc:
            raise YoutubeAPIError(
                f"Network error during YouTube search for query '{query}': {exc}"
            ) from exc
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


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def yt_videos_stats(api_key, video_ids):
    """
    Retrieve snippet/statistics for video ids via videos.list.
    """
    results = {}
    for batch in chunked(video_ids, 50):
        params = {
            "key": api_key,
            "part": "snippet,statistics",
            "id": ",".join(batch),
        }
        try:
            resp = requests.get(VIDEOS_URL, params=params, timeout=30)
            resp.raise_for_status()
        except requests.HTTPError as exc:
            context = (
                f"fetching video stats (batch starting with {batch[0]})"
                if batch
                else "fetching video stats"
            )
            error_msg = interpret_yt_http_error(resp, context)
            raise YoutubeAPIError(error_msg) from exc
        except requests.RequestException as exc:
            first_id = batch[0] if batch else ""
            raise YoutubeAPIError(
                f"Network error while fetching video stats for batch starting with {first_id}: {exc}"
            ) from exc
        data = resp.json()
        for it in data.get("items", []):
            vid = it.get("id")
            snippet = it.get("snippet", {})
            stats = it.get("statistics", {})
            results[vid] = build_video_row(vid, snippet, stats)
    return results


def fetch_most_liked_videos(
    api_key,
    *,
    region_code,
    pool_limit,
    keywords_lower=None,
    video_category_id=None,
    sleep_sec=0.2,
):
    """
    Pull a slice of the mostPopular feed and keep the entries that match the keyword filter.
    """
    if pool_limit <= 0:
        return [], {
            "requests": 0,
            "examined": 0,
            "region": (region_code or "").upper(),
            "pool_limit": 0,
            "categoryId": video_category_id,
        }

    pool_limit = min(max(pool_limit, 1), 200)
    keywords_lower = keywords_lower or []

    region = (region_code or "").strip()
    if not region and not video_category_id:
        raise YoutubeAPIError(
            "Region code or video category id must be provided for most-liked mode."
        )

    results = []
    seen_ids = set()
    page_token = None
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
            resp.raise_for_status()
        except requests.HTTPError as exc:
            context_bits = ["fetching most liked videos"]
            if region:
                context_bits.append(f"region {region.upper()}")
            if video_category_id:
                context_bits.append(f"category {video_category_id}")
            error_msg = interpret_yt_http_error(resp, " ".join(context_bits))
            raise YoutubeAPIError(error_msg) from exc
        except requests.RequestException as exc:
            raise YoutubeAPIError(
                f"Network error while retrieving most liked videos: {exc}"
            ) from exc

        requests_made += 1
        data = resp.json()
        for item in data.get("items", []):
            vid = item.get("id")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)
            snippet = item.get("snippet", {})
            if keywords_lower and not snippet_matches_keywords(snippet, keywords_lower):
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


def print_top_videos(rows, title):
    print(f"\n{title}\n")
    print(f"{'Rank':<4} {'Likes':>8} {'Views':>10}  {'PublishedAt':<20}  {'Channel':<30}  Title")
    for idx, row in enumerate(rows, 1):
        print(
            f"{idx:<4} {row['likeCount']:>8} {row['viewCount']:>10}  "
            f"{row['publishedAt']:<20}  {row['channelTitle']:<30}  "
            f"{row['title']}  https://youtu.be/{row['videoId']}"
        )


def write_csv_output(path, rows):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "rank",
                "videoId",
                "title",
                "channelTitle",
                "publishedAt",
                "likeCount",
                "viewCount",
                "commentCount",
                "url",
            ]
        )
        for idx, row in enumerate(rows, 1):
            writer.writerow(
                [
                    idx,
                    row["videoId"],
                    row["title"],
                    row["channelTitle"],
                    row["publishedAt"],
                    row["likeCount"],
                    row["viewCount"],
                    row["commentCount"],
                    f"https://youtu.be/{row['videoId']}",
                ]
            )
    print(f"\nCSV exported to {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Find top-liked AI-related YouTube videos."
    )
    parser.add_argument(
        "--mode",
        choices=("windowed", "most-liked"),
        default="windowed",
        help="Choose between date-window search and direct most-liked retrieval.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="YouTube Data API key (default from env YOUTUBE_API_KEY)",
    )
    parser.add_argument("--start", default="2025-09-10", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2025-09-20", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--max-per-query",
        type=int,
        default=300,
        help="Max items to fetch per keyword query (windowed mode).",
    )
    parser.add_argument("--csv", default=None, help="Output CSV path")
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=None,
        help="Override the default AI keyword list.",
    )
    parser.add_argument(
        "--region",
        default="US",
        help="ISO 3166-1 alpha-2 region code used in most-liked mode.",
    )
    parser.add_argument(
        "--most-liked-pool",
        type=int,
        default=120,
        help="Number of trending videos to inspect in most-liked mode (<=200).",
    )
    parser.add_argument(
        "--category-id",
        default=None,
        help="Optional videoCategoryId for most-liked mode.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top videos to display and export.",
    )
    args = parser.parse_args()

    api_key = args.api_key or API_KEY
    if not api_key:
        print(
            "ERROR: Please provide API key via --api-key or env YOUTUBE_API_KEY",
            file=sys.stderr,
        )
        sys.exit(1)

    keywords, keywords_lower = prepare_keyword_filters(args.keywords or AI_QUERIES)
    top_limit = max(1, args.top)

    if args.mode == "most-liked":
        pool_limit = max(args.most_liked_pool, top_limit * 2)
        try:
            rows, meta = fetch_most_liked_videos(
                api_key,
                region_code=args.region,
                pool_limit=pool_limit,
                keywords_lower=keywords_lower,
                video_category_id=args.category_id,
            )
        except YoutubeAPIError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)

        if not rows:
            print(
                "No videos matched the provided keywords in the most-liked feed. "
                "Increase --most-liked-pool or adjust --keywords."
            )
            return

        rows.sort(key=lambda r: r["likeCount"], reverse=True)
        top_rows = rows[:top_limit]

        print(
            f"Inspected {meta['examined']} trending videos (pool limit {meta['pool_limit']}) "
            f"across {meta['requests']} API call(s); {len(rows)} matched the keyword filter."
        )
        if meta["categoryId"]:
            print(f"Restricted to category {meta['categoryId']}.")
        label_region = meta["region"] or "unspecified region"
        label = (
            f"Top {top_limit} videos by likes (most-liked mode, region {label_region})"
        )
        print_top_videos(top_rows, label)
        if args.csv:
            write_csv_output(args.csv, top_rows)
        return

    # windowed mode
    start_iso = rfc3339_day_start(args.start)
    end_iso = rfc3339_day_end(args.end)

    all_ids = set()
    query_results = {}
    query_errors = []
    blocked = False

    for q in keywords:
        try:
            ids = yt_search(
                api_key, q, start_iso, end_iso, max_total=args.max_per_query
            )
        except YoutubeAPIError as exc:
            message = str(exc)
            query_errors.append((q, message))
            lower_msg = message.lower()
            if "quota exceeded" in lower_msg or "rate limit hit" in lower_msg:
                blocked = True
                break
            continue

        query_results[q] = len(ids)
        for vid in ids:
            all_ids.add(vid)

    if not all_ids:
        if query_errors:
            print(
                "ERROR: Unable to fetch video IDs for the requested window.",
                file=sys.stderr,
            )
            for q, err in query_errors:
                print(f"  - {q}: {err}", file=sys.stderr)
            if blocked:
                print(
                    "Hint: Provide a fresh API key or wait for the quota to reset.",
                    file=sys.stderr,
                )
            sys.exit(2)
        print("No videos found in the specified window/queries.")
        return

    try:
        stats_map = yt_videos_stats(api_key, list(all_ids))
    except YoutubeAPIError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    rows = list(stats_map.values())
    rows.sort(key=lambda r: r["likeCount"], reverse=True)
    top_rows = rows[:top_limit]

    successful_queries = len(query_results)
    queries_with_hits = sum(1 for count in query_results.values() if count)
    print(
        f"Collected {len(all_ids)} unique video IDs from {successful_queries} keyword searches "
        f"({queries_with_hits} returned matches)."
    )
    if query_errors:
        print(
            "\nWARNING: Some keyword searches failed and results may be incomplete:",
            file=sys.stderr,
        )
        for q, err in query_errors:
            print(f"  - {q}: {err}", file=sys.stderr)
        if blocked:
            print(
                "Quota-related failure detected; output only covers successful searches.",
                file=sys.stderr,
            )

    date_label = f"{args.start} to {args.end}"
    label = f"Top {top_limit} videos by likes (windowed mode, {date_label})"
    print_top_videos(top_rows, label)

    if args.csv:
        write_csv_output(args.csv, top_rows)


if __name__ == "__main__":
    main()

