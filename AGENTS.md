# Repository Guidelines

## Project Structure & Module Organization
The project is anchored by `yt_ai_toplikes.py`, a single entry-point script that orchestrates YouTube search calls, result aggregation, and CSV export. Keep shared helpers near the top of this file, and split new functionality into adjacent modules only when a feature spans multiple responsibilities. Configuration assets for automation live under `.codex/` and `.specify/`; modify them only when adjusting agent workflows.

## Build, Test, and Development Commands
Use Python 3.11+ and isolate dependencies in a virtual environment: `python -m venv .venv` then `.venv\Scripts\activate`. Install runtime needs with `pip install requests`. Run the collector via `python yt_ai_toplikes.py --start 2025-09-10 --end 2025-09-20 --csv data/top_ai.csv` (create the `data/` directory beforehand). Add a `requirements.txt` if you introduce new packages so contributors can sync with `pip install -r requirements.txt`.

## Coding Style & Naming Conventions
Follow PEP 8 defaults: four-space indentation, snake_case for functions, and CONSTANT_CASE for module-level settings like `SEARCH_URL`. Prefer descriptive names (`fetch_video_stats`) over abbreviations and keep CLI argument names aligned with the public API. Preserve UTF-8 encoding so existing bilingual comments remain legible, and favor concise inline comments for non-obvious API behaviors.

## Testing Guidelines
Add tests under a `tests/` directory with files named `test_*.py`, and execute them using `pytest -q`. Mock outbound HTTP calls (e.g., with `responses`) so the suite runs offline and without consuming quota. For new features, supply at least one regression test that covers failure handling, such as API throttling or missing `likeCount` fields.

## Commit & Pull Request Guidelines
Write commits in the imperative mood ("Add pagination guard") and keep them focused on a single concern. Reference related issues in the body when applicable. Pull requests should summarize user-facing changes, list verification steps (commands run, sample outputs), and attach redacted logs when debugging API issues. Screenshot the resulting CSV or CLI output only if it clarifies the change.

## Environment & Secrets
Set the YouTube API key via `YOUTUBE_API_KEY` or `--api-key`; never commit keys or `.env` files. Document any new environment variables in this guide or the README so future agents can reproduce your setup.
