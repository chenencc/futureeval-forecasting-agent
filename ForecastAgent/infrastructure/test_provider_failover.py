"""Offline acceptance: routing, real HTTP accounting and immutable child workers."""
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from ForecastAgent.infrastructure.provider_failover import ProviderTransport, exhaustion, setup_bootstrap

CREDENTIALS = {"openrouter": ("primary-test", "backup-test"), "tavily": ("tavily-primary", "tavily-backup")}


def request(provider="openrouter", path=None):
    host = "openrouter.ai" if provider == "openrouter" else "api.tavily.com"
    path = path or ("/api/v1/chat/completions" if provider == "openrouter" else "/search")
    return Request("https://" + host + path, data=b'{"query":"agency","model":"unchanged"}',
        headers={"Authorization": "Bearer " + CREDENTIALS[provider][0], "Content-Type": "application/json",
                 "X-Test": "preserved"}, method="POST")


def error(code, message="Insufficient credits", metadata=None, headers=None):
    return HTTPError("https://openrouter.ai/api/v1/chat/completions", code, message, headers or {},
        io.BytesIO(json.dumps({"error": {"code": code, "message": message, "metadata": metadata or {}}}).encode()))


class Acceptance(unittest.TestCase):
    def test_all_endpoints_identical_payload_and_sticky_restore(self):
        for provider, path, status in [("openrouter", "/api/v1/chat/completions", 402),
                ("openrouter", "/api/alpha/decisions", 402), ("tavily", "/search", 432),
                ("tavily", "/extract", 433)]:
            with self.subTest(provider=provider, path=path), TemporaryDirectory() as root:
                original = Mock(side_effect=[error(status), io.BytesIO(b"{}"), io.BytesIO(b"{}")])
                ProviderTransport(original, CREDENTIALS, root)(request(provider, path), timeout=30)
                ProviderTransport(original, CREDENTIALS, root)(request(provider, path), timeout=30)
                sent = [c.args[0] for c in original.call_args_list]
                primary, backup = CREDENTIALS[provider]
                self.assertEqual([r.get_header("Authorization") for r in sent],
                                 ["Bearer " + primary, "Bearer " + backup, "Bearer " + backup])
                self.assertEqual(len({r.data for r in sent}), 1)
                self.assertTrue(all(r.get_header("X-test") == "preserved" for r in sent))
                self.assertTrue(all(c.kwargs == {"timeout": 30} for c in original.call_args_list))
                receipts = [json.loads(p.read_text()) for p in Path(root).rglob("attempts/*.json")]
                self.assertEqual(len(receipts), 3)
                self.assertEqual(len({r["logical_transport_id"] for r in receipts}), 2)
                self.assertEqual(sorted(r["http_status"] for r in receipts), sorted([status, 200, 200]))
                self.assertTrue(all(r["usage"] == "unknown" for r in receipts))
                self.assertFalse(any(key in p.read_text() for pair in CREDENTIALS.values() for key in pair
                                     for p in Path(root).rglob("*.json")))
                self.assertFalse(any("agency" in p.read_text() for p in Path(root).rglob("*.json")))

    def test_daily_limit_and_month_reset(self):
        now = [datetime(2026, 10, 7, 23, 59, tzinfo=timezone.utc)]
        for provider, status, message, advance in [("openrouter", 429, "Rate limit exceeded: free-models-per-day", 1),
                                                  ("tavily", 432, "Plan limit", 25)]:
            with TemporaryDirectory() as root:
                original = Mock(side_effect=[error(status, message), io.BytesIO(b"{}"), io.BytesIO(b"{}")])
                transport = ProviderTransport(original, CREDENTIALS, root, lambda: now[0])
                transport(request(provider))
                now[0] += timedelta(days=advance)
                transport(request(provider))
                self.assertEqual(original.call_args_list[-1].args[0].get_header("Authorization"),
                                 "Bearer " + CREDENTIALS[provider][0])

    def test_adjustable_credit_caps_probe_primary_after_one_hour(self):
        with TemporaryDirectory() as root:
            now = [datetime(2026, 10, 7, tzinfo=timezone.utc)]
            original = Mock(side_effect=[error(402), io.BytesIO(b"{}"), io.BytesIO(b"{}")])
            transport = ProviderTransport(original, CREDENTIALS, root, lambda: now[0])
            transport(request())
            now[0] += timedelta(hours=1)
            transport(request())
            self.assertEqual(original.call_args_list[-1].args[0].get_header("Authorization"), "Bearer primary-test")

    def test_rotation_of_either_key_invalidates_old_route(self):
        for role in (0, 1):
            with TemporaryDirectory() as root:
                original = Mock(side_effect=[error(402), io.BytesIO(b"{}"), io.BytesIO(b"{}")])
                ProviderTransport(original, CREDENTIALS, root)(request())
                pair = list(CREDENTIALS["openrouter"])
                pair[role] = "rotated-test"
                transport = ProviderTransport(original, {"openrouter": tuple(pair)}, root)
                req = request()
                req.add_header("Authorization", "Bearer " + pair[0])
                transport(req)
                self.assertEqual(original.call_args_list[-1].args[0].get_header("Authorization"), "Bearer " + pair[0])

    def test_no_switch_for_non_quota_or_unknown_outcome(self):
        failures = [error(400), error(401), error(403), error(429, "Rate limit exceeded"), error(502),
                    error(402, metadata={"limit_source": "openrouter_in_flight_budget"}),
                    error(402, metadata={"reason": "weight_exceeds_budget", "limit_source": "openrouter_credits"}),
                    error(402, headers={"Retry-After": "60"}),
                    error(429, "daily quota", {"provider_name": "upstream"}),
                    TimeoutError("primary-test backup-test"), URLError("primary-test backup-test")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), TemporaryDirectory() as root:
                original = Mock(side_effect=failure)
                with self.assertRaises((HTTPError, URLError)) as caught:
                    ProviderTransport(original, CREDENTIALS, root)(request())
                self.assertNotIn("primary-test", str(caught.exception))
                self.assertEqual(original.call_count, 1)
                self.assertFalse(Path(root, "openrouter/route.json").exists())
        for status in (400, 401, 403, 429, 500):
            self.assertIsNone(exhaustion("tavily", status, "monthly limit"))

    def test_backup_failure_is_bounded_and_sanitized(self):
        with TemporaryDirectory() as root:
            original = Mock(side_effect=[error(402), error(401, "primary-test backup-test")])
            with self.assertRaises(HTTPError) as caught:
                ProviderTransport(original, CREDENTIALS, root)(request())
            value = caught.exception.read().decode() + str(caught.exception)
            self.assertNotIn("backup-test", value)
            self.assertNotIn("primary-test", value)
            self.assertEqual(original.call_count, 2)

    def test_both_exhausted_fail_fast_across_restart(self):
        with TemporaryDirectory() as root:
            original = Mock(side_effect=[error(429, "free-models-per-day"), error(429, "free-models-per-day")])
            for _ in range(3):
                with self.assertRaises(HTTPError) as caught:
                    ProviderTransport(original, CREDENTIALS, root)(request())
                self.assertIn("free-models-per-day", caught.exception.read().decode())
            self.assertEqual(original.call_count, 2)

    def test_missing_identical_backup_only_one_attempt(self):
        for backup in ("", "primary-test"):
            with TemporaryDirectory() as root:
                original = Mock(side_effect=error(402))
                with self.assertRaises(HTTPError):
                    ProviderTransport(original, {"openrouter": ("primary-test", backup)}, root)(request())
                self.assertEqual(original.call_count, 1)

    def test_exact_endpoint_and_auth_guard(self):
        with TemporaryDirectory() as root:
            original = Mock()
            transport = ProviderTransport(original, CREDENTIALS, root)
            for url in ("https://openrouter.ai.evil/api/v1/chat/completions",
                        "http://openrouter.ai/api/v1/chat/completions",
                        "https://openrouter.ai/api/v1/chat/completions?key=secret",
                        "https://api.tavily.com/crawl"):
                req = Request(url, data=b"{}", headers={"Authorization": "Bearer primary-test"})
                transport(req)
                self.assertIs(original.call_args.args[0], req)
            req = request()
            req.add_header("Authorization", "Bearer unrelated-key")
            transport(req)
            transport("https://example.org")
            self.assertEqual(original.call_count, 6)
            self.assertFalse(list(Path(root).rglob("*.json")))

    def test_tavily_logical_search_budget_unchanged(self):
        from ForecastAgent.runtime.retrieval import RetrievalTask
        with TemporaryDirectory() as root, patch.dict(os.environ, {"TAVILY_API_KEY": "tavily-primary"}):
            response = json.dumps({"results": []}).encode()
            original = Mock(side_effect=[error(432), io.BytesIO(response)])
            transport = ProviderTransport(original, CREDENTIALS, Path(root, "transport"))
            task = RetrievalTask(Path(root, "task"), {"id": "1", "question": "Agency release",
                "resolution_criteria": "Official release", "mode": "live", "pipeline": "collection",
                "acquisition_profile": "collection_v3"})
            task.bundle["plan"] = [{"id": "n", "priority": "critical"}]
            before = task.budget()
            args = {"query": "Agency release", "need_ids": ["n"], "reason": "missing source",
                    "search_role": "official_gap", "purpose": "gap", "category": "general"}
            with patch("ForecastAgent.providers.tavily_search.urlopen", transport):
                task.execute("search_tavily", args, "tavily-primary")
            self.assertEqual(task.budget()["tavily_basic_remaining"], 2)
            self.assertEqual(task.budget()["exa_search_remaining"], before["exa_search_remaining"])
            self.assertEqual(original.call_count, 2)

    def test_checkpoint_retains_routes_and_receipts(self):
        from ForecastAgent.infrastructure.storage import split, verify
        with TemporaryDirectory() as root:
            state = Path(root, "official")
            state.mkdir()
            (state / "campaign.json").write_text(json.dumps({"schema": "official-competition-v1",
                "tournament": "fall-futureeval-2026", "tasks": {}}))
            transport = ProviderTransport(Mock(side_effect=[error(402), io.BytesIO(b"{}")]),
                                          CREDENTIALS, state / "provider-transport")
            transport(request())
            split(state, Path(root, "export"))
            checkpoint = Path(root, "export/checkpoint")
            self.assertTrue((checkpoint / "provider-transport/openrouter/route.json").exists())
            self.assertEqual(len(list(checkpoint.rglob("attempts/*.json"))), 2)
            verify(checkpoint)

    def test_child_bootstrap_composes_exa_and_survives_pythonpath_replacement(self):
        from ForecastAgent.infrastructure.exa_failover import setup_bootstrap as exa_setup
        with TemporaryDirectory() as root:
            with patch("site.getsitepackages", return_value=[root]):
                setup_bootstrap()
                exa_setup()
            directory = Path(__file__).parent.resolve()
            env = dict(os.environ, OPENROUTER_API_KEY="primary-test", OPENROUTER_API_KEY2="backup-test",
                       TAVILY_API_KEY="tavily-primary", TAVILY_API_KEY2="tavily-backup",
                       EXA_API_KEY="exa-primary", EXA_API_KEY2="exa-backup",
                       FORECAST_PROVIDER_FAILOVER_MODULE=str(directory / "provider_failover.py"),
                       FORECAST_EXA_FAILOVER_MODULE=str(directory / "exa_failover.py"),
                       FORECAST_PROVIDER_TRANSPORT_ROOT=root, FORECAST_EXA_TRANSPORT_ROOT=root, PYTHONPATH=root)
            code = ("import site,sys,urllib.request; site.addsitedir(sys.argv[1]); "
                    "u=urllib.request.urlopen; assert type(u).__name__=='ProviderTransport'; "
                    "assert type(u.original).__name__=='ExaTransport'; "
                    "assert len(u.credentials)==2; print('three provider transports verified')")
            result = subprocess.run([sys.executable, "-c", code, root], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("verified", result.stdout)

    def test_mercury_decision_keeps_one_logical_reservation(self):
        from ForecastAgent.providers.decisions import decide
        with TemporaryDirectory() as root:
            result = {"model": "inception/mercury-decide:free", "answers": {"event": {"type": "noul", "noul": 0.63}}}
            response = io.BytesIO(json.dumps(result).encode())
            response.status = 200
            original = Mock(side_effect=[error(402, metadata={"limit_source": "openrouter_key_limit"}), response])
            observer = Mock(return_value="one-reservation")
            transport = ProviderTransport(original, CREDENTIALS, root)
            with patch("ForecastAgent.providers.decisions.urlopen", transport):
                output = decide("Raw evidence", {"event": {"type": "noul", "description": "Event?"}},
                                "primary-test", observer)
            self.assertEqual(output, result)
            self.assertEqual([call.args[0] for call in observer.call_args_list], ["reserve", "complete"])
            self.assertEqual(original.call_count, 2)

    def test_exa_composition_still_retries_once(self):
        from ForecastAgent.infrastructure.exa_failover import ExaTransport
        with TemporaryDirectory() as root:
            failure = HTTPError("https://api.exa.ai/search", 402, "Exhausted", {},
                                io.BytesIO(b'{"tag":"NO_MORE_CREDITS"}'))
            original = Mock(side_effect=[failure, io.BytesIO(b"{}")])
            exa = ExaTransport(original, "exa-primary", "exa-backup", Path(root, "exa"))
            outer = ProviderTransport(exa, CREDENTIALS, Path(root, "providers"))
            outer(Request("https://api.exa.ai/search", data=b'{"query":"agency"}',
                          headers={"x-api-key": "exa-primary"}, method="POST"))
            self.assertEqual(original.call_count, 2)
            self.assertEqual(original.call_args.args[0].get_header("X-api-key"), "exa-backup")


if __name__ == "__main__":
    unittest.main()
