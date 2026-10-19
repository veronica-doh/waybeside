"""service.json：服务的 pid、端口和日期设置；不含任何凭据。助手命令靠它判断服务在不在跑。"""
import json
import os
from pathlib import Path

FILE = 'service.json'

def write(directory, port, today_pinned, today):
    target = Path(directory) / FILE
    temp = Path(directory) / (FILE + '.new')
    temp.unlink(missing_ok=True)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump({'pid': os.getpid(), 'port': port, 'today': today, 'today_pinned': today_pinned}, out)
    os.replace(temp, target)

def remove(directory):
    (Path(directory) / FILE).unlink(missing_ok=True)

def pid_alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

def read(directory):
    """服务在跑则返回信息，否则 None（文件缺失、损坏或 pid 已不存活都算没在跑）。"""
    try:
        info = json.loads((Path(directory) / FILE).read_text())
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and pid_alive(info.get('pid')) else None
