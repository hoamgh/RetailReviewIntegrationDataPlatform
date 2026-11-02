"""Focused offline invariants for source evaluation."""
import unittest

from evaluate import candidates, clip, dedup, sample_review, stats
from shapely.geometry import box


def row(id_, lat, lon, name='A', address='12 Main St'):
    return dict(id=id_, lat=lat, lon=lon, name=name, address=address,
                category='restaurant', price=None, cuisine=None, reviews=None,
                phone=None, updated=None, status=None, geometry_valid=True)


class EvaluationTests(unittest.TestCase):
    def test_invalid_geometry_and_boundary_inclusion(self):
        good = row('one', 10.5, 106.5)
        invalid = row('bad', None, 106.5)
        outside = row('outside', 11.0, 106.5)
        items, excluded = clip([good, invalid, outside], box(106.0, 10.0, 107.0, 10.5))
        self.assertEqual([r['id'] for r in items], ['one'])
        self.assertEqual(excluded['missing_or_invalid_coordinates'], 1)
        self.assertEqual(excluded['outside_polygon'], 1)

    def test_dedup_source_id_and_denominator(self):
        records, duplicates, missing = dedup([row('a',10,106),row('a',10,106),row('',10,106),row('b',10,106)])
        self.assertEqual((len(records), duplicates, missing), (2,1,1))
        self.assertEqual(stats(records)['present']['name'], 2)

    def test_conflicting_branches_not_accepted(self):
        pairs = candidates([row('f',10.75,106.70,'Cafe One','12 Main St')],
                           [row('o',10.75001,106.70001,'Cafe One','14 Main St')])
        self.assertEqual(len(pairs),1)
        self.assertTrue(pairs[0]['branch_conflict'])
        self.assertEqual(pairs[0]['decision'],'NON_MATCH')
        self.assertFalse(pairs[0]['identity_verified'])
        self.assertIsNone(pairs[0]['category_compatible'])

    def test_far_unrelated_is_not_candidate(self):
        self.assertEqual(candidates([row('f',10.75,106.70,'Cafe Alpha','12 One')],
                                    [row('o',10.751,106.701,'Pizza Beta','99 Two')]), [])

    def test_no_transitive_merge_and_strata(self):
        f=[row('f',10.75,106.70),row('x',10.80,106.80)]
        o=[row('o1',10.75,106.70),row('o2',10.7501,106.7001)]
        pairs=candidates(f,o)
        self.assertEqual({(x['foody_id'],x['overture_id']) for x in pairs},{('f','o1'),('f','o2')})
        queue, sizes=sample_review(f,o,pairs)
        self.assertEqual(len(queue),2)
        self.assertEqual(sizes['FOODY_NO_CANDIDATE']['population'],1)
        self.assertTrue(all(not x['identity_review_decision'] for x in queue))


if __name__ == '__main__':
    unittest.main()
