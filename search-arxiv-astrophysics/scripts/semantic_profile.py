#!/usr/bin/env python3
"""
Semantic profile + embedding cache utilities for daily arXiv digest ranking.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

OPENAI_EMBEDDING_URL = "https://api.openai.com/v1/embeddings"
DEFAULT_PREPROCESS_VERSION = "title-abstract-v1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_EMBEDDING_BATCH_SIZE = 64
DEFAULT_SEMANTIC_WEIGHT = 0.90
DEFAULT_KEYWORD_WEIGHT = 0.10


@dataclass(frozen=True)
class SemanticRecord:
    record_id: str
    title: str
    abstract: str


@dataclass
class SyncStats:
    embedded_new: int = 0
    reused_cached: int = 0


def normalize_text(value: str) -> str:
    return " ".join((value or "").split())


def build_embedding_text(title: str, abstract: str) -> str:
    title_norm = normalize_text(title)
    abstract_norm = normalize_text(abstract)
    if title_norm and abstract_norm:
        return f"Title: {title_norm}\nAbstract: {abstract_norm}"
    if title_norm:
        return f"Title: {title_norm}"
    if abstract_norm:
        return f"Abstract: {abstract_norm}"
    return ""


def build_content_hash(
    title: str,
    abstract: str,
    preprocess_version: str = DEFAULT_PREPROCESS_VERSION,
) -> str:
    payload = (
        f"{preprocess_version}\n"
        f"{normalize_text(title)}\n\n"
        f"{normalize_text(abstract)}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def l2_normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(v * v for v in vector))
    if magnitude == 0:
        return [0.0 for _ in vector]
    return [v / magnitude for v in vector]


def mean_vector(vectors: list[list[float]]) -> list[float] | None:
    if not vectors:
        return None
    width = len(vectors[0])
    if width == 0:
        return None
    total = [0.0] * width
    for vector in vectors:
        if len(vector) != width:
            raise ValueError("Embedding vectors have inconsistent dimensions.")
        for idx, value in enumerate(vector):
            total[idx] += float(value)
    averaged = [value / len(vectors) for value in total]
    return l2_normalize(averaged)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Cannot compute cosine similarity for different dimensions.")
    return float(sum(a * b for a, b in zip(left, right)))


class EmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: int = 20,
        insecure_tls: bool = False,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.insecure_tls = insecure_tls
        self.batch_size = max(1, batch_size)

    def _post_json(self, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            OPENAI_EMBEDDING_URL,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        context = ssl._create_unverified_context() if self.insecure_tls else None
        with urllib.request.urlopen(
            request, timeout=self.timeout, context=context
        ) as response:
            body = response.read().decode("utf-8")
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise RuntimeError("Unexpected embedding response shape.")
        return parsed

    def _embed_batch(self, texts: list[str], max_retries: int = 5) -> list[list[float]]:
        delay_seconds = 1.0
        for attempt in range(max_retries):
            try:
                payload = {"model": self.model, "input": texts}
                parsed = self._post_json(payload)
                raw_rows = parsed.get("data")
                if not isinstance(raw_rows, list):
                    raise RuntimeError("Embedding response missing data list.")
                sorted_rows = sorted(
                    raw_rows, key=lambda row: int(row.get("index", 0))  # type: ignore[arg-type]
                )
                vectors: list[list[float]] = []
                for row in sorted_rows:
                    embedding = row.get("embedding") if isinstance(row, dict) else None
                    if not isinstance(embedding, list):
                        raise RuntimeError("Embedding row missing embedding vector.")
                    vector = [float(value) for value in embedding]
                    vectors.append(l2_normalize(vector))
                if len(vectors) != len(texts):
                    raise RuntimeError(
                        f"Embedding response row count mismatch: got {len(vectors)}, expected {len(texts)}."
                    )
                return vectors
            except urllib.error.HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504} and attempt < max_retries - 1:
                    time.sleep(delay_seconds)
                    delay_seconds *= 2
                    continue
                raise RuntimeError(
                    f"Embedding request failed with HTTP {exc.code}."
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < max_retries - 1:
                    time.sleep(delay_seconds)
                    delay_seconds *= 2
                    continue
                raise RuntimeError(f"Embedding request failed: {exc}") from exc

        raise RuntimeError("Embedding request retry loop exhausted.")

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for idx in range(0, len(texts), self.batch_size):
            batch = texts[idx : idx + self.batch_size]
            vectors.extend(self._embed_batch(batch))
        return vectors


class SemanticCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def close(self) -> None:
        self._conn.close()

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS zotero_embeddings (
                item_key TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS arxiv_embeddings (
                paper_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        return str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    def ensure_compatible(
        self,
        *,
        model: str,
        preprocess_version: str,
        rebuild: bool = False,
    ) -> bool:
        stored_model = self.get_meta("embedding_model")
        stored_preprocess = self.get_meta("preprocess_version")
        needs_rebuild = rebuild or (
            stored_model is not None
            and stored_preprocess is not None
            and (stored_model != model or stored_preprocess != preprocess_version)
        )
        if needs_rebuild:
            self.clear_embeddings()
        self.set_meta("embedding_model", model)
        self.set_meta("preprocess_version", preprocess_version)
        self.set_meta("last_sync_at", datetime.now(timezone.utc).isoformat())
        return needs_rebuild

    def clear_embeddings(self) -> None:
        self._conn.execute("DELETE FROM zotero_embeddings")
        self._conn.execute("DELETE FROM arxiv_embeddings")
        self._conn.commit()

    def _select_row(
        self,
        table: str,
        id_column: str,
        record_id: str,
    ) -> tuple[str, str, list[float]] | None:
        row = self._conn.execute(
            f"SELECT content_hash, model, vector_json FROM {table} WHERE {id_column} = ?",
            (record_id,),
        ).fetchone()
        if not row:
            return None
        try:
            parsed = json.loads(str(row[2]))
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, list):
            return None
        vector = [float(value) for value in parsed]
        return str(row[0]), str(row[1]), vector

    def _upsert_row(
        self,
        table: str,
        id_column: str,
        record_id: str,
        content_hash: str,
        model: str,
        vector: list[float],
    ) -> None:
        vector_json = json.dumps(vector)
        self._conn.execute(
            f"INSERT INTO {table}({id_column}, content_hash, model, vector_json, updated_at) "
            "VALUES(?, ?, ?, ?, ?) "
            f"ON CONFLICT({id_column}) DO UPDATE SET "
            "content_hash=excluded.content_hash, "
            "model=excluded.model, "
            "vector_json=excluded.vector_json, "
            "updated_at=excluded.updated_at",
            (
                record_id,
                content_hash,
                model,
                vector_json,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def _prune_missing(self, table: str, id_column: str, keep_ids: set[str]) -> None:
        if not keep_ids:
            self._conn.execute(f"DELETE FROM {table}")
            self._conn.commit()
            return
        placeholders = ",".join("?" for _ in keep_ids)
        self._conn.execute(
            f"DELETE FROM {table} WHERE {id_column} NOT IN ({placeholders})",
            tuple(sorted(keep_ids)),
        )
        self._conn.commit()

    def sync_records(
        self,
        *,
        table: str,
        id_column: str,
        records: list[SemanticRecord],
        model: str,
        embed_texts: Callable[[list[str]], list[list[float]]],
        preprocess_version: str = DEFAULT_PREPROCESS_VERSION,
        prune_missing: bool = False,
    ) -> tuple[dict[str, list[float]], SyncStats]:
        vectors: dict[str, list[float]] = {}
        stats = SyncStats()
        to_embed_ids: list[str] = []
        to_embed_texts: list[str] = []
        to_embed_hashes: dict[str, str] = {}
        keep_ids = {record.record_id for record in records}

        for record in records:
            content_hash = build_content_hash(
                record.title,
                record.abstract,
                preprocess_version=preprocess_version,
            )
            cached = self._select_row(
                table=table, id_column=id_column, record_id=record.record_id
            )
            if cached and cached[0] == content_hash and cached[1] == model:
                vectors[record.record_id] = cached[2]
                stats.reused_cached += 1
                continue

            text = build_embedding_text(record.title, record.abstract)
            if not text:
                continue
            to_embed_ids.append(record.record_id)
            to_embed_texts.append(text)
            to_embed_hashes[record.record_id] = content_hash

        if to_embed_texts:
            embedded = embed_texts(to_embed_texts)
            if len(embedded) != len(to_embed_ids):
                raise RuntimeError(
                    f"Embedded vector count mismatch: got {len(embedded)} for {len(to_embed_ids)} records."
                )
            for record_id, vector in zip(to_embed_ids, embedded):
                vectors[record_id] = vector
                self._upsert_row(
                    table=table,
                    id_column=id_column,
                    record_id=record_id,
                    content_hash=to_embed_hashes[record_id],
                    model=model,
                    vector=vector,
                )
                stats.embedded_new += 1
            self._conn.commit()

        if prune_missing:
            self._prune_missing(table=table, id_column=id_column, keep_ids=keep_ids)

        return vectors, stats

    def get_all_vectors(self, *, table: str, model: str) -> dict[str, list[float]]:
        id_column = "item_key" if table == "zotero_embeddings" else "paper_id"
        rows = self._conn.execute(
            f"SELECT {id_column}, model, vector_json FROM {table}"
        ).fetchall()
        vectors: dict[str, list[float]] = {}
        for row in rows:
            if str(row[1]) != model:
                continue
            try:
                vector_raw = json.loads(str(row[2]))
            except json.JSONDecodeError:
                continue
            if not isinstance(vector_raw, list):
                continue
            vectors[str(row[0])] = [float(value) for value in vector_raw]
        return vectors

