"""Offline tests; no Google requests, browser installation, or production writes."""
import hashlib
import json
from pathlib import Path
import unittest
import tempfile
import sys
from types import SimpleNamespace
from unittest.mock import patch

from experiments.browser_framework.adapters import (
    PlaywrightElement, SourceBlocked, NavigationFailed, collect, content_hash, guard)
from experiments.browser_framework.profiles import validate_profiles
from experiments.browser_framework.run import ROOT, environment, optional_digest, metrics, schedule, summary, validate
from crawl_experiment.core.models import Review
from crawl_experiment.sources.google_maps import selectors as S
from crawl_experiment.sources.google_maps.extractor import ReviewExtractor


def workload():
    data = json.loads((Path(__file__).with_name("historical_center_framework_benchmark.json")).read_text(encoding="utf-8"))
    return data


class Element:
    def __init__(self, text="", **attrs):
        self.text, self.attrs = text, attrs

    def get_attribute(self, name):
        return self.attrs.get(name)


class FakeBrowser:
    def __init__(self, text="", links=(), cards=()):
        self.url, self.text, self.links, self.cards = "https://www.google.com/maps/", text, links, cards
        self.scrolls = 0

    def go(self, url):
        self.url = url

    def elements(self, selector):
        if selector == "body":
            return [Element(self.text)]
        if selector == S.SEARCH_RESULT_CANDIDATES:
            return list(self.links)
        if selector == S.REVIEW_CARD:
            return list(self.cards)
        if selector in (S.REVIEW_PANE_PRIMARY, "div[role='feed']"):
            return [Element()]
        return []

    def scroll(self, element):
        self.scrolls += 1


class BenchmarkTests(unittest.TestCase):
    def test_discovery_waits_for_delayed_maps_render(self):
        class Delayed(FakeBrowser):
            checks = 0
            def elements(self, selector):
                if selector == S.SEARCH_RESULT_CANDIDATES:
                    self.checks += 1
                    if self.checks < 3:
                        return []
                    return [Element(href="https://www.google.com/maps/place/a/data=!1sChIJready!8m2")]
                return super().elements(selector)
        browser = Delayed()
        waits = []
        result = collect(browser, workload()["cases"][0], sleep=waits.append)
        self.assertIn("ChIJready", result["items"])
        self.assertGreaterEqual(waits.count(.5), 2)

    def test_discovery_unrendered_ui_is_navigation_failure(self):
        elapsed = [0]
        def wait(seconds):
            elapsed[0] += seconds
        result = collect(FakeBrowser(), workload()["cases"][0], clock=lambda:elapsed[0], sleep=wait)
        self.assertEqual(result["status"], "navigation_failed")
        self.assertEqual(result["reason"], "discovery_results_not_ready")
        self.assertEqual(elapsed[0], 20)

    def test_environment_does_not_require_production_driver(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chrome, driver = root / "chrome.exe", root / "chromedriver.exe"
            chrome.write_bytes(b"synthetic chrome")
            driver.write_bytes(b"synthetic benchmark driver")
            package = SimpleNamespace(__file__=str(root / "seleniumbase/__init__.py"))
            with patch.dict(sys.modules, {"seleniumbase": package}), patch(
                    "experiments.browser_framework.run.subprocess.check_output",
                    side_effect=["156.0.8078.73", "ChromeDriver 156.0.8078.73"]), patch(
                    "experiments.browser_framework.run.importlib.metadata.version",
                    return_value="test-fixture"), patch(
                    "experiments.browser_framework.run.platform.platform",
                    return_value="Windows-test"):
                result = environment(str(chrome), str(driver))
            self.assertIsNone(result["production_driver_sha256"])
            self.assertEqual(result["driver_path"], str(driver.resolve()))
            with patch.dict(sys.modules, {"seleniumbase": package}):
                with self.assertRaises(ValueError):
                    environment(str(chrome), str(root / "missing-isolated-driver.exe"))

    def test_optional_driver_hash_detects_creation_or_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            driver = Path(temporary) / "driver.exe"
            self.assertIsNone(optional_digest(driver))
            driver.write_bytes(b"one")
            first = optional_digest(driver)
            self.assertIsNotNone(first)
            driver.write_bytes(b"two")
            self.assertNotEqual(first, optional_digest(driver))

    def test_profile_gate_rejects_anonymous_run(self):
        with self.assertRaisesRegex(ValueError, "NOT COMPARABLE"):
            validate_profiles(None)

    def test_private_profile_pair_needs_equivalence_attestation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {"session_equivalence":"UNVERIFIED", "common_seed_id":"synthetic",
                      "verified_at":"2026-10-28"}
            for name in ("seleniumbase", "playwright"):
                profile = root / name
                (profile / "Default").mkdir(parents=True)
                (profile / "Local State").write_text("{}")
                (profile / "Default/Preferences").write_text("{}")
                config[name] = str(profile)
            path = root / "profiles.json"
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "NOT COMPARABLE"):
                validate_profiles(path)
            config["session_equivalence"] = "MANUALLY_VERIFIED"
            path.write_text(json.dumps(config))
            with patch("experiments.browser_framework.profiles.assert_available"):
                self.assertEqual(len(validate_profiles(path)), 2)
            config["playwright"] = config["seleniumbase"]
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "separate"):
                validate_profiles(path)

    def test_restriction_states_are_distinct(self):
        for text, state in (("limited view", "LIMITED_VIEW"),
                            ("unusual traffic", "CHALLENGE"), ("access denied", "BLOCKED")):
            with self.subTest(state=state), self.assertRaisesRegex(SourceBlocked, state):
                guard(FakeBrowser(text))

    def test_navigation_failure_is_not_extraction_failure(self):
        class BrokenNavigation(FakeBrowser):
            def go(self, url):
                raise TimeoutError("unlogged private diagnostic")
        with self.assertRaisesRegex(NavigationFailed, "TimeoutError"):
            collect(BrokenNavigation(), workload()["cases"][0])

    def test_frozen_inputs_match_original_artifact_evidence(self):
        data = workload()
        self.assertEqual([c["query"] for c in data["cases"][:5]],
                         ["quán ăn", "nhà hàng", "restaurant", "cafe", "quán cơm"])
        self.assertEqual([c["historical_extracted_count"] for c in data["cases"][5:]], [22, 60, 60])
        self.assertFalse(data["boundary_evidence"]["covers_anchor"])
        for case in data["cases"][5:]:
            self.assertIsNone(case["current_displayed_review_count"])
            evidence = case["identity_evidence"]
            path = Path(evidence["path"])
            if path.exists():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), evidence["sha256"])
                event = json.loads(path.read_text(encoding="utf-8").splitlines()[evidence["line"] - 1])
                self.assertEqual(event["place_key"], "overture:" + case["overture_id"])
                self.assertEqual(event["resolved_url"], case["url"])
                self.assertEqual(event["google_place_id"], case["google_place_id"])
                self.assertEqual(event["status"], "RESOLVED")
        anchor_evidence = data["anchor_evidence"]
        path = Path(anchor_evidence["path"])
        if path.exists():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), anchor_evidence["sha256"])
            center = next(r for r in json.loads(path.read_text(encoding="utf-8"))["anchors"] if r["anchor"] == "center")
            self.assertEqual((data["anchor"]["lat"], data["anchor"]["lng"]),
                             (center["anchor_lat"], center["anchor_lng"]))

    def test_valid_workload_and_48_trials(self):
        cases = validate(workload())
        trials = list(schedule(cases))
        self.assertEqual(len(trials), 48)
        self.assertEqual(len({t[0] for t in trials}), 48)
        self.assertEqual([t[1] for t in trials[:4]],
                         ["seleniumbase", "playwright", "playwright", "seleniumbase"])

    def test_example_fails_closed(self):
        data = workload()
        data["anchor_resolution"] = "UNRESOLVED"
        with self.assertRaises(ValueError):
            validate(data)

    def test_bad_inputs(self):
        for value in (0, 21, True, None):
            data = workload()
            data["cases"][5]["max_reviews"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate(data)
        for field, value in (("polygon_filter", True), ("historical_results_are_ground_truth", True),
                             ("concurrency", 2), ("repeats", 1)):
            data = workload()
            data[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(data)

    def test_bad_urls_and_duplicate_ids(self):
        for url in ("https://evil.example/maps/", "https://www.google.com/sorry/",
                    "https://user:pass@www.google.com/maps/", "http://www.google.com/maps/"):
            data = workload()
            data["cases"][0]["url"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate(data)
        data = workload()
        data["cases"][1]["id"] = "D01"
        with self.assertRaises(ValueError):
            validate(data)

    def test_restrictions_stop(self):
        for text in ("unusual traffic", "Sign in to see reviews", "Access denied",
                     "Before you continue to Google", "Limited view"):
            with self.subTest(text=text), self.assertRaises(SourceBlocked):
                guard(FakeBrowser(text))

    def test_consent_url_stops(self):
        browser = FakeBrowser()
        browser.url = "https://consent.google.com/"
        with self.assertRaises(SourceBlocked):
            guard(browser)

    def test_discovery_deduplicates_stable_ids(self):
        case = workload()["cases"][0]
        links = [Element(href="https://www.google.com/maps/place/a/data=!1sChIJone!8m2"),
                 Element(href="https://www.google.com/maps/place/b/data=!1sChIJone!8m2"),
                 Element(href="https://www.google.com/maps/place/unresolved")]
        result = collect(FakeBrowser(links=links), case, sleep=lambda _: None)
        self.assertEqual(set(result["items"]), {"ChIJone"})
        self.assertEqual(result["unresolved_links"], 1)
        self.assertEqual(result["status"], "observed")

    def test_review_completeness_and_reference_accuracy(self):
        case = workload()["cases"][5]
        reviews = [Review("google_maps", str(i), case["id"], rating=5, text="fixture") for i in range(2)]
        case["reference_hashes"] = {r.source_review_id: content_hash(r) for r in reviews}
        with patch.object(ReviewExtractor, "extract", side_effect=lambda e, _: reviews[e.attrs["index"]]):
            result = collect(FakeBrowser(cards=[Element(index=0), Element(index=1)]), case, sleep=lambda _: None)
        self.assertIsNone(result["completeness"])
        self.assertEqual(result["target_attainment"], .1)
        self.assertEqual(result["accuracy"], 1)
        self.assertNotIn("fixture", json.dumps(result))

    def test_twenty_review_hard_cap_and_unknown_total(self):
        case = workload()["cases"][5]
        cards = [Element(index=i) for i in range(25)]
        with patch.object(ReviewExtractor, "extract", side_effect=lambda e, _: Review(
                "google_maps", str(e.attrs["index"]), case["id"], rating=5)):
            result = collect(FakeBrowser(cards=cards), case, sleep=lambda _: None)
        self.assertEqual(len(result["items"]), 20)
        self.assertIsNone(result["completeness"])
        self.assertEqual(result["target_attainment"], 1)

    def test_discovery_url_must_match_frozen_anchor(self):
        data = workload()
        data["cases"][0]["url"] = "https://www.google.com/maps/search/elsewhere"
        with self.assertRaises(ValueError):
            validate(data)

    def test_wrong_store_identity_is_resolution_error(self):
        class WrongStore(FakeBrowser):
            def go(self, url):
                self.url = "https://www.google.com/maps/place/Other/data=!1sChIJwrong!8m2"
        result = collect(WrongStore(), workload()["cases"][5])
        self.assertEqual(result["status"], "resolution_error")

    def test_missing_reviews_entry_not_success(self):
        ticks = iter([0, 0, 30])
        result = collect(FakeBrowser(), workload()["cases"][5],
                         clock=lambda: next(ticks), sleep=lambda _: None)
        self.assertEqual(result["status"], "workflow_error")

    def test_summary_distinguishes_source_and_framework(self):
        rows = [dict(framework="playwright", kind="reviews", case_id="a", status=s,
                     total_seconds=None, items={}) for s in ("source_blocked", "timeout", "framework_error")]
        result = summary(rows)["aggregates"][0]
        self.assertEqual((result["source_blocks"], result["timeouts"], result["framework_errors"]), (1, 1, 1))
        self.assertEqual(result["observed"], 0)

    def test_empty_resource_metrics_are_unknown(self):
        self.assertIsNone(metrics([])["sampled_peak_tree_rss_mb"])
        self.assertIsNone(metrics([])["mean_host_normalized_cpu_percent"])

    def test_local_playwright_dom_uses_real_production_extractor(self):
        # Isolated synthetic DOM only. All page requests aborted; never visits Google.
        from playwright.sync_api import sync_playwright
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        if not chrome.exists():
            self.skipTest("Installed Chrome required for local DOM integration")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(chrome), headless=True,
                                       args=["--disable-background-networking"])
            try:
                context = browser.new_context(service_workers="block")
                context.route("**/*", lambda route: route.abort())
                page = context.new_page()
                page.set_content(Path(__file__).with_name("fixtures").joinpath("reviews.html").read_text())
                cards = page.locator(S.REVIEW_CARD)
                first = ReviewExtractor().extract(PlaywrightElement(cards.nth(0)), "fixture-store")
                second = ReviewExtractor().extract(PlaywrightElement(cards.nth(1)), "fixture-store")
                self.assertEqual((first.source_review_id, first.rating, first.text),
                                 ("fixture-review-1", 5, "A synthetic fixture review."))
                self.assertEqual(second.rating, 3)
                self.assertIsNone(second.text)
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
