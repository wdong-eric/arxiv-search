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

## 3) Daily schedule (cron example)

Run every day at 08:30 local time:

```cron
30 8 * * * cd /Users/Eric.Dong/Downloads/arxiv-search/search-arxiv-astrophysics && /usr/bin/python3 scripts/daily_arxiv_zotero_alert.py --env-file .env >> /tmp/arxiv_daily_alert.log 2>&1
```

## Output files

- latest report: `assets/daily_alert_latest.md`
- sent-state cache: `assets/daily_alert_state.json`
