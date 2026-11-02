"""Manual 48-trial runner. No live work occurs on import or --check."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.browser_framework.adapters import (
    PlaywrightBrowser, SeleniumBaseBrowser, SourceBlocked, NavigationFailed, collect)


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def optional_digest(path):
    """An absent production driver is a state to preserve, not a dependency."""
    try:
        return digest(path)
    except FileNotFoundError:
        return None


def validate(workload):
    from urllib.parse import urlsplit
    from crawl_experiment.sources.google_maps.place_resolution import google_identity
    if (workload.get("fixture_id") != "historical_center_framework_benchmark"
            or workload.get("benchmark_scope") != "browser_framework"
            or workload.get("polygon_filter") is not False
            or workload.get("historical_results_are_ground_truth") is not False
            or workload.get("concurrency") != 1 or workload.get("repeats") != 3):
        raise ValueError("Historical framework benchmark requires no polygon filter, no historical ground truth, concurrency 1 and three repeats")
    expected_anchor = dict(name="center", lat=10.776223449846349, lng=106.69714944271055)
    if workload.get("anchor") != expected_anchor:
        raise ValueError("Historical center coordinates must remain unchanged")
    cases = workload["cases"]
    if len(cases) != 8 or sum(c["kind"] == "discovery" for c in cases) != 5:
        raise ValueError("Exactly five discovery configurations and three review stores required")
    if len({c["id"] for c in cases}) != 8:
        raise ValueError("Case IDs must be unique")
    expected_queries = ["quán ăn", "nhà hàng", "restaurant", "cafe", "quán cơm"]
    if [(c["id"], c.get("query")) for c in cases if c["kind"] == "discovery"] != [
            (f"D{i:02d}", query) for i, query in enumerate(expected_queries, 1)]:
        raise ValueError("Discovery must use the five approved historical queries in order")
    for case in cases:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", case["id"]):
            raise ValueError("Use simple non-sensitive case IDs")
        url = urlsplit(case["url"])
        if (url.scheme != "https" or url.hostname != "www.google.com"
                or not url.path.startswith("/maps/") or url.username or url.password
                or url.port is not None):
            raise ValueError("Use public https://www.google.com/maps/ URLs only")
        if case["kind"] == "reviews":
            if type(case.get("max_reviews")) is not int or case["max_reviews"] != 20:
                raise ValueError("Review limit must be exactly 20")
            count = case.get("current_displayed_review_count")
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("Current displayed count must be a non-negative integer or UNKNOWN/null")
            if not google_identity(case["url"])[0]:
                raise ValueError("Review URL must contain stable Google ID")
            if case.get("google_place_id") != google_identity(case["url"])[0]:
                raise ValueError("Review URL and locked place identity differ")
            for key, value in case.get("reference_hashes", {}).items():
                if not key or not re.fullmatch("[0-9a-f]{64}", value):
                    raise ValueError("Reference accuracy hashes must be SHA-256")
        elif case["kind"] == "discovery":
            if case.get("zoom") != 15 or case.get("max_places") != 40:
                raise ValueError("Discovery requires zoom 15 and limit 40")
            if case.get("anchor") != workload.get("anchor"):
                raise ValueError("Discovery anchors differ")
            from urllib.parse import quote
            anchor = case["anchor"]
            expected = ("https://www.google.com/maps/search/" + quote(case["query"], safe="") +
                        f"/@{anchor['lat']},{anchor['lng']},15z?hl=en")
            if case["url"] != expected:
                raise ValueError("Discovery URL does not match locked query/anchor/zoom")
        else:
            raise ValueError("Unknown case kind")
    if workload.get("anchor_resolution") != "CONFIRMED":
        raise ValueError("Anchor scope conflict unresolved; no live run allowed")
    return cases


def schedule(cases):
    # Paired order alternates by repeat AND case to reduce order bias.
    for repeat in range(3):
        for i, case in enumerate(cases):
            names = ("seleniumbase", "playwright")
            if (repeat + i) % 2:
                names = names[::-1]
            for name in names:
                yield f"r{repeat + 1}-{case['id']}-{name}", name, case


def environment(chrome, driver_path=None):
    import seleniumbase
    production_driver = Path(seleniumbase.__file__).parent / "drivers" / "chromedriver.exe"
    driver = Path(driver_path or ROOT / "output/browser-framework-env/chromedriver-156.0.8078.73/chromedriver.exe").resolve()
    if not Path(chrome).is_file() or not driver.is_file():
        raise ValueError("Existing Chrome and cached ChromeDriver required; no automatic installation")
    # Windows version resource, without launching a browser or making network requests.
    version = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command",
         "(Get-Item -LiteralPath $env:FNB_BENCH_CHROME).VersionInfo.ProductVersion"],
        env={**os.environ, "FNB_BENCH_CHROME": str(Path(chrome).resolve())},
        text=True, timeout=15).strip()
    driver_version = subprocess.check_output([str(driver), "--version"],
                                             text=True, timeout=10).split()[1]
    versions = {name: importlib.metadata.version(name)
                for name in ("playwright", "seleniumbase", "psutil")}
    import psutil
    result = dict(python=platform.python_version(), platform=platform.platform(),
                  packages=versions, chrome=version, driver_version=driver_version,
                  chrome_path=str(Path(chrome).resolve()), driver_path=str(driver),
                  production_driver_path=str(production_driver),
                  production_driver_sha256=optional_digest(production_driver),
                  chrome_sha256=digest(Path(chrome)), driver_sha256=digest(driver),
                  logical_cpus=psutil.cpu_count(), ram_bytes=psutil.virtual_memory().total,
                  uc=False, headless=False, locale="en-US", window="1280,900")
    if version.split(".")[0] != driver_version.split(".")[0]:
        raise ValueError(f"Chrome {version} / cached ChromeDriver {driver_version} incompatible; "
                         "select a matching installed browser/driver before benchmarking")
    return result


def trial(plan, destination):
    framework = plan["framework"]
    browser = (PlaywrightBrowser if framework == "playwright" else SeleniumBaseBrowser)(
        plan["chrome"], plan["environment"]["driver_version"], plan["environment"]["driver_path"], plan["profile_path"])
    started = time.monotonic()
    result = dict(framework=framework, case_id=plan["case"]["id"],
                  kind=plan["case"]["kind"], started_at=datetime.now(timezone.utc).isoformat())
    try:
        startup = time.monotonic()
        result["browser_version"] = browser.start()
        if result["browser_version"] != plan["environment"]["chrome"]:
            raise RuntimeError("Browser version differs from frozen preflight")
        result["startup_seconds"] = time.monotonic() - startup
        result.update(collect(browser, plan["case"]))
    except SourceBlocked as exc:
        result.update(status="source_blocked", reason=str(exc), access_state=str(exc))
    except NavigationFailed as exc:
        result.update(status="navigation_failed", access_state="NAVIGATION_FAILED", error_type=str(exc))
    except Exception as exc:
        # Do not persist arbitrary driver messages, URLs, cookies, or raw review text.
        result.update(status="timeout" if "timeout" in type(exc).__name__.lower()
                      else "framework_error", error_type=type(exc).__name__)
    finally:
        try:
            browser.close()
        except Exception as exc:
            result["cleanup_error_type"] = type(exc).__name__
        result["total_seconds"] = time.monotonic() - started
        write(destination, result)


class Samples:
    """Facade for reusing the existing sampler without touching its harness."""
    def __init__(self):
        self.config = {"configured_concurrency": 1}
        self.run_id = "browser-framework-comparison"
        self.rows = []

    def active_workers(self):
        return 1

    def pending_count(self):
        return 0


def stop_owned(process):
    """Only descendants of this trial, never existing user browser processes."""
    import psutil
    try:
        parent = psutil.Process(process.pid)
        owned = parent.children(recursive=True) + [parent]
    except psutil.NoSuchProcess:
        return
    for item in owned:
        try:
            item.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(owned, timeout=5)


def metrics(rows):
    memory = [r["crawler_process_rss_mb"] + r["browser_rss_mb_total"] for r in rows]
    cpu = [r["crawler_tree_cpu_percent"] for r in rows
           if r["crawler_tree_cpu_percent"] is not None]
    return dict(sampled_peak_tree_rss_mb=max(memory, default=None),
                mean_host_normalized_cpu_percent=sum(cpu) / len(cpu) if cpu else None,
                resource_samples=len(rows),
                sample_errors=sum(r["sample_error_count"] for r in rows))


def summary(results):
    import statistics
    import math
    def p95(values):
        return sorted(values)[math.ceil(.95 * len(values)) - 1] if values else None
    groups = {}
    for row in results:
        groups.setdefault((row["framework"], row["kind"]), []).append(row)
    aggregates = []
    for (framework, kind), rows in groups.items():
        measured = [r for r in rows if r.get("status") == "observed"]
        measured_seconds = sum(r["total_seconds"] for r in measured)
        aggregates.append(dict(
            framework=framework, kind=kind, trials=len(rows), observed=len(measured),
            total_seconds=sum(r.get("total_seconds") or 0 for r in rows),
            median_observed_seconds=statistics.median(r["total_seconds"] for r in measured) if measured else None,
            p95_observed_seconds=p95([r["total_seconds"] for r in measured]),
            p95_method="nearest_rank",
            extracted_item_observations=sum(len(r.get("items", {})) for r in rows),
            startup_median_seconds=statistics.median([r["startup_seconds"] for r in rows if "startup_seconds" in r]) if any("startup_seconds" in r for r in rows) else None,
            sampled_peak_tree_rss_mb=max((r["sampled_peak_tree_rss_mb"] for r in rows if r.get("sampled_peak_tree_rss_mb") is not None), default=None),
            framework_errors=sum(r["status"] in ("framework_error", "workflow_error") for r in rows),
            timeouts=sum(r["status"] == "timeout" for r in rows),
            resolution_errors=sum(r["status"] == "resolution_error" for r in rows),
            navigation_failures=sum(r["status"] == "navigation_failed" for r in rows),
            access_states={state: sum(r.get("access_state") == state for r in rows)
                           for state in ("LIMITED_VIEW", "CHALLENGE", "BLOCKED", "NAVIGATION_FAILED")},
            source_blocks=sum(r["status"] == "source_blocked" for r in rows),
            observed_nonempty_rate=len(measured) / len(rows),
            success_rate=sum(r.get("target_attainment") == 1 if kind == "reviews"
                             else r.get("status") == "observed" for r in rows) / len(rows),
            throughput_items_per_second=(sum(len(r.get("items", {})) for r in measured) /
                                         measured_seconds if measured_seconds else None),
            unique_items=len({key for r in rows for key in r.get("items", {})})))
    pairs = []
    for case in sorted({r["case_id"] for r in results}):
        left = {k for r in results if r["case_id"] == case and r["framework"] == "seleniumbase" for k in r.get("items", {})}
        right = {k for r in results if r["case_id"] == case and r["framework"] == "playwright" for k in r.get("items", {})}
        pairs.append(dict(case_id=case, intersection=len(left & right), union=len(left | right),
                          jaccard=len(left & right) / len(left | right) if left | right else None))
    return dict(aggregates=aggregates, case_identity_overlap=pairs,
                recommendation="Need More Evidence",
                caveat="Identity overlap is agreement, not independent accuracy. Inspect trial completeness/reference hashes.")


def run(args):
    workload_path = Path(args.workload).resolve()
    workload = json.loads(workload_path.read_text(encoding="utf-8"))
    cases = validate(workload)
    from experiments.browser_framework.profiles import validate_profiles
    profiles = validate_profiles(args.profiles_config,
                                 require_verified=not args.diagnostic_unverified_sessions)
    env = environment(args.chrome, args.driver)
    env["profile_mode"] = "persistent"
    env["profile_config_sha256"] = digest(Path(args.profiles_config))
    env["comparability"] = ("NOT COMPARABLE" if args.diagnostic_unverified_sessions
                            else "SESSION_VALIDATED")
    fingerprint = hashlib.sha256(json.dumps(dict(
        workload=workload, environment=env,
        code={str(p.relative_to(ROOT)): digest(p) for p in [
            Path(__file__).resolve(), Path(__file__).with_name("adapters.py"),
            Path(__file__).with_name("profiles.py"),
            ROOT / "crawl_experiment/sources/google_maps/extractor.py",
            ROOT / "crawl_experiment/sources/google_maps/selectors.py",
            ROOT / "crawl_experiment/sources/google_maps/health.py",
            ROOT / "crawl_experiment/sources/google_maps/place_resolution.py",
            ROOT / "crawl_experiment/core/models.py",
            ROOT / "crawl_experiment/observability/crawler_benchmark.py"]}), sort_keys=True).encode()).hexdigest()
    if args.check:
        # Verify actual headed launches against local content, never Google Maps.
        previous_directory = Path.cwd()
        preflight_directory = ROOT / "output/browser-framework-env/preflight"
        preflight_directory.mkdir(parents=True, exist_ok=True)
        observed = {}
        viewports = {}
        try:
            os.chdir(preflight_directory)
            for name, cls in (("seleniumbase", SeleniumBaseBrowser), ("playwright", PlaywrightBrowser)):
                browser = cls(env["chrome_path"], env["driver_version"], env["driver_path"], profiles[name])
                try:
                    observed[name] = browser.start()
                    if observed[name] != env["chrome"]:
                        raise ValueError("Actual browser version mismatch")
                    viewports[name] = browser.viewport()
                    if viewports[name] != dict(width=1280, height=900):
                        raise ValueError(f"{name} actual viewport {viewports[name]} differs from frozen 1280x900")
                    browser.go("data:text/html,<title>Offline benchmark preflight</title><body>offline fixture</body>")
                finally:
                    browser.close()
        finally:
            os.chdir(previous_directory)
        if optional_digest(Path(env["production_driver_path"])) != env["production_driver_sha256"]:
            raise ValueError("Production driver changed during preflight")
        env["actual_headed_browser_versions"] = observed
        env["actual_viewports"] = viewports
        write(ROOT / "output/browser-framework-env/preflight.json", env)
        print(json.dumps(dict(status="PREFLIGHT_PASS", trials=48, environment=env), indent=2))
        return
    directory = Path(args.output).resolve()
    if not directory.is_relative_to(ROOT / "output"):
        raise ValueError("Benchmark artifacts must stay under ignored output/")
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "run.lock"
    handle = lock.open("x", encoding="utf-8")
    handle.write(str(os.getpid()))
    handle.close()
    from crawl_experiment.observability.crawler_benchmark import ResourceSampler
    results = []
    try:
        manifest = directory / "manifest.json"
        if manifest.exists() and json.loads(manifest.read_text())["fingerprint"] != fingerprint:
            raise ValueError("Inputs/code/environment changed; use a new output directory")
        write(manifest, dict(fingerprint=fingerprint, environment=env, workload=workload,
                             comparability=env["comparability"]))
        for key, framework, case in schedule(cases):
            result_path = directory / (key + ".json")
            if result_path.exists():
                result = json.loads(result_path.read_text())
                results.append(result)
                if result["status"] == "source_blocked":
                    break
                if not result.get("resource_metrics_verified"):
                    raise ValueError("Partial trial retained; review it before starting a new run directory")
                continue
            plan = directory / (key + ".plan.json")
            write(plan, dict(framework=framework, case=case, environment=env, chrome=args.chrome,
                             profile_path=str(profiles[framework])))
            worker_started = time.monotonic()
            child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                                      "--trial", str(plan), "--result", str(result_path)],
                                     cwd=directory, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            samples = Samples()
            sampler = ResourceSampler(samples, pid=child.pid)
            # Each trial has a hard wall timeout. This is the self-contained manual job,
            # not agent polling. No framework/browser retries on timeout or challenge.
            deadline = time.monotonic() + 120
            timed_out = False
            try:
                while child.poll() is None:
                    if time.monotonic() >= deadline:
                        timed_out = True
                        stop_owned(child)
                        break
                    try:
                        samples.rows.append(sampler.sample())
                    except sampler.psutil.NoSuchProcess:
                        break
                    time.sleep(1)
            finally:
                if child.poll() is None:
                    stop_owned(child)
                child.wait(timeout=10)
                # A crashed driver may leave an already sampled descendant alive.
                # psutil Process objects guard against PID reuse on kill().
                survivors = []
                for owned in sampler.processes.values():
                    try:
                        if owned.is_running():
                            owned.kill()
                            survivors.append(owned)
                    except sampler.psutil.NoSuchProcess:
                        pass
                sampler.psutil.wait_procs(survivors, timeout=5)
            result = (json.loads(result_path.read_text()) if result_path.exists()
                      else dict(framework=framework, case_id=case["id"], kind=case["kind"],
                                status="timeout" if timed_out else "framework_error",
                                total_seconds=120 if timed_out else None))
            if timed_out:
                result.update(status="timeout", reason="hard_worker_deadline")
            result.update(metrics(samples.rows))
            result.update(process_wall_seconds=time.monotonic() - worker_started,
                          resource_metrics_verified=True)
            write(result_path, result)
            write(directory / (key + ".resources.json"), samples.rows)
            results.append(result)
            write(directory / "summary.json", summary(results))
            with (directory / "progress.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(dict(trial=key, status=result["status"],
                                             completed=len(results), planned=48)) + "\n")
            print(f"{len(results)}/48 {key}: {result['status']}", flush=True)
            if result["status"] == "source_blocked" or result.get("cleanup_error_type"):
                break
            time.sleep(10)
        write(directory / "summary.json", dict(**summary(results),
              status="COMPLETE" if len(results) == 48 else "STOPPED", completed=len(results), planned=48,
              comparability=env["comparability"]))
        write(directory / "run-state.json", dict(
            status="COMPLETE" if len(results) == 48 else "STOPPED", completed=len(results), planned=48))
    except BaseException as exc:
        write(directory / "run-state.json", dict(status="BLOCKED", completed=len(results),
                                                planned=48, error_type=type(exc).__name__))
        raise
    finally:
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload")
    parser.add_argument("--chrome", default=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    parser.add_argument("--driver", default=str(ROOT / "output/browser-framework-env/chromedriver-156.0.8078.73/chromedriver.exe"))
    parser.add_argument("--output", default="output/browser-framework-benchmark")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--profiles-config", help="Private JSON with distinct benchmark copies and verified session equivalence; no credentials")
    parser.add_argument("--diagnostic-unverified-sessions", action="store_true",
                        help="Explicit diagnostic run with NOT COMPARABLE label; profile structure and restrictions still enforced")
    parser.add_argument("--trial", help=argparse.SUPPRESS)
    parser.add_argument("--result", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.trial:
        trial(json.loads(Path(args.trial).read_text(encoding="utf-8")), Path(args.result))
    elif not args.workload:
        parser.error("--workload is required")
    else:
        run(args)


if __name__ == "__main__":
    main()
