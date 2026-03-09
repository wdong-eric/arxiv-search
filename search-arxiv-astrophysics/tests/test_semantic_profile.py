from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from semantic_profile import SemanticCache, SemanticRecord


class SemanticProfileCacheTests(unittest.TestCase):
    def test_incremental_sync_only_embeds_changed_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_path = Path(tmp_dir) / "semantic_cache.sqlite"
            cache = SemanticCache(cache_path)
            self.addCleanup(cache.close)

            embed_calls: list[list[str]] = []

            def fake_embed(texts: list[str]) -> list[list[float]]:
                embed_calls.append(list(texts))
                return [[1.0, 0.0] for _ in texts]

            records_v1 = [
                SemanticRecord(
                    record_id="item-1",
                    title="Title A",
                    abstract="Abstract A",
                )
            ]
            vectors_first, stats_first = cache.sync_records(
                table="zotero_embeddings",
                id_column="item_key",
                records=records_v1,
                model="text-embedding-3-small",
                embed_texts=fake_embed,
                prune_missing=True,
            )
            self.assertEqual(stats_first.embedded_new, 1)
            self.assertEqual(stats_first.reused_cached, 0)
            self.assertIn("item-1", vectors_first)
            self.assertEqual(len(embed_calls), 1)

            vectors_second, stats_second = cache.sync_records(
                table="zotero_embeddings",
                id_column="item_key",
                records=records_v1,
                model="text-embedding-3-small",
                embed_texts=fake_embed,
                prune_missing=True,
            )
            self.assertEqual(stats_second.embedded_new, 0)
            self.assertEqual(stats_second.reused_cached, 1)
            self.assertIn("item-1", vectors_second)
            self.assertEqual(len(embed_calls), 1)

            records_v2 = [
                SemanticRecord(
                    record_id="item-1",
                    title="Title A",
                    abstract="Abstract changed",
                )
            ]
            vectors_third, stats_third = cache.sync_records(
                table="zotero_embeddings",
                id_column="item_key",
                records=records_v2,
                model="text-embedding-3-small",
                embed_texts=fake_embed,
                prune_missing=True,
            )
            self.assertEqual(stats_third.embedded_new, 1)
            self.assertEqual(stats_third.reused_cached, 0)
            self.assertIn("item-1", vectors_third)
            self.assertEqual(len(embed_calls), 2)


if __name__ == "__main__":
    unittest.main()
