"""Behavioral regressions for production failure patterns and bounded replay."""
import copy
import hashlib
import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ForecastAgent.intelligence.admission import prepare, inspect, unique_information
from ForecastAgent.intelligence.requirements import contract, classify_need
from ForecastAgent.intelligence.sources import recovery_plan, read_candidate
from ForecastAgent.intelligence.decision import registry, validate_brief, ground_brief, compare, build_brief
from ForecastAgent.intelligence.pipeline import prepare_package, collect
from ForecastAgent.intelligence.repair import recover
from ForecastAgent.analysis.pilot import load, save, digest


def question():
    return {"id": "1", "question_type": "binary", "question": "Will approval fall below 38% before November 1, 2026?",
            "resolution_criteria": "Use absolute approval, not net approval, from https://tracker.example.org/approval.",
            "background": "The current baseline must be measured.", "mode": "live"}


def bundle(pages=None):
    return {"request": question(), "pages": pages or {}, "plan": [], "gaps": [],
            "searches": [{"status": "failed"}], "model_attempts": [{"status": "received"}],
            "fetch_attempts": [{"status": "reserved"}], "result": {"status": "collected"}}


class AdmissionTests(TestCase):
    def test_site_name_and_identical_boilerplate_are_not_original_evidence(self):
        chrome = "Study record managers: refer to the Data Element Definitions if submitting registration or results information."
        original = bundle({"https://a.example/1": {"content": chrome},
                           "https://a.example/2": {"content": chrome},
                           "https://b.example/app": {"content": "DeFlock"}})
        before = copy.deepcopy(original)
        view, report = prepare(original)
        self.assertEqual(original, before)
        self.assertEqual(view["pages"], {})
        self.assertEqual(report["status"], "needs_material_recovery")
        self.assertEqual(report["excluded_bodies"], 3)
        self.assertEqual(view["fetch_attempts"], original["fetch_attempts"])

    def test_short_valid_announcements_and_structured_measurements_survive(self):
        pages = [
            {"content": "27th Annual Terminator Weigh-Off\nSaturday, October 17, 2026\n12:00 PM 5:00 PM\nStickmen Brewing Company"},
            {"content": "Flavio received 56,104,268 votes, equivalent to 47.03%. Lula received 45.16%."},
            {"content": "date,approval\n2026-10-08,38.4\n2026-10-09,38.2", "content_type": "text/csv"},
            {"content": '{"count": 12}', "content_type": "application/json"}
        ]
        self.assertTrue(all(inspect(p)["usable_text"] for p in pages))

    def test_duplicate_urls_keep_the_rule_source_and_original_coordinates(self):
        text = "Observed approval was 38.4 percent on October 8, 2026. This is an absolute approval measurement."
        original = bundle({"https://tracker.example.org/approval?stream=top": {"content": text},
                           "https://tracker.example.org/approval": {"content": text}})
        view, audit = prepare(original)
        self.assertEqual(list(view["pages"]), ["https://tracker.example.org/approval"])
        self.assertEqual(view["pages"]["https://tracker.example.org/approval"]["content"], text)
        self.assertEqual(audit["duplicate_bodies"], 1)
        self.assertTrue(any(r.get("representative_url") == "https://tracker.example.org/approval" for r in audit["sources"]))

    def test_integrity_failure_is_not_reclassified_as_a_gap(self):
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            inspect({"content": "Changed content", "content_sha256": "0" * 64})

    def test_snippets_remain_discovery_leads(self):
        self.assertFalse(inspect({"content": "Approval reaches a new low in today's tracker.",
                                 "capture_method": "tavily_basic_snippet_only"})["usable_text"])

    def test_second_read_ignores_duplicate_url_and_coordinate_novelty(self):
        text = "A recorded announcement provides current scheduling information." * 20
        first = {"evidence": [{"evidence_id": "A", "text": text, "source_id": "S1"}]}
        second = {"evidence": first["evidence"] + [{"evidence_id": "B", "text": text, "source_id": "S2"}]}
        self.assertEqual(unique_information(first, second)["new_distinct_chars"], 0)


class RequirementsAndSourceTests(TestCase):
    def test_future_outcomes_and_failed_present_observations_are_different(self):
        clock = "2026-10-09T00:00:00+00:00"
        future = classify_need({"purpose": "future_outcome", "available_after_utc": "2026-11-01T00:00:00+00:00"}, clock)
        current = classify_need({"purpose": "current_baseline"}, clock)
        self.assertFalse(future["blocking_capture_gap"])
        self.assertTrue(current["blocking_capture_gap"])
        self.assertIsNone(classify_need({"id": "weight_2026"}, clock)["blocking_capture_gap"])
        with self.assertRaises(ValueError):
            classify_need({"purpose": "future_outcome", "available_after_utc": "2026-10-01T00:00:00+00:00"}, clock)

    def test_exact_rules_and_scales_are_preserved_in_contract(self):
        q = question()
        before = copy.deepcopy(q)
        c = contract(q, clock_utc="2026-10-09T00:00:00+00:00")
        self.assertEqual(q, before)
        self.assertEqual(c["original_question"]["resolution_criteria"], q["resolution_criteria"])
        with self.assertRaisesRegex(ValueError, "timezone"):
            contract(q, clock_utc="2026-10-09T00:00:00")

    def test_observed_csv_precedes_related_navigation_and_private_links_are_rejected(self):
        body = "The approval tracker publishes its current observations daily. [Download current data](https://docs.google.com/spreadsheets/d/e/existing/pub?output=csv) [Privacy](https://tracker.example.org/privacy) [Private](http://127.0.0.1/file.csv)"
        b = bundle({"https://tracker.example.org/approval": {"content": body}})
        _, a = prepare(b)
        p = recovery_plan(b, a)
        urls = [r["url"] for r in p["candidates"]]
        self.assertEqual(urls[0], "https://docs.google.com/spreadsheets/d/e/existing/pub?output=csv")
        self.assertFalse(any("127.0.0.1" in url or "privacy" in url for url in urls))

    def test_markdown_escaped_query_is_redecoded_from_original_only(self):
        b = bundle()
        b["request"]["resolution_criteria"] = r"Use [Article IV](https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=CONS\&sectionNum=SEC.%203.\&article=IV)."
        _, a = prepare(b)
        urls = [r["url"] for r in recovery_plan(b, a)["candidates"]]
        self.assertEqual(len(urls), 1)
        self.assertIn("&sectionNum=", urls[0])
        self.assertNotIn("§", urls[0])
        self.assertNotIn("\\", urls[0])

    def test_reader_uses_only_callers_reserved_capture(self):
        calls = []
        def capture(url):
            calls.append(url)
            return {"content": "date,approval\n2026-10-09,38.2", "content_type": "text/csv"}
        candidate = {"url": "https://tracker.example.org/data.csv", "parent_url": "https://tracker.example.org/approval"}
        self.assertTrue(read_candidate(candidate, capture)["body_diagnostics"]["usable_text"])
        self.assertEqual(calls, [candidate["url"]])

    def test_study_shell_routes_to_documented_official_json_with_identity_check(self):
        b = bundle({"https://clinicaltrials.gov/study/NCT06865534": {"content": "Study record managers: refer to the Data Element Definitions if submitting registration or results information."}})
        _, audit = prepare(b)
        candidate = next(r for r in recovery_plan(b, audit)["candidates"] if r.get("adapter"))
        self.assertEqual(candidate["url"], "https://clinicaltrials.gov/api/v2/studies/NCT06865534")
        data = {"protocolSection": {"identificationModule": {"nctId": "NCT06865534", "briefTitle": "A chatbot study"},
                "statusModule": {"overallStatus": "RECRUITING"},
                "armsInterventionsModule": {"interventions": [{"name": "LLM-based Chatbot", "type": "BEHAVIORAL"}]}}}
        result = read_candidate(candidate, lambda url: {"content": json.dumps(data), "content_type": "application/json"})
        self.assertEqual(result["structured_view"]["overall_status"], "RECRUITING")
        self.assertFalse(result["structured_view"]["population_count_verified"])
        data["protocolSection"]["identificationModule"]["nctId"] = "NCT00000000"
        with self.assertRaisesRegex(ValueError, "identity differs"):
            read_candidate(candidate, lambda url: {"content": json.dumps(data), "content_type": "application/json"})


class DecisionTests(TestCase):
    def test_cached_brief_preserves_quarantine_metadata_without_more_http(self):
        state = {"evidence": [{"evidence_id": "E1", "text": "Current approval is 38.2 percent."}]}
        brief = {"observations": [{"claim": "A current baseline exists.", "evidence_id": "E1", "quote": state["evidence"][0]["text"]}],
                 "factors": [], "uncertainties": []}
        message = {"tool_calls": [{"function": {"name": "record_forecast_brief", "arguments": json.dumps(brief)}}]}
        with tempfile.TemporaryDirectory() as root, patch("ForecastAgent.intelligence.decision.ask_ultra", return_value=message) as caller:
            first = build_brief(state, root, "unused_test_key")
            second = build_brief(state, root, "unused_test_key")
            self.assertEqual(first, second)
            self.assertEqual(caller.call_count, 1)
            saved = load(Path(root) / "brief.json")
            saved["uncertainties"].append("Tampered text")
            save(Path(root) / "brief.json", saved)
            with self.assertRaisesRegex(ValueError, "binding audit changed"):
                build_brief(state, root, "unused_test_key")
    def test_brief_quotes_are_bound_and_hypotheses_are_not_certified(self):
        state = {"evidence": [{"evidence_id": "E1", "text": "Current approval is 38.2 percent."}]}
        brief = {"observations": [{"claim": "A current baseline exists.", "evidence_id": "E1", "quote": "Current approval is 38.2 percent."}],
                 "factors": [{"hypothesis": "A further decline could set a record.", "evidence_ids": ["E1"]}], "uncertainties": ["Future changes remain unknown."]}
        result = validate_brief(brief, state)
        self.assertFalse(result["claim_entailment_verified"])
        brief["observations"][0]["quote"] = "Approval is 12.0 percent."
        with self.assertRaisesRegex(ValueError, "verbatim"):
            validate_brief(brief, state)

    def test_future_proof_is_not_the_sufficiency_definition(self):
        questions, _ = registry(question())
        self.assertIn("future outcome can remain unknown", questions["evidence_sufficiency"]["criteria"][3])
        self.assertIn("current baselines", questions["observation_coverage"]["instructions"])

    def test_one_bad_quote_does_not_discard_valid_observations_or_rewrite_it(self):
        state = {"evidence": [{"evidence_id": "E1", "text": "Current approval is 38.2 percent."},
                              {"evidence_id": "E2", "text": "The tracker updates each afternoon."}]}
        brief = {"observations": [{"claim": "A baseline exists.", "evidence_id": "E1", "quote": "Current approval is 38.2 percent."},
                                   {"claim": "Invented value", "evidence_id": "E2", "quote": "Current approval is 12.0 percent."}],
                 "factors": [{"hypothesis": "Unsupported factor", "evidence_ids": ["E2"]}], "uncertainties": []}
        before = copy.deepcopy(brief)
        kept, rejected = ground_brief(brief, state)
        self.assertEqual(brief, before)
        self.assertEqual(len(kept["observations"]), 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(kept["factors"], [])
        validate_brief(kept, state)

    def test_dry_run_keeps_originals_and_has_no_provider_calls(self):
        b = bundle({"https://tracker.example.org/approval": {"content": "Current approval was 38.2 percent on October 9, 2026. The previous minimum was 38.3 percent."}})
        with tempfile.TemporaryDirectory() as root, patch("ForecastAgent.intelligence.decision.chain.call", side_effect=AssertionError("Network during preparation")):
            result = compare(b, root, "2026-10-09T00:00:00+00:00")
            self.assertEqual(result["status"], "prepared")
            state = json.loads((Path(root) / "shared-originals.json").read_text(encoding="utf-8"))
            self.assertTrue(all(s["text"] == b["pages"][s["url"]]["content"][s["start"]:s["end"]] for s in state["evidence"]))

    def test_saved_package_rejects_input_changes_instead_of_resetting(self):
        b = bundle({"https://tracker.example.org/approval": {"content": "Current approval was 38.2 percent on October 9, 2026."}})
        with tempfile.TemporaryDirectory() as root:
            prepare_package(b, root, "2026-10-09T00:00:00+00:00")
            b["pages"]["https://tracker.example.org/approval"]["content"] += "Changed."
            with self.assertRaisesRegex(ValueError, "identity changed"):
                prepare_package(b, root, "2026-10-09T00:00:00+00:00")

    def test_complete_collector_and_supplement_are_connected_to_admission(self):
        b = bundle({"https://tracker.example.org/approval": {"content": "Current approval was 38.2 percent on October 9, 2026."}})
        with tempfile.TemporaryDirectory() as root, \
             patch("ForecastAgent.releases.v1_0_5.collect", return_value={"release_acquisition": {"version": "1.0.5"}}) as initial, \
             patch("ForecastAgent.releases.v1_0_5.supplement", return_value=b) as supplement:
            result = collect(question(), root, clock_utc="2026-10-09T00:00:00+00:00")
            self.assertEqual(initial.call_count, 1)
            self.assertEqual(supplement.call_count, 1)
            self.assertIn("predictive_information_contract", initial.call_args.args[0])
            self.assertEqual(result["report"]["status"], "ready_with_gaps")
            self.assertFalse(result["submitted"])

    def test_connected_extension_uses_existing_supplement_budget_before_export(self):
        b = bundle({"https://tracker.example.org/approval": {"content":
            "The tracker publishes measurements. Download https://tracker.example.org/approval.csv"}})
        with tempfile.TemporaryDirectory() as root, \
             patch("ForecastAgent.releases.v1_0_5.collect", return_value={"release_acquisition": {"version": "1.0.5"}}), \
             patch("ForecastAgent.releases.v1_0_5.supplement", return_value=b), \
             patch("ForecastAgent.intelligence.repair.read_candidate") as reader:
            prior = Path(root) / "retrieval/release-1.0.5/supplement"
            save(prior / "manifest.json", {"protocol": "supplement_v1", "ids": ["1"], "http_limit_per_task": 2})
            save(prior / "tasks/1/supplement.json", {"task_id": "1", "attempts": [
                {"method": "http", "status": "failed", "url": "https://tracker.example.org/old"}]})
            page = {"content": "date,approval\n2026-10-09,38.2", "content_type": "text/csv"}
            reader.return_value = {"page": page, "body_diagnostics": inspect(page)}
            result = collect(question(), root, clock_utc="2026-10-10T00:00:00Z")
            self.assertEqual(reader.call_count, 1)
            self.assertIn("https://tracker.example.org/approval.csv", result["view"]["pages"])
            self.assertEqual(result["report"]["data_recovery"]["remaining_http_slots"], 0)
            self.assertFalse(result["analysis_started"])


class RepairTests(TestCase):
    def setup_budget(self, directory, attempts=None):
        root = Path(directory) / "prior"
        save(root / "manifest.json", {"protocol": "supplement_v1", "ids": ["1"], "http_limit_per_task": 2})
        save(root / "tasks/1/supplement.json", {"task_id": "1", "attempts": attempts or []})
        return {"prior_manifest": root / "manifest.json", "prior_ledger": root / "tasks/1/supplement.json"}

    def candidate_bundle(self):
        return bundle({"https://tracker.example.org/approval": {"content":
            "The tracker publishes current measurements. Download https://tracker.example.org/approval.csv"}})

    def test_recovery_reuses_capture_and_consumes_only_remaining_original_slots(self):
        with tempfile.TemporaryDirectory() as root:
            prior = self.setup_budget(root, [{"method": "http", "status": "failed", "url": "https://tracker.example.org/old"}])
            ledger_before = Path(prior["prior_ledger"]).read_bytes()
            calls = []
            def capture(url):
                calls.append(url)
                return {"content": "date,approval\n2026-10-09,38.2", "content_type": "text/csv", "retrieved_at_utc": "2026-10-10T00:00:00Z"}
            original = self.candidate_bundle()
            frozen = copy.deepcopy(original)
            folder = Path(root) / "repair"
            overlay, report = recover(original, folder, **prior, network=True, capture=capture)
            resumed, again = recover(original, folder, **prior, network=True, capture=capture)
            self.assertEqual(len(calls), 1)
            self.assertEqual(report["remaining_http_slots"], 0)
            self.assertEqual(overlay, resumed)
            self.assertEqual(original, frozen)
            self.assertEqual(Path(prior["prior_ledger"]).read_bytes(), ledger_before)
            self.assertEqual(again["own_http_reservations"], 1)
            entry = next(iter(load(folder / "state.json")["captures"].values()))
            save(folder / entry["file"], {"content": "Tampered"})
            with self.assertRaisesRegex(ValueError, "integrity failure"):
                recover(original, folder, **prior, network=True, capture=capture)

    def test_interrupted_reservation_is_not_automatically_retried(self):
        with tempfile.TemporaryDirectory() as root:
            prior = self.setup_budget(root, [{"method": "http", "status": "reserved", "url": "https://tracker.example.org/old"}])
            folder = Path(root) / "repair"
            def interrupted(url):
                raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                recover(self.candidate_bundle(), folder, **prior, network=True, capture=interrupted)
            with patch("ForecastAgent.intelligence.repair.fetch_document", side_effect=AssertionError("Unexpected I/O")):
                _, report = recover(self.candidate_bundle(), folder, **prior, network=True,
                                    capture=lambda url: self.fail("Reserved call was repeated"))
            self.assertEqual(report["unknown_reservations"], 1)
            self.assertEqual(report["remaining_http_slots"], 0)

    def test_exhausted_or_missing_parent_budget_cannot_create_an_allowance(self):
        with tempfile.TemporaryDirectory() as root:
            prior = self.setup_budget(root, [{"method": "http", "status": "failed", "url": "https://tracker.example.org/old1"},
                                            {"method": "http", "status": "reserved", "url": "https://tracker.example.org/old2"}])
            _, report = recover(self.candidate_bundle(), Path(root) / "repair", **prior, network=True,
                                capture=lambda url: self.fail("Budget exceeded"))
            self.assertEqual(report["own_http_reservations"], 0)
            Path(prior["prior_ledger"]).unlink()
            _, report = recover(self.candidate_bundle(), Path(root) / "missing", **prior, network=True,
                                capture=lambda url: self.fail("Missing ledger grants no budget"))
            self.assertEqual(report["status"], "blocked_missing_budget_ledger")

    def test_unreadable_responses_remain_gaps_and_failed_identity_uses_its_slot(self):
        with tempfile.TemporaryDirectory() as root:
            prior = self.setup_budget(root, [{"method": "http", "status": "failed", "url": "https://clinicaltrials.gov/old"}])
            b = bundle({"https://clinicaltrials.gov/study/NCT06865534": {"content": "ClinicalTrials.gov"}})
            def wrong_study(url):
                return {"content": json.dumps({"protocolSection": {"identificationModule": {"nctId": "NCT00000000"}}}), "content_type": "application/json"}
            _, report = recover(b, Path(root) / "repair", **prior, network=True, capture=wrong_study)
            self.assertEqual(report["status"], "needs_material_recovery")
            self.assertGreater(report["own_http_reservations"], 0)
            self.assertFalse(report["budget_reset"])
