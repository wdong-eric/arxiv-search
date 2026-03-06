---
name: search-arxiv-astrophysics
description: Search arXiv for astrophysics papers using user-provided and frequently changing research keywords, then return a ranked shortlist with concise relevance summaries. Use when a user asks to find recent or foundational astrophysics literature, monitor new papers for evolving interests, or explore broad research directions from keyword inputs.
---

# Search Arxiv Astrophysics

## Overview

Run repeatable arXiv searches for astrophysics topics from the user's current keyword list.
Use the bundled script to fetch results, rank them by keyword match, and present a compact reading shortlist.

## Workflow

1. Collect a fresh keyword set for this run.
- Ask for 5-15 terms or short phrases.
- Encourage coverage across object, process, method, and data context.
- Treat the keyword list as disposable and update it each session.

2. Build one or more focused queries.
- Use `--match any` for broad discovery and `--match all` for tighter filtering.
- Add `--exclude` terms to remove common false positives.
- Keep astrophysics scope using default categories unless the user requests otherwise.

3. Run the script.

```bash
python3 scripts/search_arxiv_astro.py \
  --keywords-file assets/keywords.txt \
  --match any \
  --days 365 \
  --max-results 40
```

Keyword file format supports:
- one term per line
- comma-separated terms on the same line
- comments with `#`

4. Present results for decision-making.
- Report top matches with title, date, category, and one-line relevance note.
- Highlight why each item matched the provided keywords.
- Separate "read now" from "background scan" papers.

## Query Tactics For Broad Interests

When the user has broad or shifting interests, split search into 2-3 batches instead of one giant query:
- Batch A: science target keywords (objects or systems)
- Batch B: mechanism keywords (physical process)
- Batch C: method keywords (inference/modeling/instrument)

Run each batch and merge the top matches manually in the final summary.
Read [references/keyword-playbook.md](references/keyword-playbook.md) for reusable keyword templates and category guidance.

## Script Interface

- Script: `scripts/search_arxiv_astro.py`
- Required: at least one of `--keyword` (repeatable) or `--keywords-file`
- Common optional flags:
  - `--keywords-file <path/to/keywords.txt>`
  - `--match any|all`
  - `--exclude <term>` (repeatable)
  - `--days <N>` (recency filter after fetch)
  - `--max-results <N>`
  - `--sort-by relevance|lastUpdatedDate|submittedDate`
  - `--output markdown|json`
  - `--insecure-tls` (use only if local cert trust is broken)
  - `--dry-run` (print API URL without fetching)

## Troubleshooting

- If HTTPS fails with certificate verification errors, retry with `--insecure-tls`.
- If the query is too broad, move generic terms to `--exclude` or split into batch runs.

## Output Standard

For each recommended paper, include:
- Title and arXiv ID link
- Published date (ISO date)
- Primary category
- Matched keywords
- One-sentence relevance summary tied to the user's stated interests
