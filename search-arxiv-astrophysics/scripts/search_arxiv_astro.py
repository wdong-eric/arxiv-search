#!/usr/bin/env python3
"""
Search arXiv for astrophysics papers with user-provided keywords.

Examples:
  python3 scripts/search_arxiv_astro.py \
    --keyword "dark matter" --keyword "pulsar timing" --days 365
  python3 scripts/search_arxiv_astro.py \
    --keywords-file assets/keywords.txt --match any --days 90
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Iterable

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_API_URL_HTTP = "http://export.arxiv.org/api/query"
DEFAULT_TIMEOUT = 20
DEFAULT_CATEGORIES = [
    "astro-ph.CO",
    "astro-ph.EP",
    "astro-ph.GA",
    "astro-ph.HE",
    "astro-ph.IM",
    "astro-ph.SR",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search arXiv astrophysics papers from keywords."
    )
    parser.add_argument(
        "--keyword",
        "-k",
        action="append",
        help="Keyword or phrase to include (repeat for multiple terms).",
    )
    parser.add_argument(
        "--keywords-file",
        type=str,
        help=(
            "Path to a text file containing keywords (comma-separated or one-per-line). "
            "Lines may include comments starting with #."
        ),
    )
    parser.add_argument(
        "--match",
        choices=["any", "all"],
        default="any",
        help="Combine keywords with OR (any) or AND (all). Default: any.",
    )
    parser.add_argument(
        "--exclude",
        "-x",
        action="append",
        default=[],
        help="Keyword or phrase to exclude (repeatable).",
    )
    parser.add_argument(
        "--category",
        "-c",
        action="append",
        help=(
            "arXiv category filter (repeatable). "
            "Defaults to astrophysics categories."
        ),
    )
    parser.add_argument(
        "--start",
        type=int,
        default=0,
        help="Start index for paginated arXiv results.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=40,
        help="Number of records to request from arXiv. Default: 40.",
    )
    parser.add_argument(
        "--sort-by",
        choices=["relevance", "lastUpdatedDate", "submittedDate"],
        default="submittedDate",
        help="arXiv API sort field. Default: submittedDate.",
    )
    parser.add_argument(
        "--sort-order",
        choices=["ascending", "descending"],
        default="descending",
        help="arXiv API sort order. Default: descending.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Keep only papers published in the last N days.",
    )
    parser.add_argument(
        "--no-keyword-rank",
        action="store_true",
        help="Do not re-rank by local keyword matching score.",
    )
    parser.add_argument(
        "--output",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format. Default: markdown.",
    )
    parser.add_argument(
        "--save",
        type=str,
        help="Optional file path to save the rendered output.",
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
        help=(
            "Disable TLS certificate verification for HTTPS requests. "
            "Use only when local cert trust is broken."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the API URL and exit without network request.",
    )
    return parser.parse_args()


def clean_term(term: str) -> str:
    normalized = " ".join(term.strip().split())
    return normalized.replace('"', "")


def read_keywords_file(path: str) -> list[str]:
    source = Path(path)
    if not source.exists():
        raise ValueError(f"--keywords-file does not exist: {path}")
    if not source.is_file():
        raise ValueError(f"--keywords-file is not a file: {path}")

    raw_keywords: list[str] = []
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Unable to read --keywords-file {path}: {exc}") from exc

    for line in text.splitlines():
        payload = line.split("#", 1)[0].strip()
        if not payload:
            continue
        raw_keywords.extend(payload.split(","))

    deduped: list[str] = []
    seen: set[str] = set()
    for term in raw_keywords:
        cleaned = clean_term(term)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def resolve_keywords(inline_keywords: list[str] | None, keywords_file: str | None) -> list[str]:
    combined: list[str] = []
    if inline_keywords:
        combined.extend(inline_keywords)
    if keywords_file:
        combined.extend(read_keywords_file(keywords_file))

    deduped: list[str] = []
    seen: set[str] = set()
    for term in combined:
        cleaned = clean_term(term)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def build_search_query(
    keywords: list[str], match: str, excludes: list[str], categories: list[str]
) -> str:
    operator = " OR " if match == "any" else " AND "
    keyword_terms = [f'all:"{clean_term(k)}"' for k in keywords if clean_term(k)]
    if not keyword_terms:
        raise ValueError("At least one non-empty keyword is required.")
    keyword_clause = (
        keyword_terms[0]
        if len(keyword_terms) == 1
        else f"({operator.join(keyword_terms)})"
    )

    category_terms = [f"cat:{clean_term(c)}" for c in categories if clean_term(c)]
    if not category_terms:
        raise ValueError("At least one category must be provided.")
    category_clause = (
        category_terms[0]
        if len(category_terms) == 1
        else f"({' OR '.join(category_terms)})"
    )

    query_parts = [keyword_clause, category_clause]
    for term in excludes:
        cleaned = clean_term(term)
        if cleaned:
            query_parts.append(f'NOT all:"{cleaned}"')
    return " AND ".join(query_parts)


def build_url(args: argparse.Namespace) -> str:
    categories = args.category if args.category else DEFAULT_CATEGORIES
    search_query = build_search_query(
        args.keyword, args.match, args.exclude, categories
    )
    params = {
        "search_query": search_query,
        "start": args.start,
        "max_results": args.max_results,
        "sortBy": args.sort_by,
        "sortOrder": args.sort_order,
    }
    return f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"


def _http_get(url: str, timeout: int, insecure_tls: bool = False) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "codex-arxiv-astro-search/1.0"}
    )
    context = None
    if insecure_tls and url.startswith("https://"):
        context = ssl._create_unverified_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read()


def fetch_feed(url: str, timeout: int, insecure_tls: bool = False) -> bytes:
    try:
        return _http_get(url, timeout=timeout, insecure_tls=insecure_tls)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        is_ssl_error = isinstance(reason, ssl.SSLCertVerificationError)
        if is_ssl_error and url.startswith(ARXIV_API_URL):
            fallback_url = url.replace(ARXIV_API_URL, ARXIV_API_URL_HTTP, 1)
            try:
                return _http_get(
                    fallback_url, timeout=timeout, insecure_tls=insecure_tls
                )
            except urllib.error.URLError as fallback_exc:
                raise RuntimeError(
                    "Failed to fetch arXiv results over HTTPS and HTTP fallback. "
                    f"HTTPS URL: {url}. HTTP URL: {fallback_url}. Error: {fallback_exc}"
                ) from fallback_exc
        raise RuntimeError(
            f"Failed to fetch arXiv results. URL: {url}. Error: {exc}"
        ) from exc


def parse_datetime(raw_value: str) -> datetime:
    value = raw_value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def keyword_match_info(text: str, keywords: Iterable[str]) -> tuple[int, list[str]]:
    haystack = text.casefold()
    matched = []
    score = 0
    for raw in keywords:
        token = clean_term(raw).casefold()
        if not token:
            continue
        count = len(re.findall(re.escape(token), haystack))
        if count > 0:
            matched.append(raw)
            score += min(count, 3)
    return score, matched


def extract_pdf_url(entry: ET.Element, ns: dict[str, str]) -> str | None:
    for link in entry.findall("atom:link", ns):
        href = link.attrib.get("href", "")
        title = link.attrib.get("title", "")
        mime_type = link.attrib.get("type", "")
        if title.lower() == "pdf" or mime_type == "application/pdf":
            return href
    return None


def parse_feed(xml_bytes: bytes, keywords: list[str], days: int | None) -> list[dict]:
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"Unable to parse arXiv response XML: {exc}") from exc

    cutoff = None
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    parsed: list[dict] = []
    for entry in root.findall("atom:entry", ns):
        title = normalize_whitespace(entry.findtext("atom:title", default="", namespaces=ns))
        summary = normalize_whitespace(
            entry.findtext("atom:summary", default="", namespaces=ns)
        )
        published_raw = entry.findtext("atom:published", default="", namespaces=ns)
        updated_raw = entry.findtext("atom:updated", default="", namespaces=ns)
        paper_id = entry.findtext("atom:id", default="", namespaces=ns)

        if not published_raw:
            continue
        published_dt = parse_datetime(published_raw)
        if cutoff is not None and published_dt < cutoff:
            continue

        authors = [
            normalize_whitespace(name.text or "")
            for name in entry.findall("atom:author/atom:name", ns)
        ]
        categories = [node.attrib.get("term", "") for node in entry.findall("atom:category", ns)]
        primary_node = entry.find("arxiv:primary_category", ns)
        primary_category = primary_node.attrib.get("term", "") if primary_node is not None else ""
        pdf_url = extract_pdf_url(entry, ns)

        score, matched_keywords = keyword_match_info(f"{title} {summary}", keywords)
        parsed.append(
            {
                "id": paper_id,
                "title": title,
                "summary": summary,
                "published": published_dt.date().isoformat(),
                "updated": parse_datetime(updated_raw).date().isoformat() if updated_raw else "",
                "authors": authors,
                "categories": categories,
                "primary_category": primary_category,
                "pdf_url": pdf_url,
                "keyword_score": score,
                "matched_keywords": matched_keywords,
                "_published_dt": published_dt,
            }
        )

    return parsed


def render_markdown(entries: list[dict]) -> str:
    if not entries:
        return "No papers matched this query."

    lines = []
    for idx, entry in enumerate(entries, start=1):
        summary = entry["summary"]
        if len(summary) > 420:
            summary = summary[:417].rstrip() + "..."
        lines.append(f"{idx}. {entry['title']}")
        lines.append(f"   - arXiv: {entry['id']}")
        lines.append(f"   - Published: {entry['published']}")
        if entry["primary_category"]:
            lines.append(f"   - Primary category: {entry['primary_category']}")
        if entry["matched_keywords"]:
            lines.append(
                f"   - Matched keywords ({entry['keyword_score']}): "
                + ", ".join(entry["matched_keywords"])
            )
        if entry["pdf_url"]:
            lines.append(f"   - PDF: {entry['pdf_url']}")
        lines.append(f"   - Summary: {summary}")
        lines.append("")
    return "\n".join(lines).rstrip()


def prepare_output(entries: list[dict], output_format: str) -> str:
    if output_format == "json":
        cleaned = []
        for item in entries:
            row = dict(item)
            row.pop("_published_dt", None)
            cleaned.append(row)
        return json.dumps(cleaned, indent=2)
    return render_markdown(entries)


def main() -> int:
    args = parse_args()
    args.keyword = resolve_keywords(args.keyword, args.keywords_file)
    if not args.keyword:
        raise ValueError("Provide at least one keyword via --keyword or --keywords-file.")
    if args.start < 0:
        raise ValueError("--start must be non-negative.")
    if args.max_results < 1:
        raise ValueError("--max-results must be at least 1.")
    if args.days is not None and args.days < 0:
        raise ValueError("--days must be non-negative.")

    url = build_url(args)
    if args.dry_run:
        print(url)
        return 0

    feed = fetch_feed(url, timeout=args.timeout, insecure_tls=args.insecure_tls)
    entries = parse_feed(feed, keywords=args.keyword, days=args.days)

    if not args.no_keyword_rank:
        entries.sort(
            key=lambda row: (row["keyword_score"], row["_published_dt"]),
            reverse=True,
        )
    else:
        entries.sort(key=lambda row: row["_published_dt"], reverse=True)

    rendered = prepare_output(entries, args.output)
    print(rendered)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
