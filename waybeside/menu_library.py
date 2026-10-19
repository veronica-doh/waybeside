"""整日五餐库、版本冻结和独立实际记录。"""
import argparse
import copy
from datetime import date, timedelta
import hashlib
import json
import math
import re
from pathlib import Path
from flask import abort, g, jsonify, request
from .storage import connect, encode, audit
from . import rules as rule_filter

MEALS = ('早餐', '上午加餐', '午餐', '下午加餐', '晚餐')
# 合成演示库的组数；真实菜单库的组数不设上限，至少一组。
MENU_GROUP_COUNT = 7
TAG = re.compile(r'^[a-z0-9_]{1,40}$')
NUTRIENTS = ('energy_kcal', 'protein_g', 'fat_g', 'carbohydrate_g', 'fibre_g')

def number(value, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError('数值范围')

def validate(data):
    if not isinstance(data, dict) or not isinstance(data.get('version'), str) or not data['version'].strip():
        raise ValueError('缺少版本')
    foods = data['food_reference']['foods']
    if not isinstance(foods, dict) or not foods:
        raise ValueError('缺少食材')
    for food in foods.values():
        if not isinstance(food,dict) or not isinstance(food.get('per_100g'),dict):
            raise ValueError('食材结构')
        for key in ('name', 'weight_basis'):
            if not isinstance(food.get(key), str) or not food[key].strip():
                raise ValueError('名称或重量口径')
        if not isinstance(food.get('source'), (str, dict)) or not food['source']:
            raise ValueError('缺少来源')
        for key in NUTRIENTS:
            if food['per_100g'][key] is not None:
                number(food['per_100g'][key])
        if 'tags' in food:
            tags = food['tags']
            if not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(t, str) or not TAG.match(t) for t in tags):
                raise ValueError('食材标签')
    days = data['days']
    if not isinstance(days, list) or len(days) < 1:
        raise ValueError('菜单里至少要有一组')
    ids = set()
    for group in days:
        if not isinstance(group,dict):
            raise ValueError('菜单组结构')
        gid = group['group_id']
        if type(gid) not in (str, int) or not str(gid).strip() or str(gid) in ids:
            raise ValueError('组号必须唯一')
        ids.add(str(gid))
        meals = group['meals']
        if not isinstance(meals,list) or any(not isinstance(m,dict) for m in meals):
            raise ValueError('餐次结构')
        if len(meals) != 5 or {m['meal'] for m in meals} != set(MEALS):
            raise ValueError('必须五个不同餐次')
        for meal in meals:
            if not isinstance(meal.get('title'), str) or not meal['title'].strip() or not isinstance(meal.get('steps'), (list, str)):
                raise ValueError('缺少餐名或步骤')
            if not isinstance(meal['ingredients'], list) or not meal['ingredients']:
                raise ValueError('缺少份量')
            for item in meal['ingredients']:
                if not isinstance(item,dict):
                    raise ValueError('食材份量结构')
                if item['food_id'] not in foods:
                    raise ValueError('未知食材')
                number(item['g'], True)
            for key in NUTRIENTS:
                values=[foods[x['food_id']]['per_100g'][key] for x in meal['ingredients']]
                if all(v is not None for v in values):
                    number(sum(v*x['g']/100 for v,x in zip(values,meal['ingredients'])))
        for key in NUTRIENTS:
            values=[(foods[x['food_id']]['per_100g'][key],x['g']) for m in meals for x in m['ingredients']]
            if all(v is not None for v,g in values):
                number(sum(v*g/100 for v,g in values))
    return data

def load_file(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('重复JSON键')
            result[key] = value
        return result
    return validate(json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique))

def digest(data):
    return hashlib.sha256(encode(data).encode()).hexdigest()

def synthetic_library():
    return {'version':'synthetic-7-v1','fictional':True,'role':'合成界面测试','purpose':'非饮食建议',
        'weight_rules':'演示称重','limitations':'非真实菜单',
        'food_reference':{'foods':{'demo':{'name':'演示食材','weight_basis':'演示生重','source':'合成数值',
            'per_100g':dict(zip(NUTRIENTS,(100,5,2,15,None)))}}},
        'days':[{'day':i,'group_id':f'{i:02}','meals':[{'meal':meal,'title':f'演示第{i:02}组 · {meal}',
            'ingredients':[{'food_id':'demo','g':50+i}],'steps':['仅软件演示，不是烹饪建议。']}
            for meal in MEALS]} for i in range(1, MENU_GROUP_COUNT + 1)]}

def import_library(path, data, expected=None):
    """校验后启用菜单库。已有库时换版本必须带当前库的 digest（expected），否则 409。
    库内 fictional:true 只用于界面横幅，不改变任何权限。"""
    validate(data)
    fictional = data.get('fictional') is True
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        current=active(db)
        checksum=digest(data)
        if expected!=(current['digest'] if current else None):
            abort(409)
        old=db.execute('SELECT digest FROM menu_library WHERE version=?',(data['version'],)).fetchone()
        if old:
            if old['digest']!=checksum:
                raise ValueError('同版本不同内容')
            return {'version':data['version'],'digest':checksum,'idempotent':True}
        db.execute('INSERT INTO menu_library VALUES(?,?,?,?)',(data['version'],checksum,encode(data),1 if fictional else 0))
        db.execute('INSERT INTO menu_active VALUES(1,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version',(data['version'],))
        audit(db,'menu_library',data['version'],None,{'digest':checksum,'fictional':fictional})
        return {'version':data['version'],'digest':checksum,'idempotent':False}

def active(db):
    row=db.execute('SELECT l.* FROM menu_library l JOIN menu_active a ON a.version=l.version').fetchone()
    return dict(row) if row else None

def frozen(library,gid):
    data=json.loads(library['content'])
    group=next((g for g in data['days'] if str(g['group_id'])==str(gid)),None)
    if not group:
        raise ValueError('未知组号')
    group=copy.deepcopy(group)
    group['group_id']=str(group['group_id'])
    foods=data['food_reference']['foods']
    for meal in group['meals']:
        for item in meal['ingredients']:
            item['food']=foods[item['food_id']]
            item['unit']='g'
        meal['nutrition']={key:None if any(foods[x['food_id']]['per_100g'][key] is None for x in meal['ingredients'])
            else sum(foods[x['food_id']]['per_100g'][key]*x['g']/100 for x in meal['ingredients']) for key in NUTRIENTS}
    group['nutrition']={key:None if any(m['nutrition'][key] is None for m in group['meals'])
        else sum(m['nutrition'][key] for m in group['meals']) for key in NUTRIENTS}
    group.update(library_version=library['version'],library_hash=library['digest'])
    return group

def read_day(db,target):
    row=db.execute('SELECT * FROM menu_day WHERE target=?',(target,)).fetchone()
    return (json.loads(row['content']),row['revision']) if row else ({'recommendation':None,'draft':None,
        'adopted':None,'previous':[],'locks':{},'actual':{}},0)

def save_day(db,target,state,revision):
    db.execute('INSERT INTO menu_day VALUES(?,?,?) ON CONFLICT(target) DO UPDATE SET revision=excluded.revision,content=excluded.content',
        (target,revision+1,encode(state)))
    db.execute('INSERT INTO menu_event(target,content) VALUES(?,?)',(target,encode(state)))

def recommend(db,library,excluded=()):
    groups=[str(g['group_id']) for g in json.loads(library['content'])['days']]
    prefs={r['group_id']:dict(r) for r in db.execute('SELECT * FROM menu_preference')}
    used={}
    for row in db.execute('SELECT target,content FROM menu_day'):
        group=json.loads(row['content']).get('adopted')
        if group:
            gid=group['group_id']
            used[gid]=max(used.get(gid,''),row['target'])
    choices=[g for g in groups if not prefs.get(g,{}).get('avoided') and g not in excluded]
    return min(choices,key=lambda g:(g in used,used.get(g,''),-prefs.get(g,{}).get('favorite',0),g)) if choices else None

def check_locks(state,group):
    for meal,locked in state['locks'].items():
        if locked:
            before=next((m for m in (state['adopted'] or {}).get('meals',[]) if m['meal']==meal),None)
            after=next((m for m in (group or {}).get('meals',[]) if m['meal']==meal),None)
            if before!=after:
                abort(409)

def register(app):
    path=app.config['DB_PATH']
    def today():
        return app.config.get('TODAY') or date.today().isoformat()
    def target_day(value):
        if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:
            raise ValueError('日期格式')
        return value
    def payload(db,target,lib):
        state,rev=read_day(db,target)
        return dict(target_date=target,revision=rev,library_version=lib['version'],library_hash=lib['digest'],**state)

    @app.post('/api/menu/import-preview')
    def import_preview():
        data=request.get_json()
        validate(data)
        with connect(path) as db:
            lib=active(db)
            return jsonify(version=data['version'],digest=digest(data),digest_kind='canonical-json-sha256',groups=len(data['days']),meals=5,
                expected=lib['digest'] if lib else None,identity='演示（虚构）菜单' if data.get('fictional') is True else '本人菜单库',
                message='确认后启用完整菜单库；旧预览失效，已采用历史保持冻结。')

    @app.post('/api/menu/import-confirm')
    def import_confirm():
        data=request.get_json()
        if set(data)!={'menu','digest','expected','confirmed'} or data['confirmed'] is not True or digest(data['menu'])!=data['digest']:
            raise ValueError('必须确认同一份预览')
        try:
            return jsonify(import_library(path,data['menu'],expected=data['expected']))
        except ValueError as error:
            return jsonify(error=str(error)),400

    @app.get('/api/menu')
    def menu_state():
        target=target_day(request.args.get('date',str(date.fromisoformat(today())+timedelta(days=1))))
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            lib=active(db)
            if not lib:
                return jsonify(enabled=False,today=today())
            ctx=rule_filter.context(db,path,lib)
            rule_filter.record_digest(db,ctx)
            hidden=set(ctx['result']['excluded'])
            state,rev=read_day(db,target)
            stale_pick=(state['recommendation'] or {}).get('group_id') in hidden and state['draft'] is None and state['adopted'] is None
            if (state['recommendation'] is None and state['draft'] is None) or stale_pick:
                state['recommendation']={'group_id':recommend(db,lib,hidden),'library_hash':lib['digest']}
                save_day(db,target,state,rev)
            return jsonify(enabled=True,rules=rule_filter.public_view(ctx),today=today(),tomorrow=str(date.fromisoformat(today())+timedelta(days=1)),
                synthetic=bool(lib['synthetic']),state=payload(db,target,lib),
                groups=[frozen(lib,g['group_id']) for g in json.loads(lib['content'])['days']],
                preferences=[dict(r) for r in db.execute('SELECT * FROM menu_preference')],
                history=[{'target_date':r['target'],'revision':r['revision'],**json.loads(r['content'])}
                         for r in db.execute('SELECT * FROM menu_day ORDER BY target DESC')])

    @app.post('/api/menu/action')
    def action():
        data=request.get_json()
        if set(data)-{'action','target_date','revision','library_hash','group_id','meal','status','note','locked','favorite','avoided','rule_confirmed'}:
            raise ValueError('未知字段')
        target=target_day(data['target_date'])
        with connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            lib=active(db)
            if not lib or data.get('library_hash')!=lib['digest']:
                abort(409)
            state,rev=read_day(db,target)
            if type(data.get('revision')) is not int or data['revision']!=rev:
                abort(409)
            before=copy.deepcopy(state)
            op=data['action']
            if op in ('select','adopt'):
                if target<today():
                    raise ValueError('历史日期不能重新选用')
                group=frozen(lib,data['group_id'])
                hidden_group=group['group_id'] in rule_filter.context(db,path,lib)['result']['excluded']
                if hidden_group and data.get('rule_confirmed') is not True:
                    return jsonify(code='RULE_CONFIRM_REQUIRED',error='这一组被你的规则隐藏了。要看的话请先确认。'),409
                if hidden_group and g.actor=='agent':
                    # rule_confirmed 对助手只是意图标记不是护栏：允许，但单独留痕，页面据此标注。
                    audit(db,'menu_rule_override',target,None,{'op':op,'group_id':group['group_id']})
                if op=='select':
                    state['draft']=group
                else:
                    preview=state['draft'] or (frozen(lib,state['recommendation']['group_id']) if state['recommendation'] and state['recommendation']['group_id'] else None)
                    preview_hash=state['draft']['library_hash'] if state['draft'] else (state['recommendation'] or {}).get('library_hash')
                    if not preview or preview['group_id']!=group['group_id'] or preview_hash!=lib['digest']:
                        abort(409)
                    if state['adopted']==group:
                        abort(409)
                    check_locks(state,group)
                    state['previous'].append(state['adopted'])
                    state['adopted']=group
                    state['draft']=None
            elif op=='cancel_draft':
                if state['draft'] is None:
                    abort(409)
                state['draft']=None
            elif op=='undo':
                if not state['previous']:
                    abort(409)
                check_locks(state,state['previous'][-1])
                state['adopted']=state['previous'].pop()
            elif op in ('lock','actual'):
                meal=data['meal']
                if meal not in MEALS:
                    raise ValueError('未知餐次')
                if op=='lock':
                    if type(data['locked']) is not bool:
                        raise ValueError('锁定类型')
                    if data['locked'] and not state['adopted']:
                        raise ValueError('先采用再锁餐')
                    state['locks'][meal]=data['locked']
                else:
                    if target>today():
                        raise ValueError('未来事实')
                    if state['locks'].get(meal):
                        abort(409)
                    if data['status'] not in ('unknown','done','partial','other','not_done'):
                        raise ValueError('实际状态')
                    note=data.get('note','')
                    if not isinstance(note,str) or len(note)>500:
                        raise ValueError('备注长度')
                    state['actual'][meal]={'status':data['status'],'note':note,'menu_snapshot':state['adopted'],'intake_nutrition':None}
                    state['locks'][meal]=True
            elif op=='preference':
                frozen(lib,data['group_id'])
                if type(data['favorite']) is not bool or type(data['avoided']) is not bool:
                    raise ValueError('偏好类型')
                db.execute('INSERT INTO menu_preference VALUES(?,?,?) ON CONFLICT(group_id) DO UPDATE SET favorite=excluded.favorite,avoided=excluded.avoided',
                    (str(data['group_id']),data['favorite'],data['avoided']))
            else:
                raise ValueError('未知操作')
            save_day(db,target,state,rev)
            audit(db,'menu_'+op,target,before,state)
            return jsonify(payload(db,target,lib))

    @app.get('/api/menu/shopping')
    def shopping():
        dates=request.args.getlist('date')
        if not dates:
            raise ValueError('请选择已采用日期')
        result={}
        with connect(path) as db:
            for target in sorted(set(dates)):
                target_day(target)
                state,_=read_day(db,target)
                if not state['adopted']:
                    raise ValueError('该日期未采用')
                for meal in state['adopted']['meals']:
                    for item in meal['ingredients']:
                        key=(item['food_id'],item['food']['weight_basis'],item['unit'])
                        row=result.setdefault(key,{'food_id':key[0],'name':item['food']['name'],'weight_basis':key[1],'unit':key[2],'g':0})
                        row['g']+=item['g']
        return jsonify(dates=sorted(set(dates)),items=list(result.values()),actual_intake=False)

def main():
    parser=argparse.ArgumentParser(description='校验菜单库文件（导入请在网页里确认，或经助手 api 通道）')
    parser.add_argument('command',choices=('validate',))
    parser.add_argument('file',type=Path)
    args=parser.parse_args()
    try:
        data=load_file(args.file)
        print(encode({'valid':True,'groups':len(data['days']),'meals_per_group':5,'canonical_digest':digest(data),
            'raw_file_sha256':hashlib.sha256(args.file.read_bytes()).hexdigest()}))
    except (ValueError,KeyError,TypeError,OSError):
        parser.exit(2,'未通过：请检查结构和数值。\n')

if __name__=='__main__':
    main()
