"""agent 共享开关：七类默认全关；exact/bool/乐观锁；脱敏 audit；类别关闭时的间接泄露封堵。"""
import json
import re
from pathlib import Path
import pytest
from support import make_env, open_shares, SHARE_CATEGORIES
from waybeside.agent_door import export_package, submit_candidate
from waybeside.menu_door import CandidateError
from waybeside.menu_library import synthetic_library, import_library
from waybeside.storage import connect

TARGET = '2026-10-17'

def share(env, category, enabled, version):
    return env[2]('agent-share', {'category': category, 'enabled': enabled, 'version': version})

def test_seven_categories_default_off(tmp_path):
    e = make_env(tmp_path)
    rows = e[3]('agent-share').json
    assert [r['category'] for r in rows] == list(SHARE_CATEGORIES)
    assert all(r['enabled'] is False and r['version'] == 1 for r in rows)

@pytest.mark.parametrize('body,status', [
    ({'category': 'age_band', 'enabled': True}, 400),                                        # 缺 version
    ({'category': 'age_band', 'enabled': True, 'version': 1, 'extra': 1}, 400),              # exact 拒多余键
    ({'category': 'nickname', 'enabled': True, 'version': 1}, 400),                          # 未知类别
    ({'category': 'allergy_reminders', 'enabled': True, 'version': 1}, 400),                 # 永不出包的字段没有开关
    ({'category': ['age_band'], 'enabled': True, 'version': 1}, 400),
    ({'category': 'age_band', 'enabled': 1, 'version': 1}, 400),                             # 必须是 bool
    ({'category': 'age_band', 'enabled': 'true', 'version': 1}, 400),
    ({'category': 'age_band', 'enabled': True, 'version': 2}, 409),                          # 乐观锁
    ({'category': 'age_band', 'enabled': True, 'version': '1'}, 409),
    ({'category': 'age_band', 'enabled': True, 'version': True}, 409),
])
def test_post_validation(tmp_path, body, status):
    e = make_env(tmp_path)
    assert e[2]('agent-share', body).status_code == status
    assert all(r['enabled'] is False and r['version'] == 1 for r in e[3]('agent-share').json)     # 失败不改任何开关

def test_toggle_bumps_version_and_audit_is_redacted(tmp_path):
    e = make_env(tmp_path)
    r = share(e, 'age_band', True, 1)
    assert r.status_code == 200 and r.json == {'category': 'age_band', 'enabled': True, 'version': 2}
    assert share(e, 'age_band', True, 1).status_code == 409                                  # 旧版本再提交
    assert share(e, 'age_band', False, 2).json['version'] == 3
    with connect(e[0].config['DB_PATH']) as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM audit WHERE entity='agent_share' ORDER BY id")]
    assert len(rows) == 2
    for row in rows:
        assert row['old_value'] is None and row['new_value'] is None
        d = json.loads(row['detail'])
        assert d['category'] == 'age_band' and d['fields'] == ['enabled'] and set(d) == {'category', 'enabled', 'was', 'fields', 'version', 'at', 'actor'}
    assert [json.loads(r['detail'])['enabled'] for r in rows] == [True, False]

def library_env(tmp_path):
    e = make_env(tmp_path, today='2026-10-16')
    import_library(tmp_path / 'waybeside.sqlite', synthetic_library())
    return e

def export(env):
    result = export_package(env[0].config['DB_PATH'], TARGET)
    return json.loads(Path(result['package']).read_text())

def test_share_disabled_without_menu_library_switch(tmp_path):
    e = library_env(tmp_path)
    for category in SHARE_CATEGORIES[1:]:                                                    # 除 menu_library 外全开也没用
        rows = {r['category']: r for r in e[3]('agent-share').json}
        share(e, category, True, rows[category]['version'])
    with pytest.raises(CandidateError, match='SHARE_DISABLED'):
        export_package(e[0].config['DB_PATH'], TARGET)
    file = tmp_path / 'x.json'; file.write_text(json.dumps({'job_id': 'x'}))
    with pytest.raises(CandidateError, match='SHARE_DISABLED'):
        submit_candidate(e[0].config['DB_PATH'], file)

def test_agent_door_has_no_command_to_change_switches():
    import waybeside.agent_door as door
    parser_source = Path(door.__file__).read_text()
    assert "add_parser('export')" in parser_source and "add_parser('submit')" in parser_source
    commands = set(re.findall(r"add_parser\('([a-z-]+)'", parser_source))
    assert commands == {'status', 'open-url', 'export', 'submit', 'api', 'dump', 'backup'}
    assert 'agent_share' not in parser_source and 'assistant-mode' not in parser_source

def test_menu_library_only_exposes_groups(tmp_path):
    e = library_env(tmp_path)
    open_shares(e[2], e[3], ('menu_library',))
    package = export(e)
    snap = package['snapshot']
    assert len(snap['groups']) == len(synthetic_library()['days'])
    assert not {'current', 'avoided_group_ids', 'favorite_group_ids', 'recent_records',
                'requirement_tags', 'age_band', 'course_progress'} & set(snap)
    assert set(snap['snapshot_versions']) == {'library_hash', 'state_digest'}                 # 只放聚合摘要
    assert package['candidate_template']['group_id'] is None                                   # 采用状态未开：不预填组

def test_adoption_state_gates_template_and_current(tmp_path):
    e = library_env(tmp_path)
    open_shares(e[2], e[3], ('menu_library', 'adoption_state'))
    package = export(e)
    assert 'current' in package['snapshot'] and package['candidate_template']['group_id'] is not None

def test_category_fields_appear_exactly_when_enabled(tmp_path):
    e = library_env(tmp_path)
    e[2]('onboarding/basic', {'answers': {'helper_mode': 'self', 'age_band': '65_plus'}, 'version': 0})
    e[2]('onboarding/diet', {'answers': {'custom_tags': [{'id': 'sentinel_tag_7f3a', 'label': '测试'}], 'requirement_tags': ['sentinel_tag_7f3a']}, 'version': 0})
    open_shares(e[2], e[3], ('menu_library',))
    off = json.dumps(export(e), ensure_ascii=False)
    assert 'sentinel_tag_7f3a' not in off and '65_plus' not in off
    open_shares(e[2], e[3], ('requirement_tags',))
    on = export(e)
    assert on['snapshot']['requirement_tags'] == ['sentinel_tag_7f3a'] and json.dumps(on).count('sentinel_tag_7f3a') == 1
    assert len(on['snapshot']['groups']) == len(synthetic_library()['days'])                  # 标签不改变 groups
    open_shares(e[2], e[3], ('age_band', 'course_progress'))
    full = export(e)
    assert full['snapshot']['age_band'] == '65_plus' and json.dumps(full).count('65_plus') == 1
    assert full['snapshot']['course_progress'] == {'completed_count': 0, 'total_count': None}

def test_generic_errors_when_category_hidden(tmp_path):
    e = library_env(tmp_path)
    open_shares(e[2], e[3], ('menu_library',))
    package = export(e)
    groups = [g['group_id'] for g in package['snapshot']['groups']]
    # 把第一个组标为“暂不想吃”（经应用自己的接口），再让助手提交它
    menu = e[3]('menu?date=' + TARGET).json
    state = menu['state']
    r = e[2]('menu/action', dict(action='preference', target_date=TARGET, revision=state['revision'], library_hash=state['library_hash'], group_id=groups[0], favorite=False, avoided=True))
    assert r.status_code == 200, r.data
    package = export(e)
    template = dict(package['candidate_template'], group_id=groups[0])
    file = tmp_path / 'c.json'; file.write_text(json.dumps(template))
    with pytest.raises(CandidateError, match='GROUP_NOT_ALLOWED'):                           # preferences 未开：不说明原因
        submit_candidate(e[0].config['DB_PATH'], file)
    open_shares(e[2], e[3], ('preferences',))
    package = export(e)
    file.write_text(json.dumps(dict(package['candidate_template'], group_id=groups[0])))
    with pytest.raises(CandidateError, match='AVOIDED_MENU'):
        submit_candidate(e[0].config['DB_PATH'], file)

def test_changing_a_switch_makes_old_candidates_stale(tmp_path):
    e = library_env(tmp_path)
    open_shares(e[2], e[3], ('menu_library',))
    first = export(e)
    file = tmp_path / 'c.json'
    file.write_text(json.dumps(dict(first['candidate_template'], group_id='01')))
    open_shares(e[2], e[3], ('age_band',))
    with pytest.raises(CandidateError) as info:
        submit_candidate(e[0].config['DB_PATH'], file)
    assert info.value.code == 'DEPENDENCIES_CHANGED'
