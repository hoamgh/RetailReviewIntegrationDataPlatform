"""Windows offline quality gate: reviewed Harness, Google and TikTok suites."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SUITES = (
    'tests/test_harness_restrictions.py', 'tests/test_harness_browser_commands.py',
    'tests/test_harness_containment.py', 'tests/test_harness_publication.py',
    'tests/test_harness_tiktok_managed.py', 'tests/test_harness_state.py',
    'tests/test_harness_hardening.py', 'tests/test_harness_recovery.py',
    'experiments/tiktok_comment_dom/test_dom.py',
    'experiments/tiktok_comment_dom/test_replies_scroll.py',
    'experiments/tiktok_comment_dom/test_persistence.py',
    'experiments/tiktok_comment_dom/test_compaction.py',
    'experiments/tiktok_place_intelligence/test_live_dom_persistence.py',
    'experiments/tiktok_access_benchmark/test_overture_study.py',
    'experiments/tiktok_access_benchmark/test_reliability.py',
    'experiments/tiktok_access_benchmark/test_network.py', 'tests/test_google_baseline.py',
    'tests/test_architecture.py', 'tests/test_navigation_health.py',
    'tests/test_review_surface.py',
    'tests/test_browser_session.py', 'tests/test_extractor.py',
    'tests/test_paginator.py', 'tests/test_review_pane_handoff.py',
    'tests/test_smoke_test_runner.py',
)
MODULES = {p[:-3].replace('/', '.') for p in SUITES}
# Verified with guarded pytest --collect-only: 25 suites, 528 cases.
MINIMUM_CASES = 528


def project_report(raw, exit_code, elapsed):
    """Allowlist projection: no captured output, traceback, properties or paths."""
    document = ET.Element('testsuites')
    suite = ET.SubElement(document, 'testsuite', name='windows-offline')
    summary = {'pytest_exit_code': exit_code, 'elapsed_seconds': round(elapsed, 3),
               'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0,
               'missing_suites': sorted(MODULES), 'gate_passed': False}
    seen = set()
    try:
        parsed = ET.parse(raw).getroot()
        for index, case in enumerate(parsed.iter('testcase')):
            module = case.get('classname', '')
            if module not in MODULES:
                module = 'UNEXPECTED_SUITE'
            else:
                seen.add(module)
            # Parameter values may contain synthetic secret-test payloads.
            name = re.sub(r'[^a-zA-Z0-9_]', '_', case.get('name', '').split('[', 1)[0])[:160]
            try:
                duration = max(0, float(case.get('time', 0)))
                if not duration < float('inf'):
                    duration = 0
            except ValueError:
                duration = 0
            safe = ET.SubElement(suite, 'testcase', classname=module,
                name=f'{name}__case_{index}', time=f'{duration:.6f}')
            summary['tests'] += 1
            for tag, counter in [('failure', 'failures'), ('error', 'errors'), ('skipped', 'skipped')]:
                if case.find(tag) is not None:
                    summary[counter] += 1
                    child = ET.SubElement(safe, tag, type='OfflineRegressionOutcome')
                    child.text = 'Reproduce this test locally. Raw output is intentionally excluded.'
    except (OSError, ET.ParseError):
        summary['errors'] += 1
    summary['missing_suites'] = sorted(MODULES - seen)
    summary['gate_passed'] = bool(exit_code == 0 and summary['tests'] >= MINIMUM_CASES
        and not summary['missing_suites'] and not any(summary[k] for k in ('failures','errors','skipped')))
    for key in ('tests','failures','errors','skipped'):
        suite.set(key, str(summary[key]))
    return document, summary


def run(reports, dependency_root=None):
    reports = Path(reports).resolve()
    reports.mkdir(parents=True, exist_ok=False)  # Never overwrite an old run.
    summary = {'gate_passed': False, 'stage': 'preflight', 'python': platform.python_version(),
               'os': platform.system(), 'os_release': platform.release(),
               'os_build': platform.version(), 'architecture_bits': struct.calcsize('P') * 8}
    start = time.monotonic()
    try:
        if os.name != 'nt' or sys.version_info[:3] != (3,12,7) or struct.calcsize('P') != 8:
            summary['error'] = 'UNSUPPORTED_WINDOWS_PYTHON_PROFILE'
            raise RuntimeError('UNSUPPORTED_WINDOWS_PYTHON_PROFILE')
        if sys.prefix != sys.base_prefix:
            # Windows venv python.exe is a redirector: the retained CreateProcess
            # identity can differ from the real interpreter/pipe owner. Never
            # weaken C2a identity checks to accommodate this environment.
            summary['error'] = 'UNSUPPORTED_REDIRECTING_VENV'
            raise RuntimeError('UNSUPPORTED_REDIRECTING_VENV')
        if dependency_root is not None:
            dependency_root = Path(dependency_root).resolve()
            if not dependency_root.is_dir():
                raise RuntimeError('DEPENDENCY_DIRECTORY_MISSING')
            sys.path.insert(0, str(dependency_root))
        if any(not (ROOT / path).is_file() for path in SUITES):
            raise RuntimeError('REQUIRED_SUITE_MISSING_FROM_CHECKOUT')
        work = Path(tempfile.mkdtemp(prefix='hci-', dir=os.environ.get('RUNNER_TEMP') or None))
        raw = work / 'junit-raw.xml'
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
                   PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
        # No local personal PYTHONPATH or pytest selection/settings leakage.
        env['PYTHONPATH'] = str(ROOT / 'scripts/ci/offline_guard') + os.pathsep + str(ROOT)
        if dependency_root is not None:
            env['PYTHONPATH'] += os.pathsep + str(dependency_root)
        env.pop('PYTEST_ADDOPTS', None)
        command = [sys.executable, '-B', '-m', 'pytest', '-q', '--import-mode=importlib',
                   '-p', 'no:cacheprovider', '--basetemp', str(work/'t'),
                   '--junitxml', str(raw), '--durations=20', *SUITES]
        summary['stage'] = 'regression'
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
        safe_xml, outcomes = project_report(raw, result.returncode, time.monotonic()-start)
        summary.update(outcomes)
        ET.ElementTree(safe_xml).write(reports/'junit.xml', encoding='utf-8', xml_declaration=True)
    except Exception:
        # Never serialize exceptions, arbitrary environment or dependency URLs.
        summary['gate_passed'] = False
        summary.setdefault('error', 'CI_GATE_INFRASTRUCTURE_OR_PREFLIGHT_FAILURE')
    summary['elapsed_seconds'] = round(time.monotonic()-start, 3)
    summary['dependency_versions'] = {}
    for name in ('pytest','seleniumbase','selenium','crawlee','pyarrow','numpy','pandas','shapely','psutil'):
        try:
            summary['dependency_versions'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            summary['dependency_versions'][name] = 'MISSING'
    (reports/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    passed = summary['gate_passed']
    (reports/'summary.md').write_text(
        '# Windows offline quality gate\n\n'
        f"Result: {'PASS' if passed else 'FAIL'}. "
        f"Tests: {summary.get('tests',0)}; failures: {summary.get('failures',0)}; "
        f"errors: {summary.get('errors',0)}; skipped: {summary.get('skipped',0)}.\n\n"
        f"Elapsed: {summary['elapsed_seconds']:.3f}s. Python: {summary['python']}.\n\n"
        'Only sanitized outcomes and timings are published. No fixture DB, profile, '
        'cookie, screenshot, raw JUnit output or crawl evidence is included.\n', encoding='utf-8')
    print(f"CI-01 {'PASS' if passed else 'FAIL'}: {summary.get('tests',0)} tests; {summary['elapsed_seconds']}s")
    return 0 if passed else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--dependency-root', type=Path,
        help='Local only: reuse hash-locked site-packages with a direct Windows interpreter')
    args = parser.parse_args()
    raise SystemExit(run(args.reports,args.dependency_root))
