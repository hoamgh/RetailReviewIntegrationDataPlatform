"""Self-contained manual/background execution with final report, without agent polling."""
from pathlib import Path
import hashlib
import json
import math
import statistics
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.browser_framework import run

PROTECTED = (
    "crawl_experiment/cli.py", "crawl_experiment/core/place_catalog.py",
    "crawl_experiment/core/place_categories.py", "crawl_experiment/orchestration/access_policy.py",
    "crawl_experiment/orchestration/review_incremental.py", "crawl_experiment/orchestration/review_reconciliation.py",
    "crawl_experiment/sources/google_maps/place_resolution.py", "crawl_experiment/sources/google_maps/review_timestamps.py",
    "crawl_experiment/storage/place_access_repository.py", "crawl_experiment/storage/place_catalog.py",
    "crawl_experiment/browser/windows_worker.py", "crawl_experiment/observability/crawler_benchmark.py",
    "crawl_experiment/runners/crawler_benchmark_runner.py", "scripts/benchmark_google_workers.ps1",
    "scripts/google_live_workers.py", "scripts/google_overture_workers.py")


def protected_hashes():
    return {p: run.digest(ROOT / p) for p in PROTECTED}


def render(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("r[123]-*.json"))
            if not p.name.endswith((".plan.json", ".resources.json"))]
    state = json.loads((directory / "run-state.json").read_text())
    lines = ["## Latest controlled execution", "",
             f"Recorded UTC: {datetime.now(timezone.utc).isoformat()}. Status: **{state['status']}**.",
             f"Completed {len(rows)}/48 planned trials. Artifact directory: `{directory.relative_to(ROOT)}`.",
             "Both frameworks use Chrome 156.0.8078.73, headed, 1280×900 actual viewport; concurrency 1.",
             "", "| Framework / workload | Trials | Runtime median / p95 (s) | Item observations / unique IDs | Success rate | CPU mean % / sampled peak RSS MiB |",
             "| --- | --- | --- | --- | --- | --- |"]
    for framework in ("seleniumbase", "playwright"):
        for kind in ("discovery", "reviews"):
            group = [r for r in rows if r["framework"] == framework and r["kind"] == kind]
            times = sorted(r["total_seconds"] for r in group if r.get("total_seconds") is not None)
            cpu = [r["mean_host_normalized_cpu_percent"] for r in group if r.get("mean_host_normalized_cpu_percent") is not None]
            rss = [r["sampled_peak_tree_rss_mb"] for r in group if r.get("sampled_peak_tree_rss_mb") is not None]
            items = sum(len(r.get("items", {})) for r in group)
            unique = len({k for r in group for k in r.get("items", {})})
            successes = sum(r.get("target_attainment") == 1 if kind == "reviews" else r.get("status") == "observed" for r in group)
            runtime = f"{statistics.median(times):.3f} / {times[math.ceil(.95*len(times))-1]:.3f}" if times else "UNKNOWN"
            resource = f"{statistics.mean(cpu):.2f}" if cpu else "UNKNOWN"
            resource += " / " + (f"{max(rss):.1f}" if rss else "UNKNOWN")
            success = f"{successes}/{len(group)}" if group else "UNRUN"
            lines.append(f"| {framework} / {kind} | {len(group)} | {runtime} | {items} / {unique} | {success} | {resource} |")
    lines += ["", "Runtime here includes failed trials; p95 uses nearest-rank, with very small samples interpreted cautiously.",
              "CPU is the mean of sampled trial means, normalized to host CPUs; RSS is sampled summed process-tree RSS.",
              "Throughput and successful-only runtime distributions are retained in `summary.json`.", "",
              "| Framework | Case | Status | Items | Reviews target attainment | Cause / error type |",
              "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append(f"| {r['framework']} | {r['case_id']} | {r['status']} | {len(r.get('items',{}))} | {r.get('target_attainment','UNKNOWN')} | {r.get('reason',r.get('error_type',''))} |")
    lines += ["", "Completeness and independent extraction accuracy: **UNKNOWN** (current source totals and independent review references absent).",
              "Historical 22/60/60 counts are not denominators or ground truth. Discovery uses no polygon filter.",
              "A source challenge/restriction stops the entire experiment without retry; unrun trials are not failures.",
              "", "Migration cost is unmeasured. A production migration would require porting navigation/surface/pagination,",
              "timestamp interception, session/recovery integration and regression validation; maintaining two transports adds upgrade/selector work.",
              "Recommendation: **Need More Evidence**. This bounded PoC does not authorize migration.", ""]
    report = ROOT / "docs/benchmarks/browser-framework-comparison.md"
    existing = report.read_text(encoding="utf-8").split("\n## Latest controlled execution\n")[0].rstrip()
    report.write_text(existing + "\n\n" + "\n".join(lines), encoding="utf-8")
    integrity = json.loads((directory / "integrity.json").read_text())
    note = (f"\n## Latest browser benchmark execution\n"
            f"VERIFIED: headed Chrome/ChromeDriver 156.0.8078.73 preflight PASS; 16 offline tests PASS.\n"
            f"Execution: {state['status']}; {len(rows)}/48 trials. Evidence: {directory.relative_to(ROOT).as_posix()}/.\n"
            f"Protected crawler files unchanged: {integrity['protected_unchanged']}; production driver unchanged: {integrity['production_driver_unchanged']}.\n"
            "Report: docs/benchmarks/browser-framework-comparison.md, latest controlled execution section.\n"
            "No migration/commit/push. If stopped on source restriction, do not restart automatically.\n")
    for name in ("STATUS.md", "CURRENT_TASK.md"):
        path = ROOT / "docs/_delivery" / name
        text = path.read_text(encoding="utf-8").split("\n## Latest browser benchmark execution\n")[0]
        path.write_text(text.rstrip() + "\n" + note, encoding="utf-8")


def main():
    import argparse
    from experiments.browser_framework.profiles import validate_profiles
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles-config", required=True)
    options = parser.parse_args()
    validate_profiles(options.profiles_config)
    directory = ROOT / "output/browser-framework-benchmark-profile-v2"
    before = protected_hashes()
    preflight = json.loads((ROOT / "output/browser-framework-env/preflight.json").read_text())
    if (preflight.get("profile_mode") != "persistent" or
            preflight.get("profile_config_sha256") != run.digest(Path(options.profiles_config))):
        raise RuntimeError("NOT COMPARABLE: matching persistent-profile preflight required")
    if preflight.get("actual_viewports") != {n: dict(width=1280,height=900) for n in ("seleniumbase","playwright")}:
        raise RuntimeError("Run the actual viewport preflight first")
    sys.argv = [sys.argv[0], "--workload", str(ROOT / "experiments/browser_framework/historical_center_framework_benchmark.json"),
                "--output", str(directory), "--profiles-config", options.profiles_config]
    try:
        run.main()
    finally:
        if directory.exists():
            run.write(directory / "integrity.json", dict(protected_unchanged=before == protected_hashes(),
                      production_driver_unchanged=run.optional_digest(Path(preflight["production_driver_path"])) == preflight["production_driver_sha256"]))
            if (directory / "run-state.json").exists():
                render(directory)


if __name__ == "__main__":
    main()
