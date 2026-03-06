# Keyword Playbook

Use this guide to build better keyword lists before running `scripts/search_arxiv_astro.py`.

## Use A Persistent Keyword Profile

Store your evolving list in `assets/keywords.txt` and run:

```bash
python3 scripts/search_arxiv_astro.py \
  --keywords-file assets/keywords.txt \
  --match any \
  --days 90 \
  --max-results 80
```

File parsing rules:
- One keyword per line or comma-separated terms
- `#` comments are ignored
- Duplicate keywords are deduplicated case-insensitively

If your Python TLS certificates are broken, set once per shell:

```bash
export ARXIV_INSECURE_TLS=1
```

Override it for one command with `--secure-tls`.

## Quick Keyword Framework

Collect terms across at least three buckets:

1. Science target: object/system/population
- Examples: `pulsar timing array`, `dwarf galaxies`, `strong lensing`

2. Physical mechanism:
- Examples: `ultra-light dark matter`, `reionization`, `cosmic ray transport`

3. Method or data:
- Examples: `Bayesian inference`, `Gaussian process`, `JWST`, `21cm tomography`

Optional fourth bucket:
- Observable/constraint terms such as `power spectrum`, `timing residuals`, `mass function`

## Batching Strategy For Broad Interests

Avoid one giant query with mixed intent.
Split into smaller runs:

1. Discovery batch: broad target + mechanism (`--match any`)
2. Precision batch: method-heavy terms (`--match all`)
3. Exclusion batch: same as 1 or 2 plus `--exclude` noise terms

Merge top hits manually across batches.

## Astrophysics Categories

The script defaults to:
- `astro-ph.CO` (Cosmology and Nongalactic Astrophysics)
- `astro-ph.EP` (Earth and Planetary Astrophysics)
- `astro-ph.GA` (Astrophysics of Galaxies)
- `astro-ph.HE` (High Energy Astrophysical Phenomena)
- `astro-ph.IM` (Instrumentation and Methods for Astrophysics)
- `astro-ph.SR` (Solar and Stellar Astrophysics)

Override with repeated `--category` flags when needed.

## Command Patterns

Broad scan:

```bash
python3 scripts/search_arxiv_astro.py \
  --keywords-file assets/keywords.txt \
  --match any \
  --days 365 \
  --max-results 60
```

Narrow scan:

```bash
python3 scripts/search_arxiv_astro.py \
  --keyword "ultra-light dark matter" \
  --keyword "timing residuals" \
  --keyword "N-body simulation" \
  --match all \
  --exclude "axion haloscope" \
  --max-results 40
```
