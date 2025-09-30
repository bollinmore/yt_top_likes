# YouTube AI Top Likes

A command-line collector that ranks AI-related YouTube videos by like count. It supports
fetching results from a date window via keyword searches or pulling the most-liked entries
from the trending feed.

## Requirements

- Python 3.11 or newer
- A YouTube Data API v3 key (set `YOUTUBE_API_KEY` or pass `--api-key`)
- Dependencies: `requests` (install via `pip install requests`)

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install requests
```

If you add new packages in the future, capture them in a `requirements.txt` file so others
can sync with `pip install -r requirements.txt`.

## Usage

Windowed mode (default) searches for videos published within a date range per keyword:

```powershell
python yt_ai_toplikes.py --start 2025-09-10 --end 2025-09-20 --csv data/top_ai.csv
```

Most-liked mode inspects the trending feed for a region or category:

```powershell
python yt_ai_toplikes.py --mode most-liked --region US --top 15
```

Key options:

- `--keywords`: override the default AI keyword set
- `--max-per-query`: cap the number of search results per keyword in windowed mode
- `--most-liked-pool`: size of the trending slice to examine (<= 200)
- `--csv`: write the ranked output to a UTF-8 CSV file

Run `python yt_ai_toplikes.py --help` to see the complete flag list.

## Output

The CLI prints a ranked table to stdout and can optionally export the same data to CSV. The
CSV includes rank, video metadata, engagement counts, and the canonical watch URL.

## Project Layout

The executable script is a thin wrapper; package code lives under `yt_top_likes/`. See
`docs/README.md` for an architectural overview and diagram.
