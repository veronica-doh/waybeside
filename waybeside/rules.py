"""营养规则筛选：只有机制，没有内置规则。

规则文件由用户或其营养师放在数据目录 rules/*.json，本程序只做标签匹配：
- 不允许数值条件（v0.1），不执行任何内容，文字一律当纯文本；
- 校验失败的整个文件不加载，并在界面如实说明；
- 过敏提醒（questionnaire diet.allergy_reminders）不参与筛选，规则引用它会被拒收；
- 食材没有标记 ≠ 安全，界面会提示未标注数量。
"""
import hashlib
import json
import re
from pathlib import Path

LIMIT = 256 * 1024
TAG = re.compile(r'^[a-z0-9_]{1,40}$')
ID = re.compile(r'^[A-Za-z0-9_\-]{1,40}$')
SCOPES = ('food', 'meal', 'group')
ACTIONS = ('exclude', 'warn')
MATCH_KEYS = ('food_tags_any', 'food_tags_all')
MESSAGE_LIMIT = 200
FILE_KEYS = {'schema_version', 'fictional', 'title', 'tag_vocabulary', 'food_tags', 'rules'}
RULE_KEYS = {'id', 'when_requirement', 'match', 'scope', 'action', 'message'}
# 规则不得把过敏提醒这个问卷字段名当作需求标签引用（过敏提醒不参与筛选）。
RESERVED_TAGS = {'allergy_reminders'}


class RuleFileError(ValueError):
    pass


def rules_directory(db_path):
    return Path(db_path).parent / 'rules'


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise RuleFileError('重复的 JSON 键')
        out[key] = value
    return out


def _tag_list(value, what):
    if not isinstance(value, list) or not value or len(value) > 50:
        raise RuleFileError(f'{what} 须为 1–50 项的列表')
    for item in value:
        if not isinstance(item, str) or not TAG.match(item):
            raise RuleFileError(f'{what} 含不合法标签')
    return value


def parse(raw, allergy_reminders=()):
    """校验一份规则文件的原始字节；返回规范化后的 dict，失败抛 RuleFileError。"""
    if len(raw) > LIMIT:
        raise RuleFileError('文件超过 256 KB')
    def constant(_):
        raise RuleFileError('含非有限数字')
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=constant)
    except RuleFileError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise RuleFileError('不是合法的 UTF-8 JSON')
    if not isinstance(data, dict) or set(data) - FILE_KEYS:
        raise RuleFileError('含未知字段')
    if data.get('schema_version') != 1 or type(data.get('schema_version')) is not int:
        raise RuleFileError('schema_version 须为 1')
    if type(data.get('fictional', False)) is not bool:
        raise RuleFileError('fictional 须为布尔值')
    title = data.get('title', '')
    if not isinstance(title, str) or len(title) > 60:
        raise RuleFileError('title 须为不超过 60 字的文字')
    vocabulary = data.get('tag_vocabulary', [])
    if not isinstance(vocabulary, list) or len(vocabulary) > 100:
        raise RuleFileError('tag_vocabulary 格式不对')
    labels = {}
    for item in vocabulary:
        if not isinstance(item, dict) or set(item) != {'id', 'label'} or not isinstance(item['id'], str) \
                or not TAG.match(item['id']) or not isinstance(item['label'], str) or not 1 <= len(item['label']) <= 20:
            raise RuleFileError('tag_vocabulary 条目格式不对')
        labels[item['id']] = item['label']
    food_tags = data.get('food_tags', {})
    if not isinstance(food_tags, dict) or len(food_tags) > 2000:
        raise RuleFileError('food_tags 格式不对')
    for food, tags in food_tags.items():
        if not isinstance(food, str) or not 1 <= len(food) <= 80:
            raise RuleFileError('food_tags 的食材 id 不合法')
        _tag_list(tags, 'food_tags 的标签')
    rules = data.get('rules', [])
    if not isinstance(rules, list) or len(rules) > 200:
        raise RuleFileError('rules 格式不对')
    reminders = {str(r).strip().casefold() for r in allergy_reminders}
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - RULE_KEYS or set(rule) != RULE_KEYS:
            raise RuleFileError('规则字段不全或含未知字段（v0.1 不支持数值条件）')
        if not isinstance(rule['id'], str) or not ID.match(rule['id']) or rule['id'] in seen:
            raise RuleFileError('规则 id 不合法或重复')
        seen.add(rule['id'])
        req = rule['when_requirement']
        if not isinstance(req, str) or not TAG.match(req):
            raise RuleFileError('when_requirement 须为标签 id')
        if req in RESERVED_TAGS or req.casefold() in reminders:
            raise RuleFileError('规则不能引用过敏提醒（过敏提醒只作提示，不参与筛选）')
        match = rule['match']
        if not isinstance(match, dict) or not match or set(match) - set(MATCH_KEYS):
            raise RuleFileError('match 只支持 food_tags_any / food_tags_all（不允许数值条件）')
        for key, value in match.items():
            _tag_list(value, 'match.' + key)
        if rule['scope'] not in SCOPES or not isinstance(rule['scope'], str):
            raise RuleFileError('scope 须为 food / meal / group')
        if not isinstance(rule['action'], str) or rule['action'] not in ACTIONS:
            raise RuleFileError('action 须为 exclude / warn')
        if not isinstance(rule['message'], str) or not 1 <= len(rule['message']) <= MESSAGE_LIMIT:
            raise RuleFileError('message 须为 1–200 字的文字')
    return {'fictional': data.get('fictional', False), 'title': title, 'labels': labels, 'food_tags': food_tags, 'rules': rules}


def load(db_path, allergy_reminders=()):
    """读取并校验 rules/*.json。返回 {files, rules, food_tags, fictional, digest}。"""
    directory = rules_directory(db_path)
    files, rules, food_tags, fictional = [], [], {}, False
    digests = []
    paths = sorted(p for p in directory.glob('*.json') if p.is_file()) if directory.is_dir() else []
    for path in paths:
        try:
            with path.open('rb') as stream:
                raw = stream.read(LIMIT + 1)
        except OSError:
            files.append({'name': path.name, 'ok': False, 'error': '无法读取'})
            digests.append([path.name, 'unreadable'])
            continue
        sha = hashlib.sha256(raw).hexdigest()
        digests.append([path.name, sha])
        try:
            parsed = parse(raw, allergy_reminders)
        except RuleFileError as error:
            files.append({'name': path.name, 'ok': False, 'error': str(error), 'sha256': sha})
            continue
        files.append({'name': path.name, 'ok': True, 'sha256': sha, 'title': parsed['title'], 'fictional': parsed['fictional'],
                      'rules': len(parsed['rules'])})
        fictional = fictional or parsed['fictional']
        for food, tags in parsed['food_tags'].items():
            food_tags.setdefault(food, set()).update(tags)
        rules.extend(dict(r, file=path.name) for r in parsed['rules'])
    digest = hashlib.sha256(json.dumps(digests, sort_keys=True).encode()).hexdigest()
    return {'files': files, 'rules': rules, 'food_tags': food_tags, 'fictional': fictional, 'digest': digest}


def library_food_tags(library_data, extra):
    """菜单库自带 foods[id].tags 与规则文件的 food_tags 取并集。"""
    merged = {fid: set(extra.get(fid, ())) for fid in library_data['food_reference']['foods']}
    for fid, food in library_data['food_reference']['foods'].items():
        merged[fid].update(food.get('tags', []))
    return merged


def _hit(match, tags):
    if 'food_tags_any' in match and not set(match['food_tags_any']) & tags:
        return False
    if 'food_tags_all' in match and not set(match['food_tags_all']) <= tags:
        return False
    return True


def evaluate(loaded, library_data, requirement_tags):
    """对每个菜单组求值。返回
    {configured, excluded: {gid: [msg]}, warnings: {gid: [{message, scope, where}]}, unlabeled: {gid: n}}。
    只有用户的 requirement_tags 与 when_requirement 相交的规则才生效。"""
    active = [r for r in loaded['rules'] if r['when_requirement'] in set(requirement_tags)]
    tags = library_food_tags(library_data, loaded['food_tags'])
    excluded, warnings, unlabeled = {}, {}, {}
    foods = library_data['food_reference']['foods']
    for group in library_data['days']:
        gid = str(group['group_id'])
        if not active:
            continue
        ids = {i['food_id'] for m in group['meals'] for i in m['ingredients']}
        missing = sorted(f for f in ids if not tags.get(f))
        if missing:
            unlabeled[gid] = len(missing)
        for rule in active:
            where = []
            for meal in group['meals']:
                for item in meal['ingredients']:
                    if _hit(rule['match'], tags.get(item['food_id'], set())):
                        where.append({'meal': meal['meal'], 'food': foods[item['food_id']]['name']})
            if not where:
                continue
            if rule['action'] == 'exclude':
                excluded.setdefault(gid, []).append(rule['message'])
            else:
                warnings.setdefault(gid, []).append({'message': rule['message'], 'scope': rule['scope'], 'where': where})
    return {'excluded': excluded, 'warnings': warnings, 'unlabeled': unlabeled, 'active_rules': len(active)}


def context(db, db_path, lib):
    """把当前数据目录的规则文件、用户标签和启用中的菜单库合在一起求值。"""
    from .storage import questionnaire
    diet = questionnaire(db, 'diet')
    tags = diet.get('requirement_tags', [])
    tags = [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []
    reminders = diet.get('allergy_reminders', [])
    reminders = [r for r in reminders if isinstance(r, str)] if isinstance(reminders, list) else []
    loaded = load(db_path, reminders)
    result = evaluate(loaded, json.loads(lib['content']), tags) if lib else {'excluded': {}, 'warnings': {}, 'unlabeled': {}, 'active_rules': 0}
    valid = [f for f in loaded['files'] if f['ok']]
    return {'loaded': loaded, 'result': result, 'tags': tags, 'digest': loaded['digest'],
            'files': loaded['files'], 'configured': bool(valid),
            'tags_without_rules': bool(tags) and not valid}


def record_digest(db, ctx):
    """规则文件 sha256 写入 audit（只在变化时写一行）。"""
    from .storage import audit
    last = db.execute("SELECT new_value FROM audit WHERE entity='rules' AND entity_id='digest' ORDER BY rowid DESC LIMIT 1").fetchone()
    if last and json.loads(last['new_value']).get('digest') == ctx['digest']:
        return
    if not last and not ctx['files']:
        return                              # 从未配置过规则文件：不留空记录
    audit(db, 'rules', 'digest', None, {'digest': ctx['digest'],
          'files': [{'name': f['name'], 'ok': f['ok'], 'sha256': f.get('sha256')} for f in ctx['files']]})


def public_view(ctx):
    """给界面的筛选状态。只含规则文件与菜单组信息，不含任何问卷答案文字。"""
    result = ctx['result']
    return {'configured': ctx['configured'], 'tags_without_rules': ctx['tags_without_rules'],
            'fictional': ctx['loaded']['fictional'], 'digest': ctx['digest'],
            'files': [{k: f[k] for k in ('name', 'ok', 'error', 'title', 'rules', 'fictional') if k in f} for f in ctx['files']],
            'hidden': [{'group_id': g, 'reasons': m} for g, m in sorted(result['excluded'].items())],
            'warnings': {g: w for g, w in result['warnings'].items()},
            'unlabeled': result['unlabeled']}
