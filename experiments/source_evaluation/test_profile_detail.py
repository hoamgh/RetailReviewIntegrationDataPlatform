"""Small, offline checks for lifecycle metrics used in DAG design."""
import unittest

from profile_detail import source_lifecycle


class SourceLifecycleTests(unittest.TestCase):
    def test_upstream_age_excludes_overture_derived_timestamp(self):
        rows = [
            {'id': 'a', 'sources': [
                {'dataset': 'Overture', 'update_time': '2026-10-15T00:00:00Z'},
                {'dataset': 'meta', 'update_time': '2025-10-01T00:00:00Z'}]},
            {'id': 'b', 'sources': [{'dataset': 'Overture', 'update_time': '2026-10-15T00:00:00Z'}]},
            {'id': 'excluded', 'sources': [{'dataset': 'meta', 'update_time': '2026-10-20T00:00:00Z'}]},
        ]
        actual = source_lifecycle(rows, {'a', 'b'})
        self.assertEqual(actual['providers'], {'Overture': 2, 'meta': 1})
        self.assertEqual(actual['age_buckets']['0-30d'], 2)
        self.assertEqual(actual['upstream_age_buckets']['>365d'], 1)
        self.assertEqual(actual['upstream_age_buckets']['UNKNOWN'], 1)
        self.assertEqual(actual['multi_source_pois'], 1)


if __name__ == '__main__':
    unittest.main()
