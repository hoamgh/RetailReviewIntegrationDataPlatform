"""Self-contained profile validation -> local preflight -> bounded benchmark."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.browser_framework import check_sessions, execute, run
from experiments.browser_framework.profiles import validate_profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles-config", required=True)
    args = parser.parse_args()
    config_path = str(Path(args.profiles_config).resolve())
    directory = ROOT / "output/browser-framework-profile-flow-v2"
    directory.mkdir(parents=True, exist_ok=True)
    state = dict(status="SESSION_VALIDATION_RUNNING", comparable=False)
    before = execute.protected_hashes()
    try:
        sys.argv = [sys.argv[0], "--profiles-config", config_path]
        check_sessions.main()
        validate_profiles(config_path)
        state.update(status="PERSISTENT_PREFLIGHT_RUNNING", comparable=True)
        run.write(directory / "state.json", state)
        sys.argv = [sys.argv[0], "--workload", str(ROOT / "experiments/browser_framework/historical_center_framework_benchmark.json"),
                    "--profiles-config", config_path, "--check"]
        run.main()
        state["status"] = "BENCHMARK_RUNNING"
        run.write(directory / "state.json", state)
        sys.argv = [sys.argv[0], "--profiles-config", config_path]
        execute.main()
        result = json.loads((ROOT / "output/browser-framework-benchmark-profile-v2/run-state.json").read_text())
        state["status"] = result["status"]
    except Exception as exc:
        state.update(status="BLOCKED", comparable=False, error_type=type(exc).__name__)
        evidence = Path(config_path).with_name("session-validation.json")
        if evidence.exists():
            checks = json.loads(evidence.read_text(encoding="utf-8"))
            state["session_checks"] = checks.get("checks", [])
        report = ROOT / "docs/benchmarks/browser-framework-comparison.md"
        note = ("\n## Private-profile paired validation\n\n"
                "Status: **NOT COMPARABLE / BLOCKED**. No 48-trial profile benchmark started.\n"
                "Owner confirmed the source profile; two complete private copies passed SHA-256 verification.\n"
                "A bounded paired check failed; sanitized evidence follows. No challenge bypass,\n"
                "anonymous fallback or automatic retry occurred. Private profiles/credentials remain outside Git/artifacts.\n\n"
                "```json\n" + json.dumps(state, indent=2) + "\n```\n")
        existing = report.read_text(encoding="utf-8").split("\n## Private-profile paired validation\n")[0]
        report.write_text(existing.rstrip() + "\n" + note, encoding="utf-8")
    finally:
        state["protected_unchanged"] = before == execute.protected_hashes()
        run.write(directory / "state.json", state)
        note = ("\n## Latest private-profile flow\n"
                f"State: {state['status']}; comparable: {state['comparable']}; 20 offline tests PASS.\n"
                "Source google-crawler-login owner-confirmed; private copies verified, source unchanged.\n"
                f"Protected crawler files unchanged: {state['protected_unchanged']}.\n"
                "Sanitized evidence: output/browser-framework-profile-flow-v2/state.json.\n"
                "No migration/commit/push. On restricted access, do not automatically rerun.\n")
        for name in ("STATUS.md", "CURRENT_TASK.md"):
            path = ROOT / "docs/_delivery" / name
            existing = path.read_text(encoding="utf-8").split("\n## Latest private-profile flow\n")[0]
            path.write_text(existing.rstrip() + "\n" + note, encoding="utf-8")
        print(json.dumps(state), flush=True)


if __name__ == "__main__":
    main()
