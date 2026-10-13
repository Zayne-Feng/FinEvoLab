"""Check intent-dependent statuses, bounded repairs, and passage provenance."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from openai.types.chat import ChatCompletion

from finevolab.config import ModelConfig
from finevolab.grounded import (RequestPlan, analyze_grounded, assemble_numeric,
                                checked_passages, validate_explanation)
from finevolab.retrieval import load_index
from finevolab.tools import FinancialStore

ROOT = Path(__file__).resolve().parents[1]


def completion(text, usage=True):
    return ChatCompletion(id="completion-test", created=0, model="model", object="chat.completion",
        choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
        usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20} if usage else None)


class GroundedTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_noun_does_not_add_an_unrequested_numeric_branch(self):
        plan = RequestPlan(intent="explanation", companies=["MSFT"], fiscal_year=2024,
                           report_year=2024, metrics=["revenue"], topic="risk")
        chunk = next(item for item in load_index(ROOT, mode="bm25", profile="contextual").chunks
                     if item["company"] == "MSFT" and item["report_year"] == 2024
                     and item["kind"] != "table" and "risk" in item["body_text"].lower()
                     and len(item["body_text"]) > 150)
        payloads = [plan.model_dump_json(), json.dumps({"status": "complete", "claims": [{
            "statement": "The report discloses risks affecting business performance.",
            "chunk_id": "passage:" + chunk["id"], "quote": chunk["body_text"][:120]}]})]
        class FakeClient:
            base_url = "https://example.com/"
            def __init__(self):
                self.chat = SimpleNamespace(completions=self)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def create(self, **kwargs):
                return completion(payloads.pop(0))
        with patch("finevolab.grounded.async_client", return_value=FakeClient()), patch(
                "finevolab.retrieval.explanation_passages", return_value=[{**chunk, "score": 1.0}]):
            output = await analyze_grounded(ModelConfig("model", "https://example.com/", "unused"), self.store,
                "Which risks in the MSFT FY2024 annual report could affect revenue?", {"system": ""})
        self.assertEqual(output["answer_type"], "explanation")
        self.assertEqual(output["request"], plan.model_dump())
        self.assertFalse(any(event.get("tool") == "get_financial_metrics" for event in output["events"]))

    async def test_quote_resolution_preserves_source_values_and_ambiguity(self):
        from finevolab.grounded import source_quote
        text = "Net income | $ | 5,428 |  | $ | 4,756"
        self.assertEqual(source_quote(text, "Net income | $ | 5,428  |  | $ | 4,756"), text)
        wrong = "Net income | $ | 5,429 |  | $ | 4,756"
        self.assertEqual(source_quote(text, wrong), wrong)
        ambiguous = "Net  income rose. Net\nincome rose."
        self.assertEqual(source_quote(ambiguous, "Net income rose."), "Net income rose.")

    async def test_multiple_company_growth_cannot_become_an_amount_comparison(self):
        plan = RequestPlan(intent="hybrid", numeric_intent="growth", companies=["MSFT", "AAPL"],
                           fiscal_year=2024, metrics=["revenue"])
        answer = assemble_numeric(plan, self.store, [])
        self.assertEqual(answer["status"], "insufficient_data")
        self.assertEqual(answer["metrics"], [])

    def setUp(self):
        self.store = FinancialStore(ROOT / "data/processed/reports.json")
        self.plan = RequestPlan(intent="comparison", companies=["Microsoft", "Apple"], fiscal_year=2024,
                                metrics=["revenue"])

    async def test_numeric_comparison_and_alignment_have_distinct_statuses(self):
        output = assemble_numeric(self.plan, self.store, [])
        self.assertEqual(output["status"], "complete")
        self.assertFalse(output["periods_aligned"])
        output = assemble_numeric(self.plan.model_copy(update={"intent": "alignment"}), self.store, [])
        self.assertEqual(output["status"], "periods_not_aligned")

    async def test_alias_self_comparison_and_partial_missing_are_unresolved(self):
        plan = self.plan.model_copy(update={"companies": ["Microsoft", "MSFT"]})
        self.assertEqual(assemble_numeric(plan, self.store, [])["status"], "invalid_parameters")
        plan = self.plan.model_copy(update={"companies": ["MSFT", "ZZZZ"]})
        result = assemble_numeric(plan, self.store, [])
        self.assertEqual(result["status"], "insufficient_data")
        self.assertFalse(result["metrics"])
        self.assertFalse(result["evidence_ids"])

    async def test_request_contract_rejects_source_valid_status_and_scope_errors(self):
        from finevolab.validation import validate_answer
        answer = assemble_numeric(self.plan, self.store, [])
        wrong = copy.deepcopy(answer)
        wrong["status"] = "periods_not_aligned"
        validate_answer(self.store, {"answer": wrong})
        with self.assertRaisesRegex(ValueError, "request contract"):
            validate_answer(self.store, {"answer": wrong, "request": self.plan.model_dump()})
        other = self.plan.model_copy(update={"metrics": ["net_income"]})
        with self.assertRaisesRegex(ValueError, "cover the request"):
            validate_answer(self.store, {"answer": answer, "request": other.model_dump()})

    async def test_explanation_claims_do_not_infer_amount_scale_from_tables(self):
        from finevolab.grounded import PassageClaim
        payload = {"chunk_id": "passage:table", "quote": "Total operating income | $ | 114,301 | $ | 119,437"}
        for statement in ("Operating income declined to $114,301.", "Revenue increased by 22%.",
                          "Revenue grew by 22 percent.", "Revenue increased by 28.68 billion."):
            with self.assertRaises(ValueError):
                PassageClaim(statement=statement, **payload)
        PassageClaim(statement="Operating income declined as disclosed product margins weakened in FY2023.", **payload)

    async def test_source_tampering_and_quote_fabrication_are_rejected(self):
        chunk = next(item for item in load_index(ROOT, mode="bm25").chunks
                     if item["company"] == "MSFT" and item["report_year"] == 2024 and len(item["text"]) > 100)
        evidence = checked_passages(ROOT, [chunk])
        output = {"request": self.plan.model_copy(update={"intent": "explanation", "companies": ["MSFT"]}).model_dump(),
                  "evidence": evidence, "answer": {"status": "complete", "claims": [
                      {"statement": "A source-bound statement about the report.", "chunk_id": evidence[0]["id"],
                       "quote": chunk["text"][:80]}]}}
        self.assertEqual(validate_explanation(ROOT, output)["status"], "passed")
        wrong = copy.deepcopy(output)
        wrong["evidence"][0]["text"] += "fabricated information"
        with self.assertRaisesRegex(ValueError, "pinned index"):
            validate_explanation(ROOT, wrong)
        wrong = copy.deepcopy(output)
        wrong["answer"]["claims"][0]["quote"] = "A fabricated quotation that does not exist anywhere."
        with self.assertRaisesRegex(ValueError, "exact source"):
            validate_explanation(ROOT, wrong)

    async def test_profit_explanation_requires_the_requested_metric_and_its_quote(self):
        index = load_index(ROOT, mode="bm25")
        margin = next(item for item in index.chunks if item["id"] == "e87c5648257919c4c8a369db")
        income = next(item for item in index.chunks if item["id"] == "a1a0b8d54e91c060ea6e411e")
        evidence = checked_passages(ROOT, [margin, income])
        plan = RequestPlan(intent="explanation", companies=["AAPL"], fiscal_year=2023,
                           metrics=["operating_income"], topic="profit")
        claim = {"statement": "Products gross margin declined because foreign currencies weakened.",
                 "chunk_id": evidence[0]["id"], "quote": margin["text"][-100:]}
        output = {"request": plan.model_dump(), "answer": {"status": "complete", "claims": [claim]}, "evidence": evidence[:1]}
        with self.assertRaisesRegex(ValueError, "Directly describe"):
            validate_explanation(ROOT, output)
        claim = {"statement": "Total operating income declined while research and development expenses increased.",
                 "chunk_id": evidence[1]["id"], "quote": income["text"][-350:]}
        output.update(answer={"status": "complete", "claims": [claim]}, evidence=evidence[1:])
        self.assertEqual(validate_explanation(ROOT, output)["status"], "passed")
        output["answer"]["claims"][0]["statement"] = "The table presents total operating income and research expenses for both years."
        with self.assertRaisesRegex(ValueError, "State explicitly"):
            validate_explanation(ROOT, output)
        output["answer"]["claims"][0]["statement"] = "Operating income faced risks from research expenses in both years."
        with self.assertRaisesRegex(ValueError, "State explicitly"):
            validate_explanation(ROOT, output)

    async def test_invalid_json_repairs_once_and_records_usage(self):
        payloads = ["invalid", self.plan.model_dump_json()]
        class FakeClient:
            base_url = "https://example.com/"
            def __init__(self):
                self.chat = SimpleNamespace(completions=self)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def create(self, **kwargs):
                return completion(payloads.pop(0))
        with patch("finevolab.grounded.async_client", return_value=FakeClient()):
            output = await analyze_grounded(ModelConfig("model", "https://example.com/", "unused"),
                                            self.store, "Compare Microsoft and Apple FY2024 revenue.", {"system": ""})
        self.assertEqual(output["usage"]["requests"], 2)
        self.assertEqual(output["usage"]["total_tokens"], 40)
        self.assertEqual(output["repair"]["count"], 1)
        self.assertEqual(output["answer"]["status"], "complete")

    async def test_second_invalid_response_is_not_retried(self):
        class FakeClient:
            base_url = "https://example.com/"
            def __init__(self):
                self.chat = SimpleNamespace(completions=self)
                self.calls = 0
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def create(self, **kwargs):
                self.calls += 1
                return completion("invalid", usage=False)
        for enabled, calls in ((True, 2), (False, 1)):
            client = FakeClient()
            with patch("finevolab.grounded.async_client", return_value=client):
                with self.assertRaises(ValueError):
                    await analyze_grounded(ModelConfig("model", "https://example.com/", "unused"),
                                           self.store, "Compare companies.", {"system": ""}, allow_repair=enabled)
            self.assertEqual(client.calls, calls)


if __name__ == "__main__":
    unittest.main()
