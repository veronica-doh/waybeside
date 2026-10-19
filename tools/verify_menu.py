"""家庭菜单的机械核算；不判定医学适用性，不保存默认个人路径。"""
import argparse
import hashlib
import json
import math
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

NUTRIENTS = ('energy_kcal', 'protein_g', 'fat_g', 'carbohydrate_g', 'fibre_g')
BASES = {'dry', 'raw_edible', 'cooked_edible', 'ready_to_eat', 'ready_to_cook'}


def number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label}必须是数值')
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f'{label}数值不合法')
    return Decimal(str(value))


def rounded(value):
    return float(value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))


def calculate(menu):
    foods = menu['food_reference']['foods']
    if not isinstance(foods, dict) or not foods:
        raise ValueError('缺少食材参考表')
    days = menu['days']
    if not isinstance(days, list) or not days:
        raise ValueError('没有菜单日')
    results, seen_days, used = [], set(), set()
    for day in days:
        day_id = day['day']
        if isinstance(day_id, bool) or not isinstance(day_id, (int, str)) or not str(day_id).strip():
            raise ValueError('日标识必须是非空文字或整数')
        key = str(day_id)
        if key in seen_days:
            raise ValueError(f'重复日标识：{key}')
        seen_days.add(key)
        if not isinstance(day['meals'], list) or not day['meals']:
            raise ValueError(f'第{key}日没有餐次')
        totals = {k: Decimal(0) for k in NUTRIENTS}
        quantities, meals, seen_meals = defaultdict(Decimal), [], set()
        for meal in day['meals']:
            name = meal['meal']
            if not isinstance(name, str) or not name.strip() or name in seen_meals:
                raise ValueError(f'第{key}日餐次为空或重复')
            seen_meals.add(name)
            if not meal.get('title') or not meal.get('ingredients'):
                raise ValueError(f'第{key}日{name}缺少标题或食材')
            mt = {k: Decimal(0) for k in NUTRIENTS}
            ingredient_nutrients = []
            seen_foods, weight = set(), Decimal(0)
            for item in meal['ingredients']:
                fid = item['food_id']
                if fid in seen_foods:
                    raise ValueError(f'第{key}日{name}重复食材{fid}，请先合计')
                seen_foods.add(fid)
                food = foods[fid]
                if not food.get('name') or food.get('weight_basis') not in BASES or not food.get('source'):
                    raise ValueError(f'{fid}缺名称、有效重量状态或来源')
                grams = number(item['g'], f'{fid}重量', positive=True)
                weight += grams
                quantities[fid] += grams
                used.add(fid)
                portion = {}
                for k in NUTRIENTS:
                    portion[k] = grams * number(food['per_100g'][k], f'{fid}.{k}') / 100
                    mt[k] += portion[k]
                ingredient_nutrients.append({
                    'food_id': fid, 'name': food['name'], 'g': float(grams),
                    'weight_basis': food['weight_basis'],
                    'per_100g': food['per_100g'], 'source': food['source'],
                    'proxy_note': food.get('proxy_note'),
                    'is_patient_brand_measured': food.get('is_patient_brand_measured'),
                    'estimated_portion_nutrients': {k: rounded(v) for k, v in portion.items()}})
            for k in NUTRIENTS:
                totals[k] += mt[k]
            meals.append({'meal': name, 'title': meal['title'],
                          'ingredients': ingredient_nutrients,
                          'ingredient_weight_g': rounded(weight),
                          'totals': {k: rounded(v) for k, v in mt.items()}})
        results.append({'day': day_id, 'totals': {k: rounded(v) for k, v in totals.items()},
                        'food_totals_g': {k: float(v) for k, v in sorted(quantities.items())},
                        'meals': meals})
    return {'mechanical_status': 'PASS', 'clinical_status': 'NOT_ASSESSED',
            'method': 'Decimal逐食材每100g参考值相加，最后四舍五入；不使用4/4/9代替来源热量。',
            'limitations': ['食材份额重量不等于熟食重量或胃内容积；烹调用水不计入。',
                           '来源存在不代表数据库、品牌或个体适用性已经核实。',
                           '不评价医嘱、微量营养素完整性、临床安全或治疗准备是否合格。'],
            'reference_ids': sorted(used), 'days': results}


def compare_foods(current, previous):
    before = {str(d['day']): d['food_totals_g'] for d in previous['days']}
    after = {str(d['day']): d['food_totals_g'] for d in current['days']}
    changes = []
    for day in sorted(set(before) | set(after)):
        x, y = before.get(day, {}), after.get(day, {})
        delta = {f: round(y.get(f, 0) - x.get(f, 0), 6) for f in set(x) | set(y)
                 if abs(y.get(f, 0) - x.get(f, 0)) > 1e-6}
        if delta or day not in before or day not in after:
            changes.append({'day': day, 'food_delta_g': delta})
    return {'same_daily_food_quantities': not changes, 'changes': changes}


def check_expected(current, expected):
    days = {str(d['day']): d for d in expected['days']}
    if len(days) != len(expected['days']) or set(days) != {str(d['day']) for d in current['days']}:
        raise ValueError('预期核算的日标识不一致')
    for day in current['days']:
        for k, value in day['totals'].items():
            old = number(days[str(day['day'])]['totals'][k], f'预期{k}')
            # 旧程序二进制浮点在x.x5边界可能差0.1；只比较显示精度。
            if abs(Decimal(str(value)) - old) > Decimal('0.1'):
                raise ValueError(f"第{day['day']}日{k}与预期不符")


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--menu', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--compare')
    parser.add_argument('--expected')
    args = parser.parse_args()
    output = None
    try:
        destination = Path(args.out).resolve()
        inputs = [Path(p).resolve() for p in (args.menu, args.compare, args.expected) if p]
        if destination in inputs:
            raise ValueError('输出不能覆盖输入')
        output = destination
        result = calculate(read(args.menu))
        result['menu_sha256'] = hashlib.sha256(Path(args.menu).read_bytes()).hexdigest()
        if args.compare:
            result['comparison'] = compare_foods(result, calculate(read(args.compare)))
        if args.expected:
            check_expected(result, read(args.expected))
            result['expected_totals_match'] = True
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f"PASS：已核算{len(result['days'])}天；临床适用性未由脚本评估。")
    except (KeyError, ValueError, TypeError, OSError) as exc:
        if output is not None:
            # 本次失败必须替换旧PASS报告，避免读者误用上一轮结果。
            try:
                output.write_text(json.dumps({'mechanical_status': 'FAIL',
                    'clinical_status': 'NOT_ASSESSED', 'error': str(exc)},
                    ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            except OSError:
                print('注意：无法写入失败报告，不得沿用该路径的历史报告。')
        print(f'FAIL：{exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
