#!/usr/bin/env python3
"""
Build and optionally email a daily arXiv digest filtered against a Zotero library.

The digest contains:
1) recent papers not already in Zotero
2) older "classic" papers not already in Zotero, ranked by semantic relevance
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import smtplib
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

from search_arxiv_astro import (
    ARXIV_API_URL,
    DEFAULT_CATEGORIES,
    clean_term,
    fetch_feed,
    parse_feed,
    resolve_keywords,
)
from semantic_profile import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_KEYWORD_WEIGHT,
    DEFAULT_PREPROCESS_VERSION,
    DEFAULT_SEMANTIC_WEIGHT,
    EmbeddingClient,
    SemanticCache,
    SemanticRecord,
    cosine_similarity,
    mean_vector,
)

DEFAULT_TIMEOUT = 20
DEFAULT_NEW_TOP_N = 12
STATE_RETENTION_DAYS = 120
NETWORK_MAX_RETRIES = 4
ARXIV_ID_RE = re.compile(
    r"(?i)(?:arxiv:|https?://arxiv\.org/(?:abs|pdf)/)"
    r"([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?"
)
ARXIV_ID_BARE_RE = re.compile(r"(?i)\b([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?\b")
ZOTERO_API_URL_TMPL = "https://api.zotero.org/users/{user_id}/items"
ZOTERO_COLLECTIONS_URL_TMPL = "https://api.zotero.org/users/{user_id}/collections"
SEMANTIC_SCHOLAR_TMPL = (
    "https://api.semanticscholar.org/graph/v1/paper/ARXIV:{paper_id}"
    "?fields=citationCount"
)
UNFILED_COLLECTION_KEY = "__unfiled__"
UNFILED_COLLECTION_NAME = "Unfiled"


@dataclass
class ZoteroEmbeddingRecord:
    item_key: str
    title: str
    abstract: str
    collection_keys: list[str]


@dataclass
class ZoteroIndex:
    arxiv_ids: set[str]
    dois: set[str]
    titles: set[str]
    embedding_records: list[ZoteroEmbeddingRecord]
    collection_names: dict[str, str]


@dataclass
class SemanticRunMetadata:
    ranking_mode: str
    warning_message: str | None
    zotero_embedded_new: int
    zotero_cached_reused: int
    arxiv_embedded_new: int
    arxiv_cached_reused: int


def parse_args() -> argparse.Namespace:
    base_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Generate a daily arXiv alert filtered by Zotero holdings."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=base_dir / ".env",
        help="Optional KEY=VALUE config file. Default: search-arxiv-astrophysics/.env",
    )
    parser.add_argument(
        "--keywords-file",
        type=Path,
        default=None,
        help=(
            "Keyword file used for keyword scoring/fallback ranking. "
            "Defaults from env or assets/keywords.txt."
        ),
    )
    parser.add_argument(
        "--match",
        choices=["any", "all"],
        default=None,
        help="Deprecated and ignored. Retrieval is category-only in v1.",
    )
    parser.add_argument(
        "--category",
        action="append",
        help="arXiv category filter (repeatable). Defaults to astrophysics set.",
    )
    parser.add_argument(
        "--semantic",
        choices=["auto", "on", "off"],
        default=None,
        help=(
            "Semantic ranking mode. auto: use semantic when available, else fallback. "
            "on: attempt semantic then fallback with warning if unavailable. "
            "off: always keyword ranking."
        ),
    )
    parser.add_argument(
        "--openai-api-key",
        default=None,
        help="OpenAI API key for embeddings. Defaults from env OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help=f"Embedding model. Defaults from env or {DEFAULT_EMBEDDING_MODEL}.",
    )
    parser.add_argument(
        "--embedding-batch-size",
        type=int,
        default=None,
        help=(
            "Embedding API batch size. "
            f"Defaults from env or {DEFAULT_EMBEDDING_BATCH_SIZE}."
        ),
    )
    parser.add_argument(
        "--semantic-weight",
        type=float,
        default=None,
        help=(
            "Weight for semantic similarity in final score. "
            f"Defaults from env or {DEFAULT_SEMANTIC_WEIGHT}."
        ),
    )
    parser.add_argument(
        "--keyword-weight",
        type=float,
        default=None,
        help=(
            "Weight for keyword score in final score. "
            f"Defaults from env or {DEFAULT_KEYWORD_WEIGHT}."
        ),
    )
    parser.add_argument(
        "--semantic-cache-path",
        type=Path,
        default=None,
        help="Path to semantic embedding cache sqlite file.",
    )
    parser.add_argument(
        "--rebuild-embeddings",
        action="store_true",
        help="Force full semantic embedding cache rebuild.",
    )
    parser.add_argument(
        "--new-days",
        type=int,
        default=1,
        help="How many days to consider for 'new papers'. Default: 1.",
    )
    parser.add_argument(
        "--new-max-results",
        type=int,
        default=120,
        help="arXiv fetch size for new-paper search. Default: 120.",
    )
    parser.add_argument(
        "--new-top-n",
        type=int,
        default=None,
        help=(
            "Max new papers included in digest. Cannot be combined with score "
            f"thresholds. Default when no threshold is set: {DEFAULT_NEW_TOP_N}."
        ),
    )
    parser.add_argument(
        "--new-semantic-threshold",
        type=float,
        default=None,
        help=(
            "Include new papers only when semantic_score is strictly greater "
            "than this value. May be combined with --new-overall-threshold."
        ),
    )
    parser.add_argument(
        "--new-overall-threshold",
        type=float,
        default=None,
        help=(
            "Include new papers only when final_score (the weighted overall score) "
            "is strictly greater than this value. May be combined with "
            "--new-semantic-threshold."
        ),
    )
    parser.add_argument(
        "--classic-max-results",
        type=int,
        default=120,
        help="arXiv fetch size for classic-paper candidates. Default: 120.",
    )
    parser.add_argument(
        "--classic-min-age-years",
        type=int,
        default=8,
        help="Minimum age for classic papers. Default: 8 years.",
    )
    parser.add_argument(
        "--classic-lookup-limit",
        type=int,
        default=30,
        help="Max classic candidates for citation lookup. Default: 30.",
    )
    parser.add_argument(
        "--classic-top-n",
        type=int,
        default=3,
        help="Max classics included in digest. Default: 3.",
    )
    parser.add_argument(
        "--classic-pool-size",
        type=int,
        default=50,
        help=(
            "Randomly sample classics from the top-ranked pool of this size. "
            "Default: 50."
        ),
    )
    parser.add_argument(
        "--zotero-user-id",
        default=None,
        help="Zotero user id. Defaults from env.",
    )
    parser.add_argument(
        "--zotero-api-key",
        default=None,
        help="Read-only Zotero API key. Defaults from env.",
    )
    parser.add_argument(
        "--zotero-max-items",
        type=int,
        default=2500,
        help="Max Zotero items to index. Default: 2500.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=base_dir / "assets/daily_alert_state.json",
        help="Local state file to avoid repeat alerts across runs.",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=base_dir / "assets/daily_alert_latest.md",
        help="Path to write latest digest report.",
    )
    parser.add_argument(
        "--send-existing-report",
        action="store_true",
        help=(
            "Email --report-file without regenerating the digest, querying Zotero, "
            "fetching arXiv, updating embeddings, or changing sent state."
        ),
    )
    parser.add_argument(
        "--smtp-host",
        default=None,
        help="SMTP host. Defaults from env.",
    )
    parser.add_argument(
        "--smtp-port",
        type=int,
        default=None,
        help="SMTP port. Defaults from env or 587.",
    )
    parser.add_argument(
        "--smtp-user",
        default=None,
        help="SMTP username. Defaults from env.",
    )
    parser.add_argument(
        "--smtp-password",
        default=None,
        help="SMTP password/app token. Defaults from env.",
    )
    parser.add_argument(
        "--smtp-from",
        default=None,
        help="From email address. Defaults from env.",
    )
    parser.add_argument(
        "--smtp-to",
        default=None,
        help="Comma-separated recipient list. Defaults from env.",
    )
    parser.add_argument(
        "--smtp-use-ssl",
        action="store_true",
        help="Use SMTP SSL directly (usually port 465).",
    )
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Do not send email; only print and save digest.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds. Default: {DEFAULT_TIMEOUT}.",
    )
    parser.add_argument(
        "--insecure-tls",
        action="store_true",
        help="Disable TLS certificate verification for HTTPS requests.",
    )
    return parser.parse_args()


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def parse_env_bool(raw_value: str | None, default: bool = False) -> bool:
    if raw_value is None:
        return default
    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    return default


def parse_env_float(raw_value: str | None, default: float) -> float:
    if raw_value is None or not str(raw_value).strip():
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


def parse_optional_score_threshold(
    raw_value: float | str | None,
    *,
    setting_name: str,
) -> float | None:
    if raw_value is None or not str(raw_value).strip():
        return None
    try:
        threshold = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{setting_name} must be a number.") from exc
    if not math.isfinite(threshold) or not -1.0 <= threshold <= 1.0:
        raise ValueError(f"{setting_name} must be between -1 and 1.")
    return threshold


def parse_env_int(raw_value: str | None, default: int) -> int:
    if raw_value is None or not str(raw_value).strip():
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def today_local_iso() -> str:
    return datetime.now().astimezone().date().isoformat()


def normalize_doi(value: str) -> str:
    doi = value.strip().lower()
    doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return doi


def normalize_title(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^a-z0-9 ]+", "", value)
    return value.strip()


def canonical_arxiv_id(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    match = ARXIV_ID_RE.search(text)
    if not match:
        match = ARXIV_ID_BARE_RE.search(text)
    if not match:
        return None
    return match.group(1).lower()


def safe_json_get(
    url: str,
    headers: dict[str, str] | None,
    timeout: int,
    insecure_tls: bool = False,
) -> list | dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "codex-arxiv-zotero-alert/1.0",
            **(headers or {}),
        },
    )
    context = ssl._create_unverified_context() if insecure_tls else None
    delay_seconds = 1.0
    last_error: Exception | None = None

    for attempt in range(NETWORK_MAX_RETRIES):
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=context
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in {408, 409, 425, 429, 500, 502, 503, 504} and attempt < NETWORK_MAX_RETRIES - 1:
                time.sleep(delay_seconds)
                delay_seconds *= 2
                continue
            raise
        except urllib.error.URLError as exc:
            last_error = exc
            reason = getattr(exc, "reason", exc)
            is_timeout = isinstance(reason, (TimeoutError, socket.timeout)) or (
                isinstance(reason, ssl.SSLError) and "timed out" in str(reason).lower()
            ) or ("timed out" in str(reason).lower())
            if is_timeout and attempt < NETWORK_MAX_RETRIES - 1:
                time.sleep(delay_seconds)
                delay_seconds *= 2
                continue
            raise

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to fetch JSON from {url}.")


def fetch_zotero_collection_names(
    user_id: str,
    api_key: str,
    timeout: int,
    insecure_tls: bool = False,
) -> dict[str, str]:
    raw_collections: dict[str, tuple[str, str | None]] = {}
    limit = 100
    fetched = 0

    while True:
        params = urllib.parse.urlencode(
            {"format": "json", "start": fetched, "limit": limit}
        )
        url = f"{ZOTERO_COLLECTIONS_URL_TMPL.format(user_id=user_id)}?{params}"
        payload = safe_json_get(
            url,
            headers={"Zotero-API-Key": api_key},
            timeout=timeout,
            insecure_tls=insecure_tls,
        )
        if not isinstance(payload, list) or not payload:
            break

        for item in payload:
            data = item.get("data", {})
            collection_key = str(item.get("key", "") or "").strip()
            if not collection_key:
                continue
            name = str(data.get("name", "") or collection_key).strip() or collection_key
            parent_raw = str(data.get("parentCollection", "") or "").strip()
            raw_collections[collection_key] = (name, parent_raw or None)

        fetched += len(payload)
        if len(payload) < limit:
            break

    def resolve_collection_name(
        collection_key: str,
        seen: set[str] | None = None,
    ) -> str:
        if collection_key not in raw_collections:
            return collection_key
        if seen is None:
            seen = set()
        if collection_key in seen:
            return raw_collections[collection_key][0]

        name, parent_key = raw_collections[collection_key]
        if not parent_key:
            return name
        parent_name = resolve_collection_name(parent_key, seen | {collection_key})
        return f"{parent_name} / {name}"

    return {
        collection_key: resolve_collection_name(collection_key)
        for collection_key in raw_collections
    }


def fetch_zotero_index(
    user_id: str,
    api_key: str,
    max_items: int,
    timeout: int,
    insecure_tls: bool = False,
) -> ZoteroIndex:
    arxiv_ids: set[str] = set()
    dois: set[str] = set()
    titles: set[str] = set()
    embedding_records: list[ZoteroEmbeddingRecord] = []
    collection_names = fetch_zotero_collection_names(
        user_id=user_id,
        api_key=api_key,
        timeout=timeout,
        insecure_tls=insecure_tls,
    )
    limit = 100
    fetched = 0

    while fetched < max_items:
        batch_limit = min(limit, max_items - fetched)
        params = urllib.parse.urlencode(
            {"format": "json", "start": fetched, "limit": batch_limit}
        )
        url = f"{ZOTERO_API_URL_TMPL.format(user_id=user_id)}?{params}"
        payload = safe_json_get(
            url,
            headers={"Zotero-API-Key": api_key},
            timeout=timeout,
            insecure_tls=insecure_tls,
        )
        if not isinstance(payload, list) or not payload:
            break

        for item in payload:
            data = item.get("data", {})
            title_raw = str(data.get("title", "") or "")
            abstract_raw = str(data.get("abstractNote", "") or "")
            collection_keys = [
                str(collection_key).strip()
                for collection_key in data.get("collections", [])
                if str(collection_key).strip()
            ]

            title = normalize_title(title_raw)
            if title:
                titles.add(title)

            doi = normalize_doi(data.get("DOI", ""))
            if doi:
                dois.add(doi)

            for field in ("url", "archiveLocation", "extra"):
                arxiv_id = canonical_arxiv_id(str(data.get(field, "")))
                if arxiv_id:
                    arxiv_ids.add(arxiv_id)

            note = data.get("extra", "")
            if note:
                for token in re.split(r"[\s,;|]+", str(note)):
                    arxiv_id = canonical_arxiv_id(token)
                    if arxiv_id:
                        arxiv_ids.add(arxiv_id)

            item_key = str(item.get("key", "") or "").strip()
            if item_key and (title_raw.strip() or abstract_raw.strip()):
                embedding_records.append(
                    ZoteroEmbeddingRecord(
                        item_key=item_key,
                        title=title_raw,
                        abstract=abstract_raw,
                        collection_keys=collection_keys,
                    )
                )

        fetched += len(payload)
        if len(payload) < batch_limit:
            break

    return ZoteroIndex(
        arxiv_ids=arxiv_ids,
        dois=dois,
        titles=titles,
        embedding_records=embedding_records,
        collection_names=collection_names,
    )


def build_category_query(categories: list[str]) -> str:
    category_terms = [f"cat:{clean_term(c)}" for c in categories if clean_term(c)]
    if not category_terms:
        raise ValueError("At least one category must be provided.")
    if len(category_terms) == 1:
        return category_terms[0]
    return f"({' OR '.join(category_terms)})"


def build_arxiv_url(
    categories: list[str],
    max_results: int,
    sort_by: str,
    sort_order: str = "descending",
) -> str:
    query = build_category_query(categories)
    params = urllib.parse.urlencode(
        {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
    )
    return f"{ARXIV_API_URL}?{params}"


def fetch_arxiv_entries(
    *,
    keywords: list[str],
    categories: list[str],
    max_results: int,
    sort_by: str,
    sort_order: str,
    days: int | None,
    timeout: int,
    insecure_tls: bool,
) -> list[dict]:
    url = build_arxiv_url(
        categories=categories,
        max_results=max_results,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    feed = fetch_feed(url, timeout=timeout, insecure_tls=insecure_tls)
    entries = parse_feed(
        feed,
        keywords=keywords,
        days=days,
        days_field="published",
    )
    entries.sort(
        key=lambda row: row["_ranking_dt"],
        reverse=(sort_order == "descending"),
    )
    return entries


def is_known_in_zotero(entry: dict, zotero: ZoteroIndex) -> bool:
    arxiv_id = canonical_arxiv_id(entry.get("id", ""))
    if arxiv_id and arxiv_id in zotero.arxiv_ids:
        return True
    title = normalize_title(entry.get("title", ""))
    if title and title in zotero.titles:
        return True
    return False


def load_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, str):
            cleaned[key] = value
    return cleaned


def save_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prune_state(state: dict[str, str], retention_days: int) -> dict[str, str]:
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=retention_days)
    keep: dict[str, str] = {}
    for paper_id, sent_date in state.items():
        try:
            date_value = datetime.fromisoformat(sent_date).date()
        except ValueError:
            continue
        if date_value >= cutoff:
            keep[paper_id] = sent_date
    return keep


def fetch_citation_count(
    arxiv_id: str,
    timeout: int,
    insecure_tls: bool = False,
) -> int | None:
    url = SEMANTIC_SCHOLAR_TMPL.format(paper_id=urllib.parse.quote(arxiv_id, safe=""))
    try:
        payload = safe_json_get(
            url,
            headers=None,
            timeout=timeout,
            insecure_tls=insecure_tls,
        )
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 429}:
            return None
        raise
    except urllib.error.URLError:
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("citationCount")
    return int(raw) if isinstance(raw, int) else None


def score_classics(
    entries: list[dict],
    *,
    min_age_years: int,
    lookup_limit: int,
    timeout: int,
    insecure_tls: bool,
) -> list[dict]:
    now = datetime.now(timezone.utc)
    eligible: list[dict] = []
    for entry in entries:
        age_years = (now - entry["_published_dt"]).days / 365.25
        if age_years < min_age_years:
            continue
        row = dict(entry)
        row["age_years"] = round(age_years, 1)
        row["citation_count"] = None
        eligible.append(row)

    for row in eligible[:lookup_limit]:
        paper_id = canonical_arxiv_id(row.get("id", ""))
        if not paper_id:
            continue
        row["citation_count"] = fetch_citation_count(
            paper_id, timeout=timeout, insecure_tls=insecure_tls
        )

    return eligible


def entry_cache_id(entry: dict) -> str:
    return canonical_arxiv_id(entry.get("id", "")) or str(entry.get("id", "")).strip()


def normalize_weights(semantic_weight: float, keyword_weight: float) -> tuple[float, float]:
    if semantic_weight < 0 or keyword_weight < 0:
        raise ValueError("SEMANTIC_WEIGHT and KEYWORD_WEIGHT must be non-negative.")
    total = semantic_weight + keyword_weight
    if total <= 0:
        raise ValueError("SEMANTIC_WEIGHT + KEYWORD_WEIGHT must be greater than zero.")
    return semantic_weight / total, keyword_weight / total


def attach_keyword_score_norm(entries: list[dict]) -> None:
    max_keyword = max((int(entry.get("keyword_score", 0)) for entry in entries), default=0)
    for entry in entries:
        keyword_score = float(entry.get("keyword_score", 0))
        keyword_norm = keyword_score / max_keyword if max_keyword > 0 else 0.0
        entry["keyword_score_norm"] = keyword_norm


def build_collection_centroids(
    embedding_records: list[ZoteroEmbeddingRecord],
    zotero_vectors: dict[str, list[float]],
    collection_names: dict[str, str],
) -> tuple[dict[str, list[float]], dict[str, str]]:
    grouped_vectors: dict[str, list[list[float]]] = {}
    resolved_collection_names = dict(collection_names)

    for record in embedding_records:
        vector = zotero_vectors.get(record.item_key)
        if vector is None:
            continue
        target_keys = record.collection_keys or [UNFILED_COLLECTION_KEY]
        for collection_key in target_keys:
            grouped_vectors.setdefault(collection_key, []).append(vector)
            if collection_key not in resolved_collection_names:
                resolved_collection_names[collection_key] = collection_key

    if UNFILED_COLLECTION_KEY in grouped_vectors:
        resolved_collection_names[UNFILED_COLLECTION_KEY] = UNFILED_COLLECTION_NAME

    centroids: dict[str, list[float]] = {}
    for collection_key, vectors in grouped_vectors.items():
        centroid = mean_vector(vectors)
        if centroid is not None:
            centroids[collection_key] = centroid

    return centroids, resolved_collection_names


def best_collection_match(
    candidate_vector: list[float],
    collection_centroids: dict[str, list[float]],
    collection_names: dict[str, str],
) -> tuple[float, str | None]:
    if not collection_centroids:
        return 0.0, None

    best_key = max(
        collection_centroids,
        key=lambda collection_key: cosine_similarity(
            candidate_vector, collection_centroids[collection_key]
        ),
    )
    best_score = cosine_similarity(candidate_vector, collection_centroids[best_key])
    return best_score, collection_names.get(best_key, best_key)


def apply_semantic_scores(
    entries: list[dict],
    *,
    collection_centroids: dict[str, list[float]],
    collection_names: dict[str, str],
    candidate_vectors: dict[str, list[float]],
    semantic_weight: float,
    keyword_weight: float,
) -> None:
    attach_keyword_score_norm(entries)
    for entry in entries:
        semantic_score = 0.0
        semantic_collection = None
        candidate_vector = candidate_vectors.get(entry_cache_id(entry))
        if candidate_vector is not None:
            semantic_score, semantic_collection = best_collection_match(
                candidate_vector,
                collection_centroids,
                collection_names,
            )
        entry["semantic_score"] = semantic_score
        entry["semantic_collection"] = semantic_collection
        entry["final_score"] = (
            semantic_weight * semantic_score
            + keyword_weight * float(entry.get("keyword_score_norm", 0.0))
        )


def apply_keyword_only_scores(entries: list[dict]) -> None:
    attach_keyword_score_norm(entries)
    for entry in entries:
        entry["semantic_score"] = None
        entry["semantic_collection"] = None
        entry["final_score"] = float(entry.get("keyword_score_norm", 0.0))


def sort_new_entries(entries: list[dict], semantic_available: bool) -> None:
    if semantic_available:
        entries.sort(
            key=lambda row: (
                float(row.get("final_score", 0.0)),
                row.get("_ranking_dt"),
            ),
            reverse=True,
        )
        return
    entries.sort(
        key=lambda row: (
            int(row.get("keyword_score", 0)),
            row.get("_ranking_dt"),
        ),
        reverse=True,
    )


def select_new_entries(
    entries: list[dict],
    *,
    new_top_n: int,
    semantic_threshold: float | None,
    overall_threshold: float | None,
    semantic_available: bool,
) -> list[dict]:
    threshold_mode = semantic_threshold is not None or overall_threshold is not None
    if not threshold_mode:
        return list(entries[:new_top_n])
    if not semantic_available:
        raise RuntimeError(
            "New-paper score thresholds require semantic scoring, but semantic "
            "scoring is unavailable. Check OPENAI_API_KEY and the semantic warning."
        )

    selected = []
    for entry in entries:
        semantic_score = entry.get("semantic_score")
        overall_score = entry.get("final_score")
        if semantic_threshold is not None and (
            semantic_score is None or float(semantic_score) <= semantic_threshold
        ):
            continue
        if overall_threshold is not None and (
            overall_score is None or float(overall_score) <= overall_threshold
        ):
            continue
        selected.append(entry)
    return selected


def sort_classic_entries(entries: list[dict], semantic_available: bool) -> None:
    if semantic_available:
        entries.sort(
            key=lambda row: (
                float(row.get("final_score", 0.0)),
                -1 if row.get("citation_count") is None else row.get("citation_count", -1),
                int(row.get("keyword_score", 0)),
            ),
            reverse=True,
        )
        return

    entries.sort(
        key=lambda row: (
            int(row.get("keyword_score", 0)),
            -1 if row.get("citation_count") is None else row.get("citation_count", -1),
            row.get("_ranking_dt"),
        ),
        reverse=True,
    )


def select_classic_entries(
    entries: list[dict],
    *,
    classic_top_n: int,
    classic_pool_size: int,
    semantic_available: bool,
) -> list[dict]:
    if classic_top_n <= 0 or not entries:
        return []

    pool_limit = max(classic_top_n, classic_pool_size)
    candidate_pool = list(entries[:pool_limit])
    if len(candidate_pool) <= classic_top_n:
        return candidate_pool

    selected = random.sample(candidate_pool, classic_top_n)
    sort_classic_entries(selected, semantic_available=semantic_available)
    return selected


def build_candidate_semantic_records(entries: Iterable[dict]) -> list[SemanticRecord]:
    records: dict[str, SemanticRecord] = {}
    for entry in entries:
        record_id = entry_cache_id(entry)
        if not record_id:
            continue
        if record_id in records:
            continue
        records[record_id] = SemanticRecord(
            record_id=record_id,
            title=str(entry.get("title", "")),
            abstract=str(entry.get("summary", "")),
        )
    return list(records.values())


def format_lines(entries: Iterable[dict], include_citations: bool = False) -> list[str]:
    lines: list[str] = []
    for idx, entry in enumerate(entries, start=1):
        matched = ", ".join(entry.get("matched_keywords", [])) or "n/a"
        lines.append(f"{idx}. {entry.get('title', '').strip()}")
        lines.append(f"   - arXiv: {entry.get('id', '')}")
        lines.append(f"   - Published: {entry.get('published', '')}")
        if entry.get("primary_category"):
            lines.append(f"   - Category: {entry['primary_category']}")
        lines.append(
            "   - Keyword score: "
            f"{entry.get('keyword_score', 0)} "
            f"(norm={float(entry.get('keyword_score_norm', 0.0)):.4f})"
        )
        if entry.get("semantic_score") is not None:
            lines.append(f"   - Semantic score: {float(entry.get('semantic_score', 0.0)):.4f}")
        if entry.get("semantic_collection"):
            lines.append(f"   - Best collection match: {entry['semantic_collection']}")
        lines.append(f"   - Final score: {float(entry.get('final_score', 0.0)):.4f}")
        lines.append(f"   - Matched keywords: {matched}")
        if include_citations:
            citation = entry.get("citation_count")
            age = entry.get("age_years")
            citation_text = "unavailable" if citation is None else str(citation)
            lines.append(f"   - Citations: {citation_text}")
            lines.append(f"   - Approx age (years): {age}")
        lines.append("")
    return lines


def build_digest(
    *,
    new_entries: list[dict],
    classic_entries: list[dict],
    categories: list[str],
    keywords_file: Path,
    new_days: int,
    new_selection_description: str,
    semantic_metadata: SemanticRunMetadata,
) -> str:
    today = today_local_iso()
    lines = [
        f"# arXiv Daily Digest ({today})",
        "",
        "## Scope",
        f"- Categories: {', '.join(categories)}",
        f"- Keyword file: {keywords_file}",
        f"- New-paper window: last {new_days} day(s)",
        f"- New-paper selection: {new_selection_description}",
        f"- Ranking mode: {semantic_metadata.ranking_mode}",
        (
            "- Semantic cache updates: "
            f"zotero(new={semantic_metadata.zotero_embedded_new}, "
            f"reused={semantic_metadata.zotero_cached_reused}), "
            f"arxiv(new={semantic_metadata.arxiv_embedded_new}, "
            f"reused={semantic_metadata.arxiv_cached_reused})"
        ),
        "",
    ]
    if semantic_metadata.warning_message:
        lines.append(f"- Warning: {semantic_metadata.warning_message}")
        lines.append("")

    lines.append("## New Papers Not In Zotero")
    if new_entries:
        lines.extend(format_lines(new_entries, include_citations=False))
    else:
        lines.append("No new unknown papers found today.")
        lines.append("")

    lines.append("## Classic Papers You Likely Don't Have")
    if classic_entries:
        lines.extend(format_lines(classic_entries, include_citations=True))
    else:
        lines.append("No classic unknown papers found in this run.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def send_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str | None,
    smtp_password: str | None,
    smtp_from: str,
    smtp_to: list[str],
    smtp_use_ssl: bool,
    subject: str,
    body: str,
) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_from
    message["To"] = ", ".join(smtp_to)
    message.set_content(body)

    if smtp_use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            if smtp_user:
                server.login(smtp_user, smtp_password or "")
            server.send_message(message)
        return

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        if smtp_user:
            server.login(smtp_user, smtp_password or "")
        server.send_message(message)


def resolve_smtp_settings(
    args: argparse.Namespace,
    env_values: dict[str, str],
) -> tuple[str | None, int, str | None, str | None, str | None, list[str], bool]:
    smtp_host = first_non_empty(args.smtp_host, env_values.get("SMTP_HOST"))
    smtp_port = args.smtp_port or int(first_non_empty(env_values.get("SMTP_PORT"), "587") or "587")
    smtp_user = first_non_empty(args.smtp_user, env_values.get("SMTP_USER"))
    smtp_password = first_non_empty(args.smtp_password, env_values.get("SMTP_PASSWORD"))
    smtp_from = first_non_empty(args.smtp_from, env_values.get("SMTP_FROM"))
    smtp_to_raw = first_non_empty(
        args.smtp_to,
        env_values.get("SMTP_TO"),
        env_values.get("ALERT_EMAIL_TO"),
    )
    smtp_to = [addr.strip() for addr in (smtp_to_raw or "").split(",") if addr.strip()]
    smtp_use_ssl = args.smtp_use_ssl or parse_env_bool(
        env_values.get("SMTP_USE_SSL"), default=False
    )
    return (
        smtp_host,
        smtp_port,
        smtp_user,
        smtp_password,
        smtp_from,
        smtp_to,
        smtp_use_ssl,
    )


def validate_email_delivery_settings(
    *,
    smtp_host: str | None,
    smtp_from: str | None,
    smtp_to: list[str],
) -> None:
    missing = []
    if not smtp_host:
        missing.append("SMTP_HOST")
    if not smtp_from:
        missing.append("SMTP_FROM")
    if not smtp_to:
        missing.append("SMTP_TO or ALERT_EMAIL_TO")
    if missing:
        raise ValueError("Missing email delivery settings: " + ", ".join(missing))


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parents[1]
    env_values = parse_env_file(args.env_file)
    (
        smtp_host,
        smtp_port,
        smtp_user,
        smtp_password,
        smtp_from,
        smtp_to,
        smtp_use_ssl,
    ) = resolve_smtp_settings(args, env_values)
    preview_only = args.preview_only or parse_env_bool(
        env_values.get("ALERT_PREVIEW_ONLY"), default=False
    )

    if args.send_existing_report:
        if not args.report_file.exists():
            raise FileNotFoundError(f"Report file not found: {args.report_file}")
        digest = args.report_file.read_text(encoding="utf-8")
        print(digest)
        if preview_only:
            return 0
        validate_email_delivery_settings(
            smtp_host=smtp_host,
            smtp_from=smtp_from,
            smtp_to=smtp_to,
        )
        send_email(
            smtp_host=smtp_host or "",
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            smtp_from=smtp_from or "",
            smtp_to=smtp_to,
            smtp_use_ssl=smtp_use_ssl,
            subject=f"arXiv digest resend: {today_local_iso()}",
            body=digest,
        )
        print(f"Existing report emailed to: {', '.join(smtp_to)}")
        return 0

    categories_env = first_non_empty(env_values.get("ARXIV_CATEGORIES"), None)
    categories = args.category or []
    if not categories and categories_env:
        categories = [clean_term(c) for c in categories_env.split(",") if clean_term(c)]
    if not categories:
        categories = list(DEFAULT_CATEGORIES)

    if args.match is not None:
        print("Warning: --match is deprecated and ignored. Retrieval is category-only.")
    env_match = first_non_empty(env_values.get("ARXIV_MATCH"), None)
    if env_match and env_match.lower() not in {"", "any"}:
        print(
            "Warning: ARXIV_MATCH is deprecated and ignored. "
            "Retrieval is category-only."
        )

    keywords_raw = first_non_empty(
        str(args.keywords_file) if args.keywords_file else None,
        env_values.get("ARXIV_KEYWORDS_FILE"),
        str(base_dir / "assets/keywords.txt"),
    )
    keywords_path = Path(keywords_raw or str(base_dir / "assets/keywords.txt"))
    if not keywords_path.is_absolute():
        keywords_path = (base_dir / keywords_path).resolve()

    keywords = resolve_keywords(inline_keywords=None, keywords_file=str(keywords_path))
    if not keywords:
        raise ValueError(f"No keywords found in {keywords_path}.")

    semantic_mode = (
        first_non_empty(args.semantic, env_values.get("SEMANTIC_MODE"), "auto") or "auto"
    )
    if semantic_mode not in {"auto", "on", "off"}:
        raise ValueError("SEMANTIC_MODE must be one of auto|on|off.")

    semantic_threshold = parse_optional_score_threshold(
        (
            args.new_semantic_threshold
            if args.new_semantic_threshold is not None
            else env_values.get("NEW_SEMANTIC_THRESHOLD")
        ),
        setting_name="--new-semantic-threshold/NEW_SEMANTIC_THRESHOLD",
    )
    overall_threshold = parse_optional_score_threshold(
        (
            args.new_overall_threshold
            if args.new_overall_threshold is not None
            else env_values.get("NEW_OVERALL_THRESHOLD")
        ),
        setting_name="--new-overall-threshold/NEW_OVERALL_THRESHOLD",
    )
    threshold_mode = semantic_threshold is not None or overall_threshold is not None
    if args.new_top_n is not None and threshold_mode:
        raise ValueError(
            "--new-top-n cannot be combined with new-paper score thresholds."
        )
    new_top_n = DEFAULT_NEW_TOP_N if args.new_top_n is None else args.new_top_n
    if new_top_n < 0:
        raise ValueError("--new-top-n must be non-negative.")
    if threshold_mode and semantic_mode == "off":
        raise ValueError(
            "New-paper score thresholds cannot be used with semantic scoring disabled."
        )

    semantic_cache_path_raw = first_non_empty(
        str(args.semantic_cache_path) if args.semantic_cache_path else None,
        env_values.get("SEMANTIC_CACHE_PATH"),
        str(base_dir / "assets/semantic_cache.sqlite"),
    )
    semantic_cache_path = Path(
        semantic_cache_path_raw or str(base_dir / "assets/semantic_cache.sqlite")
    )
    if not semantic_cache_path.is_absolute():
        semantic_cache_path = (base_dir / semantic_cache_path).resolve()

    embedding_model = (
        first_non_empty(
            args.embedding_model,
            env_values.get("EMBEDDING_MODEL"),
            DEFAULT_EMBEDDING_MODEL,
        )
        or DEFAULT_EMBEDDING_MODEL
    )
    embedding_batch_size = (
        args.embedding_batch_size
        if args.embedding_batch_size is not None
        else parse_env_int(
            env_values.get("EMBEDDING_BATCH_SIZE"),
            DEFAULT_EMBEDDING_BATCH_SIZE,
        )
    )
    if embedding_batch_size < 1:
        raise ValueError("--embedding-batch-size must be at least 1.")

    semantic_weight_raw = (
        args.semantic_weight
        if args.semantic_weight is not None
        else parse_env_float(
            env_values.get("SEMANTIC_WEIGHT"),
            DEFAULT_SEMANTIC_WEIGHT,
        )
    )
    keyword_weight_raw = (
        args.keyword_weight
        if args.keyword_weight is not None
        else parse_env_float(
            env_values.get("KEYWORD_WEIGHT"),
            DEFAULT_KEYWORD_WEIGHT,
        )
    )
    semantic_weight, keyword_weight = normalize_weights(
        semantic_weight_raw,
        keyword_weight_raw,
    )

    openai_api_key = first_non_empty(
        args.openai_api_key,
        env_values.get("OPENAI_API_KEY"),
    )
    if threshold_mode and not openai_api_key:
        raise ValueError(
            "New-paper score thresholds require OPENAI_API_KEY for semantic scoring."
        )

    zotero_user_id = first_non_empty(args.zotero_user_id, env_values.get("ZOTERO_USER_ID"))
    zotero_api_key = first_non_empty(args.zotero_api_key, env_values.get("ZOTERO_API_KEY"))
    if not zotero_user_id or not zotero_api_key:
        raise ValueError(
            "Missing Zotero credentials. Provide ZOTERO_USER_ID and ZOTERO_API_KEY "
            "via --env-file or command flags."
        )

    zotero = fetch_zotero_index(
        user_id=zotero_user_id,
        api_key=zotero_api_key,
        max_items=args.zotero_max_items,
        timeout=args.timeout,
        insecure_tls=args.insecure_tls,
    )

    state = prune_state(load_state(args.state_file), STATE_RETENTION_DAYS)

    new_candidates = fetch_arxiv_entries(
        keywords=keywords,
        categories=categories,
        max_results=args.new_max_results,
        sort_by="submittedDate",
        sort_order="descending",
        days=args.new_days,
        timeout=args.timeout,
        insecure_tls=args.insecure_tls,
    )
    new_unknown = []
    for entry in new_candidates:
        paper_id = entry_cache_id(entry)
        if paper_id in state:
            continue
        if is_known_in_zotero(entry, zotero):
            continue
        new_unknown.append(entry)

    classic_candidates = fetch_arxiv_entries(
        keywords=keywords,
        categories=categories,
        max_results=args.classic_max_results,
        sort_by="submittedDate",
        sort_order="ascending",
        days=None,
        timeout=args.timeout,
        insecure_tls=args.insecure_tls,
    )
    classic_unknown = [
        entry for entry in classic_candidates if not is_known_in_zotero(entry, zotero)
    ]
    classic_scored = score_classics(
        classic_unknown,
        min_age_years=args.classic_min_age_years,
        lookup_limit=args.classic_lookup_limit,
        timeout=args.timeout,
        insecure_tls=args.insecure_tls,
    )

    semantic_available = False
    semantic_warning: str | None = None
    semantic_metadata = SemanticRunMetadata(
        ranking_mode="keyword fallback; semantic unavailable",
        warning_message=None,
        zotero_embedded_new=0,
        zotero_cached_reused=0,
        arxiv_embedded_new=0,
        arxiv_cached_reused=0,
    )

    if semantic_mode == "off":
        semantic_metadata.ranking_mode = "keyword only (semantic disabled)"
    elif not openai_api_key:
        semantic_warning = "OPENAI_API_KEY is missing"
    else:
        semantic_cache = SemanticCache(semantic_cache_path)
        try:
            semantic_cache.ensure_compatible(
                model=embedding_model,
                preprocess_version=DEFAULT_PREPROCESS_VERSION,
                rebuild=args.rebuild_embeddings,
            )
            embedding_client = EmbeddingClient(
                api_key=openai_api_key,
                model=embedding_model,
                timeout=args.timeout,
                insecure_tls=args.insecure_tls,
                batch_size=embedding_batch_size,
            )

            zotero_records = [
                SemanticRecord(
                    record_id=record.item_key,
                    title=record.title,
                    abstract=record.abstract,
                )
                for record in zotero.embedding_records
            ]
            zotero_vectors, zotero_stats = semantic_cache.sync_records(
                table="zotero_embeddings",
                id_column="item_key",
                records=zotero_records,
                model=embedding_model,
                embed_texts=embedding_client.embed_texts,
                preprocess_version=DEFAULT_PREPROCESS_VERSION,
                prune_missing=True,
            )
            semantic_metadata.zotero_embedded_new = zotero_stats.embedded_new
            semantic_metadata.zotero_cached_reused = zotero_stats.reused_cached

            collection_centroids, collection_names = build_collection_centroids(
                zotero.embedding_records,
                zotero_vectors,
                zotero.collection_names,
            )

            if not collection_centroids:
                semantic_warning = "no embeddable Zotero records"
            else:
                candidate_records = build_candidate_semantic_records(
                    [*new_unknown, *classic_scored]
                )
                candidate_vectors, arxiv_stats = semantic_cache.sync_records(
                    table="arxiv_embeddings",
                    id_column="paper_id",
                    records=candidate_records,
                    model=embedding_model,
                    embed_texts=embedding_client.embed_texts,
                    preprocess_version=DEFAULT_PREPROCESS_VERSION,
                    prune_missing=False,
                )
                semantic_metadata.arxiv_embedded_new = arxiv_stats.embedded_new
                semantic_metadata.arxiv_cached_reused = arxiv_stats.reused_cached

                if not candidate_vectors:
                    semantic_warning = "no embeddable arXiv candidates"
                else:
                    apply_semantic_scores(
                        new_unknown,
                        collection_centroids=collection_centroids,
                        collection_names=collection_names,
                        candidate_vectors=candidate_vectors,
                        semantic_weight=semantic_weight,
                        keyword_weight=keyword_weight,
                    )
                    apply_semantic_scores(
                        classic_scored,
                        collection_centroids=collection_centroids,
                        collection_names=collection_names,
                        candidate_vectors=candidate_vectors,
                        semantic_weight=semantic_weight,
                        keyword_weight=keyword_weight,
                    )
                    semantic_available = True
                    semantic_metadata.ranking_mode = (
                        "semantic per-collection centroid "
                        f"({len(collection_centroids)} profiles; final_score = "
                        f"{semantic_weight:.2f}*semantic + {keyword_weight:.2f}*keyword)"
                    )
        except Exception as exc:
            semantic_warning = str(exc)
        finally:
            semantic_cache.close()

    if not semantic_available:
        apply_keyword_only_scores(new_unknown)
        apply_keyword_only_scores(classic_scored)
        if semantic_mode != "off":
            semantic_metadata.warning_message = (
                "Ranking mode: keyword fallback; semantic unavailable"
                + (f" ({semantic_warning})" if semantic_warning else "")
            )
            print(f"Warning: {semantic_metadata.warning_message}")
        else:
            semantic_metadata.warning_message = "Semantic ranking disabled (--semantic off)."

    sort_new_entries(new_unknown, semantic_available=semantic_available)
    sort_classic_entries(classic_scored, semantic_available=semantic_available)

    new_entries = select_new_entries(
        new_unknown,
        new_top_n=new_top_n,
        semantic_threshold=semantic_threshold,
        overall_threshold=overall_threshold,
        semantic_available=semantic_available,
    )
    if threshold_mode:
        selection_parts = []
        if semantic_threshold is not None:
            selection_parts.append(f"semantic score > {semantic_threshold:g}")
        if overall_threshold is not None:
            selection_parts.append(f"overall score > {overall_threshold:g}")
        new_selection_description = " and ".join(selection_parts)
    else:
        new_selection_description = f"top {new_top_n} by ranking"
    classic_entries = select_classic_entries(
        classic_scored,
        classic_top_n=args.classic_top_n,
        classic_pool_size=args.classic_pool_size,
        semantic_available=semantic_available,
    )

    digest = build_digest(
        new_entries=new_entries,
        classic_entries=classic_entries,
        categories=categories,
        keywords_file=keywords_path,
        new_days=args.new_days,
        new_selection_description=new_selection_description,
        semantic_metadata=semantic_metadata,
    )
    args.report_file.parent.mkdir(parents=True, exist_ok=True)
    args.report_file.write_text(digest, encoding="utf-8")
    print(digest)

    can_send = all([smtp_host, smtp_from, smtp_to])
    if preview_only or not can_send:
        if not can_send:
            print(
                "Email skipped: set SMTP_HOST, SMTP_FROM, and SMTP_TO (or ALERT_EMAIL_TO) "
                "to enable delivery."
            )
        return 0

    subject = f"arXiv digest: {today_local_iso()}"
    send_email(
        smtp_host=smtp_host or "",
        smtp_port=smtp_port,
        smtp_user=smtp_user,
        smtp_password=smtp_password,
        smtp_from=smtp_from or "",
        smtp_to=smtp_to,
        smtp_use_ssl=smtp_use_ssl,
        subject=subject,
        body=digest,
    )
    sent_today = today_local_iso()
    for entry in new_entries:
        paper_id = entry_cache_id(entry)
        state[paper_id] = sent_today
    save_state(args.state_file, state)
    print(f"Email sent to: {', '.join(smtp_to)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
