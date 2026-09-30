import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.agent import ForecastAgent, inspect_task
from ForecastAgent.retrieval_agent import run_retrieval, RetrievalTask
from ForecastAgent.skill_loader import freeze_skills, load_skill

REQUEST = {"question": "Will Agency announce a change?", "resolution_criteria": "Official announcement", "mode": "live", "pipeline": "legacy"}


def call(name, args, ident):
    return {"tool_calls": [{"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


class AgentRuntimeTests(TestCase):
    def test_skill_version_is_pinned_and_names_are_not_paths(self):
        bundle = {}
        freeze_skills(bundle)
        original = load_skill(bundle, "economic-data")
        with patch("ForecastAgent.skill_loader.ROOT", Path("nonexistent")):
            freeze_skills(bundle)
            self.assertEqual(load_skill(bundle, "economic-data"), original)
        self.assertEqual(bundle["loaded_skills"], ["economic-data"])
        with self.assertRaises(ValueError):
            load_skill(bundle, "../secrets")

    @patch("ForecastAgent.retrieval_agent.ask_ultra")
    def test_ultra_loads_skill_without_search_and_replay_is_read_only(self, ask):
        ask.side_effect = [
            call("plan_evidence", {"needs": [{"id": "n", "condition": "Announcement", "priority": "critical", "expected_source": "Agency", "query": "Agency"}]}, "p"),
            call("load_research_skill", {"name": "official-events"}, "s"),
            call("finish_retrieval", {"status": "partial", "gaps": ["No eligible sources"], "conflicts": [], "summary": "Unknown"}, "f"),
        ]
        with TemporaryDirectory() as directory:
            result = run_retrieval(REQUEST, directory, "", "")
            self.assertEqual(result["loaded_skills"], ["official-events"])
            self.assertEqual(result["resources"]["tavily_basic_attempts"], 0)
            self.assertEqual(result["agent_runtime"]["stage"], "complete")
            body = result["transcript"][1]["result"]["data"]
            self.assertIn("content", body)
            path = Path(directory) / "bundle.json"
            before = path.read_bytes()
            with patch("ForecastAgent.retrieval_agent.ask_ultra", side_effect=AssertionError("No model in replay")):
                self.assertEqual(inspect_task(directory)["loaded_skills"], ["official-events"])
            self.assertEqual(path.read_bytes(), before)

    def test_operator_and_legacy_import_share_one_engine(self):
        import scripts.retrieval_agent as legacy
        import ForecastAgent.retrieval_agent as current
        self.assertIs(legacy, current)
        with TemporaryDirectory() as directory:
            root = Path(directory); task = root / "snapshots" / "question-1"
            task.mkdir(parents=True)
            original = RetrievalTask(task, REQUEST)
            original.bundle["searches"] = [{"status": "failed", "results": []}] * 3
            original.bundle["result"] = {"status": "partial", "gaps": ["Budget used"]}
            original.save()
            agent = ForecastAgent(root)
            self.assertEqual(agent.run("snapshots/question-1")["resources"]["tavily_basic_attempts"], 3)
            with self.assertRaises(ValueError):
                agent.run("snapshots/question-1", {**REQUEST, "question": "Changed input"})
            with self.assertRaises(ValueError):
                agent.task_path("../outside")
