"""Offline failover, ledger preservation, secret hygiene and child bootstrap checks."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

from ForecastAgent.infrastructure.exa_failover import ExaTransport, exhausted


def error(code, tag, message="Failure"):
    return HTTPError("https://api.exa.ai/search", code, message, {},
                     io.BytesIO(json.dumps({"error": message, "tag": tag}).encode()))


def request(key="primary-test"):
    return Request("https://api.exa.ai/search", data=b'{"query":"agency"}',
                   headers={"x-api-key": key}, method="POST")


class FailoverTests(unittest.TestCase):
    def test_primary_exhaustion_one_backup_identical_request_and_sticky_restore(self):
        with TemporaryDirectory() as root:
            original = Mock(side_effect=[error(402, "NO_MORE_CREDITS"), io.BytesIO(b"{}"), io.BytesIO(b"{}")])
            transport = ExaTransport(original, "primary-test", "backup-test", root)
            transport(request(), timeout=30)
            ExaTransport(original, "primary-test", "backup-test", root)(request(), timeout=30)
            self.assertEqual(original.call_count, 3)
            sent = [call.args[0] for call in original.call_args_list]
            self.assertEqual([r.get_header("X-api-key") for r in sent], ["primary-test", "backup-test", "backup-test"])
            self.assertEqual(len({r.data for r in sent}), 1)
            receipts = [json.loads(p.read_text()) for p in Path(root, "attempts").glob("*.json")]
            self.assertEqual(len(receipts), 3)
            self.assertEqual(len({r["logical_transport_id"] for r in receipts}), 2)
            self.assertEqual(sorted(r["http_status"] for r in receipts), [200, 200, 402])
            self.assertFalse(any("primary-test" in p.read_text() or "backup-test" in p.read_text()
                                 for p in Path(root).rglob("*.json")))

    def test_non_credit_errors_never_switch(self):
        for code, tag in [(429, "RATE_LIMIT_EXCEEDED"), (401, "INVALID_API_KEY"),
                          (403, "FORBIDDEN"), (400, "INVALID_REQUEST"), (503, "SERVICE_OVERLOADED"),
                          (402, "X402_PAYMENT_REQUIRED"), (402, "MPP_VERIFICATION_FAILED"), (402, "UNKNOWN")]:
            with self.subTest(code=code, tag=tag), TemporaryDirectory() as root:
                original = Mock(side_effect=error(code, tag))
                with self.assertRaises(HTTPError):
                    ExaTransport(original, "primary-test", "backup-test", root)(request())
                self.assertEqual(original.call_count, 1)
                self.assertFalse(Path(root, "route.json").exists())

    def test_all_exhaustion_tags(self):
        for tag in ("NO_MORE_CREDITS", "API_KEY_BUDGET_EXCEEDED", "TEAM_BUDGET_EXCEEDED"):
            self.assertTrue(exhausted(402, json.dumps({"tag": tag})))
        self.assertFalse(exhausted(402, "credits exhausted"))

    def test_backup_failure_bounded_and_both_keys_redacted(self):
        with TemporaryDirectory() as root:
            original = Mock(side_effect=[error(402, "API_KEY_BUDGET_EXCEEDED"),
                                        error(401, "INVALID_API_KEY", "primary-test backup-test")])
            with self.assertRaises(HTTPError) as caught:
                ExaTransport(original, "primary-test", "backup-test", root)(request())
            value = caught.exception.read().decode() + str(caught.exception)
            self.assertNotIn("backup-test", value)
            self.assertNotIn("primary-test", value)
            self.assertEqual(original.call_count, 2)

    def test_missing_or_identical_backup_no_retry(self):
        for backup in ("", "primary-test"):
            with TemporaryDirectory() as root:
                original = Mock(side_effect=error(402, "TEAM_BUDGET_EXCEEDED"))
                with self.assertRaises(HTTPError):
                    ExaTransport(original, "primary-test", backup, root)(request())
                self.assertEqual(original.call_count, 1)

    def test_month_boundary_and_rotation_retry_primary(self):
        with TemporaryDirectory() as root:
            original = Mock(return_value=io.BytesIO(b"{}"))
            transport = ExaTransport(original, "primary-test", "backup-test", root)
            state = {"month": "2000-01", "primary_digest": transport.identity}
            Path(root, "route.json").write_text(json.dumps(state))
            self.assertFalse(transport.backup_active())
            state["month"] = transport.scope
            Path(root, "route.json").write_text(json.dumps(state))
            self.assertTrue(transport.backup_active())
            self.assertFalse(ExaTransport(original, "rotated-test", "backup-test", root).backup_active())

    def test_other_endpoints_credentials_and_methods_passthrough(self):
        with TemporaryDirectory() as root:
            original = Mock()
            transport = ExaTransport(original, "primary-test", "backup-test", root)
            for req in [request("unrelated"), Request("https://api.exa.ai/contents"),
                        Request("https://example.org/search"), "https://example.org"]:
                transport(req)
            self.assertEqual(original.call_count, 4)
            self.assertFalse(Path(root, "attempts").exists())

    def test_single_task_logical_budget_unchanged(self):
        from ForecastAgent.runtime.retrieval import RetrievalTask
        with TemporaryDirectory() as root, patch.dict(os.environ, {"EXA_API_KEY": "primary-test"}):
            response = io.BytesIO(json.dumps({"results": []}).encode())
            original = Mock(side_effect=[error(402, "NO_MORE_CREDITS"), response])
            transport = ExaTransport(original, "primary-test", "backup-test", Path(root, "transport"))
            args = {"query": "Agency release", "need_ids": ["n"], "reason": "missing source",
                    "search_role": "official_gap", "category": "general"}
            task = RetrievalTask(Path(root, "task"), {"id": "1", "question": "Agency release",
                "resolution_criteria": "Official release", "mode": "live", "pipeline": "collection",
                "acquisition_profile": "collection_v3"})
            task.bundle["plan"] = [{"id": "n", "priority": "critical"}]
            with patch("ForecastAgent.providers.exa_search.urlopen", transport):
                task.execute("search_exa", args, "")
                self.assertEqual(task.budget()["exa_search_remaining"], 0)
                self.assertEqual(task.budget()["tavily_basic_remaining"], 3)
                self.assertEqual(len(task.bundle["exa_searches"]), 1)
                with self.assertRaises(ValueError):
                    task.execute("search_exa", args, "")
            self.assertEqual(original.call_count, 2)

    def test_child_bootstrap_survives_pythonpath_replacement(self):
        # Process a test .pth through site rather than modifying this interpreter.
        from ForecastAgent.infrastructure.exa_failover import setup_bootstrap
        with TemporaryDirectory() as root:
            with patch("site.getsitepackages", return_value=[root]):
                setup_bootstrap()
            env = dict(os.environ, EXA_API_KEY="primary-test", EXA_API_KEY2="backup-test",
                       FORECAST_EXA_FAILOVER_MODULE=str(Path(__file__).with_name("exa_failover.py").resolve()),
                       FORECAST_EXA_TRANSPORT_ROOT=root, PYTHONPATH=root)
            code = ("import site,sys,urllib.request; site.addsitedir(sys.argv[1]); "
                    "assert type(urllib.request.urlopen).__name__=='ExaTransport'; print('child bootstrap verified')")
            result = subprocess.run([sys.executable, "-c", code, root], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("verified", result.stdout)


if __name__ == "__main__":
    unittest.main()
