from __future__ import annotations

import argparse
import base64
import ctypes
import getpass
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ZENTAO11 = HERE / "zentao11.py"
TASK_MANAGER = ROOT / "zentao-task-manager" / "scripts" / "zentao_task_manager.py"
CONFIG_DIR = Path(os.environ.get("ZENTAO11_CONFIG_DIR", Path.home() / ".config" / "zentao11"))
DINGTALK_FILE = CONFIG_DIR / "dingtalk.json"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
DEADLINE_DAYS = 2
TASK_COMPLETED = {"done", "closed", "cancel", "已完成", "已关闭", "已取消"}
BUG_COMPLETED = {"closed", "已关闭"}


def decode(data: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except Exception:
            pass
    return data.decode("utf-8", "replace")


def run_cmd(args):
    r = subprocess.run(args, capture_output=True)
    return r.returncode, decode(r.stdout), decode(r.stderr)


def run_zentao(args):
    return run_cmd([sys.executable, "-B", str(ZENTAO11), *args])


def _dpapi_protect(value: str) -> str:
    """Encrypt a secret for the current Windows user."""
    if os.name != "nt":
        raise RuntimeError("自动保存禅道密码只支持 Windows")
    data = value.encode("utf-16-le")
    blob = _DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    out = _DATA_BLOB()
    if not _CRYPT_PROTECT(ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return base64.b64encode(ctypes.string_at(out.pbData, out.cbData)).decode("ascii")
    finally:
        _LOCAL_FREE(out.pbData)


def _dpapi_unprotect(value: str) -> str:
    if os.name != "nt":
        raise RuntimeError("自动读取禅道密码只支持 Windows")
    raw = base64.b64decode(value)
    blob = _DATA_BLOB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw), ctypes.POINTER(ctypes.c_char)))
    out = _DATA_BLOB()
    if not _CRYPT_UNPROTECT(ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode("utf-16-le")
    finally:
        _LOCAL_FREE(out.pbData)


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


_CRYPT_PROTECT = ctypes.windll.crypt32.CryptProtectData if os.name == "nt" else None
_CRYPT_UNPROTECT = ctypes.windll.crypt32.CryptUnprotectData if os.name == "nt" else None
_LOCAL_FREE = ctypes.windll.kernel32.LocalFree if os.name == "nt" else lambda _: None


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"配置文件格式错误：{path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"配置文件必须是 JSON 对象：{path}")
    return value


def write_private_json(path: Path, value: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def credentials() -> dict:
    value = read_json(CREDENTIALS_FILE)
    server = str(value.get("server") or "").strip()
    account = str(value.get("account") or "").strip()
    password = ""
    encrypted = str(value.get("password_dpapi") or "")
    if encrypted:
        password = _dpapi_unprotect(encrypted)
    elif value.get("password"):
        # Read legacy configs, but do not create new plaintext credentials.
        password = str(value.get("password"))
    if not server or not account or not password:
        raise RuntimeError(
            "禅道会话已失效且未找到本机凭据。请运行 "
            f"py -3 -X utf8 -B {Path(__file__).name} setup，或先手动执行 zentao11 login -u 账号"
        )
    return {"server": server, "account": account, "password": password}


def login_from_credentials() -> None:
    info = credentials()
    # The CLI accepts -p; the password exists only in this short-lived child process.
    code, out, err = run_zentao(
        ["login", "-s", info["server"], "-u", info["account"], "-p", info["password"]]
    )
    if code != 0:
        detail = (err or out).strip()
        raise RuntimeError(f"禅道自动登录失败：{detail[:800]}")


def parse_json_text(text: str):
    text = (text or "").strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("not json")
    return json.loads(text[min(starts):])


def unwrap(obj):
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                obj = json.loads(s)
            except Exception:
                return obj
    if isinstance(obj, dict) and "data" in obj:
        data = unwrap(obj["data"])
        if data:
            return data
    return obj


def extract_items(obj):
    obj = unwrap(obj)
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if not isinstance(obj, dict):
        return []
    for key in ("tasks", "bugs", "items", "list", "records", "rows", "result"):
        if key in obj:
            items = extract_items(obj[key])
            if items:
                return items
    vals = list(obj.values())
    if vals and all(isinstance(v, dict) for v in vals):
        if any(("id" in v) or ("name" in v) or ("title" in v) for v in vals):
            return vals
    return []


def parse_md_table(text: str):
    rows = []
    headers = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or not any(cells):
            continue
        joined = "".join(cells)
        if set(joined) <= set("-: ") and headers:
            continue
        if not headers:
            headers = cells
            continue
        row = {}
        for i, key in enumerate(headers):
            row[key] = cells[i] if i < len(cells) else ""
        rows.append(row)
    return rows


def field(item, *names):
    lower = {str(k).strip().lower(): v for k, v in item.items()}
    for name in names:
        for key in (name, name.lower(), name.upper()):
            if key in item and str(item.get(key) or "").strip():
                return item.get(key)
            if key in lower and str(lower.get(key) or "").strip():
                return lower.get(key)
    return ""


def as_text(value):
    if isinstance(value, dict):
        for key in ("account", "realname", "name", "title", "id"):
            if value.get(key):
                return str(value.get(key)).split(":", 1)[0].strip()
        return ""
    text = str(value or "").strip()
    if text.startswith("{") and "我的地盘" in text:
        return ""
    return text.split(":", 1)[0].strip()


def remaining_days(deadline):
    text = as_text(deadline)
    if not text or text.startswith("0000-00-00") or text in {"-", "无", "None", "null"}:
        return None
    try:
        due = datetime.strptime(text[:10], "%Y-%m-%d").date()
    except Exception:
        m = re.search(r"(-?\d+)", text)
        if m and ("剩余" in str(deadline) or "逾期" in str(deadline) or "天" in str(deadline)):
            if "逾期" in str(deadline):
                return -int(m.group(1))
            return int(m.group(1))
        return None
    return (due - date.today()).days


def remain_text(days):
    if days is None:
        return "无截止日期"
    if days < 0:
        return f"已逾期{abs(days)}天"
    if days == 0:
        return "今天到期"
    return f"剩余{days}天"


def sort_key(item):
    days = item["remain"]
    try:
        pri_n = int(item.get("pri") or 99)
    except Exception:
        pri_n = 99
    return (days is None, days if days is not None else 9999, pri_n, str(item["id"]))


def parse_status():
    code, out, err = run_zentao(["status"])
    text = f"{out}\n{err}"
    if "已登录" not in text:
        raise RuntimeError(out.strip() or err.strip() or "禅道未登录")
    account = ""
    server = ""
    m = re.search(r"已登录:\s*([^\s(]+)", text)
    if m:
        account = m.group(1).strip()
    m = re.search(r"https?://\S+", text)
    if m:
        server = m.group(0).strip().rstrip("/")
    return account, server, text


def collect_from_rows(rows, kind, closed):
    items = []
    for raw in rows:
        status = as_text(field(raw, "status", "状态"))
        if status.lower() in {x.lower() for x in closed}:
            continue
        oid = as_text(field(raw, "id", "ID", "BugID", "任务ID"))
        title = as_text(field(raw, "name", "title", "任务名称", "Bug标题", "标题"))
        if not oid or not title:
            continue
        pri = as_text(field(raw, "pri", "P", "优先级"))
        if pri.startswith("P"):
            pri = pri[1:]
        deadline = field(raw, "deadline", "截止", "截止日期")
        remain = remaining_days(deadline)
        if remain is None:
            remain = remaining_days(field(raw, "剩余天数", "remain", "left"))
        items.append({
            "id": oid,
            "title": title,
            "pri": pri,
            "status": status or "-",
            "deadline": as_text(deadline)[:10],
            "remain": remain,
            "kind": kind,
        })
    items.sort(key=sort_key)
    return items


def load_list(kind):
    if kind == "task":
        commands = [
            [sys.executable, "-B", str(ZENTAO11), "task", "--list", "--type", "assignedTo", "--format", "json", "--limit", "200"],
            [sys.executable, "-B", str(TASK_MANAGER), "list"],
            [sys.executable, "-B", str(ZENTAO11), "task", "--list", "--type", "assignedTo", "--limit", "200"],
        ]
        closed = TASK_COMPLETED
    else:
        commands = [
            [sys.executable, "-B", str(ZENTAO11), "bug", "--list", "--type", "assignedTo", "--format", "json", "--limit", "200"],
            [sys.executable, "-B", str(ZENTAO11), "bug", "--list", "--type", "assignedTo", "--limit", "200"],
        ]
        closed = BUG_COMPLETED
    debug = []
    for cmd in commands:
        if not Path(cmd[2]).exists() and "zentao_task_manager.py" in cmd[2]:
            continue
        code, out, err = run_cmd(cmd)
        debug.append(f"$ {' '.join(cmd[-6:])}\nexit={code}\n{out[:1200]}\n{err[:400]}")
        rows = []
        try:
            rows = extract_items(parse_json_text(out))
        except Exception:
            rows = parse_md_table(out)
        items = collect_from_rows(rows, kind, closed)
        if items:
            return items, "\n\n".join(debug)
    return [], "\n\n".join(debug)


def item_line(item, server):
    prefix = "task-view" if item["kind"] == "task" else "bug-view"
    url = f"{server}/{prefix}-{item['id']}.html" if server else ""
    title = f"[{item['id']}] {item['title']}"
    if url:
        title = f"[{title}]({url})"
    bits = [title, f"P{item['pri']}" if item["pri"] else "P-", item["status"], remain_text(item["remain"])]
    if item["deadline"] and not item["deadline"].startswith("0000"):
        bits.insert(-1, f"截止{item['deadline']}")
    return "- " + " | ".join(bits)


def render(account, server, tasks, bugs):
    due_tasks = [x for x in tasks if x["remain"] is not None and 0 <= x["remain"] <= DEADLINE_DAYS]
    due_bugs = [x for x in bugs if x["remain"] is not None and 0 <= x["remain"] <= DEADLINE_DAYS]
    lines = [
        f"### 禅道待办提醒 {date.today().isoformat()}",
        f"账号：{account or '-'}",
        "",
        "#### 指派给我且未完成",
        f"**任务（{len(tasks)}）**",
    ]
    lines.extend(item_line(x, server) for x in tasks[:50])
    if not tasks:
        lines.append("- 无")
    lines.extend(["", f"**Bug（{len(bugs)}）**"])
    lines.extend(item_line(x, server) for x in bugs[:50])
    if not bugs:
        lines.append("- 无")
    lines.extend(["", f"#### 即将到期（今天起 {DEADLINE_DAYS} 天内，每天提醒）"])
    if not due_tasks and not due_bugs:
        lines.append("- 无")
    else:
        if due_tasks:
            lines.append(f"**任务（{len(due_tasks)}）**")
            lines.extend(item_line(x, server) for x in due_tasks)
        if due_bugs:
            lines.append(f"**Bug（{len(due_bugs)}）**")
            lines.extend(item_line(x, server) for x in due_bugs)
    return "\n".join(lines)


def send_dingtalk(title, text):
    if not DINGTALK_FILE.exists():
        raise RuntimeError("还没有钉钉配置")
    obj = json.loads(DINGTALK_FILE.read_text(encoding="utf-8-sig"))
    webhook = str(obj.get("webhook") or "").strip()
    secret = str(obj.get("secret") or "").strip()
    if not webhook:
        raise RuntimeError("dingtalk.json 里没有 webhook")
    url = webhook
    if secret:
        timestamp = str(int(time.time() * 1000))
        digest = hmac.new(secret.encode("utf-8"), f"{timestamp}\n{secret}".encode("utf-8"), hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest))
        url += ("&" if "?" in url else "?") + f"timestamp={timestamp}&sign={sign}"
    payload = json.dumps({"msgtype": "markdown", "markdown": {"title": title, "text": text}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(decode(resp.read()))
    if str(result.get("errcode")) not in ("0", "None"):
        raise RuntimeError(f"钉钉返回失败：{result}")
    return result


def build_message():
    try:
        account, server, status_text = parse_status()
    except RuntimeError:
        login_from_credentials()
        account, server, status_text = parse_status()
    tasks, task_debug = load_list("task")
    bugs, bug_debug = load_list("bug")
    text = render(account, server, tasks, bugs)
    return text, {"tasks": len(tasks), "bugs": len(bugs), "account": account}


def create_schedule():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cmd_path = CONFIG_DIR / "remind.cmd"
    config_path = str(CONFIG_DIR)
    python_path = str(sys.executable)
    script_path = str(Path(__file__).resolve())
    log_path = str(CONFIG_DIR / "remind.log")
    cmd_path.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        f'set "ZENTAO11_CONFIG_DIR={config_path}"\r\n'
        f'"{python_path}" -X utf8 -B "{script_path}" send >> "{log_path}" 2>&1\r\n',
        encoding="utf-8",
    )
    r = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            "ZentaoDingTalkRemind",
            "/SC",
            "DAILY",
            "/ST",
            "10:00",
            "/F",
            "/TR",
            f'"{cmd_path}"',
        ],
        capture_output=True,
    )
    out = decode(r.stdout) + decode(r.stderr)
    if r.returncode != 0:
        raise RuntimeError(out.strip() or "创建定时任务失败")
    return cmd_path


def setup() -> None:
    print("禅道钉钉提醒初始化。密码、Webhook 和密钥只保存到本机配置，不会输出。")
    existing = read_json(CREDENTIALS_FILE)
    default_server = str(existing.get("server") or "https://zentao.ihr360.com/zentao")
    server = input(f"禅道地址 [{default_server}]: ").strip() or default_server
    account = input("禅道账号: ").strip()
    if not account:
        raise RuntimeError("禅道账号不能为空")
    password = getpass.getpass("禅道密码: ")
    if not password:
        raise RuntimeError("禅道密码不能为空")
    existing_dingtalk = read_json(DINGTALK_FILE)
    webhook = input("钉钉机器人 Webhook（直接回车保留已有配置）: ").strip()
    webhook = webhook or str(existing_dingtalk.get("webhook") or "").strip()
    if not webhook:
        raise RuntimeError("钉钉 Webhook 不能为空")
    secret = getpass.getpass("钉钉机器人加签密钥（直接回车保留已有密钥）: ")
    secret = secret or str(existing_dingtalk.get("secret") or "").strip()

    write_private_json(
        CREDENTIALS_FILE,
        {"server": server, "account": account, "password_dpapi": _dpapi_protect(password)},
    )
    write_private_json(DINGTALK_FILE, {"webhook": webhook, "secret": secret})
    login_from_credentials()
    cmd_path = create_schedule()
    print(f"已创建每天 10:00 定时任务：{cmd_path}")
    text, meta = build_message()
    result = send_dingtalk("禅道待办提醒", text)
    print(f"初始化完成：任务 {meta['tasks']}，Bug {meta['bugs']}，钉钉已发送：{result}")


def main():
    parser = argparse.ArgumentParser(description="禅道待办钉钉提醒")
    parser.add_argument("action", choices=["setup", "preview", "send", "schedule"])
    args = parser.parse_args()
    if args.action == "setup":
        setup()
        return
    if args.action == "schedule":
        print(f"已创建每天 10:00 定时任务，启动脚本：{create_schedule()}")
        return
    text, meta = build_message()
    print(text)
    print(f"\n统计：任务 {meta['tasks']}，Bug {meta['bugs']}，账号 {meta['account']}")
    if args.action == "send":
        result = send_dingtalk("禅道待办提醒", text)
        print(f"钉钉已发送：{result}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
