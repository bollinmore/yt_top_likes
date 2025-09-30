"""Helpers for presenting results on the console or as CSV exports."""

from __future__ import annotations

import csv
import os
from typing import Iterable


def print_top_videos(rows: Iterable[dict[str, object]], title: str) -> None:
    """Pretty-print the ranking table to stdout."""
    print(f"\n{title}\n")
    print(f"{'Rank':<4} {'Likes':>8} {'Views':>10}  {'PublishedAt':<20}  {'Channel':<30}  Title")
    for idx, row in enumerate(rows, 1):
        print(
            f"{idx:<4} {row['likeCount']:>8} {row['viewCount']:>10}  "
            f"{row['publishedAt']:<20}  {row['channelTitle']:<30}  "
            f"{row['title']}  https://youtu.be/{row['videoId']}"
        )


def write_csv_output(path: str, rows: Iterable[dict[str, object]]) -> None:
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
