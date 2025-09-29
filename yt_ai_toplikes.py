#!/usr/bin/env python3
import os, sys, argparse, time, math, csv
from datetime import datetime, timedelta, timezone
import requests

API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# 關鍵字：英文/中文常見 AI 相關詞彙
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

def rfc3339_day_start(d):  # inclusive
    return f"{d}T00:00:00Z"

def rfc3339_day_end(d):  # inclusive end-of-day
    return f"{d}T23:59:59Z"

def yt_search(api_key, query, published_after, published_before, max_total=300, sleep_sec=0.2):
    """
    以 search.list 搜尋影片 ID（type=video），分頁直到達到上限或無更多頁。
    注意：search.list 需要 part=snippet；使用 publishedAfter/publishedBefore 過濾日期；分頁用 pageToken。參見官方文件。 
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
            "maxResults": 50,  # API 允許的上限
            "publishedAfter": published_after,
            "publishedBefore": published_before,
            # 不以按讚排序，因為官方不提供；先蒐集候選清單，稍後用 videos.list 撈 likeCount 再排序
            # 可選：加入 "safeSearch": "none"、"relevanceLanguage": "en" 等
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
        yield seq[i:i+n]

def yt_videos_stats(api_key, video_ids):
    """
    用 videos.list 取得統計資料（statistics.likeCount）與標題/頻道/發布日等（part=snippet,statistics）。
    官方說明：likeCount 在 statistics 物件中，若影片關閉評分可能缺值。 
    """
    results = {}
    for batch in chunked(video_ids, 50):  # videos.list 單次最多 50 IDs
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
                if batch else "fetching video stats"
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
            # 某些影片可能無 likeCount（關閉評分），以 0 代替
            like = int(stats.get("likeCount", 0)) if stats.get("likeCount") is not None else 0
            results[vid] = {
                "videoId": vid,
                "title": snippet.get("title", ""),
                "channelTitle": snippet.get("channelTitle", ""),
                "publishedAt": snippet.get("publishedAt", ""),
                "likeCount": like,
                "viewCount": int(stats.get("viewCount", 0)) if stats.get("viewCount") is not None else 0,
                "commentCount": int(stats.get("commentCount", 0)) if stats.get("commentCount") is not None else 0,
            }
    return results

def main():
    parser = argparse.ArgumentParser(description="Find top-liked AI-related YouTube videos in 2025/09 mid.")
    parser.add_argument("--api-key", default=None, help="YouTube Data API key (default from env YOUTUBE_API_KEY)")
    parser.add_argument("--start", default="2025-09-10", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2025-09-20", help="End date (YYYY-MM-DD)")
    parser.add_argument("--max-per-query", type=int, default=300, help="Max items to fetch per keyword query")
    parser.add_argument("--csv", default=None, help="Output CSV path")
    args = parser.parse_args()

    api_key = args.api_key or API_KEY
    if not api_key:
        print("ERROR: Please provide API key via --api-key or env YOUTUBE_API_KEY", file=sys.stderr)
        sys.exit(1)

    # 日期轉 RFC3339（YouTube API 需要）
    start_iso = rfc3339_day_start(args.start)
    end_iso = rfc3339_day_end(args.end)

    # 逐個關鍵字蒐集候選影片
    all_ids = set()
    query_results = {}
    query_errors = []
    blocked = False

    for q in AI_QUERIES:
        try:
            ids = yt_search(api_key, q, start_iso, end_iso, max_total=args.max_per_query)
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
            print("ERROR: Unable to fetch video IDs for the requested window.", file=sys.stderr)
            for q, err in query_errors:
                print(f"  - {q}: {err}", file=sys.stderr)
            if blocked:
                print("Hint: Provide a fresh API key or wait for the quota to reset.", file=sys.stderr)
            sys.exit(2)
        print("No videos found in the specified window/queries.")
        return

    # 撈取影片統計（含 likeCount），並排序
    try:
        stats_map = yt_videos_stats(api_key, list(all_ids))
    except YoutubeAPIError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    rows = list(stats_map.values())
    rows.sort(key=lambda r: r["likeCount"], reverse=True)
    top10 = rows[:10]

    successful_queries = len(query_results)
    queries_with_hits = sum(1 for count in query_results.values() if count)
    print(
        f"Collected {len(all_ids)} unique video IDs from {successful_queries} keyword searches "
        f"({queries_with_hits} returned matches)."
    )

    if query_errors:
        print("\nWARNING: Some keyword searches failed and results may be incomplete:", file=sys.stderr)
        for q, err in query_errors:
            print(f"  - {q}: {err}", file=sys.stderr)
        if blocked:
            print("Quota-related failure detected; output only covers successful searches.", file=sys.stderr)


    # 輸出結果（表格）
    print("\nTop 10 AI-related videos by likes (2025-09-10 to 2025-09-20)\n")
    print(f"{'Rank':<4} {'Likes':>8} {'Views':>10}  {'PublishedAt':<20}  {'Channel':<30}  Title")
    for i, r in enumerate(top10, 1):
        print(f"{i:<4} {r['likeCount']:>8} {r['viewCount']:>10}  {r['publishedAt']:<20}  {r['channelTitle']:<30}  {r['title']}  https://youtu.be/{r['videoId']}")

    # 輸出 CSV（可選）
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["rank","videoId","title","channelTitle","publishedAt","likeCount","viewCount","commentCount","url"])
            for i, r in enumerate(top10, 1):
                writer.writerow([
                    i, r["videoId"], r["title"], r["channelTitle"], r["publishedAt"],
                    r["likeCount"], r["viewCount"], r["commentCount"], f"https://youtu.be/{r['videoId']}"
                ])
        print(f"\nCSV 已輸出：{args.csv}")

if __name__ == "__main__":
    main()

