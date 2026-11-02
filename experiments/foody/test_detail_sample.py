"""Offline sample selection and safety contracts."""
import json
from pathlib import Path

import pytest

from detail_sample import prepare


def test_first_urls_are_bounded_and_completed_skipped(tmp_path):
    urls = [f'https://www.foody.vn/ho-chi-minh/store-{i}' for i in range(4)]
    source = tmp_path / 'state.json'
    source.write_text(json.dumps(dict(status='BLOCKED', places={u: {'url': u} for u in urls},
        detail_done=[urls[0], urls[3]], records={'1': dict(url=urls[0],source_id='1'),
        '4': dict(url=urls[3],source_id='4')}, dlq=[{'reason':'SEARCH_UI_UNVERIFIED'}])), encoding='utf-8')
    state = prepare(source, 3)
    assert state['pending'] == urls[1:3]
    assert state['detail_done'] == [urls[0]]
    assert list(state['records']) == ['1']
    assert state['dlq'] == []
    assert state['sample_size'] == 3


def test_reject_nonblocked_source_and_oversized_sample(tmp_path):
    source = tmp_path / 'state.json'
    source.write_text(json.dumps(dict(status='RUNNING', places={})), encoding='utf-8')
    with pytest.raises(ValueError):
        prepare(source, 1)
    source.write_text(json.dumps(dict(status='BLOCKED', places={})), encoding='utf-8')
    with pytest.raises(ValueError):
        prepare(source, 1001)
