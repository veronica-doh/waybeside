"""开发与双击入口：固定回环地址、独占数据锁、端口占用即停止。"""
import argparse
import json
import os
from datetime import date
from pathlib import Path
import signal
import threading
import webbrowser
from waitress import create_server
from waybeside import create_app
from waybeside import login, service
from waybeside.storage import data_directory, restore, check_demo_directory

class InstanceLock:
    def __init__(self, directory):
        self.directory=directory
        self.file=None

    def __enter__(self):
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.file=(self.directory/'.instance.lock').open('a+b')
        self.file.seek(0)
        self.file.write(b'0')
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('此数据目录已有实例运行，请返回已打开的窗口。')
        return self

    def __exit__(self,*args):
        self.file.close()

def main():
    parser=argparse.ArgumentParser(description='伴程 · WayBeside')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--ready-file',type=Path,help='测试专用：包含一次性登录码，必须使用临时私有目录')
    parser.add_argument('--demo',action='store_true',help='使用虚构演示数据（独立的演示目录，不碰真实数据）')
    parser.add_argument('--restore',type=Path,help='恢复备份到 WAYBESIDE_DATA_DIR 指向的空目录，然后退出')
    args=parser.parse_args()
    directory=data_directory(args.demo)
    if args.demo:
        directory=check_demo_directory(directory)
    if args.restore:
        restore(args.restore,directory)
        print('已恢复至空白数据目录。')
        return
    with InstanceLock(directory):
        if args.demo:
            from waybeside.demo import populate
            populate(directory/'waybeside.sqlite', os.environ.get('WAYBESIDE_TODAY'))
        app=create_app(directory/'waybeside.sqlite',args.port,today=os.environ.get('WAYBESIDE_TODAY'))
        try:
            server=create_server(app,host='127.0.0.1',port=args.port,threads=4)
        except OSError as error:
            raise RuntimeError('端口已占用，未启动；请检查本机服务或使用其他 --port。') from error
        def stop(*unused):
            raise SystemExit(0)
        signal.signal(signal.SIGTERM,stop)
        app.config['STOP']=lambda: threading.Timer(0.3,lambda:os.kill(os.getpid(),signal.SIGTERM)).start()
        url=f'http://127.0.0.1:{args.port}/#login='+app.config['LOGIN_CODE']
        today_pinned=os.environ.get('WAYBESIDE_TODAY') or None
        def sweep():
            # 服务运行期间每分钟清一次过期没用的登录码文件（open-url 随时会签发新码）。
            login.expire(directory)
            timer=threading.Timer(60,sweep);timer.daemon=True;timer.start()
        sweep()
        service.write(directory,args.port,today_pinned,today_pinned or date.today().isoformat())
        if args.ready_file:
            fd=os.open(args.ready_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as out:
                json.dump({'url':url,'pid':os.getpid(),'host':'127.0.0.1','port':args.port},out)
        if not args.no_browser:
            webbrowser.open(url)
        print(f'仅本机服务：127.0.0.1:{args.port}；数据目录：{directory}',flush=True)
        try:
            server.run()
        finally:
            service.remove(directory)
            login.discard(directory)
            server.close()
            server.task_dispatcher.shutdown()
            if args.ready_file:
                args.ready_file.unlink(missing_ok=True)

if __name__=='__main__':
    try:
        main()
    except (RuntimeError,ValueError) as error:
        raise SystemExit(str(error))
