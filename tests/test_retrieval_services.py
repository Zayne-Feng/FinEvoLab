"""Verify shared-cache isolation and exact vector-backend contracts."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from finevolab.retrieval_cache import cached_passages, restore
from finevolab.retrieval_settings import RedisSettings, MilvusSettings, retrieval_observations
from finevolab.runtime import fingerprint
from finevolab.vector_backends import MilvusVectorBackend


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.chunks = [{"id": "one", "text": "Revenue increased.", "company": "MSFT", "report_year": 2024}]
        self.rows = [{**self.chunks[0], "score": 1.0}]
        self.entries = {}
        class Client:
            def get(inner, key): return self.entries.get(key)
            def set(inner, key, value, ex): self.entries[key] = value
        self.client = Client()

    def test_repeated_request_uses_shared_ids_and_preserves_evidence(self):
        calls, events = [], []
        def compute():
            calls.append(1)
            return self.rows
        with patch("finevolab.retrieval_cache.cache_client", return_value=self.client), retrieval_observations(events):
            self.assertEqual(cached_passages(RedisSettings(), "version-a", self.chunks, compute), self.rows)
            self.assertEqual(cached_passages(RedisSettings(), "version-a", self.chunks, compute), self.rows)
        self.assertEqual(len(calls), 1)
        self.assertEqual([x["status"] for x in events], ["miss", "hit"])
        self.assertNotIn("Revenue", next(iter(self.entries.values())))

    def test_version_changes_do_not_reuse_previous_rankings(self):
        calls = []
        def compute():
            calls.append(1)
            return self.rows
        with patch("finevolab.retrieval_cache.cache_client", return_value=self.client):
            for identity in ("old-corpus", "new-corpus"):
                cached_passages(RedisSettings(), identity, self.chunks, compute)
        self.assertEqual(len(calls), 2)

    def test_timeout_uses_local_retrieval_and_records_failure(self):
        events = []
        with patch("finevolab.retrieval_cache.cache_client", side_effect=TimeoutError), retrieval_observations(events):
            self.assertEqual(cached_passages(RedisSettings(), "v", self.chunks, lambda: self.rows), self.rows)
        self.assertEqual(events[0]["status"], "error")

    def test_corruption_is_recomputed_without_trusting_cached_text(self):
        self.entries[RedisSettings().prefix + ":v"] = "invalid"
        events = []
        with patch("finevolab.retrieval_cache.cache_client", return_value=self.client), retrieval_observations(events):
            self.assertEqual(cached_passages(RedisSettings(), "v", self.chunks, lambda: self.rows), self.rows)
        self.assertEqual(events[0]["status"], "error")

    def test_cross_scope_unknown_duplicate_and_nonfinite_rows_are_rejected(self):
        for rows in ([{"id": "other", "score": 1}], [{"id": "one", "score": 1}]*2,
                     [{"id": "one", "score": float("nan")}], [{"id": "one", "score": 1, "text": "forged"}]):
            raw = json.dumps({"identity": "v", "rows": rows, "checksum": fingerprint({"rows": rows})})
            with self.assertRaises((ValueError, KeyError)):
                restore(raw, "v", self.chunks)

    def test_nonpositive_ttl_is_rejected(self):
        with self.assertRaises(ValueError): RedisSettings(ttl_seconds=0)


class VectorContractTests(unittest.TestCase):
    def setUp(self):
        self.row = {"id": "one", "company": "MSFT", "report_year": 2024,
                    "report_type": "10-K", "source_sha256": "source"}
        self.backend = MilvusVectorBackend(MilvusSettings(), "version")

    def test_filter_and_full_scope_are_sent_to_service(self):
        class Client:
            def search(inner, **kwargs):
                self.assertEqual(kwargs["limit"], 1)
                self.assertIn('report_year in [2024]', kwargs["filter"])
                self.assertEqual(kwargs["consistency_level"], "Strong")
                return [[{"id": "one", "distance": 0.5,
                          "entity": {**self.row, "corpus_version": "version"}}]]
        with patch("finevolab.vector_backends.milvus_client", return_value=Client()):
            self.assertEqual(self.backend.rank([1, 0], [self.row]), [(self.row, 0.5)])

    def test_scope_loss_wrong_version_and_wrong_source_are_rejected(self):
        for results in ([], [{"id": "other", "distance": .5, "entity": {}}],
                        [{"id": "one", "distance": .5, "entity": {**self.row, "corpus_version": "wrong"}}],
                        [{"id": "one", "distance": .5, "entity": {**self.row, "source_sha256": "wrong", "corpus_version": "version"}}]):
            class Client:
                def search(inner, **kwargs): return [results]
            with patch("finevolab.vector_backends.milvus_client", return_value=Client()), self.assertRaises(ValueError):
                self.backend.rank([1, 0], [self.row])

    def test_empty_scope_does_not_contact_service(self):
        with patch("finevolab.vector_backends.milvus_client") as client:
            self.assertEqual(self.backend.rank([1], []), [])
            client.assert_not_called()


class IndexCacheIdentityTests(unittest.TestCase):
    def test_redis_configuration_does_not_duplicate_vector_indexes(self):
        import os
        from finevolab.retrieval import load_index
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data/indexes").mkdir(parents=True)
            (root / "configs").mkdir()
            for name in ("structured.json", "structured.npy", "structured.metadata.json"):
                (root / "data/indexes" / name).write_text("{}")
            (root / "configs/retrieval.json").write_text("{}")
            settings_path = root / "configs/retrieval_services.json"
            settings_path.write_text('{}')
            with patch.dict(os.environ, {"FINEVOLAB_RETRIEVAL_SERVICES_CONFIG": str(settings_path)}), patch(
                    "finevolab.retrieval._cached_index", return_value=object()) as cached:
                load_index(root, mode="hybrid")
                first = cached.call_args.args
                settings_path.write_text(json.dumps({"redis": {"enabled": True, "prefix": "other"}}))
                load_index(root, mode="hybrid")
                self.assertEqual(first, cached.call_args.args)
                settings_path.write_text(json.dumps({"vector_backend": "milvus"}))
                load_index(root, mode="hybrid")
                self.assertNotEqual(first, cached.call_args.args)


class MilvusRecoveryTests(unittest.TestCase):
    def test_released_collection_is_loaded_once_without_reimporting_vectors(self):
        row = {"id": "one", "company": "MSFT", "report_year": 2024, "report_type": "10-K", "source_sha256": "s"}
        class Released(Exception):code = 101
        class Client:
            calls = 0
            loads = 0
            def search(inner, **kwargs):
                inner.calls += 1
                if inner.calls == 1:raise Released()
                return [[{"id":"one","distance":.5,"entity":{**row,"corpus_version":"v"}}]]
            def load_collection(inner, **kwargs):inner.loads += 1
        client = Client()
        with patch("finevolab.vector_backends.milvus_client", return_value=client):
            self.assertEqual(MilvusVectorBackend(MilvusSettings(),"v").rank([1],[row]), [(row,.5)])
        self.assertEqual((client.calls,client.loads),(2,1))


class ClientTransportTests(unittest.TestCase):
    def test_client_does_not_send_idle_keepalives(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        from finevolab.vector_backends import milvus_client
        milvus_client.cache_clear()
        try:
            constructor = Mock()
            with patch.dict(sys.modules, {"pymilvus": SimpleNamespace(MilvusClient=constructor)}):
                milvus_client("http://127.0.0.1:19530")
                options = constructor.call_args.kwargs["grpc_options"]
                self.assertFalse(options["grpc.keepalive_permit_without_calls"])
                self.assertGreaterEqual(options["grpc.keepalive_time_ms"], 60000)
        finally:
            milvus_client.cache_clear()
