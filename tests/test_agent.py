"""Verify canonical company identities at the revenue validation boundary."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from finevolab.agent import AnalysisAnswer, analyze_revenue
from finevolab.config import ModelConfig
from finevolab.tools import FinancialStore

ROOT = Path(__file__).resolve().parents[1]


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_alias_calculation_satisfies_the_required_tool_check(self):
        store = FinancialStore(ROOT / "data/processed/reports.json")
        expected = store.growth("MSFT", "revenue", 2024)
        usage = SimpleNamespace(requests=1, input_tokens=1, output_tokens=1, total_tokens=2)
        context = SimpleNamespace(usage=usage)
        prepared = SimpleNamespace(context_wrapper=context, to_input_list=lambda: [])
        answer = AnalysisAnswer(status="complete", company="MSFT", fiscal_year=2024,
            answer="Annual revenue growth follows the disclosed observations.",
            growth_percent=str(expected.value), evidence_ids=expected.evidence_ids)
        finalized = SimpleNamespace(context_wrapper=context, final_output=answer)
        for company in ("MSFT", "Microsoft"):
            with self.subTest(company=company):
                events = [{"tool": "calculate_metrics", "company": "Microsoft", "metric": "revenue",
                           "fiscal_year": 2024, "output": expected.model_dump(mode="json")}]
                with patch("finevolab.agent.Runner.run", AsyncMock(side_effect=[prepared, finalized])):
                    result = await analyze_revenue(ModelConfig("example", "https://example.com/", "unused"),
                                                   store, company, 2024, events)
                self.assertEqual(result["answer"]["company"], "MSFT")
                self.assertEqual(result["answer"]["growth_percent"], str(expected.value))


if __name__ == "__main__":
    unittest.main()
