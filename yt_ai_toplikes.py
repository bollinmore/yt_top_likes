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

        resp = requests.get(SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
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
        resp = requests.get(VIDEOS_URL, params=params, timeout=30)
        resp.raise_for_status()
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
    for q in AI_QUERIES:
        ids = yt_search(api_key, q, start_iso, end_iso, max_total=args.max_per_query)
        for vid in ids:
            all_ids.add(vid)

    if not all_ids:
        print("No videos found in the specified window/queries.")
        return

    # 撈取影片統計（含 likeCount），並排序
    stats_map = yt_videos_stats(api_key, list(all_ids))
    rows = list(stats_map.values())
    rows.sort(key=lambda r: r["likeCount"], reverse=True)
    top10 = rows[:10]

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

