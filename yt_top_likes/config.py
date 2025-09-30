"""Configuration constants and helpers for the yt_top_likes package."""

from __future__ import annotations

import os

DEFAULT_API_KEY_ENV = "YOUTUBE_API_KEY"

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# 預設的 AI 關鍵字集合/默认 AI 关键字集合
DEFAULT_KEYWORDS = [
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


def resolve_api_key(explicit: str | None) -> str:
    """Return the effective API key, preferring CLI input over environment."""
    if explicit:
        return explicit.strip()
    return os.getenv(DEFAULT_API_KEY_ENV, "").strip()
