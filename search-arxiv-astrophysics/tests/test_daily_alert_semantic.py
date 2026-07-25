from __future__ import annotations

import io
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import daily_arxiv_zotero_alert as alert
import search_arxiv_astro as arxiv_search


def make_entry(
    arxiv_id: str,
    title: str,
    summary: str,
    published_iso: str,
    *,
    keyword_score: int,
    primary_category: str = "astro-ph.CO",
) -> dict:
    published_dt = datetime.fromisoformat(published_iso).replace(tzinfo=timezone.utc)
    return {
        "id": f"https://arxiv.org/abs/{arxiv_id}",
        "title": title,
        "summary": summary,
        "published": published_dt.date().isoformat(),
        "updated": published_dt.date().isoformat(),
        "authors": ["A. Author"],
        "categories": [primary_category],
        "primary_category": primary_category,
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "keyword_score": keyword_score,
        "matched_keywords": ["dark matter"] if keyword_score > 0 else [],
        "_published_dt": published_dt,
        "_ranking_dt": published_dt,
    }


class DailyAlertSemanticTests(unittest.TestCase):
    def test_category_query_has_no_keyword_terms(self) -> None:
        url = alert.build_arxiv_url(
            categories=["astro-ph.CO", "gr-qc"],
            max_results=50,
            sort_by="submittedDate",
            sort_order="descending",
        )
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["search_query"][0]
        self.assertIn("cat:astro-ph.CO", query)
        self.assertIn("cat:gr-qc", query)
        self.assertNotIn("all:", query)

    def test_fetch_zotero_index_includes_collection_paths(self) -> None:
        collection_payload = [
            {
                "key": "parent",
                "data": {
                    "name": "Cosmology",
                    "parentCollection": False,
                },
            },
            {
                "key": "child",
                "data": {
                    "name": "CMB",
                    "parentCollection": "parent",
                },
            },
        ]
        item_payload = [
            {
                "key": "item-1",
                "data": {
                    "title": "Acoustic peaks",
                    "abstractNote": "CMB analysis",
                    "collections": ["child"],
                },
            }
        ]

        with mock.patch.object(
            alert,
            "safe_json_get",
            side_effect=[collection_payload, item_payload],
        ):
            index = alert.fetch_zotero_index(
                user_id="1",
                api_key="dummy",
                max_items=10,
                timeout=5,
            )

        self.assertEqual(index.collection_names["child"], "Cosmology / CMB")
        self.assertEqual(index.embedding_records[0].collection_keys, ["child"])

    def test_safe_json_get_retries_timeout_urlerror(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"[]"

        with mock.patch.object(
            alert.urllib.request,
            "urlopen",
            side_effect=[urllib.error.URLError(socket.timeout("timed out")), response],
        ) as mock_urlopen, mock.patch.object(alert.time, "sleep", return_value=None):
            payload = alert.safe_json_get(
                "https://example.com",
                headers=None,
                timeout=5,
            )

        self.assertEqual(payload, [])
        self.assertEqual(mock_urlopen.call_count, 2)

    def test_fetch_feed_retries_timeout_urlerror(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"<feed />"

        with mock.patch.object(
            arxiv_search.urllib.request,
            "urlopen",
            side_effect=[urllib.error.URLError(socket.timeout("timed out")), response],
        ) as mock_urlopen, mock.patch.object(arxiv_search.time, "sleep", return_value=None):
            payload = arxiv_search.fetch_feed(
                "https://example.com",
                timeout=5,
            )

        self.assertEqual(payload, b"<feed />")
        self.assertEqual(mock_urlopen.call_count, 2)

    def test_select_classic_entries_samples_from_top_pool(self) -> None:
        classics = []
        for idx in range(7):
            entry = make_entry(
                f"9999.000{idx}",
                f"Classic {idx}",
                "classic abstract",
                "2010-01-01T00:00:00",
                keyword_score=7 - idx,
            )
            entry["citation_count"] = 100 - idx
            entry["age_years"] = 10 + idx
            classics.append(entry)

        alert.sort_classic_entries(classics, semantic_available=False)
        sampled_pool = [classics[4], classics[1], classics[5], classics[0], classics[2]]

        with mock.patch.object(alert.random, "sample", return_value=sampled_pool) as mock_sample:
            selected = alert.select_classic_entries(
                classics,
                classic_top_n=5,
                classic_pool_size=6,
                semantic_available=False,
            )

        self.assertEqual(mock_sample.call_count, 1)
        self.assertEqual(mock_sample.call_args.args[0], classics[:6])
        self.assertEqual(len(selected), 5)
        self.assertEqual(
            {entry["id"] for entry in selected},
            {entry["id"] for entry in sampled_pool},
        )

    def test_select_new_entries_by_semantic_threshold_without_top_n_cap(self) -> None:
        entries = [
            {"id": "above-1", "semantic_score": 0.70, "final_score": 0.40},
            {"id": "above-2", "semantic_score": 0.66, "final_score": 0.30},
            {"id": "equal", "semantic_score": 0.65, "final_score": 0.90},
        ]

        selected = alert.select_new_entries(
            entries,
            new_top_n=1,
            semantic_threshold=0.65,
            overall_threshold=None,
            semantic_available=True,
        )

        self.assertEqual([entry["id"] for entry in selected], ["above-1", "above-2"])

    def test_select_new_entries_combines_thresholds_with_and(self) -> None:
        entries = [
            {"id": "both-1", "semantic_score": 0.70, "final_score": 0.70},
            {"id": "overall-equal", "semantic_score": 0.75, "final_score": 0.65},
            {"id": "semantic-low", "semantic_score": 0.64, "final_score": 0.80},
            {"id": "both-2", "semantic_score": 0.80, "final_score": 0.90},
        ]

        selected = alert.select_new_entries(
            entries,
            new_top_n=1,
            semantic_threshold=0.65,
            overall_threshold=0.65,
            semantic_available=True,
        )

        self.assertEqual([entry["id"] for entry in selected], ["both-1", "both-2"])

    def test_select_new_entries_keeps_top_n_as_default_mode(self) -> None:
        entries = [{"id": "first"}, {"id": "second"}, {"id": "third"}]

        selected = alert.select_new_entries(
            entries,
            new_top_n=2,
            semantic_threshold=None,
            overall_threshold=None,
            semantic_available=False,
        )

        self.assertEqual([entry["id"] for entry in selected], ["first", "second"])

    def test_select_new_entries_rejects_thresholds_without_semantic_scores(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "require semantic scoring"):
            alert.select_new_entries(
                [{"id": "candidate", "semantic_score": None, "final_score": 1.0}],
                new_top_n=12,
                semantic_threshold=None,
                overall_threshold=0.65,
                semantic_available=False,
            )

    def test_score_threshold_validation_rejects_non_finite_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "between -1 and 1"):
            alert.parse_optional_score_threshold(
                "nan",
                setting_name="NEW_SEMANTIC_THRESHOLD",
            )

    def test_collection_centroid_scoring_handles_multitopic_library(self) -> None:
        entries = [
            make_entry(
                "1111.1111",
                "Strong match to one collection",
                "topic a",
                "2020-01-01T00:00:00",
                keyword_score=0,
            ),
            make_entry(
                "2222.2222",
                "Closer to the global average",
                "topic b",
                "2020-01-01T00:00:00",
                keyword_score=10,
            ),
        ]
        alert.apply_semantic_scores(
            entries,
            collection_centroids={
                "col-a": [1.0, 0.0],
                "col-b": [0.0, 1.0],
            },
            collection_names={
                "col-a": "Cosmology",
                "col-b": "Gravitational Waves",
            },
            candidate_vectors={
                "1111.1111": [1.0, 0.0],
                "2222.2222": [0.70710678, 0.70710678],
            },
            semantic_weight=0.90,
            keyword_weight=0.10,
        )
        alert.sort_new_entries(entries, semantic_available=True)
        self.assertEqual(entries[0]["id"], "https://arxiv.org/abs/1111.1111")
        self.assertAlmostEqual(entries[0]["semantic_score"], 1.0, places=6)
        self.assertEqual(entries[0]["semantic_collection"], "Cosmology")
        self.assertLess(entries[1]["semantic_score"], entries[0]["semantic_score"])

    def test_keyword_fallback_warns_and_writes_digest_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            env_file = tmp / ".env"
            report_file = tmp / "report.md"
            state_file = tmp / "state.json"
            keywords_file = tmp / "keywords.txt"
            keywords_file.write_text("dark matter\n", encoding="utf-8")
            env_file.write_text(
                "\n".join(
                    [
                        "ZOTERO_USER_ID=1",
                        "ZOTERO_API_KEY=dummy",
                        f"ARXIV_KEYWORDS_FILE={keywords_file}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            index = alert.ZoteroIndex(
                arxiv_ids=set(),
                dois=set(),
                titles=set(),
                embedding_records=[],
                collection_names={},
            )
            new_entries = [
                make_entry(
                    "3333.3333",
                    "Fallback paper",
                    "Some abstract",
                    "2025-01-01T00:00:00",
                    keyword_score=2,
                )
            ]
            classic_entries = [
                make_entry(
                    "4444.4444",
                    "Old fallback paper",
                    "Some old abstract",
                    "2010-01-01T00:00:00",
                    keyword_score=1,
                )
            ]

            stdout_buffer = io.StringIO()
            with mock.patch.object(
                sys,
                "argv",
                [
                    "daily_arxiv_zotero_alert.py",
                    "--env-file",
                    str(env_file),
                    "--preview-only",
                    "--state-file",
                    str(state_file),
                    "--report-file",
                    str(report_file),
                    "--semantic",
                    "auto",
                ],
            ), mock.patch.object(alert, "fetch_zotero_index", return_value=index), mock.patch.object(
                alert,
                "fetch_arxiv_entries",
                side_effect=[new_entries, classic_entries],
            ), mock.patch.object(alert, "fetch_citation_count", return_value=10), redirect_stdout(
                stdout_buffer
            ):
                return_code = alert.main()

            self.assertEqual(return_code, 0)
            output = stdout_buffer.getvalue()
            self.assertIn(
                "Warning: Ranking mode: keyword fallback; semantic unavailable",
                output,
            )
            digest = report_file.read_text(encoding="utf-8")
            self.assertIn(
                "Ranking mode: keyword fallback; semantic unavailable",
                digest,
            )

    def test_integration_semantic_mode_with_mocked_apis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            env_file = tmp / ".env"
            report_file = tmp / "report.md"
            state_file = tmp / "state.json"
            keywords_file = tmp / "keywords.txt"
            semantic_cache = tmp / "semantic_cache.sqlite"
            keywords_file.write_text("dark matter\n", encoding="utf-8")
            env_file.write_text(
                "\n".join(
                    [
                        "ZOTERO_USER_ID=1",
                        "ZOTERO_API_KEY=dummy",
                        "OPENAI_API_KEY=sk-test",
                        f"ARXIV_KEYWORDS_FILE={keywords_file}",
                        "SEMANTIC_MODE=on",
                        "SEMANTIC_WEIGHT=0.90",
                        "KEYWORD_WEIGHT=0.10",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            # One known title should be excluded from candidates.
            known_title_norm = alert.normalize_title("Known Zotero Paper")
            index = alert.ZoteroIndex(
                arxiv_ids=set(),
                dois=set(),
                titles={known_title_norm},
                embedding_records=[
                    alert.ZoteroEmbeddingRecord(
                        item_key="item-1",
                        title="Dark matter constraints",
                        abstract="Survey of constraints.",
                        collection_keys=["cosmo"],
                    )
                ],
                collection_names={"cosmo": "Cosmology"},
            )

            new_entries = [
                make_entry(
                    "5555.5555",
                    "Known Zotero Paper",
                    "Should be filtered",
                    "2025-01-01T00:00:00",
                    keyword_score=9,
                ),
                make_entry(
                    "6666.6666",
                    "Novel Candidate",
                    "Interesting abstract",
                    "2025-01-02T00:00:00",
                    keyword_score=4,
                ),
            ]
            classic_entries = [
                make_entry(
                    "7777.7777",
                    "Classic Candidate",
                    "Old but relevant",
                    "2010-01-01T00:00:00",
                    keyword_score=2,
                )
            ]

            def fake_embed_texts(texts: list[str]) -> list[list[float]]:
                vectors: list[list[float]] = []
                for text in texts:
                    if "Dark matter constraints" in text:
                        vectors.append([1.0, 0.0])
                    elif "Novel Candidate" in text:
                        vectors.append([1.0, 0.0])
                    else:
                        vectors.append([0.0, 1.0])
                return vectors

            stdout_buffer = io.StringIO()
            with mock.patch.object(
                sys,
                "argv",
                [
                    "daily_arxiv_zotero_alert.py",
                    "--env-file",
                    str(env_file),
                    "--preview-only",
                    "--state-file",
                    str(state_file),
                    "--report-file",
                    str(report_file),
                    "--semantic-cache-path",
                    str(semantic_cache),
                    "--semantic",
                    "on",
                    "--new-semantic-threshold",
                    "0.65",
                    "--new-overall-threshold",
                    "0.65",
                ],
            ), mock.patch.object(alert, "fetch_zotero_index", return_value=index), mock.patch.object(
                alert,
                "fetch_arxiv_entries",
                side_effect=[new_entries, classic_entries],
            ), mock.patch.object(alert, "fetch_citation_count", return_value=20), mock.patch.object(
                alert.EmbeddingClient,
                "embed_texts",
                side_effect=fake_embed_texts,
            ), redirect_stdout(stdout_buffer):
                return_code = alert.main()

            self.assertEqual(return_code, 0)
            digest = report_file.read_text(encoding="utf-8")
            self.assertIn(
                "- New-paper selection: semantic score > 0.65 and overall score > 0.65",
                digest,
            )
            self.assertIn("- Ranking mode: semantic", digest)
            self.assertIn("- Final score:", digest)
            self.assertIn("- Semantic score:", digest)
            self.assertIn("- Best collection match: Cosmology", digest)
            self.assertIn("Novel Candidate", digest)
            self.assertNotIn("Known Zotero Paper", digest)


if __name__ == "__main__":
    unittest.main()
