"""Rank and import source-bound vectors through Milvus."""

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

import numpy as np

from finevolab.retrieval_settings import service_event
from finevolab.runtime import fingerprint


@lru_cache(maxsize=8)
def _identity(path: str, signature: tuple) -> str:
    index = Path(path)
    metadata = json.loads(index.with_suffix(".metadata.json").read_text(encoding="utf-8"))
    return fingerprint({"chunks": metadata["chunks_sha256"], "config": metadata["config"],
        "vectors": hashlib.sha256(index.with_suffix(".npy").read_bytes()).hexdigest()})


def corpus_identity(index: Path) -> str:
    signature = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in
                      (index, index.with_suffix(".metadata.json"), index.with_suffix(".npy")))
    return _identity(str(index.resolve()), signature)


def collection_name(identity: str) -> str:
    return "finevolab_" + identity[:32]


@lru_cache(maxsize=4)
def milvus_client(uri: str):
    from pymilvus import MilvusClient
    return MilvusClient(uri=uri, token=os.environ.get("FINEVOLAB_MILVUS_TOKEN", ""), timeout=10,
        grpc_options={"grpc.keepalive_time_ms": 60000, "grpc.keepalive_permit_without_calls": False})


class MilvusVectorBackend:
    def __init__(self, settings, identity: str):
        self.settings, self.identity = settings, identity
        self.collection = collection_name(identity)

    def rank(self, query_vector, filtered: list[dict]):
        if not filtered:
            return []
        if len(filtered) > 16384:
            raise ValueError("Exact Milvus ranking scope exceeds the supported search limit")
        companies = sorted({item["company"] for item in filtered})
        years = sorted({item["report_year"] for item in filtered})
        types = sorted({item["report_type"] for item in filtered})
        expression = (f"company in {json.dumps(companies)} and report_year in {json.dumps(years)} "
                      f"and report_type in {json.dumps(types)} and corpus_version == {json.dumps(self.identity)}")
        client = milvus_client(self.settings.uri)
        parameters = {"collection_name": self.collection, "data": [np.asarray(query_vector).tolist()],
            "anns_field": "embedding", "filter": expression, "limit": len(filtered),
            "search_params": {"metric_type": "IP", "params": {}}, "consistency_level": "Strong",
            "output_fields": ["company", "report_year", "report_type", "corpus_version", "source_sha256"],
            "timeout": self.settings.timeout_seconds}
        try:
            results = client.search(**parameters)[0]
        except Exception as error:
            if getattr(error, "code", None) != 101:
                raise
            client.load_collection(collection_name=self.collection, timeout=self.settings.timeout_seconds)
            results = client.search(**parameters)[0]
        lookup = {item["id"]: item for item in filtered}
        if len(results) != len(lookup) or {str(row["id"]) for row in results} != set(lookup):
            raise ValueError("Milvus ranking does not cover the exact source scope")
        ranked = []
        for row in results:
            item = lookup[str(row["id"])]
            if row["entity"].get("corpus_version") != self.identity or any(
                    row["entity"].get(key) != item[key] for key in ("company", "report_year", "report_type", "source_sha256")):
                raise ValueError("Milvus source metadata differs from the local corpus")
            score = float(row["distance"])
            if not np.isfinite(score):
                raise ValueError("Milvus score must be finite")
            ranked.append((item, score))
        service_event("vector_backend", configured_backend="milvus", actual_backend="milvus", status="complete")
        return sorted(ranked, key=lambda row: (-row[1], row[0]["id"]))


def import_vectors(index: Path, settings) -> dict:
    from pymilvus import DataType
    from finevolab.embeddings import load_vectors
    chunks = json.loads(index.read_text(encoding="utf-8"))
    metadata = json.loads(index.with_suffix(".metadata.json").read_text(encoding="utf-8"))
    vectors = load_vectors(index, chunks, metadata["config"])
    identity = corpus_identity(index)
    name = collection_name(identity)
    client = milvus_client(settings.uri)
    if not client.has_collection(name):
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=vectors.shape[1])
        schema.add_field("company", DataType.VARCHAR, max_length=24)
        schema.add_field("report_year", DataType.INT64)
        schema.add_field("report_type", DataType.VARCHAR, max_length=32)
        schema.add_field("corpus_version", DataType.VARCHAR, max_length=64)
        schema.add_field("source_sha256", DataType.VARCHAR, max_length=64)
        indexes = client.prepare_index_params()
        indexes.add_index(field_name="embedding", index_type="FLAT", metric_type="IP")
        client.create_collection(name, schema=schema, index_params=indexes, consistency_level="Strong")
    for start in range(0, len(chunks), 256):
        rows = [{"id": item["id"], "embedding": vectors[position].tolist(),
                 "corpus_version": identity, **{key: item[key] for key in
                     ("company", "report_year", "report_type", "source_sha256")}}
                for position, item in enumerate(chunks[start:start+256], start)]
        client.upsert(collection_name=name, data=rows, timeout=120)
    client.flush(collection_name=name, timeout=120)
    count = client.query(collection_name=name, filter="", output_fields=["count(*)"],
                         consistency_level="Strong", timeout=120)[0]["count(*)"]
    if count != len(chunks):
        raise ValueError("Imported Milvus entity count differs from the source corpus")
    return {"collection": name, "corpus_version": identity, "chunks": len(chunks),
            "dimensions": int(vectors.shape[1]), "index_type": "FLAT", "metric_type": "IP"}
