"""Operator-submitted, one-session local A.3.1 validation with bounded stages.

No retries. Existing output refuses before any child or browser is started.
This controller's subprocess timeout kills only its exact retained Popen child;
the contained controller's sole Job handle then enforces kill-on-close. Such a
timeout is UNKNOWN/FAIL, never verified quiescence or durable resolution.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from .browser_profile import atomic_json

ROOT=Path(__file__).resolve().parents[1]
FOCUSED=('tests/test_harness_worker_resolution.py','tests/test_harness_local_faults.py',
         'tests/test_harness_local_browser.py','tests/test_harness_browser_commands.py',
         'tests/test_ci_windows_quality_gate.py','tests/test_harness_local_resolution.py')


def run(output,dependency_root,actionlint,*,operator_submit=False):
    if not operator_submit:raise ValueError('Explicit --operator-submit required')
    if os.name!='nt':raise ValueError('Windows native containment required')
    dependency_root=Path(dependency_root).resolve(strict=True)
    actionlint=Path(actionlint).resolve(strict=True)
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)  # Permanent one-shot reservation.
    summary={'status':'FAIL','real_session_attempts':0,'stages':{},'external_navigation':False}
    atomic_json(output/'summary.json',summary)
    env=os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    # The real child's provider permits only the fixed file HTML. Offline tests
    # additionally use the unchanged CI network guard, including DNS refusal.
    env['PYTHONPATH']=os.pathsep.join((str(ROOT),str(dependency_root)))
    offline=dict(env,PYTHONPATH=os.pathsep.join((str(ROOT/'scripts/ci/offline_guard'),env['PYTHONPATH'])))

    def stage(name,args,timeout,environment):
        print(name,flush=True);started=time.monotonic()
        try:
            with (output/(name+'.log')).open('w',encoding='utf-8') as log:
                result=subprocess.run(args,cwd=ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
            fact={'exit_code':result.returncode,'elapsed_seconds':time.monotonic()-started}
        except subprocess.TimeoutExpired:
            fact={'exit_code':None,'outcome':'TIMEOUT_UNKNOWN','elapsed_seconds':time.monotonic()-started}
        summary['stages'][name]=fact
        atomic_json(output/'summary.json',summary)
        return fact['exit_code']==0

    try:
        # Match the CI gate: nested job/attempt fixtures can exceed MAX_PATH
        # under a long worktree/output path. Retain this isolated directory for
        # debugging; never reuse/delete prior fixtures or change OS policy.
        test_root=Path(tempfile.mkdtemp(prefix='a31-',dir=os.environ.get('RUNNER_TEMP') or None))
        summary['offline_fixture_root']=str(test_root)
        atomic_json(output/'summary.json',summary)
        # Offline preflight is deliberately first. No child/session on failure.
        if not stage('preflight',[sys.executable,'-B','-m','pytest','-q','--import-mode=importlib',
                '-p','no:cacheprovider','--basetemp',str(test_root/'p'),
                '--junitxml',str(output/'preflight.xml'),*FOCUSED],180,offline):return 1
        summary['real_session_attempts']=1
        atomic_json(output/'summary.json',summary)
        real=stage('single-real-scenario',[sys.executable,'-B','-m','crawler_harness.local_smoke',
            '--output',str(output/'scenario'),'--crash-worker','--terminal-resolution',
            '--startup-timeout','45'],100,env)
        # Always record regression outcomes after a returned/failed scenario;
        # never dispatch another real session.
        gate=stage('offline-gate',[sys.executable,'-B','scripts/ci/windows_quality_gate.py',
            '--reports',str(output/'gate'),'--dependency-root',str(dependency_root)],240,env)
        focused=stage('focused',[sys.executable,'-B','-m','pytest','-q','--import-mode=importlib',
            '-p','no:cacheprovider','--basetemp',str(test_root/'f'),
            '--junitxml',str(output/'focused.xml'),*FOCUSED],180,offline)
        lint=stage('actionlint',[str(actionlint),'.github/workflows/harness-windows.yml'],20,env)
        whitespace=stage('whitespace',['git','diff','--check'],20,env)
        manifest=json.loads((ROOT/'docs/baselines/google_reviews_manifest.json').read_text())
        changed=[p for p,digest in manifest['files'].items() if
                 hashlib.sha256((ROOT/p).read_text(encoding='utf-8').replace('\r\n','\n').encode()).hexdigest()!=digest]
        summary['google_frozen']={'total':len(manifest['files']),'changed':changed}
        if real:
            report=json.loads((output/'scenario/summary.json').read_text())
            real=report.get('status')=='PASS' and report.get('terminal_resolution',{}).get('fresh_connection_worker_gate')=='PASS'
        summary['status']='PASS' if all((real,gate,focused,lint,whitespace,not changed)) else 'FAIL'
        return 0 if summary['status']=='PASS' else 1
    except Exception as exc:
        summary['error_class']=type(exc).__name__
        return 1
    finally:
        atomic_json(output/'summary.json',summary)
        print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operator-submit',action='store_true')
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--dependency-root',required=True,type=Path)
    parser.add_argument('--actionlint',required=True,type=Path)
    args=parser.parse_args()
    raise SystemExit(run(args.output,args.dependency_root,args.actionlint,operator_submit=args.operator_submit))
