"""A.3.1 orchestration contracts, contained fake Python worker; no Chrome."""
import pytest

from crawler_harness.browser_commands import CommandFault
from crawler_harness.containment import ContainmentError
from crawler_harness.local_faults import terminate_owned_worker,resolve_terminal_session
from crawler_harness.local_smoke import run
from tests.test_harness_browser_commands import work,session


def test_explicit_resolution_before_owner_disposal(work):
    root,store,_,_,token,_,_=work
    with session(work) as s:
        assert s.driver.current_url=='about:blank'
        terminate_owned_worker(s)
        with pytest.raises(CommandFault):_ = s.driver.current_url
        result=resolve_terminal_session(s,root/'lake')
        assert result['fresh_connection_worker_gate']=='PASS'
        assert len(result['events'])==2 and result['repeated']['already_resolved']
        assert result['durable_state']['refused_count']==1
        assert result['command_replay_count']==result['source_records']==0
        assert store.get(token.job_id)['state']=='PAUSED_NEEDS_HUMAN'
    assert s.owner._proof['durable_resolution'] is True


def test_live_session_cannot_enter_resolution(work):
    with session(work) as s:
        with pytest.raises(ContainmentError):resolve_terminal_session(s,work[0]/'lake')
        assert not s.owner.api.wait(s.owner.process)


@pytest.mark.parametrize('options',[{}, {'timeout_command':True},
                                  {'crash_worker':True,'timeout_command':True}])
def test_invalid_resolution_scenario_refused_before_output_creation(tmp_path,options):
    output=tmp_path/'unused'
    with pytest.raises(ValueError):run(output,terminal_resolution=True,**options)
    assert not output.exists()


def test_operator_submission_is_required_before_reservation(tmp_path):
    from crawler_harness.local_resolution_validation import run as submit
    with pytest.raises(ValueError,match='operator-submit'):
        submit(tmp_path/'unused',tmp_path,tmp_path/'not-used')
    assert not (tmp_path/'unused').exists()


def test_existing_output_refuses_without_starting_subprocess(tmp_path,monkeypatch):
    from crawler_harness import local_resolution_validation as runner
    executable=tmp_path/'actionlint';executable.touch()
    def refuse(*a,**k):raise AssertionError('No process allowed')
    monkeypatch.setattr(runner.subprocess,'run',refuse)
    with pytest.raises(FileExistsError):
        runner.run(tmp_path,tmp_path,executable,operator_submit=True)


def test_failed_offline_preflight_never_launches_real_scenario(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import json
    from crawler_harness import local_resolution_validation as runner
    executable=tmp_path/'actionlint';executable.touch();calls=[]
    def failed(args,**kwargs):
        calls.append(args)
        assert 'pytest' in args and kwargs['timeout']==180
        from pathlib import Path
        base=Path(args[args.index('--basetemp')+1])
        assert base.name=='p' and not base.is_relative_to(output)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(runner.subprocess,'run',failed)
    output=tmp_path/'run'
    assert runner.run(output,tmp_path,executable,operator_submit=True)==1
    assert len(calls)==1
    report=json.loads((output/'summary.json').read_text())
    assert report['status']=='FAIL' and report['real_session_attempts']==0
    assert report['offline_fixture_root']
