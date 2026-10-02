"""Verify model overrides at the runner boundary."""

import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from finrobot_equity.core.src.modules.equity_agents.agent_manager import (
    EquityResearchAgentManager,
)


class ModelConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_model_reaches_run_config(self):
        runner = AsyncMock(
            return_value=SimpleNamespace(final_output=SimpleNamespace(tagline="Local result"))
        )
        with patch.dict(os.environ, {"OPENAI_MODEL_NAME": "configured-model"}), patch(
            "finrobot_equity.core.src.modules.equity_agents.agent_manager.Runner.run",
            runner,
        ):
            result = await EquityResearchAgentManager().generate_text_section(
                {}, "tagline", "Example Company", "EXAMPLE"
            )
        self.assertEqual(result, "Local result")
        self.assertEqual(runner.call_args.kwargs["run_config"].model, "configured-model")


if __name__ == "__main__":
    unittest.main()
