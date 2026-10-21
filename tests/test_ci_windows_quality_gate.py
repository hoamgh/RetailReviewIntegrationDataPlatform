"""CI plumbing invariants, no crawler or source network calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_gate', ROOT/'scripts/ci/windows_quality_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_workflow_required_windows_offline_contract():
    workflow = yaml.safe_load((ROOT/'.github/workflows/harness-windows.yml').read_text())
    assert set(workflow['on']) == {'pull_request','push'}
    assert workflow['on']['push']['branches'] == ['main']
    assert workflow['permissions'] == {'contents':'read'}
    job = workflow['jobs']['windows-offline']
    assert job['runs-on'] == 'windows-latest'
    assert job['env']['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
    steps = job['steps']
    node = next(step for step in steps if 'setup-node@' in step.get('uses', ''))
    assert node['with']['node-version'] == '24.11.0'
    assert node['with']['check-latest'] is False
    assert not any(step.get('continue-on-error') for step in steps)
    for step in steps:
        if 'uses' in step:
            assert len(step['uses'].split('@')[1]) == 40
    upload = next(s for s in steps if 'upload-artifact@' in s.get('uses',''))
    assert 'failure()' in upload['if'] and upload['with']['include-hidden-files'] is False
    paths = upload['with']['path'].splitlines()
    assert {Path(p).name for p in paths} == {'junit.xml','summary.json','summary.md'}
    assert not any('*' in p for p in paths)
    assert not any('secrets.' in json.dumps(step) for step in steps)


def fixture_xml(path, *, failure=False, skipped=False):
    doc = ET.Element('testsuites');suite = ET.SubElement(doc,'testsuite')
    modules = sorted(gate.MODULES)
    for index in range(gate.MINIMUM_CASES):
        case = ET.SubElement(suite,'testcase',classname=modules[index % len(modules)],
            name='test_fixture[SHOULD_NOT_PUBLISH]',time='0.01',file='private-profile/secret.py')
        ET.SubElement(case,'system-out').text='SHOULD_NOT_PUBLISH'
        ET.SubElement(case,'properties').text='SHOULD_NOT_PUBLISH'
        if index == 0 and failure:
            ET.SubElement(case,'failure',message='SHOULD_NOT_PUBLISH').text='SHOULD_NOT_PUBLISH'
        if index == 0 and skipped:ET.SubElement(case,'skipped').text='UNSUPPORTED_NATIVE_INVARIANT'
    ET.ElementTree(doc).write(path)


def test_report_projection_never_uploads_raw_evidence(tmp_path):
    raw = tmp_path/'raw.xml';fixture_xml(raw,failure=True)
    safe,summary = gate.project_report(raw,1,1.2)
    text = ET.tostring(safe).decode()
    assert 'SHOULD_NOT_PUBLISH' not in text and 'private-profile' not in text
    assert 'system-out' not in text and 'properties' not in text
    assert summary['failures'] == 1 and not summary['gate_passed']


@pytest.mark.parametrize('fault',['skip','nonzero_exit','missing_xml','missing_suite'])
def test_skips_missing_results_and_failure_never_turn_green(tmp_path,fault):
    raw = tmp_path/'raw.xml'
    if fault != 'missing_xml':fixture_xml(raw,skipped=fault=='skip')
    if fault == 'missing_suite':
        doc = ET.parse(raw)
        for case in doc.iter('testcase'):case.set('classname','tests.test_harness_state')
        doc.write(raw)
    _,summary = gate.project_report(raw,1 if fault=='nonzero_exit' else 0,1.2)
    assert not summary['gate_passed']


def test_full_non_skipped_suites_pass_projection(tmp_path):
    raw = tmp_path/'raw.xml';fixture_xml(raw)
    _,summary = gate.project_report(raw,0,1.2)
    assert summary['gate_passed'] and summary['tests']==gate.MINIMUM_CASES
    assert not summary['missing_suites']


def test_offline_guard_refuses_inet_before_connect():
    env=dict(os.environ,PYTHONPATH=str(ROOT/'scripts/ci/offline_guard'),PYTHONDONTWRITEBYTECODE='1')
    result=subprocess.run([sys.executable,'-B','-c',
        "import socket\ntry: socket.socket().connect(('127.0.0.1',9))\nexcept RuntimeError as e: print(str(e))"],
        env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and result.stdout.strip()=='CI_OFFLINE_NETWORK_REFUSED'


def test_lock_covers_frozen_production_requirements():
    from packaging.requirements import Requirement
    lock=(ROOT/'requirements/ci-windows.txt').read_text().splitlines()
    pins={}
    for line in lock:
        if not line or line.startswith(('#','--')):continue
        requirement=Requirement(line.split(' --hash=')[0])
        assert str(requirement.specifier).startswith('==') and ' --hash=sha256:' in line
        pins[requirement.name.lower().replace('_','-')]=str(requirement.specifier)[2:]
    for line in (ROOT/'requirements.txt').read_text().splitlines():
        if not line.strip() or line.startswith('#'):continue
        requirement=Requirement(line)
        assert pins[requirement.name.lower().replace('_','-')] in requirement.specifier
    assert pins['seleniumbase']=='4.54.11' and pins['selenium']=='4.49.0'


def test_redirecting_venv_is_a_failed_preflight_not_skipped_tests(tmp_path,monkeypatch):
    monkeypatch.setattr(gate.sys,'prefix','fixture-redirecting-venv')
    reports=tmp_path/'reports'
    assert gate.run(reports)==1
    summary=json.loads((reports/'summary.json').read_text())
    assert summary['error']=='UNSUPPORTED_REDIRECTING_VENV'
    assert summary['gate_passed'] is False and summary['stage']=='preflight'
    assert 'tests' not in summary


def guarded_python(code):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, (
        str(ROOT / "scripts/ci/offline_guard"), os.environ.get("PYTHONPATH", "")))),
        PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", "-c", code], env=env,
                          capture_output=True, text=True, timeout=15)


def test_required_suites_include_google_without_replacing_original_suites():
    assert len(gate.SUITES) == len(set(gate.SUITES)) == 25
    assert {"tests/test_architecture.py", "tests/test_navigation_health.py",
            "tests/test_review_surface.py"}.issubset(gate.SUITES)
    assert len(gate.MODULES) == 25


def test_guard_allows_repeated_real_asyncio_self_pipes_and_cleans_registration():
    result = guarded_python("""
import asyncio, sitecustomize
async def task():
    await asyncio.sleep(0)
    return 42
for _ in range(3):
    assert asyncio.run(task()) == 42
assert not sitecustomize._listeners
print('ASYNCIO_OK')
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ASYNCIO_OK"


@pytest.mark.parametrize("operation", [
    "socket.socket().connect(('127.0.0.1', 9))",
    "socket.socket().connect(('203.0.113.1', 443))",
    "socket.socket().connect_ex(('203.0.113.1', 443))",
    "socket.socket().bind(('127.0.0.1', 0))",
    "socket.socket().bind(('127.0.0.1', 9000))",
    "socket.socket().bind(('0.0.0.0', 0))",
    "socket.socket(socket.AF_INET6).bind(('::1', 0))",
    "socket.socketpair()",
    "socket.getaddrinfo('example.invalid', 443)",
    "socket.getaddrinfo('127.0.0.1', 443)",
    "socket.gethostbyname('example.invalid')",
    "socket.gethostbyname_ex('example.invalid')",
    "socket.gethostbyaddr('203.0.113.1')",
    "socket.getnameinfo(('203.0.113.1', 443), 0)",
    "socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'x', ('203.0.113.1', 53))",
])
def test_guard_refuses_direct_network_and_dns_even_while_event_loop_exists(operation):
    code = """
import asyncio, socket
loop = asyncio.new_event_loop()
try:
    try:
        OPERATION
    except RuntimeError as error:
        assert str(error) == 'CI_OFFLINE_NETWORK_REFUSED'
        print('REFUSED')
    else:
        raise AssertionError('Guard allowed a prohibited operation')
finally:
    loop.close()
""".replace("OPERATION", operation)
    result = guarded_python(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "REFUSED"


def test_guard_child_interpreter_inherits_network_denial():
    result = guarded_python("""
import subprocess, sys
child = subprocess.run([sys.executable, '-B', '-c',
    "import socket; socket.socket().connect(('203.0.113.1',443))"],
    capture_output=True, text=True, timeout=10)
assert child.returncode != 0
assert 'CI_OFFLINE_NETWORK_REFUSED' in child.stderr
print('CHILD_REFUSED')
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "CHILD_REFUSED"


def test_missing_cases_fail_even_when_every_suite_is_present(tmp_path):
    raw = tmp_path / "raw.xml"
    fixture_xml(raw)
    tree = ET.parse(raw)
    suite = next(tree.iter("testsuite"))
    suite.remove(list(suite)[-1])
    tree.write(raw)
    _, summary = gate.project_report(raw, 0, 0.1)
    assert not summary["missing_suites"]
    assert summary["tests"] == gate.MINIMUM_CASES - 1
    assert not summary["gate_passed"]


def test_asyncio_self_pipe_allowance_does_not_leak_to_another_thread():
    result = guarded_python("""
import asyncio, socket, threading
loop = asyncio.new_event_loop()
outcomes = []
def worker():
    try:
        socket.socket().bind(('127.0.0.1', 0))
    except RuntimeError as error:
        outcomes.append(str(error))
thread = threading.Thread(target=worker)
try:
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert outcomes == ['CI_OFFLINE_NETWORK_REFUSED']
finally:
    loop.close()
print('THREAD_REFUSED')
""")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "THREAD_REFUSED"


def test_all_inherited_google_offline_modules_are_in_gate():
    inherited_google = {
        "tests/test_architecture.py", "tests/test_browser_session.py",
        "tests/test_extractor.py", "tests/test_google_baseline.py",
        "tests/test_navigation_health.py", "tests/test_paginator.py",
        "tests/test_review_pane_handoff.py", "tests/test_review_surface.py",
        "tests/test_smoke_test_runner.py",
    }
    assert inherited_google.issubset(gate.SUITES)
    assert all((ROOT / module).is_file() for module in inherited_google)
