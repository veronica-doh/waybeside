"""i18n 抽取机制：文案包完整、前端引用的 key 全部存在、服务端注入、HTML 兜底文字与文案包一致。"""
import json
import re
from pathlib import Path
from support import make_env, ORIGIN

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'waybeside' / 'static'
PACK = json.loads((ROOT / 'waybeside' / 'i18n' / 'zh-CN.json').read_text(encoding='utf-8'))
DYNAMIC = ([f'wizard.title.{b}' for b in ('basic', 'treatment', 'diet', 'habit')]
           + [f'wizard.{b}.{x}' for b in ('basic', 'treatment', 'diet', 'habit') for x in 'hp']
           + [f'share.{c}.{x}' for c in ('menu_library', 'adoption_state', 'preferences', 'recent_meal_status',
                                         'requirement_tags', 'age_band', 'course_progress') for x in ('title', 'copy')
              if (c, x) != ('menu_library', 'copy')]
           # 按使用方式显示的文案：前缀 + .menu_only / .full，由 tm() 在运行时拼出。
           + [f'{prefix}.{mode}' for mode in ('menu_only', 'full')
              for prefix in ('wizard.age_hint', 'wizard.allergy_legend', 'wizard.note_label', 'settings.export.copy',
                             'export.intro', 'export.never', 'export.eyebrow', 'share.menu_library.copy', 'menu.candidate_empty',
                             'menu.candidate_tail', 'menu.import_copy')]
           + [f'mode.{mode}.{q}' for mode in ('menu_only', 'full') for q in ('name', 'sees', 'does', 'you')]
           + [f'mode.q.{q}' for q in ('sees', 'does', 'you')]
           # 助手动作与标注：类别由服务端给出，界面按 activity.<kind> 取文案。
           + [k for k in PACK if k.startswith(('activity.', 'mark.off_schedule.'))])


def test_pack_is_flat_nonempty_strings():
    assert PACK and all(isinstance(k, str) and isinstance(v, str) and v for k, v in PACK.items())


def test_every_referenced_key_exists_and_pack_has_no_orphans():
    used = set(DYNAMIC)
    for js in STATIC.glob('*.js'):
        used |= set(re.findall(r"\bt\('([\w.]+)'[,)]", js.read_text(encoding='utf-8')))
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    used |= set(re.findall(r'data-i18n(?:-aria-label)?="([\w.]+)"', html))
    assert used - set(PACK) == set(), '前端引用了文案包里没有的 key'
    assert set(PACK) - used == set(), '文案包里有没人引用的 key'


def test_html_fallback_text_matches_pack():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    for key, text in re.findall(r'data-i18n="([\w.]+)"[^>]*>([^<]*)<', html):
        assert PACK[key] == text, key


def test_script_order_pack_then_helper_then_app():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    order = [html.index(s) for s in ('i18n-data.js', 'i18n.js', 'journey.js')]
    assert order == sorted(order)


def test_served_pack_matches_file(tmp_path):
    _, client, _, _ = make_env(tmp_path)
    r = client.get('/i18n-data.js', base_url=ORIGIN)
    assert r.status_code == 200 and r.mimetype == 'text/javascript'
    body = r.get_data(as_text=True)
    assert body.startswith('window.I18N=') and json.loads(body[len('window.I18N='):-1]) == PACK


def test_placeholders_are_named_only():
    for key, text in PACK.items():
        assert not re.search(r'\{\d', text), key


def test_every_activity_kind_has_a_label():
    from waybeside.activity import kind_of
    entities = ('observation', 'questionnaire', 'treatment_course', 'treatment_session', 'menu_library', 'menu_preference',
                'menu_select', 'menu_adopt', 'menu_undo', 'menu_cancel_draft', 'menu_lock', 'menu_actual',
                'menu_rule_override', 'menu_candidate', 'rules')
    kinds = {kind_of(e, 'aux' if e == 'observation' else 'x', None) for e in entities} | {kind_of('observation', 'abc', None)}
    for kind in kinds:
        assert f'activity.{kind}' in PACK, kind
    for action in ('settings', 'reschedule', 'generate', 'complete', 'correct', 'postpone', 'cancel', 'restore', 'schedule', 'record'):
        key = f'activity.treatment_session.{action}'
        assert key in PACK or action in ('settings',), key
    for status in ('WAITING_EXTERNAL', 'READY_TO_REVIEW', 'APPLIED', 'DECLINED', 'STALE'):
        assert f'activity.menu_candidate.{status}' in PACK
