import importlib.util
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from shapely.geometry import box

spec = importlib.util.spec_from_file_location('foody_q4_profile', Path(__file__).with_name('profile_q4.py'))
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def row(identity='1', **kwargs):
    result = dict(source_id=identity, latitude=10.75, longitude=106.69, name='Fixture Cafe',
                  fields={'streetAddress': ['42 Example']}, categories={'Thể loại': ['Café/Dessert']})
    result.update(kwargs)
    return result


def test_same_address_branches_preserved_and_boundary_included():
    rows, dq, unique = profile.profile_rows([row('1'), row('2')], box(106.69, 10.75, 106.72, 10.77))
    assert unique == len(rows) == 2
    assert all(x['classification'] == 'strict_fnb' for x in rows)


def test_unknown_category_not_inferred_from_name():
    assert profile.classify(row(categories={})) == 'uncertain'
    assert profile.classify(row(categories={'Thể loại': ['Cửa hàng thực phẩm']})) == 'uncertain'


def test_missing_outside_and_duplicate_counts():
    records = [row(), row(), row('invalid', latitude=None), row('out', latitude=12)]
    rows, dq, unique = profile.profile_rows(records, box(106.69, 10.75, 106.72, 10.77))
    assert len(rows) == 1 and unique == 3
    assert dq['exact_duplicates'] == dq['unlocated_or_invalid_geometry'] == dq['outside_polygon'] == 1


def test_conflicting_ids_stop():
    with pytest.raises(ValueError, match='Conflicting'):
        profile.profile_rows([row(), row(name='Different')], box(106.69, 10.75, 106.72, 10.77))


def test_atomic_outputs_and_idempotency(tmp_path):
    raw = tmp_path / 'pois.json'; raw.write_text(json.dumps([row()]), encoding='utf-8')
    root = tmp_path / 'profile'
    boundary = box(106.69, 10.75, 106.72, 10.77)
    result = profile.publish(raw, root, boundary, {'sha256': 'fixture'})
    assert result == profile.publish(raw, root, boundary, {'sha256': 'fixture'})
    summary = json.loads((result / 'summary.json').read_text(encoding='utf-8'))
    assert summary['strict_fnb'] == 1 and not summary['is_full_district_count']
    with pq.ParquetFile(result / 'foody_fnb_district4.parquet') as f:
        assert f.metadata.num_rows == 1
        assert 'review_samples' not in f.schema_arrow.names


def test_empty_polygon_result_preserves_pointer(tmp_path):
    raw = tmp_path / 'pois.json'; raw.write_text(json.dumps([row(latitude=12)]))
    root = tmp_path / 'profile'; root.mkdir()
    pointer = root / 'current.json'; pointer.write_text('{"old":"good"}')
    with pytest.raises(ValueError, match='No located'):
        profile.publish(raw, root, box(106.69, 10.75, 106.72, 10.77), {'sha256': 'fixture'})
    assert pointer.read_text() == '{"old":"good"}'
