from __future__ import annotations

import json
import os
import base64
import hashlib
import tempfile
from pathlib import Path

from webapp_core.learning.learning_courses import course_chapters
from webapp_core.learning.learning_program import _run, _PROGRAM_SLOTS, ProgramRunnerUnavailable

LABS = {
    "database": (
        "数据库约束实验",
        1,
        "创建 /output/lab.db。devices(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,address TEXT UNIQUE NOT NULL)；events(id INTEGER PRIMARY KEY,device_id INTEGER NOT NULL REFERENCES devices(id),outcome TEXT NOT NULL CHECK(outcome IN ('allow','deny')))；导入 /input/config.json 中 devices 和 events。生成 report.json，键 deny_devices，值为至少一次 deny 的设备名称，排序去重。必须启用并正确声明外键。",
        "用 sqlite3 参数化写入；先建设备再建事件。检查唯一、非空、枚举和外键约束。",
    ),
    "encryption": (
        "文件加密实验",
        2,
        "使用 openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt，用 /input/password.txt 中的密码加密 /input/message.bin，写 /output/cipher.bin。随机盐，每次输出可不同。禁止明文复制作为密文。此实验练习密码派生与文件加密，CBC本身不提供完整性保护。",
        "用 -pass file:/input/password.txt 避免拼接密码；验证解密后字节一致，不能比较随机密文是否相同。",
    ),
    "authentication": (
        "密码派生与登录实验",
        3,
        "创建 /output/credentials.db，表 users(username TEXT PRIMARY KEY,salt TEXT NOT NULL,digest TEXT NOT NULL,iterations INTEGER NOT NULL)。从 config.json 导入用户，salt 为给定十六进制盐；digest 为 PBKDF2-HMAC-SHA256（密码UTF-8，盐解码，100000次，32字节）十六进制。固定HTTP实验服务在独立容器读取凭据，实际检验正确登录、错误口令、未知用户和SQL注入用户名。",
        "用 hashlib.pbkdf2_hmac，盐必须先 bytes.fromhex。数据库里保存派生值，不保存明文密码。",
    ),
    "permissions": (
        "Linux 文件权限实验",
        4,
        "把 /input/report.txt、secret.txt、shared.txt 原样复制到 /output；设置 report.txt=0640、secret.txt=0600、shared.txt=0644。检查真实Linux模式位，不能用JSON描述替代 chmod。禁止符号链接；本实验检查DAC文件权限，不包含ACL或角色系统。",
        "使用 shutil.copyfile 与 os.chmod；0640是所有者读写、组只读、其他无权限。",
    ),
    "signature": (
        "标准数字签名实验",
        5,
        "使用 /input/private.pem（仅本次实验的RSA2048测试密钥）和 openssl dgst -sha256 -sign 对 /input/message.bin 签名，写 /output/signature.bin。导出 /output/public.pem。独立校验用预置可信公钥验证原消息通过、篡改消息失败。不得生成另一个密钥冒充给定身份。",
        "通过 openssl pkey -pubout 导出公钥；签名覆盖原始字节，校验失败不能当成工具运行失败忽略。",
    ),
    "audit": (
        "真实登录日志审计实验",
        6,
        "读取 /input/access.jsonl（由本轮隔离HTTP登录服务实际请求产生）。按 config.json 的 target_user、target_source 筛选。写 /output/report.json：failure_ids、success_ids 为请求编号列表，顺序不限但不得重复；after_failure 为是否存在失败之后的成功（严格按 seq 比较）。日志只支持登录现象，不直接证明入侵。",
        "使用 json.loads 逐行解析，同时过滤用户和来源；按 seq 比较，不能混入其他用户的成功。",
    ),
}


def build_security_lab_exercises():
    result = {}
    for task, (title, chapter, description, hint) in LABS.items():
        section = next(
            c for c in course_chapters("cybersec_lab") if c["number"] == chapter
        )
        for variant in range(1, 4):
            key = f"security_lab_{task}_{variant:02d}"
            result[key] = dict(
                id=key,
                subject_id="cybersec_lab",
                subject_name="网络安全实验",
                chapter_id=section["id"],
                chapter_title=section["title"],
                title=f"{title} · 场景 {variant}",
                algorithm=title,
                kind="security_lab",
                difficulty="进阶",
                parameters=dict(
                    lab_task=task,
                    variant=variant,
                    description=description,
                    checkpoints=["实验脚本 lab.py"],
                    source_files=["lab.py"],
                    starter_files=[
                        'import json\nfrom pathlib import Path\n\nconfig = json.loads(Path("/input/config.json").read_text())\n'
                    ],
                    starter='import json\nfrom pathlib import Path\n\nconfig = json.loads(Path("/input/config.json").read_text())\n',
                    hint=hint,
                    code="",
                ),
                rules="提交 Python3 实验脚本；隔离容器提供 Python标准库、OpenSSL和只读输入，真实操作生成 /output 产物。每次重新生成环境，无外网、无宿主个人文件；64 MiB内存、3秒CPU、15秒墙钟、64 KiB日志、16 MiB临时空间；导出产物最多8个普通文件且合计32 KiB。产物由另一个独立容器验证，学生脚本不能修改校验器。",
                training_tags=["lab_artifact", "runtime_error", "time_limit"],
            )
    return result


def lab_solution(exercise):
    task = exercise["parameters"]["lab_task"]
    prefix = 'import json, sqlite3, hashlib, shutil, os, subprocess\nfrom pathlib import Path\nc=json.loads(Path("/input/config.json").read_text())\n'
    bodies = {
        "database": """db=sqlite3.connect('/output/lab.db')
db.execute('PRAGMA foreign_keys=ON')
db.executescript("CREATE TABLE devices(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,address TEXT UNIQUE NOT NULL); CREATE TABLE events(id INTEGER PRIMARY KEY,device_id INTEGER NOT NULL REFERENCES devices(id),outcome TEXT NOT NULL CHECK(outcome IN ('allow','deny')));")
db.executemany('INSERT INTO devices VALUES (?,?,?)',c['devices'])
db.executemany('INSERT INTO events VALUES (?,?,?)',c['events'])
db.commit()
names=[r[0] for r in db.execute("SELECT DISTINCT d.name FROM devices d JOIN events e ON e.device_id=d.id WHERE e.outcome=? ORDER BY d.name",('deny',))]
Path('/output/report.json').write_text(json.dumps({'deny_devices':names}))
db.close()
""",
        "encryption": "subprocess.run(['openssl','enc','-aes-256-cbc','-pbkdf2','-iter','100000','-salt','-pass','file:/input/password.txt','-in','/input/message.bin','-out','/output/cipher.bin'],check=True)\n",
        "authentication": """db=sqlite3.connect('/output/credentials.db')
db.execute('CREATE TABLE users(username TEXT PRIMARY KEY,salt TEXT NOT NULL,digest TEXT NOT NULL,iterations INTEGER NOT NULL)')
for u in c['users']:
 digest=hashlib.pbkdf2_hmac('sha256',u['password'].encode(),bytes.fromhex(u['salt']),100000).hex()
 db.execute('INSERT INTO users VALUES (?,?,?,?)',(u['username'],u['salt'],digest,100000))
db.commit()
db.close()
""",
        "permissions": "for name,mode in [('report.txt',0o640),('secret.txt',0o600),('shared.txt',0o644)]:\n shutil.copyfile('/input/'+name,'/output/'+name)\n os.chmod('/output/'+name,mode)\n",
        "signature": "subprocess.run(['openssl','dgst','-sha256','-sign','/input/private.pem','-out','/output/signature.bin','/input/message.bin'],check=True)\nsubprocess.run(['openssl','pkey','-in','/input/private.pem','-pubout','-out','/output/public.pem'],check=True)\n",
        "audit": """rows=[json.loads(line) for line in Path('/input/access.jsonl').read_text().splitlines()]
rows=[r for r in rows if r['user']==c['target_user'] and r['source']==c['target_source']]
f=[r for r in rows if r['status']==401];s=[r for r in rows if r['status']==200]
Path('/output/report.json').write_text(json.dumps({'failure_ids':[r['id'] for r in f],'success_ids':[r['id'] for r in s],'after_failure':any(a['seq']<b['seq'] for a in f for b in s)}))
""",
    }
    return dict(
        code=prefix + bodies[task],
        explanation="参考脚本实际生成实验产物，产物在独立容器校验；不同正确实现同样接受。隔离实验不等于生产系统安全审计。",
    )


# 此服务只在隔离容器的 loopback 端口运行，终止容器即销毁。
HTTP_SERVICE = r"""
import threading, http.server, urllib.request, urllib.error, hmac, hashlib, json, sqlite3
class LoginHandler(http.server.BaseHTTPRequestHandler):
 def do_POST(self):
  data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
  user=data.get('username','');password=data.get('password','')
  with sqlite3.connect(auth_path) as db:
   row=db.execute('SELECT salt,digest,iterations FROM users WHERE username=?',(user,)).fetchone()
  ok=False
  if row:
   salt,digest,iterations=row
   ok=hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),iterations).hex(),digest)
  status=200 if ok else 401
  logs.append(dict(id='L'+str(len(logs)+1),seq=len(logs)+1,user=user,source=data.get('source','local'),status=status))
  self.send_response(status);self.end_headers();self.wfile.write(b'allowed' if ok else b'denied')
 def log_message(self,*args): pass
server=http.server.HTTPServer(('127.0.0.1',0),LoginHandler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
def login(user,password,source='local'):
 request=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/login',json.dumps(dict(username=user,password=password,source=source)).encode(),{'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(request,timeout=2) as response: return response.status
 except urllib.error.HTTPError as error: return error.code
"""


def lab_setup_code(config):
    return """from pathlib import Path
import json,sqlite3,hashlib,subprocess
c=json.loads(Path('/input/config.json').read_text())
""" + (
        """auth_path='/tmp/auth.db';logs=[]
with sqlite3.connect(auth_path) as db:
 db.execute('CREATE TABLE users(username TEXT PRIMARY KEY,salt TEXT,digest TEXT,iterations INTEGER)')
 for u in c['users']:
  digest=hashlib.pbkdf2_hmac('sha256',u['password'].encode(),bytes.fromhex(u['salt']),100000).hex()
  db.execute('INSERT INTO users VALUES (?,?,?,?)',(u['username'],u['salt'],digest,100000))
"""
        + HTTP_SERVICE
        + """
u=c['users'][0];other=c['users'][1]
login(u['username'],'wrong','terminal-a');login(other['username'],other['password'],'terminal-a');login(u['username'],u['password'],'terminal-b');login(u['username'],'wrong','terminal-a');login(u['username'],u['password'],'terminal-a')
server.shutdown();server.server_close()
Path('/input/access.jsonl').write_text(''.join(json.dumps(r)+'\\n' for r in logs))
"""
        if config["task"] == "audit"
        else (
            """subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out','/input/private.pem'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
subprocess.run(['openssl','pkey','-in','/input/private.pem','-pubout','-out','/input/public.pem'],check=True)
"""
            if config["task"] == "signature"
            else "pass\n"
        )
    )


def lab_validator(task):
    prefix = r"""import json,sqlite3,hashlib,subprocess,stat,os
from pathlib import Path
c=json.loads(Path('/input/config.json').read_text())
o=Path('/output')
# 非常规文件及符号链接不能作为实验产物，避免穿越其他路径。
for path in o.iterdir():
 assert path.is_file() and not path.is_symlink() and path.stat().st_size<=8388608, '产物须为大小受限的普通文件'
"""
    bodies = {
        "database": """db=sqlite3.connect('file:/output/lab.db?mode=ro',uri=True)
assert db.execute('SELECT id,name,address FROM devices ORDER BY id').fetchall()==[tuple(x) for x in c['devices']], '设备数据不符'
assert db.execute('SELECT id,device_id,outcome FROM events ORDER BY id').fetchall()==[tuple(x) for x in c['events']], '事件数据不符'
# 在内存复制数据库后真实尝试违反约束，不修改学生产物。
copy=sqlite3.connect(':memory:');db.backup(copy);copy.execute('PRAGMA foreign_keys=ON')
invalid=[("INSERT INTO devices VALUES (99,NULL,'new')",()),("INSERT INTO devices VALUES (99,?,'new')",(c['devices'][0][1],)),("INSERT INTO devices VALUES (99,'new',?)",(c['devices'][0][2],)),("INSERT INTO events VALUES (99,999,'allow')",()),("INSERT INTO events VALUES (99,1,'other')",()),("INSERT INTO events VALUES (99,NULL,'allow')",())]
for query,args in invalid:
 try: copy.execute(query,args)
 except sqlite3.IntegrityError: pass
 else: raise AssertionError('缺少约束：'+query)
expected=sorted({d[1] for d in c['devices'] for e in c['events'] if e[1]==d[0] and e[2]=='deny'})
assert json.loads((o/'report.json').read_text())=={'deny_devices':expected}, '统计产物不符'
""",
        "encryption": """cipher=(o/'cipher.bin').read_bytes();message=Path('/input/message.bin').read_bytes()
assert cipher.startswith(b'Salted__') and cipher!=message, '须使用随机盐加密'
r=subprocess.run(['openssl','enc','-d','-aes-256-cbc','-pbkdf2','-iter','100000','-pass','file:/input/password.txt','-in','/output/cipher.bin'],capture_output=True,timeout=3)
assert r.returncode==0 and r.stdout==message, '解密后的内容不符'
""",
        "authentication": """auth_path='/output/credentials.db';logs=[]
with sqlite3.connect('file:'+auth_path+'?mode=ro',uri=True) as db:
 rows=db.execute('SELECT username,salt,digest,iterations FROM users ORDER BY username').fetchall()
 expected=[]
 for u in c['users']:
  expected.append((u['username'],u['salt'],hashlib.pbkdf2_hmac('sha256',u['password'].encode(),bytes.fromhex(u['salt']),100000).hex(),100000))
 assert rows==sorted(expected), '凭据产物不符'
 assert {x[1] for x in db.execute('PRAGMA table_info(users)')}=={'username','salt','digest','iterations'}, '不得在凭据表保存明文密码'
"""
        + HTTP_SERVICE
        + """
try:
 for u in c['users']:
  assert login(u['username'],u['password'])==200, '正确口令未能登录'
  assert login(u['username'],'wrong')==401, '错误口令未被拒绝'
 assert login("' OR 1=1 --",'wrong')==401 and login('unknown','wrong')==401, '未知或注入用户名未被拒绝'
finally: server.shutdown();server.server_close()
""",
        "permissions": """for name,mode in [('report.txt',0o640),('secret.txt',0o600),('shared.txt',0o644)]:
 p=o/name
 assert stat.S_IMODE(p.stat().st_mode)==mode, name+' 权限位不符'
 assert p.read_bytes()==Path('/input/'+name).read_bytes(), name+' 内容不符'
""",
        "signature": """assert (o/'public.pem').read_bytes()==Path('/input/public.pem').read_bytes(), '导出公钥不符'
args=['openssl','dgst','-sha256','-verify','/input/public.pem','-signature','/output/signature.bin']
r=subprocess.run(args+['/input/message.bin'],capture_output=True,timeout=3)
assert r.returncode==0, '原消息签名校验失败'
Path('/tmp/tampered.bin').write_bytes(Path('/input/message.bin').read_bytes()+b'changed')
assert subprocess.run(args+['/tmp/tampered.bin'],capture_output=True,timeout=3).returncode!=0, '篡改消息必须失败'
""",
        "audit": """rows=[json.loads(x) for x in Path('/input/access.jsonl').read_text().splitlines()]
rows=[r for r in rows if r['user']==c['target_user'] and r['source']==c['target_source']]
f=[r for r in rows if r['status']==401];s=[r for r in rows if r['status']==200]
r=json.loads((o/'report.json').read_text());assert set(r)=={'failure_ids','success_ids','after_failure'}
for key,expected in [('failure_ids',[x['id'] for x in f]),('success_ids',[x['id'] for x in s])]:
 assert isinstance(r[key],list) and len(r[key])==len(set(r[key])) and set(r[key])==set(expected), key+' 不符'
assert type(r['after_failure']) is bool and r['after_failure']==any(a['seq']<b['seq'] for a in f for b in s), '时序结论不符'
""",
    }
    return prefix + bodies[task] + "\nprint('产物校验通过')\n"


def evaluate_lab(exercise, code, labels):
    if not _PROGRAM_SLOTS.acquire(blocking=False):
        raise ProgramRunnerUnavailable("实验评测正在忙，请稍后重试。")
    try:
        with tempfile.TemporaryDirectory(prefix="learning-lab-") as temp:
            root = Path(temp).resolve()
            root.chmod(0o755)
            source = root / "source"
            source.mkdir()
            source.chmod(0o755)
            input_dir = root / "input"
            input_dir.mkdir()
            input_dir.chmod(0o777)
            output = root / "output"
            output.mkdir()
            output.chmod(0o777)
            p = exercise["parameters"]
            v = p["variant"]
            task = p["lab_task"]
            users = [
                dict(
                    username=f"student{v}",
                    password=f"training-{v}-password",
                    salt=hashlib.sha256(f"salt-{v}".encode()).hexdigest()[:32],
                ),
                dict(
                    username=f"other{v}",
                    password=f"other-{v}-password",
                    salt=hashlib.sha256(f"other-salt-{v}".encode()).hexdigest()[:32],
                ),
            ]
            config = dict(
                task=task,
                devices=[
                    [1, f"router{v}", f"10.0.{v}.1"],
                    [2, f"device'{v}", f"10.0.{v}.2"],
                    [3, f"server{v}", f"10.0.{v}.3"],
                ],
                events=[
                    [1, 1, "allow"],
                    [2, 2, "deny"],
                    [3, 2, "deny"],
                    [4, 3, "allow" if v == 1 else "deny"],
                ],
                users=users,
                target_user=users[0]["username"] if v != 3 else users[1]["username"],
                target_source="terminal-a" if v != 2 else "terminal-b",
            )
            (input_dir / "config.json").write_text(json.dumps(config))
            (input_dir / "message.bin").write_bytes(
                b"\0" + f"Training laboratory {v}\n".encode() + bytes([255, v])
            )
            (input_dir / "password.txt").write_text(f"lab-encryption-{v}\n")
            for name in ["report.txt", "secret.txt", "shared.txt"]:
                (input_dir / name).write_text(f"{name} scenario {v}\n")
            for path in input_dir.iterdir():
                path.chmod(0o644)
            (source / "setup.py").write_text(lab_setup_code(config))
            (source / "lab.py").write_text(code)
            (source / "verify.py").write_text(lab_validator(task))
            status, _, stderr, timed = _run(
                ["python3", "/source/setup.py"],
                [(source, "/source", True), (input_dir, "/input", False)],
                timeout=15,
                writable=True,
            )
            if status or timed:
                raise ProgramRunnerUnavailable(
                    "隔离实验初始化失败，请检查镜像中的 Python3 和 OpenSSL。"
                )
            # 初始化后收回输入写权限；学生脚本只可写本轮专用 output 目录。
            # 学生在限额 tmpfs 写产物，仅导出有限数量的小文件，避免把宿主目录交给任意脚本。
            collector = r"""import subprocess,json,base64,stat
from pathlib import Path
r=subprocess.run(['python3','/source/lab.py'],capture_output=True)
files={};total=0
for p in Path('/output').iterdir():
 if not p.is_file() or p.is_symlink(): raise ValueError('产物仅支持普通文件')
 size=p.stat().st_size;total+=size
 if total>32768 or len(files)>=8: raise ValueError('导出产物合计不得超过32 KiB或8个文件')
 files[p.name]={'data':base64.b64encode(p.read_bytes()).decode(),'mode':stat.S_IMODE(p.stat().st_mode)}
print(json.dumps({'status':r.returncode,'log':(r.stdout+r.stderr).decode(errors='replace')[:2000],'files':files}))
"""
            (source / "collect.py").write_text(collector)
            status, stdout, stderr, timed = _run(
                ["python3", "/source/collect.py"],
                [(source, "/source", True), (input_dir, "/input", True)],
                timeout=15,
                output_tmpfs=True,
                workdir="/output",
            )
            run_log = stderr[:4000]
            if not status and not timed:
                try:
                    collected = json.loads(stdout)
                    status = collected["status"]
                    run_log = collected["log"]
                    for name, artifact in collected["files"].items():
                        if (
                            "/" in name
                            or name in {".", ".."}
                            or not name
                            or len(name) > 100
                        ):
                            raise ValueError("产物文件名无效")
                        data = base64.b64decode(artifact["data"], validate=True)
                        (output / name).write_bytes(data)
                        (output / name).chmod(artifact["mode"] & 0o777)
                except (ValueError, KeyError, TypeError):
                    status = 1
                    run_log = "产物导出失败或输出超限。"
            error = "time_limit" if timed else "runtime_error" if status else None
            check_output = ""
            if not error:
                status, check_output, stderr, timed = _run(
                    ["python3", "/source/verify.py"],
                    [
                        (source, "/source", True),
                        (input_dir, "/input", True),
                        (output, "/output", True),
                    ],
                    timeout=15,
                    user=f"{os.getuid()}:{os.getgid()}",
                )
                if status or timed:
                    error = "lab_artifact"
                check_output = (check_output + "\n" + stderr)[:4000]
            return dict(
                passed=error is None,
                row_statuses=["error" if error else "correct"],
                first_error=(
                    dict(
                        step=1,
                        field="code",
                        error_code=error,
                        label=labels[error],
                        message=(
                            "实验超时。" if timed else "请查看实验运行与产物检查反馈。"
                        ),
                        possible_cause=None,
                    )
                    if error
                    else None
                ),
                program_feedback=dict(
                    compiler=run_log,
                    tests=[
                        dict(
                            number=1,
                            input=exercise["parameters"]["description"],
                            expected="产物校验通过",
                            output=check_output or run_log,
                            passed=error is None,
                            status="通过" if error is None else labels[error],
                        )
                    ],
                ),
                lab_environment=dict(
                    task=task,
                    variant=v,
                    inputs={
                        path.name: path.read_text()
                        for path in input_dir.iterdir()
                        if path.suffix in {".json", ".jsonl", ".txt"}
                    },
                    artifacts=[
                        {
                            "name": path.name,
                            "size": path.stat().st_size,
                            "mode": oct(path.stat().st_mode & 0o777),
                            "base64": base64.b64encode(path.read_bytes()).decode(),
                        }
                        for path in output.iterdir()
                    ],
                ),
            )
    finally:
        _PROGRAM_SLOTS.release()
