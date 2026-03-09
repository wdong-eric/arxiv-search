from __future__ import annotations

import io
import sys
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import daily_arxiv_zotero_alert as alert


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

    def test_semantic_weighted_ranking_is_deterministic(self) -> None:
        entries = [
            make_entry(
                "1111.1111",
                "Semantically close",
                "topic a",
                "2020-01-01T00:00:00",
                keyword_score=0,
            ),
            make_entry(
                "2222.2222",
                "Keyword heavy",
                "topic b",
                "2020-01-01T00:00:00",
                keyword_score=10,
            ),
        ]
        alert.apply_semantic_scores(
            entries,
            profile_vector=[1.0, 0.0],
            candidate_vectors={
                "1111.1111": [1.0, 0.0],
                "2222.2222": [0.0, 1.0],
            },
            semantic_weight=0.90,
            keyword_weight=0.10,
        )
        alert.sort_new_entries(entries, semantic_available=True)
        self.assertEqual(entries[0]["id"], "https://arxiv.org/abs/1111.1111")
        self.assertAlmostEqual(entries[0]["final_score"], 0.9, places=6)

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
                    )
                ],
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
            self.assertIn("- Ranking mode: semantic", digest)
            self.assertIn("- Final score:", digest)
            self.assertIn("- Semantic score:", digest)
            self.assertIn("Novel Candidate", digest)
            self.assertNotIn("Known Zotero Paper", digest)


if __name__ == "__main__":
    unittest.main()
