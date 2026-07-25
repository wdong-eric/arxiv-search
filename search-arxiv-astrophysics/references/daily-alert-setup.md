# Daily Alert Setup

## 1) Configure environment

From `search-arxiv-astrophysics/`:

```bash
cp .env.example .env
```

Then edit `.env`:
- set `ZOTERO_API_KEY`
- set `ALERT_EMAIL_TO` and SMTP fields

## 2) Run once manually

```bash
python3 scripts/daily_arxiv_zotero_alert.py --env-file .env --preview-only
```

Then send a real email run:

```bash
python3 scripts/daily_arxiv_zotero_alert.py --env-file .env
```

If you hit local certificate issues, retry with:

```bash
python3 scripts/daily_arxiv_zotero_alert.py --env-file .env --insecure-tls
```

## 3) Select new papers by score threshold

The default remains the top 12 ranked new papers. You can instead include every
new paper whose semantic score is strictly greater than a threshold:

```bash
python3 scripts/daily_arxiv_zotero_alert.py \
  --env-file .env \
  --new-semantic-threshold 0.65 \
  --preview-only
```

To require both the semantic score and the weighted overall (`final_score`) to
be strictly greater than 0.65:

```bash
python3 scripts/daily_arxiv_zotero_alert.py \
  --env-file .env \
  --new-semantic-threshold 0.65 \
  --new-overall-threshold 0.65 \
  --preview-only
```

For scheduled runs, set `NEW_SEMANTIC_THRESHOLD` and/or
`NEW_OVERALL_THRESHOLD` in `.env`. The two thresholds use AND when both are
set. Threshold selection requires semantic scoring to succeed and cannot be
combined with `--new-top-n`.

## 4) Daily schedule (cron example)

Run every day at 08:30 local time:

```cron
30 8 * * * cd /Users/Eric.Dong/Downloads/arxiv-search/search-arxiv-astrophysics && /usr/bin/python3 scripts/daily_arxiv_zotero_alert.py --env-file .env >> /tmp/arxiv_daily_alert.log 2>&1
```

## Output files

- latest report: `assets/daily_alert_latest.md`
- sent-state cache: `assets/daily_alert_state.json`
