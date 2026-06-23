# enricher-py

Audio enrichment for `ult-scrape`. For each scraped tab in the shared `output/`
tree, finds and downloads the best-available full audio (YouTube, Topic-first
via `yt-dlp`) into the tab's directory. See
[docs/enricher-py/overview.md](../docs/enricher-py/overview.md) and the
[output contract](../docs/output-contract.md).

## Requirements

- Python >= 3.13
- `ffmpeg` installed (provides `ffprobe`)

## Setup

```bash
cd enricher-py
pip install -e ".[dev]"
cp .env.example .env   # edit as needed
```

## Use

```bash
enricher scan          # walk output/, enqueue tabs needing audio
enricher run --jobs 2  # download (Ctrl-C = graceful pause; rerun resumes)
enricher status        # counts by job state
```

## Test

```bash
python -m pytest                 # unit tests (network-free)
python -m pytest -m integration  # live yt-dlp + ffprobe; needs network
```
