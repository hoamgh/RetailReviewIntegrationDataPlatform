import importlib.util
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import profile_access as access


def test_only_dedicated_manual_profile_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    profile = tmp_path / 'FnbFoodyResearch/manual-foody-20261029-135111'
    profile.mkdir(parents=True)
    assert access.validate_profile(profile) == profile.resolve()
    personal = tmp_path / 'Google/Chrome/User Data'; personal.mkdir(parents=True)
    with pytest.raises(ValueError, match='task-specific'):
        access.validate_profile(personal)


def test_active_profile_blocks_before_browser_launch(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    profile = tmp_path / 'FnbFoodyResearch/manual-foody-20261029-135111'
    profile.mkdir(parents=True)
    monkeypatch.setattr(access, 'profile_owners', lambda path: [123])
    with pytest.raises(RuntimeError, match='PROFILE_IN_USE'):
        access.available_profile(profile)


def test_closed_profile_can_be_reused(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    profile = tmp_path / 'FnbFoodyResearch/manual-foody-20261029-135111'
    profile.mkdir(parents=True)
    monkeypatch.setattr(access, 'profile_owners', lambda path: [])
    assert access.available_profile(profile) == profile.resolve()
