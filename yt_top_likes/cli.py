"""Command-line interface for the yt_top_likes package."""

from __future__ import annotations

import argparse
import sys
from typing import Iterable

from .api import (
    YoutubeAPIError,
    fetch_most_liked_videos,
    yt_search,
    yt_videos_stats,
)
from .config import DEFAULT_KEYWORDS, resolve_api_key
from .output import print_top_videos, write_csv_output
from .utils import (
    prepare_keyword_filters,
    rfc3339_day_end,
    rfc3339_day_start,
)


def configure_output_streams() -> None:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find top-liked AI-related YouTube videos.",
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
    return parser


def _run_most_liked(
    api_key: str,
    *,
    keywords_lower: Iterable[str],
    region: str,
    pool: int,
    category_id: str | None,
    top_limit: int,
    csv_path: str | None,
) -> None:
    pool_limit = max(pool, top_limit * 2)
    try:
        rows, meta = fetch_most_liked_videos(
            api_key,
            region_code=region,
            pool_limit=pool_limit,
            keywords_lower=list(keywords_lower),
            video_category_id=category_id,
        )
    except YoutubeAPIError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

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
    label = f"Top {top_limit} videos by likes (most-liked mode, region {label_region})"
    print_top_videos(top_rows, label)
    if csv_path:
        write_csv_output(csv_path, top_rows)


def _run_windowed(
    api_key: str,
    *,
    keywords: Iterable[str],
    start_day: str,
    end_day: str,
    per_query_limit: int,
    top_limit: int,
    csv_path: str | None,
) -> None:
    start_iso = rfc3339_day_start(start_day)
    end_iso = rfc3339_day_end(end_day)

    all_ids: set[str] = set()
    query_results: dict[str, int] = {}
    query_errors: list[tuple[str, str]] = []
    blocked = False

    for keyword in keywords:
        try:
            ids = yt_search(
                api_key,
                keyword,
                start_iso,
                end_iso,
                max_total=per_query_limit,
            )
        except YoutubeAPIError as exc:
            message = str(exc)
            query_errors.append((keyword, message))
            lower_msg = message.lower()
            if "quota exceeded" in lower_msg or "rate limit hit" in lower_msg:
                blocked = True
                break
            continue

        query_results[keyword] = len(ids)
        all_ids.update(ids)

    if not all_ids:
        if query_errors:
            print(
                "ERROR: Unable to fetch video IDs for the requested window.",
                file=sys.stderr,
            )
            for keyword, err in query_errors:
                print(f"  - {keyword}: {err}", file=sys.stderr)
            if blocked:
                print(
                    "Hint: Provide a fresh API key or wait for the quota to reset.",
                    file=sys.stderr,
                )
            raise SystemExit(2)
        print("No videos found in the specified window/queries.")
        return

    try:
        stats_map = yt_videos_stats(api_key, list(all_ids))
    except YoutubeAPIError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

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
        for keyword, err in query_errors:
            print(f"  - {keyword}: {err}", file=sys.stderr)
        if blocked:
            print(
                "Quota-related failure detected; output only covers successful searches.",
                file=sys.stderr,
            )

    date_label = f"{start_day} to {end_day}"
    label = f"Top {top_limit} videos by likes (windowed mode, {date_label})"
    print_top_videos(top_rows, label)

    if csv_path:
        write_csv_output(csv_path, top_rows)


def main(argv: list[str] | None = None) -> None:
    configure_output_streams()
    parser = build_parser()
    args = parser.parse_args(argv)

    api_key = resolve_api_key(args.api_key)
    if not api_key:
        print(
            "ERROR: Please provide API key via --api-key or env YOUTUBE_API_KEY",
            file=sys.stderr,
        )
        raise SystemExit(1)

    keywords, keywords_lower = prepare_keyword_filters(
        args.keywords or DEFAULT_KEYWORDS
    )
    top_limit = max(1, args.top)

    if args.mode == "most-liked":
        _run_most_liked(
            api_key,
            keywords_lower=keywords_lower,
            region=args.region,
            pool=args.most_liked_pool,
            category_id=args.category_id,
            top_limit=top_limit,
            csv_path=args.csv,
        )
        return

    _run_windowed(
        api_key,
        keywords=keywords,
        start_day=args.start,
        end_day=args.end,
        per_query_limit=args.max_per_query,
        top_limit=top_limit,
        csv_path=args.csv,
    )
