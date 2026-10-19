"""合成数据的行为与进程验收；每项仅使用 pytest 临时目录。"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import http.cookiejar
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from waybeside import create_app
from waybeside.storage import connect, initialize, backup, restore
from support import make_env, make_course, session, act

TOTAL=10

@pytest.fixture
def env(tmp_path):
    e=make_env(tmp_path,today='2031-12-31')
    make_course(e,total_count=TOTAL)
    return e

def observation(**overrides):
    return dict(date='2031-03-04',time='00:15',session='4',note='虚构夜间观察',**overrides)

def update(env,identifier,action,day=None,**extra):
    return act(env,identifier,action,day,**extra)

@pytest.mark.parametrize('percent',[20,50,100])
def test_course_milestones(env,percent):
    target=TOTAL*percent//100
    for i in range(1,target+1): assert update(env,i,'complete').status_code==200
    assert env[3]().json['completed']==target
    if percent==100:
        summary=json.loads(env[3]('summary').data)
        assert summary['completed']==TOTAL and len(summary['treatment_session'])==TOTAL
        assert 'audit' not in summary and 'calendar' not in summary      # 摘要白名单：不含 audit

def test_off_schedule_day_needs_confirmation_and_is_marked(env):
    day=lambda s:__import__('datetime').date.fromisoformat(s['planned_date']).weekday()
    assert all(day(s) in (0,2,4) for s in env[3]().json['treatment_session'])
    assert update(env,4,'complete','2031-03-08').status_code==409          # 周六
    assert update(env,4,'postpone','2031-03-15').status_code==409
    assert env[2]('observations',dict(observation(),date='2031-03-08',session='')).status_code==200   # 观察记录不受治疗日限制
    assert env[3]().json['completed']==0
    assert update(env,4,'complete','2031-03-08',confirm_off_schedule=True).status_code==200
    assert env[3]().json['audit'][-1]['new_value']['off_schedule'] is True

def test_postpone_identity_and_calendar(env):
    assert update(env,4,'postpone','2031-04-02').status_code==200
    state=env[3]().json
    s=state['treatment_session'][3]
    assert (s['id'],s['ordinal'],s['status'],s['actual_date'])==(4,4,'postponed',None)
    assert state['calendar'][3]['date']=='2031-04-02'
    assert state['completed']==0

def test_correction_audit(env):
    assert update(env,4,'complete').status_code==200
    assert env[3]().json['completed']==1
    assert update(env,4,'correct').status_code==200
    state=env[3]().json
    assert state['completed']==0
    a=state['audit'][-1]
    assert a['actor']=='local-user' and a['occurred_at']
    assert a['old_value']['status']=='completed' and a['new_value']['status']=='planned'

def test_midnight_and_multiple_observations(env):
    a=env[2]('observations',observation()).json
    b=env[2]('observations',dict(observation(),time='01:15')).json
    assert a['id']!=b['id']
    rows=env[3]().json['observation']
    assert len(rows)==2 and all(r['session_id']==4 for r in rows)
    assert rows[0]['occurred_at']=='2031-03-04T00:15'

def test_edit_version_and_audit(env):
    a=env[2]('observations',observation()).json
    assert env[2]('observations',dict(a,note='虚构更正')).status_code==200
    assert env[2]('observations',dict(a,note='旧页面')).status_code==409
    state=env[3]().json
    assert len(state['observation'])==1 and state['observation'][0]['version']==2
    assert state['audit'][-1]['old_value'] is not None

def test_fresh_directory_is_empty_and_initialize_idempotent(tmp_path):
    app=create_app(tmp_path/'waybeside.sqlite',today='2031-12-31')
    path=app.config['DB_PATH']
    with connect(path) as db:
        for table in ('source','profile','habit','baseline','treatment_course','treatment_session','calendar','observation','audit','menu_library'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0,table
        meta=dict(db.execute('SELECT key,value FROM metadata').fetchall())
    assert set(meta)=={'app_id','schema_version','assistant_mode','assistant_mode_version'} and meta['app_id']=='waybeside'
    assert meta['assistant_mode']=='unset'
    initialize(path)
    with connect(path) as db:
        assert dict(db.execute('SELECT key,value FROM metadata').fetchall())==meta
        assert db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]==0

def test_backup_restore_without_agent(env,tmp_path):
    env[2]('observations',observation())
    update(env,4,'postpone','2031-04-02')
    response=env[2]('backup',{})
    assert response.status_code==200
    file=tmp_path/'backup.sqlite';file.write_bytes(response.data)
    target=tmp_path/'restored';restore(file,target)
    other=create_app(target/'waybeside.sqlite')
    for table in ('source','profile','habit','baseline','treatment_course','course_revision','treatment_session','calendar','observation','audit','schema_migrations','metadata'):
        with connect(env[0].config['DB_PATH']) as a,connect(other.config['DB_PATH']) as b:
            query=f'SELECT * FROM {table}'+(" WHERE key NOT LIKE 'assistant_mode%'" if table=='metadata' else '')
            assert [tuple(r) for r in a.execute(query)]==[tuple(r) for r in b.execute(query)]
    with connect(other.config['DB_PATH']) as b:
        assert b.execute("SELECT value FROM metadata WHERE key='assistant_mode'").fetchone()[0]=='unset'   # 恢复后方式重置
    with pytest.raises(ValueError): restore(file,target)

@pytest.mark.parametrize('headers',[{}, {'Origin':'http://evil.example'}, {'Origin':'null'}, {'Origin':'http://127.0.0.1:8765','X-CSRF-Token':'wrong'}])
def test_csrf_origin_rejection(env,headers):
    assert env[2]('observations',observation(),headers=headers).status_code==403
    assert not env[3]().json['observation']

def test_host_and_auth(env):
    app,c,post,get=env
    assert c.get('/api/state',base_url='http://localhost:8765').status_code==403
    assert c.post('/api/observations',base_url='http://evil.example',json=observation()).status_code==403
    assert app.test_client().get('/api/state',base_url='http://127.0.0.1:8765').status_code==401
    other=create_app(app.config['DB_PATH'])
    assert app.config['TOKEN']!=other.config['TOKEN'] and app.config['CSRF']!=other.config['CSRF']

def test_unknown_not_normal_and_forbidden_fields(env):
    r=env[2]('observations',observation()).json
    assert all(r[k]=='unknown' for k in ('diet','exercise','sleep','meditation'))
    assert r['symptoms']=={}                                   # 症状条目由用户自选自加，默认空
    assert r['minutes']==''
    assert env[2]('observations',dict(observation(),medication='更改')).status_code==400
    assert env[2]('observations',dict(observation(),symptoms={'食欲':'normal'})).status_code==400
    assert env[2]('observations',dict(observation(),session='999')).status_code==400

def test_script_remains_data(env):
    payload='<script>window.injected=true</script><img src=x onerror=alert(1)>'
    r=env[2]('observations',dict(observation(),note=payload))
    assert r.status_code==200 and r.json['note']==payload
    assert env[3]().json['observation'][0]['content']['note']==payload
    assert "script-src 'self'" in env[3]().headers['Content-Security-Policy']

def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]

class Service:
    def __init__(self,root,number):
        self.root=root;self.port=number;self.ready=root/'ready.json';self.proc=None
    def start(self):
        self.ready.unlink(missing_ok=True)
        self.proc=subprocess.Popen([sys.executable,str(ROOT/'run.py'),'--port',str(self.port),'--no-browser','--ready-file',str(self.ready)],env=dict(os.environ,WAYBESIDE_DATA_DIR=str(self.root/'data'),WAYBESIDE_TODAY='2031-12-31'),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        for _ in range(100):
            if self.ready.exists(): break
            if self.proc.poll() is not None: raise AssertionError(self.proc.stdout.read())
            time.sleep(.05)
        assert self.ready.exists()
        self.meta=json.loads(self.ready.read_text());self.base=f'http://127.0.0.1:{self.port}'
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf=self.call('login',{'token':self.meta['url'].split('#login=')[1]})['csrf']
        return self
    def call(self,path,data=None):
        req=urllib.request.Request(self.base+'/api/'+path,data=None if data is None else json.dumps(data).encode(),headers={'Origin':self.base,'Content-Type':'application/json','X-CSRF-Token':getattr(self,'csrf','')})
        with self.client.open(req,timeout=5) as res:return json.load(res)
    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate();self.proc.wait(timeout=8)
        if self.proc and self.proc.stdout:self.proc.stdout.close()

def make_course_over_http(svc):
    from support import course_settings
    svc.call('course',course_settings(total_count=TOTAL))
    svc.call('course/generate',{'expected_revision':svc.call('state')['course_revision']})

def test_real_process_restart_loopback_and_exit(tmp_path):
    svc=Service(tmp_path,port())
    try:
        svc.start();first_token=svc.meta['url']
        assert svc.meta['host']=='127.0.0.1'
        # 从实际监听 socket 的系统读回验证，非仅检查配置常量。
        if sys.platform=='darwin':
            listing=subprocess.check_output(['lsof','-nP','-a','-p',str(svc.proc.pid),'-iTCP','-sTCP:LISTEN'],text=True)
            assert f'127.0.0.1:{svc.port}' in listing and '*:' not in listing
        assert svc.call('state')['treatment_session']==[]          # 首启不带任何内容
        make_course_over_http(svc)
        svc.call('observations',dict(observation(),date='2031-03-03'))
        s=svc.call('state')['treatment_session'][0]
        svc.call('sessions/1',{'action':'complete','date':s['planned_date'],'version':s['version']})
        before=svc.call('state');svc.stop();assert svc.proc.poll() is not None
        svc.start();assert svc.meta['url']!=first_token
        assert svc.call('state')==before
        svc.call('exit',{});svc.proc.wait(timeout=8);assert svc.proc.returncode==0
    finally:svc.stop()

def test_single_instance_and_occupied_port(tmp_path):
    svc=Service(tmp_path,port())
    try:
        svc.start()
        make_course_over_http(svc)
        svc.call('sessions/1',{'action':'complete','date':'2031-03-03','version':1})
        for directory,number,expected in [(tmp_path/'data',port(),'已有实例'),(tmp_path/'other',svc.port,'端口已占用')]:
            r=subprocess.run([sys.executable,str(ROOT/'run.py'),'--port',str(number),'--no-browser'],env=dict(os.environ,WAYBESIDE_DATA_DIR=str(directory)),capture_output=True,text=True,timeout=10)
            assert r.returncode!=0 and expected in r.stderr
        assert svc.call('state')['completed']==1
    finally:svc.stop()

def test_aux_records_persist_and_validate(env):
    data={'question':'<script>虚构问题</script>','labs':[{'date':'2026-10-15','metric':'示例指标','unit':'虚构单位','lab':'虚构实验室','value':None,'low':None,'high':None}],'rhythm':{'blood':5,'chemistry':9},'celebration':True,'version':0}
    result=env[2]('aux',data)
    assert result.status_code==200 and result.json['version']==1
    stored=env[3]().json['observation'][0]
    assert stored['content']['question']==data['question'] and stored['content']['labs'][0]['value'] is None
    assert env[2]('aux',data).status_code==409
    invalid=dict(data,version=1,labs=[dict(data['labs'][0],low=10,high=2)])
    assert env[2]('aux',invalid).status_code==400
    assert env[3]().json['observation'][0]==stored
