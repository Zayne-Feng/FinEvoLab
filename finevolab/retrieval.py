"""Retrieve versioned report passages with BM25 and rank fusion."""

from collections import OrderedDict, defaultdict
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from threading import RLock

from bs4 import BeautifulSoup
import numpy as np
from rank_bm25 import BM25Okapi


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())


def chunk_report(source: dict, root: Path, strategy: str = "structured", size: int = 1200,
                 contextual: bool = False) -> list[dict]:
    if contextual:
        from finevolab.table_context import context_prefix, table_context, unit_definitions
        chunks = chunk_report(source, root, strategy, size)
        soup = BeautifulSoup((root / source["path"]).read_bytes(), "html.parser")
        definitions, contexts = unit_definitions(soup), {}
        for article in soup.select("article[id]"):
            previous = []
            for index, element in enumerate(article.select("p,table")):
                if element.name != "table" and element.find_parent("table"):
                    continue
                if element.name == "table":
                    contexts[(article["id"], index)] = table_context(element, definitions, previous[-3:], [],
                        f"article#{article['id']}|block={index}", source.get("currency"))
                elif len(element.get_text()) < 1000:
                    previous.append((f"article#{article['id']}|block={index}", element.get_text(" ", strip=True)))
        for item in chunks:
            item["body_text"] = item["text"].split("\n", 1)[-1]
            match = re.search(r"article#([^|]+)\|structured=(\d+)", item["locator"])
            if item["kind"] == "table" and match:
                metadata = contexts.get((match[1], int(match[2])))
                if metadata:
                    item["text"] = context_prefix(metadata) + item["text"]
                    item["unit_context"] = metadata
                    item["id"] = hashlib.sha256((item["source_sha256"] + item["locator"] + item["text"]).encode()).hexdigest()[:24]
        return chunks
    if strategy not in {"fixed", "structured"} or size < 100:
        raise ValueError("Invalid chunk configuration")
    raw = (root / source["path"]).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    soup = BeautifulSoup(raw, "html.parser")
    chunks, seen = [], set()
    for article in soup.select("article[id]"):
        section = article["id"]
        blocks = []
        for index, element in enumerate(article.select("p,table")):
            if element.name != "table" and element.find_parent("table"):
                continue
            if element.name == "table":
                text = "\n".join(" | ".join(" ".join(cell.stripped_strings)
                                             for cell in row.find_all(["td", "th"]))
                                 for row in element.find_all("tr"))
            else:
                text = " ".join(element.stripped_strings)
            if len(text) < 40 or text in seen:
                continue
            seen.add(text)
            blocks.append((index, text, element.name))
        if strategy == "fixed":
            joined = "\n".join(block[1] for block in blocks)
            pieces = [(offset, joined[offset:offset + size], "text")
                      for offset in range(0, len(joined), size)]
        else:
            pieces = []
            for offset, text, kind in blocks:
                if kind != "table" or len(text) <= size:
                    pieces.append((offset, text, kind))
                    continue
                lines = text.splitlines()
                header = "\n".join(lines[:2])
                group, length, part = [], len(header), 0
                for line in lines[2:]:
                    if group and length + len(line) > size:
                        pieces.append((f"{offset}.{part}", header + "\n" + "\n".join(group), kind))
                        group, length, part = [], len(header), part + 1
                    group.append(line)
                    length += len(line) + 1
                if group:
                    pieces.append((f"{offset}.{part}", header + "\n" + "\n".join(group), kind))
        for offset, text, kind in pieces:
            text = f"{source['company']} FY{source['fiscal_year']} {section.replace('-', ' ')}\n" + text
            locator = f"article#{section}|{strategy}={offset}"
            chunks.append({"id": hashlib.sha256((digest + locator + text).encode()).hexdigest()[:24],
                           "source_sha256": digest, "source_url": source["url"],
                           "company": source["company"], "report_year": source["fiscal_year"],
                           "report_type": source["report_type"], "section": section,
                           "locator": locator, "kind": kind, "text": text})
    return chunks


def chunk_filing(source: dict, root: Path, strategy: str = "structured", size: int = 1200,
                 contextual: bool = False) -> list[dict]:
    raw = (root / source["path"]).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != source["sha256"]:
        raise ValueError("Filing snapshot hash mismatch")
    soup = BeautifulSoup(raw, "html.parser")
    from finevolab.table_context import context_prefix, table_context, unit_definitions
    definitions = unit_definitions(soup) if contextual else {}
    for hidden in soup.find_all(["script", "style", "noscript", "ix:hidden"]):
        hidden.decompose()
    blocks, seen, section, contexts, previous = [], set(), "report", {}, []
    declarations = []
    for index, element in enumerate(soup.find_all(["p", "div", "table"])):
        if element.find_parent("table"):
            continue
        if element.name != "table" and element.find(["p", "div", "table"]):
            continue
        if element.name == "table":
            if element.find("table"):
                continue
            text = "\n".join(" | ".join(" ".join(cell.stripped_strings)
                                         for cell in row.find_all(["td", "th"]))
                             for row in element.find_all("tr"))
            if contextual:
                contexts[index] = table_context(element, definitions, previous[-3:], declarations,
                    f"dom-block={index}", source.get("currency"))
        else:
            text = re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()
        match = re.match(r"^Item\s+(\d+[A-C]?)\s*[.\-:]", text, flags=re.IGNORECASE)
        if match and len(text) < 250:
            section = "item-" + match.group(1).lower()
            previous = []
        if len(text) < 40 or text in seen:
            continue
        seen.add(text)
        blocks.append((index, section, text, element.name))
        if element.name != "table" and len(text) < 1000:
            previous.append((f"dom-block={index}", text))
            if contextual and re.search(r"\b(?:all (?:tabular |dollar |monetary )?amounts|tabular amounts)\b", text, re.I):
                declarations.append((f"dom-block={index}", text))
    chunks = []
    for index, section, text, kind in blocks:
        pieces = [text]
        if strategy == "fixed" or kind != "table":
            pieces = [text[offset:offset + size] for offset in range(0, len(text), size)]
        elif len(text) > size:
            lines = [line for line in text.splitlines() if line.strip(" |")]
            year_rows = [i for i, line in enumerate(lines[:8]) if re.search(r"20\d{2}", line)]
            header_count = max(2, max(year_rows, default=1) + 1)
            header = "\n".join(lines[:header_count])
            pieces, group, length = [], [], len(header)
            for line in lines[header_count:]:
                if group and length + len(line) > size:
                    pieces.append(header + "\n" + "\n".join(group))
                    group, length = [], len(header)
                group.append(line)
                length += len(line)
            if group:
                pieces.append(header + "\n" + "\n".join(group))
        for part, piece in enumerate(pieces):
            locator = f"dom-block={index}|part={part}|strategy={strategy}"
            contextual_text = f"{source['company']} FY{source['fiscal_year']} {section}\n" + piece
            metadata = contexts.get(index)
            if metadata:
                contextual_text = context_prefix(metadata) + contextual_text
            chunks.append({"id": hashlib.sha256((digest + locator + contextual_text).encode()).hexdigest()[:24],
                           "source_sha256": digest, "source_url": source["url"],
                           "company": source["company"], "report_year": source["fiscal_year"],
                           "report_type": "10-K", "section": section, "locator": locator,
                           "kind": kind, "text": contextual_text, **({"unit_context": metadata} if metadata else {}),
                           **({"body_text": piece} if contextual else {})})
    return chunks


def reciprocal_rank_fusion(rankings: list[list[str]], constant: int = 60) -> list[tuple[str, float]]:
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, identifier in enumerate(dict.fromkeys(ranking), start=1):
            scores[identifier] += 1 / (constant + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


class ReportIndex:
    def __init__(self, chunks: list[dict], vectors=None, encoder=None, cache_size: int = 32, vector_backend=None):
        if cache_size < 0:
            raise ValueError("Cache capacity must be nonnegative")
        self.chunks = chunks
        self.cache_size = cache_size
        self._scopes, self._queries = OrderedDict(), OrderedDict()
        self._lock = RLock()
        self.vector_backend = vector_backend
        self.vectors = vectors
        self.encoder = encoder
        self.positions = {item["id"]: index for index, item in enumerate(chunks)}
        if vectors is not None and len(vectors) != len(chunks):
            raise ValueError("Vector and chunk counts differ")

    def search(self, query: str, company: str | None = None, year: int | None = None,
               report_type: str | None = None, limit: int = 5, max_chars: int = 12000,
               mode: str = "bm25") -> list[dict]:
        if limit < 1 or max_chars < 1:
            raise ValueError("Positive retrieval limits are required")
        if mode not in {"bm25", "vector", "hybrid"}:
            raise ValueError("Unknown retrieval mode")
        if not tokenize(query):
            return []
        filtered, lexical = self._scope(company, year, report_type)
        if not filtered:
            return []
        scores = lexical.get_scores(tokenize(query))
        ranked = sorted(zip(filtered, scores), key=lambda item: (-item[1], item[0]["id"]))
        if mode != "bm25":
            if self.vectors is None or self.encoder is None:
                raise ValueError("Vector retrieval requires a loaded index and encoder")
            query_vector = self._query_vector(query)
            positions = [self.positions[item["id"]] for item in filtered]
            if self.vector_backend is None:
                similarities = self.vectors[positions] @ query_vector
                vector_ranked = sorted(zip(filtered, similarities), key=lambda item: (-item[1], item[0]["id"]))
            else:
                try:
                    vector_ranked = self.vector_backend.rank(query_vector, filtered)
                except Exception as error:
                    if not self.vector_backend.settings.fallback:
                        raise
                    from finevolab.retrieval_settings import service_event
                    service_event("vector_backend", configured_backend="milvus", actual_backend="numpy",
                                  status="fallback", error_type=type(error).__name__)
                    similarities = self.vectors[positions] @ query_vector
                    vector_ranked = sorted(zip(filtered, similarities), key=lambda item: (-item[1], item[0]["id"]))
            if mode == "vector":
                ranked = vector_ranked
            else:
                lexical_ids = [item["id"] for item, score in ranked if score > 0]
                vector_ids = [item["id"] for item, score in vector_ranked]
                fusion = reciprocal_rank_fusion([lexical_ids, vector_ids])
                lookup = {item["id"]: item for item in filtered}
                ranked = [(lookup[identifier], score) for identifier, score in fusion]
        results, seen, used = [], set(), 0
        for item, score in ranked:
            if score <= 0 or item["text"] in seen:
                continue
            if used + len(item["text"]) > max_chars:
                continue
            seen.add(item["text"])
            results.append({**item, "score": float(score)})
            used += len(item["text"])
            if len(results) >= limit:
                break
        return results


    def _remember(self, cache, key, value, capacity):
        if capacity:
            cache[key] = value
            cache.move_to_end(key)
            while len(cache) > capacity:
                cache.popitem(last=False)
        return value

    def _scope(self, company, year, report_type):
        key = (company, year, report_type)
        with self._lock:
            if key in self._scopes:
                self._scopes.move_to_end(key)
                return self._scopes[key]
            filtered = [item for item in self.chunks
                        if (company is None or item["company"] == company)
                        and (year is None or item["report_year"] == year)
                        and (report_type is None or item["report_type"] == report_type)]
            lexical = BM25Okapi([tokenize(item["text"]) for item in filtered]) if filtered else None
            if lexical is not None:
                lexical.idf = {token: max(0.0, weight) for token, weight in lexical.idf.items()}
            return self._remember(self._scopes, key, (filtered, lexical), self.cache_size)

    def _query_vector(self, query):
        with self._lock:
            if query in self._queries:
                self._queries.move_to_end(query)
                return self._queries[query]
            vector = self.encoder.encode([query], query=True)[0]
            return self._remember(self._queries, query, vector, self.cache_size * 4)


@lru_cache(maxsize=2)
def cached_encoder(config_json: str, cache_path: str):
    from finevolab.embeddings import PassageEncoder
    return PassageEncoder(json.loads(config_json), Path(cache_path), local_files_only=True)


def load_index(root: Path, strategy: str = "structured", mode: str = "bm25", profile: str = "legacy") -> ReportIndex:
    if strategy not in {"fixed", "structured"}:
        raise ValueError("Unknown chunk strategy")
    if profile not in {"legacy", "contextual"}:
        raise ValueError("Unknown corpus profile")
    directory = root / "data/indexes" / ("contextual" if profile == "contextual" else "")
    path = directory / (strategy + ".json")
    config = root / "configs/retrieval.json"
    signature = tuple((item.stat().st_mtime_ns, item.stat().st_size) for item in
                      ([path, config] if mode == "bm25" else
                       [path, config, path.with_suffix(".metadata.json"), path.with_suffix(".npy")]))
    from finevolab.retrieval_settings import load_settings, RetrievalSettings
    settings = load_settings(root)
    index_settings = RetrievalSettings(vector_backend=settings.vector_backend, milvus=settings.milvus) if mode != "bm25" else RetrievalSettings()
    return _cached_index(str(root.resolve()), strategy, mode, signature, profile, index_settings.model_dump_json())


@lru_cache(maxsize=4)
def _cached_index(root_path: str, strategy: str, mode: str, signature: tuple, profile: str = "legacy", services_json: str = "{}") -> ReportIndex:
    root = Path(root_path)
    if strategy not in {"fixed", "structured"}:
        raise ValueError("Unknown chunk strategy")
    path = root / "data/indexes" / ("contextual" if profile == "contextual" else "") / (strategy + ".json")
    chunks = json.loads(path.read_text(encoding="utf-8"))
    if mode == "bm25":
        return ReportIndex(chunks)
    from finevolab.embeddings import load_vectors
    config = json.loads((root / "configs/retrieval.json").read_text())
    vectors = load_vectors(path, chunks, config)
    encoder = cached_encoder(json.dumps(config, sort_keys=True), str(root / "data/models"))
    from finevolab.retrieval_settings import RetrievalSettings
    from finevolab.vector_backends import MilvusVectorBackend, corpus_identity
    settings = RetrievalSettings.model_validate_json(services_json)
    backend = MilvusVectorBackend(settings.milvus, corpus_identity(path)) if settings.vector_backend == "milvus" else None
    return ReportIndex(chunks, vectors, encoder, vector_backend=backend)


def _explanation_passages(root: Path, query: str, company: str, year: int, topic: str,
                         variant: str = "improved", profile: str = "legacy", uploads: list[dict] | None = None,
                         metric_focus: str | None = None) -> list[dict]:
    if variant not in {"baseline", "improved", "no_expansion", "no_priority", "none", "pooled", "reranked"}:
        raise ValueError("Unknown explanation retrieval variant")
    if variant == "none":
        return []
    from finevolab.reranking import load_config
    reranker_path = root / "configs/reranker.json"
    reranker_config = load_config(root) if reranker_path.exists() else None
    if variant == "improved" and reranker_config is not None and reranker_config.enabled:
        variant = "reranked"
    mode = "bm25" if uploads else "hybrid"
    index = ReportIndex([chunk for document in uploads for chunk in document["chunks"]]) if uploads else load_index(root, mode=mode, profile=profile)
    if variant == "baseline":
        return index.search(query, company=company, year=year, limit=5, max_chars=10000, mode=mode)
    terms = {"revenue": "revenue increased decreased growth primarily driven sales products services",
             "profit": "operating income increased decreased expenses costs primarily margin",
             "risk": "risk factors adversely affect operations demand competition regulation"}
    net_focus = topic == "profit" and metric_focus == "net_income" and (profile == "contextual" or uploads)
    if net_focus:
        terms["profit"] = "net income net loss earnings income taxes tax expense increased decreased primarily driven"
    expanded = query if variant == "no_expansion" else f"{company} {year} " + terms[topic]
    if variant != "no_expansion" and (profile == "contextual" or uploads):
        expanded += " " + query
    candidates = index.search(expanded, company=company, year=year, limit=20, max_chars=50000, mode=mode)
    direct = index.search(query, company=company, year=year, limit=20, max_chars=50000, mode=mode)
    candidates += direct
    focused = profile == "contextual" or bool(uploads)
    if focused and variant != "no_expansion":
        queries = {
            "revenue": ["revenue net sales increased decreased primarily driven", "revenue products services demand growth",
                        "total revenue increased", "advertising revenue increased due to"],
            "profit": (["net income net loss", "income tax effective tax rate higher lower primarily"] if net_focus else
                       ["operating income income from operations", "gross margin operating expenses increased decreased primarily"]),
            "risk": ["risk factors adversely affect operations", "competition demand regulation may could negatively"],
        }
        for text in queries[topic]:
            candidates += index.search(f"{company} {year} {text}", company=company, year=year,
                                       limit=20, max_chars=50000, mode=mode)
    lookup = {item["id"]: item for item in candidates}
    risk_revenue = topic == "risk" and bool(re.search(r"\brevenues?\b|\bsales\b|收入|销售", query, re.I))
    def priority(item):
        if variant == "no_priority":
            return (0, 0, item["score"], item["id"])
        text = item.get("body_text", item["text"]).lower()
        direct_revenue = bool(re.match(r"(?:total\s+|net\s+)?(?:revenues?|sales)\s+(?:increas|decreas|grew|rose|fell)", text))
        narrative = item.get("kind") != "table" and (150 <= len(text) <= 4500 or
                    (topic == "revenue" and direct_revenue and 40 <= len(text) < 150))
        if focused:
            section = item.get("section", "").lower()
            section_match = section in ({"item-1a", "risk-factors"} if topic == "risk" else {"item-7", "financial-review"})
            subject = (any(term in text for term in ("risk", "adversely", "could", "may")) if topic == "risk" else
                       any(term in text for term in ("revenue", "net sales")) if topic == "revenue" else
                       any(term in text for term in ("net income", "net loss", "income tax", "effective tax")) if net_focus else
                       any(term in text for term in ("operating income", "income from operations", "operating expense", "gross margin", "cost of sales")))
            causal = any(term in text for term in ("primarily", "due to", "driven", "reflect", "offset", "adversely", "could", "may"))
            change = bool(re.search(r"increas|decreas|declin|grew|growth|higher|lower|compared|\b(?:up|down|rose|fell)\b", text))
            annual_revenue = topic == "revenue" and bool(re.search(
                r"(?<!cost of )\b(?:total\s+|net\s+|advertising\s+)?(?:revenues?|sales)\s+"
                r"(?:for|in|during|increas|decreas|grew|growth|declin|was|were|rose|fell)", text)) and not bool(re.search(
                r"\b(?:quarter|quarterly|seasonal\w*)\b", text))
            consolidated = annual_revenue and bool(re.search(r"\btotal\s+(?:net\s+)?(?:revenues?|sales)\b", text))
            cash_flow = bool(re.search(r"cash[ -]?flows?|net cash (?:provided|used)", text))
            focused_profit = net_focus and narrative and subject and change and causal and not cash_flow
            targeted_risk = risk_revenue and narrative and section_match and causal and bool(re.search(r"\brevenues?\b|\bsales\b", text))
            return (int(targeted_risk or focused_profit or (narrative and section_match and consolidated and change and causal)),
                    int(narrative and section_match and annual_revenue and change and causal),
                    int(narrative and section_match and subject and change and causal),
                    int(narrative and subject and change and causal), int(section_match and subject and change),
                    int(narrative and subject and causal), int(subject), item["score"], item["id"])
        relevant = ("risk" in text or "adversely" in text) if topic == "risk" else (
            ("revenue" in text if topic == "revenue" else
             any(term in text for term in ("net income", "net loss", "tax expense")) if net_focus else "operating income" in text)
            and any(word in text for word in ("increas", "decreas", "driven", "primarily", "offset")))
        return (int(narrative and relevant), int(relevant), item["score"], item["id"])
    ranked = sorted(lookup.values(), key=priority, reverse=True)
    if variant in {"pooled", "reranked"}:
        if reranker_config is None:
            raise ValueError("A pinned reranker configuration is required")
        ranked = ranked[:reranker_config.max_candidates]
        if variant == "reranked":
            from finevolab.reranking import rerank_passages
            ranked = rerank_passages(root, query, ranked)
    selected, used = [], 0
    if focused and topic in {"profit", "revenue"} and variant != "no_priority":
        label = (r"(?:total\s+)?(?:net\s+)?(?:revenue[s]?|sales)\b" if topic == "revenue" else
                 r"net (?:income|loss|earnings)\b" if net_focus else r"(?:operating (?:income|profit)|income from operations)\b")
        row = re.compile(r"(?:^|\n)\s*(?:\|\s*)*(?:" + label + r")(?:\s+\([^|\n]{1,40}\))?\s*(?:\||$)", re.I)
        tables = [item for item in index.chunks if item.get("kind") == "table"
                  and item["company"] == company and item["report_year"] == year
                  and row.search(item.get("body_text", item["text"])) and len(item["text"]) <= 6000]
        def table_priority(item):
            text = item.get("body_text", item["text"]).lower()
            consolidated_revenue = topic == "revenue" and bool(re.search(
                r"(?:^|\n)\s*(?:\|\s*)*total\s+(?:net\s+)?(?:revenues?|sales)\s*\|", text))
            income_statement = (any(term in text for term in ("operating income", "operating profit", "income from operations", "income (loss) from operations", "loss from operations"))
                                and any(term in text for term in ("net income", "net loss", "net earnings"))
                                and any(term in text for term in ("revenue", "net sales")))
            years = all(str(value) in text for value in (year, year - 1))
            verified_unit = item.get("unit_context", {}).get("status") == "verified"
            partial_period = bool(re.search(r"\b(?:three|six|nine|[369]) months?\b|\bquarter(?:ly)?\b", text))
            common_size = bool(re.search(r"\b(?:revenues?|sales)\s*\|\s*100\s*\|?\s*%", text))
            return (int(not partial_period), int(not common_size), int(consolidated_revenue), int(income_statement), int(years),
                    int(verified_unit), item.get("score", 0), item["id"])
        table = max(tables, key=table_priority, default=None)
        if table:
            selected, used = [table], len(table["text"])
    if focused and topic == "risk" and not risk_revenue:
        for item in direct[:3]:
            if all(existing["id"] != item["id"] for existing in selected) and used + len(item["text"]) <= 10000:
                selected.append(item)
                used += len(item["text"])
    for item in ranked:
        if any(existing["id"] == item["id"] for existing in selected):
            continue
        if used + len(item["text"]) > 10000:
            continue
        selected.append(item)
        used += len(item["text"])
        if len(selected) == 5:
            break
    return selected


def search_corpus(root: Path, query: str, company: str, year: int, mode: str = "hybrid") -> list[dict]:
    from finevolab.uploads import uploaded_chunks
    main = load_index(root, mode=mode).search(query, company=company, year=year, mode=mode)
    uploads = ReportIndex(uploaded_chunks(root / "data/uploads")).search(query, company=company, year=year)
    if not uploads:
        return main
    lookup = {item["id"]: item for item in main + uploads}
    ranked = reciprocal_rank_fusion([[item["id"] for item in main], [item["id"] for item in uploads]])
    return [{**lookup[identifier], "score": score} for identifier, score in ranked[:5]]


def explanation_passages(root: Path, query: str, company: str, year: int, topic: str,
                         variant: str = "improved", profile: str = "legacy", uploads: list[dict] | None = None,
                         metric_focus: str | None = None) -> list[dict]:
    from finevolab.retrieval_settings import load_settings
    settings = load_settings(root)
    def compute():
        return _explanation_passages(root, query, company, year, topic, variant, profile, uploads, metric_focus)
    if not settings.redis.enabled or uploads or variant == "none":
        return compute()
    from finevolab.retrieval_cache import cached_passages
    from finevolab.runtime import fingerprint
    from finevolab.vector_backends import corpus_identity
    index_path = root / "data/indexes" / ("contextual" if profile == "contextual" else "") / "structured.json"
    identity = fingerprint({"query": query, "company": company, "report_year": year, "topic": topic,
        "variant": variant, "profile": profile, "metric_focus": metric_focus, "schema": 1,
        "corpus": corpus_identity(index_path), "vector_backend": settings.vector_backend,
        "milvus": settings.milvus.model_dump(), "limit": 5, "max_chars": 10000,
        "code": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                 for name in ("retrieval.py", "retrieval_cache.py", "vector_backends.py", "reranking.py")},
        "reranker": (root / "configs/reranker.json").read_text(encoding="utf-8")})
    chunks = [item for item in load_index(root, mode="bm25", profile=profile).chunks
              if item["company"] == company and item["report_year"] == year]
    return cached_passages(settings.redis, identity, chunks, compute)
