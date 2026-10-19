"""一次性登录码：数据目录里只留码的哈希和时效，用过即删；服务运行期间不常驻任何令牌文件。"""
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

FILE = '.login-code.json'
TTL_SECONDS = 300

def _digest(code):
    return hashlib.sha256(code.encode()).hexdigest()

def issue(directory, ttl=TTL_SECONDS):
    expire(directory)
    """生成新码，写入 0600 新文件（覆盖上一个未用的码）。返回明文码，只应交给浏览器地址。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    code = secrets.token_urlsafe(32)
    target = directory / FILE
    temp = directory / (FILE + '.new')
    temp.unlink(missing_ok=True)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump({'sha256': _digest(code), 'expires_at': time.time() + ttl}, out)
    os.replace(temp, target)
    return code

def consume(directory, code):
    """码对且未过期则作废并删除文件，返回 True；其余一律 False。同一个码并发使用只会有一方删除成功。"""
    target = Path(directory) / FILE
    try:
        record = json.loads(target.read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(code, str) or time.time() > record.get('expires_at', 0):
        target.unlink(missing_ok=True)
        return False
    if not secrets.compare_digest(_digest(code), str(record.get('sha256', ''))):
        return False
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    return True

def discard(directory):
    (Path(directory) / FILE).unlink(missing_ok=True)

def expire(directory):
    """过期仍没用的码就删掉文件（服务定时调用），不让它在数据目录里一直躺着。"""
    target = Path(directory) / FILE
    try:
        if time.time() > json.loads(target.read_text()).get('expires_at', 0):
            target.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass
