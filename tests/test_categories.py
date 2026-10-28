import unittest
from fnb_platform.categories import is_food_place, get_product_category_group, normalize_categories

class CategoriesTests(unittest.TestCase):
    def test_food_hierarchy_required(self):
        self.assertFalse(is_food_place({'taxonomy': {'primary':'coffee_shop'}}))
        self.assertIsNone(get_product_category_group({'taxonomy': {'primary':'coffee_shop'}}))
    def test_overture_category_preserved(self):
        place={'taxonomy': {'primary':'coffee_shop','hierarchy':['food_and_drink','non_alcoholic_beverage_venue','coffee_shop']}}
        self.assertTrue(is_food_place(place))
        self.assertEqual(get_product_category_group(place),'cafe_coffee')
        self.assertEqual(normalize_categories(place),['coffee_shop','non_alcoholic_beverage_venue'])
    def test_unknown_food_category_is_not_fabricated(self):
        place={'taxonomy': {'primary':'unknown_source_category','hierarchy':['food_and_drink']}}
        self.assertEqual(get_product_category_group(place),'other_food')
        self.assertEqual(normalize_categories(place),['unknown_source_category'])
    def test_malformed_taxonomy(self):
        for value in (None,[],42):
            self.assertFalse(is_food_place({'taxonomy':value}))

if __name__=='__main__': unittest.main()
