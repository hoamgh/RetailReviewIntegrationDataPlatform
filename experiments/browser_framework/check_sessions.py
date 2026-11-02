"""One bounded paired session check, never an automatic challenge retry."""
from pathlib import Path
import argparse
import json
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.browser_framework.profiles import validate_profiles
from experiments.browser_framework.adapters import PlaywrightBrowser, SeleniumBaseBrowser, SourceBlocked, NavigationFailed, collect
from experiments.browser_framework.run import environment, digest, validate, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles-config", required=True)
    args = parser.parse_args()
    config_path = Path(args.profiles_config).resolve()
    profiles = validate_profiles(config_path, require_verified=False)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not config.get("seed_owner_confirmed") or not config.get("seed_copy_integrity_verified"):
        raise ValueError("Owner-confirmed common seed and verified private copies required")
    evidence = config_path.with_name("session-validation.json")
    if evidence.exists():
        raise ValueError("Session evidence already exists; no automatic retry or overwrite")
    env = environment(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    workload = json.loads((ROOT / "experiments/browser_framework/historical_center_framework_benchmark.json").read_text(encoding="utf-8"))
    cases = validate(workload)
    selected = [next(c for c in cases if c["id"] == key) for key in ("R01", "D01")]
    result = dict(status="NOT COMPARABLE", common_seed_id=config["common_seed_id"],
                  checked_at=datetime.now(timezone.utc).isoformat(), checks=[])
    # Protect this check from parallel duplicate starts; only private files are written.
    lock = config_path.with_name("session-check.lock")
    with lock.open("x") as stream:
        stream.write("bounded-session-check")
    import os
    previous = Path.cwd()
    try:
        os.chdir(config_path.parent)
        for name, cls in (("seleniumbase", SeleniumBaseBrowser), ("playwright", PlaywrightBrowser)):
            browser = cls(env["chrome_path"], env["driver_version"], env["driver_path"], profiles[name])
            try:
                if browser.start() != env["chrome"] or browser.viewport() != dict(width=1280, height=900):
                    raise ValueError("Browser/viewport differs")
                for case in selected:
                    observation = collect(browser, case)
                    check = dict(framework=name, case_id=case["id"], status=observation["status"],
                                 items=len(observation.get("items", {})),
                                 target_attainment=observation.get("target_attainment"))
                    result["checks"].append(check)
                    write(evidence, result)
                    if observation["status"] != "observed" or (case["kind"] == "reviews" and observation.get("target_attainment") != 1):
                        return
            except (SourceBlocked, NavigationFailed) as exc:
                result["checks"].append(dict(framework=name, status="source_blocked" if isinstance(exc, SourceBlocked) else "navigation_failed",
                                             access_state=str(exc) if isinstance(exc, SourceBlocked) else "NAVIGATION_FAILED"))
                return
            except Exception as exc:
                result["checks"].append(dict(framework=name, status="validation_failed", error_type=type(exc).__name__))
                return
            finally:
                browser.close()
        result["status"] = "PASS"
    finally:
        os.chdir(previous)
        write(evidence, result)
        if result["status"] == "PASS":
            config.update(session_equivalence="BOUNDED_ACCESS_VERIFIED", verified_at=result["checked_at"],
                          validation_sha256=digest(evidence))
            write(config_path, config)
        lock.unlink()
        print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
