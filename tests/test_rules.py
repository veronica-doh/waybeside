"""规则筛选：只用虚构菜单与虚构规则；过敏提醒对推荐零影响。"""
import copy
import json
from pathlib import Path
import pytest
from support import make_env, open_shares
from waybeside import rules
from waybeside.agent_door import export_package, submit_candidate
from waybeside.menu_door import CandidateError
from waybeside.menu_library import synthetic_library, import_library, validate

ROOT = Path(__file__).resolve().parents[1]
TARGET = '2031-12-31'
TOMORROW = '2032-01-01'
EXAMPLE = ROOT / 'examples' / 'rules' / 'demo-fictional-rules.json'


def food(name, **extra):
    return dict({'name': name, 'weight_basis': 'cooked_edible', 'source': '合成数值',
                 'per_100g': {'energy_kcal': 100, 'protein_g': 2, 'fat_g': 1, 'carbohydrate_g': 20, 'fibre_g': None}}, **extra)


def library():
    """4 组虚构菜单：01 软 / 02 含“硬”（规则文件标记）/ 03 含一种未标注 / 04 含“硬”（菜单库自带标记）。"""
    foods = {'demo_noodle': food('演示面条'), 'demo_nut': food('演示坚果'), 'demo_plain': food('演示米饭'),
             'demo_cracker': food('演示脆饼', tags=['hard'])}
    uses = {'01': ['demo_noodle'], '02': ['demo_noodle', 'demo_nut'], '03': ['demo_noodle', 'demo_plain'], '04': ['demo_cracker']}
    data = synthetic_library()
    data.update(version='rules-test-v1', food_reference={'foods': foods})
    data['days'] = [{'day': i + 1, 'group_id': gid, 'meals': [
        {'meal': m, 'title': f'演示{gid}{m}', 'ingredients': [{'food_id': f, 'g': 50} for f in ids], 'steps': ['仅演示']}
        for m in ('早餐', '上午加餐', '午餐', '下午加餐', '晚餐')]} for i, (gid, ids) in enumerate(uses.items())]
    return validate(data)


def setup(tmp_path, rule_files=None, tags=('soft_texture',), allergy=None):
    env = make_env(tmp_path, today=TARGET)
    path = tmp_path / 'waybeside.sqlite'
    import_library(path, library())
    for name, content in (rule_files or {}).items():
        (tmp_path / 'rules').mkdir(exist_ok=True)
        (tmp_path / 'rules' / name).write_text(content if isinstance(content, str) else json.dumps(content), encoding='utf-8')
    answers = {}
    if tags is not None:
        answers['requirement_tags'] = list(tags)
    if allergy is not None:
        answers['allergy_reminders'] = allergy
    r = env[2]('onboarding/diet', {'answers': answers, 'version': 0})
    assert r.status_code == 200, r.data
    return env, path


def example():
    return json.loads(EXAMPLE.read_text(encoding='utf-8'))


def menu(env, date=TOMORROW):
    return env[3]('menu?date=' + date).json


def ids(menu_json):
    return [g['group_id'] for g in menu_json['groups']]


# ---------------- 示例文件自身的仓库内断言 ----------------
def test_repo_examples_are_fictional_and_numeric_free():
    files = sorted((ROOT / 'examples' / 'rules').glob('*.json'))
    assert files
    for path in files:
        data = json.loads(path.read_text(encoding='utf-8'))
        assert data['fictional'] is True
        text = path.read_text(encoding='utf-8')
        for numeric in ('per_100g', 'energy_kcal', 'protein_g', 'fibre_g', '"gt"', '"lt"', '"min"', '"max"', 'threshold'):
            assert numeric not in text
        rules.parse(text.encode('utf-8'))                 # 示例本身必须通过校验


# ---------------- 筛选 ----------------
def test_exclude_hides_groups_from_recommendation_with_reason_and_union_of_tags(tmp_path):
    env, _ = setup(tmp_path, {'demo.json': example()})
    m = menu(env)
    hidden = {h['group_id']: h['reasons'] for h in m['rules']['hidden']}
    assert set(hidden) == {'02', '04'}                              # 02 来自规则文件标记，04 来自菜单库自带标记
    assert all('演示' in r for rs in hidden.values() for r in rs)
    assert m['state']['recommendation']['group_id'] == '01'
    assert m['rules']['fictional'] is True
    assert len(m['groups']) == 4                                    # 隐藏的组仍在全集里，供“仍想看”


def test_recommendation_never_lands_on_hidden_group_even_if_all_else_adopted(tmp_path):
    env, _ = setup(tmp_path, {'demo.json': example()})
    for gid, day in (('01', '2031-12-31'), ('03', '2032-01-01')):
        state = menu(env, day)['state']
        r = env[2]('menu/action', {'action': 'adopt', 'target_date': day, 'revision': state['revision'], 'library_hash': state['library_hash'], 'group_id': gid})
        assert r.status_code == 200, r.data
    assert menu(env, '2032-01-02')['state']['recommendation']['group_id'] in ('01', '03')


def test_warn_does_not_hide_and_shows_message(tmp_path):
    files = example()
    files['rules'][0].update(action='warn')
    env, _ = setup(tmp_path, {'w.json': files})
    m = menu(env)
    assert m['rules']['hidden'] == []
    assert m['rules']['warnings']['02'][0]['message'].startswith('演示')
    assert m['rules']['warnings']['02'][0]['where'][0]['food'] == '演示坚果'
    assert m['state']['recommendation']['group_id'] == '01'


def test_unlabeled_foods_are_counted_not_treated_as_safe(tmp_path):
    env, _ = setup(tmp_path, {'demo.json': example()})
    m = menu(env)
    assert m['rules']['unlabeled'].get('03') == 1                   # 演示米饭 没有任何标记
    assert '03' not in {h['group_id'] for h in m['rules']['hidden']}
    assert '01' not in m['rules']['unlabeled']


def test_rules_not_active_without_matching_requirement(tmp_path):
    env, _ = setup(tmp_path, {'demo.json': example()}, tags=['avoid_spicy'])
    m = menu(env)
    assert m['rules']['hidden'] == [] and m['rules']['unlabeled'] == {}


def test_tags_without_any_rule_file_does_not_filter_and_says_so(tmp_path):
    env, _ = setup(tmp_path)
    m = menu(env)
    assert m['rules']['tags_without_rules'] is True
    assert m['rules']['hidden'] == [] and m['rules']['configured'] is False
    assert m['state']['recommendation']['group_id'] == '01'
    (tmp_path / 'b').mkdir()
    env2, _ = setup(tmp_path / 'b', tags=None)
    assert menu(env2)['rules']['tags_without_rules'] is False       # 没有标签也没有规则：无提示


def test_hidden_group_needs_confirmation_to_preview_or_adopt(tmp_path):
    env, _ = setup(tmp_path, {'demo.json': example()})
    state = menu(env)['state']
    base = {'target_date': TOMORROW, 'revision': state['revision'], 'library_hash': state['library_hash'], 'group_id': '02'}
    r = env[2]('menu/action', dict(base, action='select'))
    assert r.status_code == 409 and r.json['code'] == 'RULE_CONFIRM_REQUIRED'
    r = env[2]('menu/action', dict(base, action='select', rule_confirmed=True))
    assert r.status_code == 200
    state = menu(env)['state']
    assert state['draft']['group_id'] == '02'
    r = env[2]('menu/action', dict(base, revision=state['revision'], action='adopt'))
    assert r.status_code == 409 and r.json['code'] == 'RULE_CONFIRM_REQUIRED'
    r = env[2]('menu/action', dict(base, revision=state['revision'], action='adopt', rule_confirmed=True))
    assert r.status_code == 200


# ---------------- 非法规则文件被整体拒收 ----------------
def mutated(fn):
    data = example()
    fn(data)
    return json.dumps(data)

BAD = {
    'numeric_condition': mutated(lambda d: d['rules'][0]['match'].update(per_100g_gt=5)),
    'numeric_value': mutated(lambda d: d['rules'][0].update(match={'food_tags_any': [5]})),
    'unknown_match_key': mutated(lambda d: d['rules'][0]['match'].update(fibre_g_over=3)),
    'unknown_field': mutated(lambda d: d.update(command='rm -rf')),
    'unknown_rule_field': mutated(lambda d: d['rules'][0].update(threshold=3)),
    'bad_action': mutated(lambda d: d['rules'][0].update(action='delete')),
    'bad_scope': mutated(lambda d: d['rules'][0].update(scope='day')),
    'long_message': mutated(lambda d: d['rules'][0].update(message='字' * 201)),
    'empty_message': mutated(lambda d: d['rules'][0].update(message='')),
    'bad_schema_version': mutated(lambda d: d.update(schema_version=2)),
    'fictional_not_bool': mutated(lambda d: d.update(fictional='yes')),
    'duplicate_rule_id': mutated(lambda d: d['rules'].append(dict(d['rules'][0]))),
    'bad_tag_chars': mutated(lambda d: d['rules'][0]['match'].update(food_tags_any=['<script>'])),
    'duplicate_key': '{"schema_version":1,"schema_version":1}',
    'not_json': '{oops',
    'not_object': '[]',
    'nan': '{"schema_version":NaN}',
    'oversize': json.dumps({'schema_version': 1, 'title': 'x' * (256 * 1024)}),
    'allergy_literal': mutated(lambda d: d['rules'][0].update(when_requirement='allergy_reminders')),
}

@pytest.mark.parametrize('name', sorted(BAD))
def test_invalid_rule_file_is_rejected_whole_and_reported(tmp_path, name):
    env, _ = setup(tmp_path, {'bad.json': BAD[name]})
    r = menu(env)['rules']
    assert r['configured'] is False and r['tags_without_rules'] is True
    assert r['files'] == [{'name': 'bad.json', 'ok': False, 'error': r['files'][0]['error']}] and r['files'][0]['error']
    assert r['hidden'] == []                                        # 整份不加载，不静默部分生效


def test_one_bad_file_does_not_disable_a_good_one(tmp_path):
    env, _ = setup(tmp_path, {'a_bad.json': '{oops', 'b_good.json': example()})
    r = menu(env)['rules']
    assert [f['ok'] for f in r['files']] == [False, True]
    assert {h['group_id'] for h in r['hidden']} == {'02', '04'}


def test_rule_cannot_reference_users_own_allergy_text(tmp_path):
    data = example()
    data['rules'][0]['when_requirement'] = 'peanut'
    env, _ = setup(tmp_path, {'demo.json': data}, tags=['soft_texture'], allergy=['peanut'])
    r = menu(env)['rules']
    assert r['configured'] is False and 'allergy' not in json.dumps(r) and '过敏' in r['files'][0]['error']


def test_rule_text_with_script_is_plain_text_end_to_end(tmp_path):
    data = example()
    data['rules'][0]['message'] = '<script>alert(1)</script>'
    env, _ = setup(tmp_path, {'demo.json': data})
    assert menu(env)['rules']['hidden'][0]['reasons'] == ['<script>alert(1)</script>']      # 数据原样，不被改写
    text = (ROOT / 'waybeside' / 'static' / 'menu.js').read_text(encoding='utf-8')       # 规则文字只在 menu.js 渲染，必须全经 textContent
    assert 'innerHTML' not in text and 'insertAdjacentHTML' not in text and 'outerHTML' not in text


# ---------------- 过敏提醒不参与筛选、不影响推荐 ----------------
def test_allergy_reminders_have_zero_effect_on_menu_and_package(tmp_path):
    def run(base, allergy):
        base.mkdir()
        env, path = setup(base, {'demo.json': example()}, allergy=allergy)
        open_shares(env[2], env[3], ('menu_library', 'requirement_tags'))
        m = menu(env)
        package = json.loads(Path(export_package(path, TOMORROW)['package']).read_text())
        package['snapshot'].pop('snapshot_versions'); package['candidate_template'].pop('snapshot_versions'); package['candidate_template'].pop('job_id'); package['snapshot'].pop('job_id')
        return (m['rules'], [g['group_id'] for g in m['groups']], m['state']['recommendation'], package)
    a = run(tmp_path / 'a', None)
    b = run(tmp_path / 'b', ['花生', '虾'])
    assert a == b
    assert 'allergy_reminders' not in json.dumps(b[3])


def test_menu_api_never_returns_allergy_text(tmp_path):
    env, _ = setup(tmp_path, {'demo.json': example()}, allergy=['SENT_ALLERGY_x1'])
    assert 'SENT_ALLERGY_x1' not in env[3]('menu?date=' + TOMORROW).get_data(as_text=True)
    assert env[3]('settings').json['diet']['allergy_reminders'] == ['SENT_ALLERGY_x1']       # 前端从设置接口取提醒


def test_ui_has_allergy_reminder_wording():
    text = (ROOT / 'waybeside' / 'static' / 'menu.js').read_text(encoding='utf-8')
    assert '你记录了：' in text and '不能作为过敏原规避工具' in text and '未标注的食材不等于不含该成分' in text


# ---------------- agent：RULE_EXCLUDED / 通用错误 / STALE ----------------
def candidate(tmp_path, package, **fields):
    data = dict(package['candidate_template'], **fields)
    file = tmp_path / 'cand.json'
    file.write_text(json.dumps(data))
    return file


def test_agent_submit_of_excluded_group_is_rule_excluded_and_groups_filtered(tmp_path):
    env, path = setup(tmp_path, {'demo.json': example()})
    open_shares(env[2], env[3], ('menu_library', 'requirement_tags'))
    package = json.loads(Path(export_package(path, TOMORROW)['package']).read_text())
    assert [g['group_id'] for g in package['snapshot']['groups']] == ['01', '03']     # 标签已共享：排除的组不出现
    with pytest.raises(CandidateError, match='RULE_EXCLUDED'):
        submit_candidate(path, candidate(tmp_path, package, group_id='02'))
    meals = {m['meal']: f"{'04' if m['meal'] == '早餐' else '01'}:{m['meal']}" for m in synthetic_meals()}
    with pytest.raises(CandidateError, match='RULE_EXCLUDED'):
        data = dict(package['candidate_template']); data.pop('group_id'); data['meals'] = meals
        file = tmp_path / 'm.json'; file.write_text(json.dumps(data)); submit_candidate(path, file)
    assert submit_candidate(path, candidate(tmp_path, package, group_id='03'))['status'] == 'READY_TO_REVIEW'


def synthetic_meals():
    return [{'meal': m} for m in ('早餐', '上午加餐', '午餐', '下午加餐', '晚餐')]


def test_agent_gets_generic_error_when_tags_not_shared(tmp_path):
    env, path = setup(tmp_path, {'demo.json': example()})
    open_shares(env[2], env[3], ('menu_library',))
    package = json.loads(Path(export_package(path, TOMORROW)['package']).read_text())
    assert [g['group_id'] for g in package['snapshot']['groups']] == ['01', '02', '03', '04']    # 未共享标签：全集
    with pytest.raises(CandidateError, match='GROUP_NOT_ALLOWED'):
        submit_candidate(path, candidate(tmp_path, package, group_id='02'))


def test_rule_change_makes_old_candidate_stale(tmp_path):
    env, path = setup(tmp_path, {'demo.json': example()})
    open_shares(env[2], env[3], ('menu_library', 'requirement_tags'))
    package = json.loads(Path(export_package(path, TOMORROW)['package']).read_text())
    file = candidate(tmp_path, package, group_id='03')
    changed = example(); changed['rules'][0]['message'] = '演示：规则改了一个字'
    (tmp_path / 'rules' / 'demo.json').write_text(json.dumps(changed), encoding='utf-8')
    with pytest.raises(CandidateError, match='DEPENDENCIES_CHANGED'):
        submit_candidate(path, file)


def test_ready_candidate_goes_stale_and_cannot_adopt_if_rules_now_exclude_it(tmp_path):
    files = example(); files['rules'][0]['match']['food_tags_any'] = ['nut']       # 先只排除含坚果的 02
    env, path = setup(tmp_path, {'demo.json': files})
    open_shares(env[2], env[3], ('menu_library', 'requirement_tags'))
    package = json.loads(Path(export_package(path, TOMORROW)['package']).read_text())
    submit_candidate(path, candidate(tmp_path, package, group_id='03'))
    cand = env[3]('menu/candidate?target_date=' + TOMORROW).json['candidate']
    (tmp_path / 'rules' / 'demo.json').write_text(json.dumps(example()), encoding='utf-8')
    r = env[2](f"menu/candidate/{cand['id']}/adopt", {'confirmed': True})
    assert r.status_code == 409


def test_rules_digest_is_audited_once_per_change(tmp_path):
    env, path = setup(tmp_path, {'demo.json': example()})
    menu(env); menu(env)
    from waybeside.storage import connect
    with connect(path) as db:
        rows = [json.loads(r['new_value']) for r in db.execute("SELECT new_value FROM audit WHERE entity='rules'")]
    assert len(rows) == 1 and rows[0]['files'][0]['name'] == 'demo.json' and len(rows[0]['files'][0]['sha256']) == 64
    (tmp_path / 'rules' / 'demo.json').write_text(json.dumps(dict(example(), title='改')), encoding='utf-8')
    menu(env)
    with connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM audit WHERE entity='rules'").fetchone()[0] == 2
