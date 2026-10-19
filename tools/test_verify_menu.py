"""仅用合成食材检查核算、份额迁移和错误拒绝；不包含个人资料。"""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from verify_menu import calculate, compare_foods, check_expected


def fixture():
    return {'food_reference': {'foods': {'sample': {
        'name': '合成食材', 'weight_basis': 'ready_to_eat', 'source': '测试专用，非食品数据',
        'per_100g': {'energy_kcal': 200, 'protein_g': 10, 'fat_g': 5,
                     'carbohydrate_g': 25, 'fibre_g': 2}}}},
        'days': [{'day': 1, 'meals': [{'meal': '晚餐', 'title': '合成测试',
                  'ingredients': [{'food_id': 'sample', 'g': 100}]}]}]}


class MenuTests(unittest.TestCase):
    def test_actual_portion_has_three_macros_and_source(self):
        data = fixture()
        data['days'][0]['meals'][0]['ingredients'][0]['g'] = 60
        meal = calculate(data)['days'][0]['meals'][0]
        item = meal['ingredients'][0]
        self.assertEqual(item['estimated_portion_nutrients']['protein_g'], 6)
        self.assertEqual(item['estimated_portion_nutrients']['carbohydrate_g'], 15)
        self.assertEqual(item['estimated_portion_nutrients']['fat_g'], 3)
        self.assertEqual(item['per_100g']['protein_g'], 10)
        self.assertEqual(item['weight_basis'], 'ready_to_eat')
        self.assertEqual(item['source'], data['food_reference']['foods']['sample']['source'])
        self.assertEqual(item['estimated_portion_nutrients'], meal['totals'])

    def test_move_portion_conserves_totals(self):
        original = fixture()
        moved = copy.deepcopy(original)
        moved['days'][0]['meals'][0]['ingredients'][0]['g'] = 40
        moved['days'][0]['meals'].append({'meal': '下午', 'title': '迁移份额',
            'ingredients': [{'food_id': 'sample', 'g': 60}]})
        before, after = calculate(original), calculate(moved)
        self.assertEqual(after['days'][0]['totals']['energy_kcal'], 200)
        self.assertEqual(after['days'][0]['totals']['protein_g'], 10)
        self.assertTrue(compare_foods(after, before)['same_daily_food_quantities'])

    def test_duplicate_added_snack_is_visible(self):
        original = fixture()
        copied = copy.deepcopy(original)
        copied['days'][0]['meals'].append({'meal': '下午', 'title': '错误重复',
            'ingredients': [{'food_id': 'sample', 'g': 60}]})
        result = compare_foods(calculate(copied), calculate(original))
        self.assertFalse(result['same_daily_food_quantities'])
        self.assertEqual(result['changes'][0]['food_delta_g'], {'sample': 60})
        with self.assertRaises(ValueError):
            check_expected(calculate(copied), calculate(original))

    def test_invalid_quantity_rejected(self):
        for value in (0, -1, float('nan'), float('inf'), True, '100'):
            with self.subTest(value=value):
                data = fixture()
                data['days'][0]['meals'][0]['ingredients'][0]['g'] = value
                with self.assertRaises(ValueError):
                    calculate(data)

    def test_source_and_weight_state_required(self):
        for field in ('source', 'weight_basis'):
            data = fixture()
            del data['food_reference']['foods']['sample'][field]
            with self.assertRaises(ValueError):
                calculate(data)

    def test_duplicate_day_or_meal_rejected(self):
        data = fixture()
        data['days'].append(copy.deepcopy(data['days'][0]))
        with self.assertRaises(ValueError):
            calculate(data)

    def test_cli_failure_replaces_previous_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, report = root / 'input.json', root / 'report.json'
            source.write_text(json.dumps(fixture()))
            command = [sys.executable, str(Path(__file__).with_name('verify_menu.py')),
                       '--menu', str(source), '--out', str(report)]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            invalid = fixture()
            invalid['days'][0]['meals'][0]['ingredients'][0]['g'] = -10
            source.write_text(json.dumps(invalid))
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)
            self.assertEqual(json.loads(report.read_text())['mechanical_status'], 'FAIL')
            original = source.read_bytes()
            command[-1] = str(source)
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)
            self.assertEqual(source.read_bytes(), original)
        data = fixture()
        data['days'][0]['meals'].append(copy.deepcopy(data['days'][0]['meals'][0]))
        with self.assertRaises(ValueError):
            calculate(data)


if __name__ == '__main__':
    unittest.main()
