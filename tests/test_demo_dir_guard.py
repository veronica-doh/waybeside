"""--demo 必须显式传入；目录不安全时拒绝并退出（DEMO_DIR_NOT_SAFE），绝不碰真实数据。"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import http.cookiejar
from pathlib import Path
import pytest
from waybeside import storage
from waybeside.storage import check_demo_directory, connect, initialize, default_directory, data_directory, is_demo
ROOT=Path(__file__).resolve().parents[1]

def env_for(tmp_path,data=None):
    env={k:v for k,v in os.environ.items() if k!='WAYBESIDE_DATA_DIR'}
    env.update(HOME=str(tmp_path/'home'),XDG_DATA_HOME=str(tmp_path/'home/.local/share'),WAYBESIDE_TODAY='2031-12-31')
    if data is not None:env['WAYBESIDE_DATA_DIR']=str(data)
    return env

def run_demo(tmp_path,data,*extra,port=0):
    return subprocess.run([sys.executable,str(ROOT/'run.py'),'--demo','--no-browser','--port',str(port),*extra],env=env_for(tmp_path,data),capture_output=True,text=True,timeout=20)

def snapshot(directory):
    return sorted(str(p.relative_to(directory)) for p in directory.rglob('*')) if directory.exists() else None

def test_refuses_non_empty_directory_without_database(tmp_path):
    directory=tmp_path/'mine';directory.mkdir();(directory/'notes.txt').write_text('我的文件')
    before=snapshot(directory)
    result=run_demo(tmp_path,directory)
    assert result.returncode!=0 and 'DEMO_DIR_NOT_SAFE' in result.stderr
    assert snapshot(directory)==before and (directory/'notes.txt').read_text()=='我的文件'

def test_refuses_existing_database_without_demo_mark(tmp_path):
    directory=tmp_path/'real-data'
    initialize(directory/'waybeside.sqlite')
    with connect(directory/'waybeside.sqlite') as db:
        db.execute("INSERT INTO treatment_course(id,treatment_type,schedule_kind,version) VALUES(1,'other','manual',1)")
    before=snapshot(directory)
    result=run_demo(tmp_path,directory)
    assert result.returncode!=0 and 'DEMO_DIR_NOT_SAFE' in result.stderr
    assert snapshot(directory)==before
    with connect(directory/'waybeside.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM treatment_session').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM metadata WHERE key='demo'").fetchone()[0]==0

def test_refuses_the_real_default_directory(tmp_path,monkeypatch):
    if sys.platform=='win32':pytest.skip('路径由 LOCALAPPDATA 决定，另测')
    monkeypatch.setenv('HOME',str(tmp_path/'home'));monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path/'home/.local/share'))
    real=default_directory(False)
    assert default_directory(True)!=real and default_directory(True).name=='WayBeside-Demo' and real.name=='WayBeside'
    with pytest.raises(RuntimeError,match='DEMO_DIR_NOT_SAFE'):check_demo_directory(real)
    assert not real.exists()
    result=run_demo(tmp_path,real)                     # 显式指向真实默认目录（空）也拒绝
    assert result.returncode!=0 and 'DEMO_DIR_NOT_SAFE' in result.stderr and not real.exists()

def test_default_demo_directory_is_separate_from_real(tmp_path,monkeypatch):
    if sys.platform=='win32':pytest.skip('路径由 LOCALAPPDATA 决定')
    monkeypatch.setenv('HOME',str(tmp_path/'home'));monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path/'home/.local/share'));monkeypatch.delenv('WAYBESIDE_DATA_DIR',raising=False)
    assert data_directory(True)==default_directory(True)!=data_directory(False)
    check_demo_directory(data_directory(True))        # 不存在的独立目录：允许

def test_accepts_empty_missing_and_marked_directories(tmp_path):
    check_demo_directory(tmp_path/'new')
    empty=tmp_path/'empty';empty.mkdir();check_demo_directory(empty)
    (empty/'.instance.lock').write_text('0');check_demo_directory(empty)     # 仅有实例锁仍算空

def start_and_read(tmp_path,data,demo):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    ready=tmp_path/f'ready-{time.time_ns()}.json'
    args=[sys.executable,str(ROOT/'run.py'),'--no-browser','--port',str(port),'--ready-file',str(ready)]+(['--demo'] if demo else [])
    proc=subprocess.Popen(args,env=env_for(tmp_path,data),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    try:
        for _ in range(100):
            if ready.exists():break
            assert proc.poll() is None,proc.stdout.read()
            time.sleep(.05)
        base=f'http://127.0.0.1:{port}'
        client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        token=json.loads(ready.read_text())['url'].split('#login=')[1]
        client.open(urllib.request.Request(base+'/api/login',data=json.dumps({'token':token}).encode(),headers={'Origin':base,'Content-Type':'application/json'}),timeout=5).read()
        return json.load(client.open(base+'/api/state',timeout=5)),json.load(client.open(base+'/api/menu',timeout=5))
    finally:
        proc.terminate();proc.wait(timeout=10);proc.stdout.close()

def test_demo_creates_marked_fictional_data_and_is_reusable(tmp_path):
    data=tmp_path/'demo-data'
    state,menu=start_and_read(tmp_path,data,True)
    assert state['demo'] is True and state['onboarded'] is True
    course=state['treatment_course'][0]
    assert (course['treatment_type'],course['schedule_kind'],course['total_count'],course['count_unit'])==('chemotherapy','manual',6,'cycle')
    assert len(state['treatment_session'])==6 and all(s['planned_date'].startswith('2031-') for s in state['treatment_session'])
    assert menu['enabled'] is True and menu['synthetic'] is True
    assert is_demo(data/'waybeside.sqlite')
    again,_=start_and_read(tmp_path,data,True)                      # 已带标记：允许再次 --demo，不重复写入
    assert len(again['treatment_session'])==6 and len(again['audit'])==len(state['audit'])
    plain,_=start_and_read(tmp_path,data,False)                     # 普通启动指向演示目录：同样带 demo 标记（横幅）
    assert plain['demo'] is True

def test_plain_start_never_creates_demo_content(tmp_path):
    state,menu=start_and_read(tmp_path,tmp_path/'plain',False)
    assert state['demo'] is False and state['treatment_session']==[] and menu['enabled'] is False
