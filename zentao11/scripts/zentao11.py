#!/usr/bin/env python3
"""ZenTao 11.x web-session CLI. Uses PATH_INFO .json views, not REST API 2.0."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import html
import http.cookiejar
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SERVER = "https://zentao.ihr360.com/zentao"
UA = "zentao11-cli/1.0"
TIMEOUT = 30
VERSION = "1.0.0"

BUG_STATUS = {"active": "激活", "resolved": "已解决", "closed": "已关闭"}
STORY_STATUS = {"draft": "草稿", "active": "激活", "changed": "已变更", "closed": "已关闭"}
TASK_STATUS = {
    "wait": "未开始",
    "doing": "进行中",
    "done": "已完成",
    "pause": "已暂停",
    "cancel": "已取消",
    "closed": "已关闭",
}
SEVERITY = {"1": "1-致命", "2": "2-严重", "3": "3-一般", "4": "4-轻微"}
PRI = {"1": "1", "2": "2", "3": "3", "4": "4"}
BUG_TYPE = {
    "codeerror": "代码错误",
    "interface": "界面优化",
    "config": "配置相关",
    "install": "安装部署",
    "security": "安全相关",
    "performance": "性能问题",
    "standard": "标准规范",
    "automation": "测试脚本",
    "designdefect": "设计缺陷",
    "others": "其他",
}
STORY_STAGE = {
    "wait": "未开始",
    "planned": "已计划",
    "projected": "已立项",
    "developing": "研发中",
    "developed": "研发完毕",
    "testing": "测试中",
    "tested": "测试完毕",
    "verified": "已验收",
    "released": "已发布",
    "closed": "已关闭",
}


class AuthError(Exception):
    pass


class ApiError(Exception):
    pass


def configure_stdio() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def config_dir() -> Path:
    raw = os.environ.get("ZENTAO11_CONFIG_DIR")
    if raw:
        return Path(raw)
    return Path.home() / ".config" / "zentao11"


def session_path() -> Path:
    return config_dir() / "session.json"


def load_session() -> dict[str, Any]:
    path = session_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApiError(f"会话文件损坏: {path}") from exc


def save_session(data: dict[str, Any]) -> None:
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_session() -> None:
    path = session_path()
    if path.exists():
        path.unlink()


def normalize_server(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        raise ApiError("禅道地址不能为空")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ApiError(f"无效的禅道地址: {url}")
    if parsed.netloc.lower() == "zentao.ihr360.com" and parsed.path in ("", "/"):
        url = "https://zentao.ihr360.com/zentao"
    return url.rstrip("/")


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def extract_verify_rand(page: str) -> str:
    for tag in re.findall(r"(?is)<input\b[^>]*>", page):
        if not re.search(r"""(?i)\b(?:name|id)\s*=\s*['"]verifyRand['"]""", tag):
            continue
        match = re.search(r"""(?i)\bvalue\s*=\s*['"]([^'"]+)['"]""", tag)
        if match:
            return match.group(1)
    return ""


def path_info(*parts: Any) -> str:
    items = [str(p) for p in parts if p is not None and str(p) != ""]
    return "-".join(items) + ".json"


def unwrap(payload: str | bytes) -> Any:
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", "replace")
    else:
        text = payload
    text = text.strip()
    if not text:
        raise ApiError("服务器返回空响应")
    if text.startswith("<"):
        if re.search(r"user-login|self\.location", text, re.I):
            raise AuthError("会话已失效，请重新登录")
        raise ApiError("服务器返回了 HTML，而不是禅道 JSON")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApiError(f"无法解析 JSON: {exc}") from exc
    if isinstance(obj, dict):
        redirect = str(obj.get("locate") or obj.get("url") or "")
        if re.search(r"user-login", redirect, re.I):
            raise AuthError("会话已失效，请重新登录")
    if isinstance(obj, dict) and str(obj.get("status", "")).lower() == "failed":
        raise ApiError(str(obj.get("reason") or obj.get("message") or "请求失败"))
    if isinstance(obj, dict) and "data" in obj:
        data = obj["data"]
        if isinstance(data, str):
            stripped = data.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    pass
        if isinstance(data, dict):
            redirect = str(data.get("locate") or data.get("url") or "")
            if re.search(r"user-login", redirect, re.I):
                raise AuthError("会话已失效，请重新登录")
        return data
    return obj


def records(value: Any) -> list[Any]:
    if value is None or value is False or value == "":
        return []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        if value and all(isinstance(v, dict) for v in value.values()):
            return list(value.values())
        if "id" in value or "title" in value or "name" in value:
            return [value]
    return []


def first_present(data: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, "", [], {}):
            return data[key]
    return None


def user_name(users: Any, account: Any) -> str:
    if account in (None, "", "closed", "0"):
        return str(account or "")
    acc = str(account)
    if isinstance(users, dict):
        val = users.get(acc)
        if isinstance(val, str) and val:
            return f"{val} ({acc})" if val != acc else acc
        if isinstance(val, dict):
            real = val.get("realname") or val.get("name") or acc
            return f"{real} ({acc})" if real != acc else acc
    return acc


def html_to_text(raw: Any, server: str) -> str:
    if raw is None:
        return ""
    text = str(raw)
    if not text:
        return ""

    def abs_url(url: str) -> str:
        url = html.unescape(url.strip())
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("/"):
            parsed = urllib.parse.urlparse(server)
            return f"{parsed.scheme}://{parsed.netloc}{url}"
        return server.rstrip("/") + "/" + url.lstrip("/")

    def repl_img(match: re.Match[str]) -> str:
        return f"[图片 {abs_url(match.group(1))}]"

    text = re.sub(r"(?is)<script.*?>.*?</script>", "", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", "", text)
    text = re.sub(r'(?is)<img[^>]*src=["\']([^"\']+)["\'][^>]*>', repl_img, text)
    text = re.sub(r'(?is)<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r"\2 (\1)", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"(?i)</tr\s*>", "\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def map_val(mapping: dict[str, str], value: Any) -> str:
    if value is None:
        return ""
    key = str(value)
    return mapping.get(key, key)


def trunc(text: Any, width: int = 60) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) <= width:
        return s
    return s[: width - 1] + "…"


def dump_json(data: Any) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def kv(lines: list[str], label: str, value: Any) -> None:
    if value in (None, "", [], {}):
        return
    lines.append(f"- {label}: {value}")

class Client:
    def __init__(self, server: str, zentaosid: str | None = None, timeout: int = TIMEOUT):
        self.server = normalize_server(server)
        self.zentaosid = zentaosid or ""
        self.timeout = timeout
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        if self.zentaosid:
            self._set_sid(self.zentaosid)

    def _set_sid(self, sid: str) -> None:
        self.zentaosid = sid
        parsed = urllib.parse.urlparse(self.server)
        cookie = http.cookiejar.Cookie(
            version=0,
            name="zentaosid",
            value=sid,
            port=None,
            port_specified=False,
            domain=parsed.hostname or "",
            domain_specified=bool(parsed.hostname),
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=parsed.scheme == "https",
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )
        self.cj.set_cookie(cookie)

    def _sync_sid(self) -> None:
        for cookie in self.cj:
            if cookie.name == "zentaosid" and cookie.value:
                self.zentaosid = cookie.value
                return

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return self.server + "/" + path.lstrip("/")

    def request_raw(self, path: str, data: dict[str, str] | None = None) -> bytes:
        url = self._url(path)
        headers = {
            "User-Agent": UA,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        }
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Referer"] = self._url(path.split("?", 1)[0])
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
                self._sync_sid()
                return raw
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp else b""
            self._sync_sid()
            if raw:
                return raw
            raise ApiError(f"HTTP {exc.code} {url}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"无法访问 {url}: {exc.reason}") from exc

    def request(self, path: str, data: dict[str, str] | None = None) -> Any:
        return unwrap(self.request_raw(path, data))

    def post(self, path: str, data: dict[str, Any]) -> Any:
        form = {str(key): str(value) for key, value in data.items() if value is not None}
        return self.request(path, form)

    def get(self, *parts: Any) -> Any:
        return self.request(path_info(*parts))

    def try_get(self, candidates: list[tuple[Any, ...]]) -> tuple[tuple[Any, ...], Any]:
        last_err: Exception | None = None
        for parts in candidates:
            try:
                return parts, self.get(*parts)
            except AuthError:
                raise
            except ApiError as exc:
                last_err = exc
                continue
        raise last_err or ApiError("没有可用接口")

    def _extract_user(self, data: Any, account: str) -> tuple[str, str, dict[str, Any]]:
        user: Any = data if isinstance(data, dict) else {}
        if isinstance(data, dict):
            if isinstance(data.get("user"), dict):
                user = data["user"]
            elif isinstance(data.get("users"), dict) and account in data["users"]:
                user = data["users"][account]
        realname = ""
        if isinstance(user, dict):
            realname = str(user.get("realname") or user.get("name") or "")
            account = str(user.get("account") or account)
            return account, realname, user
        return account, realname, {}

    def login(self, account: str, password: str) -> dict[str, Any]:
        account = account.strip()
        password = password.strip()
        html_text = self.request_raw("user-login.html").decode("utf-8", "replace")
        rand = extract_verify_rand(html_text)
        if rand:
            hashed = password if len(password) == 32 else md5_hex(md5_hex(password) + rand)
            raw = self.request_raw(
                "user-login.html",
                {
                    "account": account,
                    "password": hashed,
                    "keepLogin[]": "on",
                    "referer": self.server + "/my/",
                    "verifyRand": rand,
                },
            )
            text = raw.decode("utf-8", "replace")
            alerts = re.findall(r"alert\(['\"]([^'\"]+)['\"]\)", text)
            if alerts:
                raise ApiError(alerts[0])
            if re.search(r"user-login", text, re.I) and not re.search(r"location|locate", text, re.I):
                raise ApiError("登录未成功，服务器没有跳转。请确认账号密码，或改用网页能登录的那个用户名。")
        else:
            raw = self.request_raw("api-getsessionid.json")
            if not raw.strip():
                raise ApiError("api-getsessionid.json 返回空响应，请确认禅道地址根路径是否正确；云禅道通常不要加 /zentao")
            session_obj = unwrap(raw)
            if not isinstance(session_obj, dict):
                raise ApiError("无法获取禅道会话 ID")
            sid = str(
                session_obj.get("sessionID")
                or session_obj.get("sessionId")
                or session_obj.get("zentaosid")
                or ""
            ).strip()
            if not sid:
                raise ApiError("api-getsessionid.json 没有返回 sessionID")
            self._set_sid(sid)
            rand = str(session_obj.get("rand") or "").strip()
            hashed = password if len(password) == 32 else md5_hex(md5_hex(password) + rand) if rand else md5_hex(password)
            raw = self.request_raw(
                f"user-login.json?zentaosid={urllib.parse.quote(sid)}",
                {
                    "account": account,
                    "password": hashed,
                    "keepLogin": "on",
                },
            )
            text = raw.decode("utf-8", "replace").strip()
            if text:
                data = unwrap(text)
                if isinstance(data, dict):
                    maybe_sid = str(data.get("zentaosid") or data.get("sessionID") or "").strip()
                    if maybe_sid:
                        self._set_sid(maybe_sid)
        self._sync_sid()
        if not self.zentaosid:
            raise ApiError("登录后没有拿到会话 Cookie")
        try:
            data = self.request("my.json")
        except AuthError as exc:
            raise ApiError("网页登录看起来成功，但会话无效。") from exc
        account, realname, user = self._extract_user(data, account)
        return {
            "server": self.server,
            "account": account,
            "realname": realname,
            "zentaosid": self.zentaosid,
            "loginTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user": user,
        }


def require_client() -> tuple[dict[str, Any], Client]:
    session = load_session()
    if not session.get("zentaosid") or not session.get("server"):
        raise AuthError("未登录")
    return session, Client(session["server"], session["zentaosid"])


def print_not_logged_in() -> int:
    print("未登录禅道 11.3。请在本机终端执行：", file=sys.stderr)
    print("  zentao11 login -u 你的账号", file=sys.stderr)
    print("不要把密码发到对话里。", file=sys.stderr)
    return 2


def format_bug(data: dict[str, Any], server: str) -> str:
    bug = data.get("bug") if isinstance(data.get("bug"), dict) else data
    users = data.get("users") if isinstance(data.get("users"), dict) else {}
    lines = [f"# BUG #{bug.get('id')} {bug.get('title') or ''}".strip()]
    kv(lines, "状态", map_val(BUG_STATUS, bug.get("status")))
    kv(lines, "严重程度", map_val(SEVERITY, bug.get("severity")))
    kv(lines, "优先级", map_val(PRI, bug.get("pri")))
    kv(lines, "类型", map_val(BUG_TYPE, bug.get("type")))
    kv(lines, "产品", bug.get("productName") or bug.get("product"))
    kv(lines, "模块", bug.get("moduleName") or bug.get("module"))
    kv(lines, "项目/迭代", bug.get("projectName") or bug.get("project"))
    kv(lines, "指派给", user_name(users, bug.get("assignedTo")))
    kv(lines, "由谁创建", user_name(users, bug.get("openedBy")))
    kv(lines, "创建日期", bug.get("openedDate"))
    kv(lines, "解决者", user_name(users, bug.get("resolvedBy")))
    kv(lines, "方案", bug.get("resolution"))
    kv(lines, "解决日期", bug.get("resolvedDate"))
    kv(lines, "关闭者", user_name(users, bug.get("closedBy")))
    kv(lines, "关键词", bug.get("keywords"))
    kv(lines, "相关需求", bug.get("story"))
    kv(lines, "相关任务", bug.get("task"))
    kv(lines, "网页", f"{server}/bug-view-{bug.get('id')}.html")
    steps = html_to_text(bug.get("steps"), server)
    if steps:
        lines.extend(["", "## 重现步骤", steps])
    actions = records(data.get("actions"))
    if actions:
        lines.extend(["", "## 历史"])
        for item in actions[-20:]:
            actor = user_name(users, item.get("actor"))
            action = item.get("action") or item.get("name") or ""
            extra = item.get("extra") or ""
            comment = html_to_text(item.get("comment"), server)
            date = item.get("date") or ""
            bit = f"- {date} {actor} {action} {extra}".strip()
            if comment:
                bit += "\n  " + comment.replace("\n", "\n  ")
            lines.append(bit)
    return "\n".join(lines).strip() + "\n"


def format_story(data: dict[str, Any], server: str) -> str:
    story = data.get("story") if isinstance(data.get("story"), dict) else data
    users = data.get("users") if isinstance(data.get("users"), dict) else {}
    lines = [f"# 需求 #{story.get('id')} {story.get('title') or ''}".strip()]
    kv(lines, "状态", map_val(STORY_STATUS, story.get("status")))
    kv(lines, "阶段", map_val(STORY_STAGE, story.get("stage")))
    kv(lines, "优先级", map_val(PRI, story.get("pri")))
    kv(lines, "产品", story.get("productName") or story.get("product"))
    kv(lines, "模块", story.get("moduleName") or story.get("module"))
    kv(lines, "计划", story.get("planTitle") or story.get("plan"))
    kv(lines, "来源", story.get("source"))
    kv(lines, "指派给", user_name(users, story.get("assignedTo")))
    kv(lines, "创建人", user_name(users, story.get("openedBy")))
    kv(lines, "创建日期", story.get("openedDate"))
    kv(lines, "预计", story.get("estimate"))
    kv(lines, "网页", f"{server}/story-view-{story.get('id')}.html")
    spec = html_to_text(story.get("spec") or story.get("desc"), server)
    if spec:
        lines.extend(["", "## 描述", spec])
    verify = html_to_text(story.get("verify"), server)
    if verify:
        lines.extend(["", "## 验收标准", verify])
    return "\n".join(lines).strip() + "\n"


def format_task(data: dict[str, Any], server: str) -> str:
    task = data.get("task") if isinstance(data.get("task"), dict) else data
    users = data.get("users") if isinstance(data.get("users"), dict) else {}
    lines = [f"# 任务 #{task.get('id')} {task.get('name') or task.get('title') or ''}".strip()]
    kv(lines, "状态", map_val(TASK_STATUS, task.get("status")))
    kv(lines, "优先级", map_val(PRI, task.get("pri")))
    kv(lines, "项目/迭代", task.get("projectName") or task.get("project"))
    kv(lines, "模块", task.get("moduleName") or task.get("module"))
    kv(lines, "指派给", user_name(users, task.get("assignedTo")))
    kv(lines, "由谁创建", user_name(users, task.get("openedBy")))
    kv(lines, "创建日期", task.get("openedDate"))
    kv(lines, "预计", task.get("estimate"))
    kv(lines, "消耗", task.get("consumed"))
    kv(lines, "剩余", task.get("left"))
    kv(lines, "截止日期", task.get("deadline"))
    kv(lines, "相关需求", task.get("story"))
    kv(lines, "网页", f"{server}/task-view-{task.get('id')}.html")
    desc = html_to_text(task.get("desc"), server)
    if desc:
        lines.extend(["", "## 描述", desc])
    return "\n".join(lines).strip() + "\n"


def format_product(data: dict[str, Any], server: str) -> str:
    product = data.get("product") if isinstance(data.get("product"), dict) else data
    lines = [f"# 产品 #{product.get('id')} {product.get('name') or ''}".strip()]
    kv(lines, "代号", product.get("code"))
    kv(lines, "状态", product.get("status"))
    kv(lines, "负责人", product.get("PO") or product.get("rd"))
    kv(lines, "类型", product.get("type"))
    kv(lines, "网页", f"{server}/product-view-{product.get('id')}.html")
    desc = html_to_text(product.get("desc"), server)
    if desc:
        lines.extend(["", "## 描述", desc])
    return "\n".join(lines).strip() + "\n"


def format_project(data: dict[str, Any], server: str) -> str:
    project = data.get("project") if isinstance(data.get("project"), dict) else data
    lines = [f"# 项目/迭代 #{project.get('id')} {project.get('name') or ''}".strip()]
    kv(lines, "代号", project.get("code"))
    kv(lines, "状态", project.get("status"))
    kv(lines, "开始", project.get("begin"))
    kv(lines, "结束", project.get("end"))
    kv(lines, "负责人", project.get("PM") or project.get("po"))
    kv(lines, "天数", project.get("days"))
    kv(lines, "网页", f"{server}/project-view-{project.get('id')}.html")
    desc = html_to_text(project.get("desc"), server)
    if desc:
        lines.extend(["", "## 描述", desc])
    return "\n".join(lines).strip() + "\n"

def md_table(rows: list[list[str]]) -> str:
    if not rows:
        return "（空）\n"
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    out = []
    for idx, row in enumerate(rows):
        out.append("| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |")
        if idx == 0:
            out.append("| " + " | ".join("-" * widths[i] for i in range(len(widths))) + " |")
    return "\n".join(out) + "\n"


def format_list(kind: str, data: dict[str, Any], extra_title: str = "") -> str:
    key_map = {
        "bug": ["bugs", "bug"],
        "story": ["stories", "story"],
        "task": ["tasks", "task"],
        "product": ["products", "product"],
        "project": ["projects", "project"],
    }
    items: list[Any] = []
    for key in key_map[kind]:
        items = records(data.get(key))
        if items:
            break
    if not items and kind in data:
        items = records(data[kind])
    pager = data.get("pager") if isinstance(data.get("pager"), dict) else {}
    title = {"bug": "Bug", "story": "需求", "task": "任务", "product": "产品", "project": "项目/迭代"}[kind]
    if extra_title:
        title = f"{title} / {extra_title}"
    lines = [f"# {title}", ""]
    if pager:
        lines.append(
            f"共 {pager.get('recTotal')} 条，第 {pager.get('pageID')}/{pager.get('pageTotal')} 页，每页 {pager.get('recPerPage')} 条。"
        )
        lines.append("")
    users = data.get("users") if isinstance(data.get("users"), dict) else {}
    if kind == "bug":
        rows = [["ID", "Bug标题", "级别", "P", "状态", "创建者", "创建日期", "确认", "指派给"]]
        for item in items:
            confirmed = str(item.get("confirmed") or "0")
            rows.append(
                [
                    str(item.get("id") or ""),
                    trunc(item.get("title"), 48),
                    str(item.get("severity") or ""),
                    str(item.get("pri") or ""),
                    map_val(BUG_STATUS, item.get("status")),
                    trunc(user_name(users, item.get("openedBy")), 16),
                    str(item.get("openedDate") or ""),
                    "是" if confirmed in ("1", "true") else "否",
                    trunc(user_name(users, item.get("assignedTo")), 16),
                ]
            )
    elif kind == "story":
        rows = [["ID", "标题", "状态", "阶段", "优先级", "指派给"]]
        for item in items:
            rows.append(
                [
                    str(item.get("id") or ""),
                    trunc(item.get("title"), 48),
                    map_val(STORY_STATUS, item.get("status")),
                    map_val(STORY_STAGE, item.get("stage")),
                    str(item.get("pri") or ""),
                    trunc(user_name(users, item.get("assignedTo")), 16),
                ]
            )
    elif kind == "task":
        rows = [["ID", "名称", "状态", "优先级", "指派给", "截止"]]
        for item in items:
            rows.append(
                [
                    str(item.get("id") or ""),
                    trunc(item.get("name") or item.get("title"), 48),
                    map_val(TASK_STATUS, item.get("status")),
                    str(item.get("pri") or ""),
                    trunc(user_name(users, item.get("assignedTo")), 16),
                    str(item.get("deadline") or ""),
                ]
            )
    elif kind == "product":
        rows = [["ID", "名称", "代号", "状态"]]
        for item in items:
            rows.append(
                [
                    str(item.get("id") or ""),
                    trunc(item.get("name"), 40),
                    str(item.get("code") or ""),
                    str(item.get("status") or ""),
                ]
            )
    else:
        rows = [["ID", "名称", "状态", "开始", "结束"]]
        for item in items:
            rows.append(
                [
                    str(item.get("id") or ""),
                    trunc(item.get("name"), 40),
                    str(item.get("status") or ""),
                    str(item.get("begin") or ""),
                    str(item.get("end") or ""),
                ]
            )
    if len(rows) == 1:
        lines.append("（空）")
        return "\n".join(lines) + "\n"
    lines.append(md_table(rows).rstrip())
    return "\n".join(lines) + "\n"


def output(fmt: str, data: Any, renderer) -> None:
    if fmt == "json":
        dump_json(data)
        return
    sys.stdout.write(renderer())


def cmd_login(args: argparse.Namespace) -> int:
    server = normalize_server(args.server or os.environ.get("ZENTAO11_URL") or DEFAULT_SERVER)
    account = args.user or os.environ.get("ZENTAO11_ACCOUNT") or ""
    password = args.password or os.environ.get("ZENTAO11_PASSWORD") or ""
    if not account:
        if sys.stdin.isatty():
            account = input("用户名: ").strip()
        else:
            raise ApiError("请使用 zentao11 login -u 账号")
    if not password:
        if sys.stdin.isatty() and sys.stderr.isatty():
            password = getpass.getpass("密码: ")
        else:
            raise ApiError("当前终端无法交互输入密码，请在本机 PowerShell 执行: zentao11 login -u 你的账号")
    client = Client(server)
    session = client.login(account, password)
    save_session(session)
    who = session.get("realname") or session.get("account")
    print(f"登录成功: {who} @ {session['server']}")
    return 0


def cmd_logout(_args: argparse.Namespace) -> int:
    clear_session()
    print("已退出登录")
    return 0


def bug_detail(client: Client, bug_id: str) -> dict[str, Any]:
    data = client.get("bug", "view", bug_id)
    if isinstance(data, dict) and isinstance(data.get("bug"), dict):
        bug = dict(data["bug"])
        if "files" not in bug and "files" in data:
            bug["files"] = data["files"]
        return bug
    return data if isinstance(data, dict) else {}


def upload_paths(values: list[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.exists():
            raise ApiError(f"Attachment was not found: {path}")
        if not path.is_file():
            raise ApiError(f"Attachment is not a file: {path}")
        if path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def encode_multipart(
    fields: dict[str, Any],
    files: list[Path],
    file_field: str = "files[]",
) -> tuple[bytes, str]:
    boundary = "----CodexZenTaoBoundary" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, raw_value in fields.items():
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        for value in values:
            chunks.append(f"--{boundary}\r\n".encode("ascii"))
            chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            chunks.append(str(value or "").encode("utf-8"))
            chunks.append(b"\r\n")
    for path in files:
        filename = path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8")
        )
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode("ascii"))
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), boundary


def request_multipart_raw(
    client: Client,
    path: str,
    fields: dict[str, Any],
    files: list[Path],
    file_field: str = "files[]",
) -> bytes:
    url = client._url(path)
    body, boundary = encode_multipart(fields, files, file_field)
    headers = {
        "User-Agent": UA,
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": client._url(path.split("?", 1)[0]),
    }
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with client.opener.open(req, timeout=client.timeout) as resp:
            raw = resp.read()
            client._sync_sid()
            return raw
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        client._sync_sid()
        if raw:
            return raw
        raise ApiError(f"HTTP {exc.code} {url}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"无法访问 {url}: {exc.reason}") from exc


IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}


def is_image_file(path: Path) -> bool:
    return path.suffix.lower().lstrip(".") in IMAGE_EXTENSIONS


def upload_editor_image(
    client: Client,
    path: Path,
    uid: str,
    object_type: str = "",
    object_id: str = "0",
) -> dict[str, str]:
    query = urllib.parse.urlencode({
        "objectType": object_type,
        "objectID": object_id,
        "extra": "editor",
    })
    raw = request_multipart_raw(
        client,
        f"file-ajaxUpload-{uid}.json?{query}",
        {},
        [path],
        file_field="imgFile",
    )
    response = mutation_response(raw)
    if isinstance(response, dict) and response.get("error") not in (None, 0, "0"):
        raise ApiError(str(response.get("message") or f"Image upload failed: {path.name}"))
    if isinstance(response, dict) and str(response.get("result") or response.get("status") or "").lower() in {
        "fail", "failed", "error"
    }:
        raise ApiError(str(response.get("message") or f"Image upload failed: {path.name}"))
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, dict):
        response = data
    url = ""
    file_id = ""
    if isinstance(response, dict):
        url = str(response.get("url") or "")
        file_id = str(response.get("id") or "")
    elif isinstance(response, str):
        url = response
    if not file_id:
        match = re.search(r"file-read-(\d+)", url, re.I)
        if match:
            file_id = match.group(1)
    if not url:
        raise ApiError(f"Image upload returned without a URL: {path.name}")
    return {"id": file_id, "url": url, "name": path.name}


def steps_with_inline_images(steps: str, images: list[dict[str, str]]) -> str:
    if not images:
        return steps
    blocks = ["<p><strong>附件截图</strong></p>"]
    for image in images:
        blocks.append(
            f'<p><img src="{html.escape(image["url"], quote=True)}" '
            f'alt="{html.escape(image["name"], quote=True)}" /></p>'
        )
    original = steps.rstrip()
    return (f"{original}\n\n" if original else "") + "\n".join(blocks)


def comment_with_inline_images(comment: str, images: list[dict[str, str]]) -> str:
    original = str(comment or "").rstrip()
    if not images:
        return original
    blocks = ["<p><strong>备注图片</strong></p>"]
    for image in images:
        blocks.append(
            f'<p><img src="{html.escape(image["url"], quote=True)}" '
            f'alt="{html.escape(image["name"], quote=True)}" /></p>'
        )
    return (f"{original}\n\n" if original else "") + "\n".join(blocks)


def locate_id(value: Any, kind: str) -> str:
    if isinstance(value, dict):
        for key in ("id", f"{kind}ID", "bugID"):
            if value.get(key):
                return str(value[key])
        for key in ("locate", "url"):
            found = locate_id(value.get(key), kind)
            if found:
                return found
    if isinstance(value, (str, bytes)):
        text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
        text = urllib.parse.unquote(html.unescape(text))
        match = re.search(rf"{re.escape(kind)}-(?:view-)?(\d+)", text, re.I)
        if match:
            return match.group(1)
    return ""


def mutation_response(raw: bytes) -> Any:
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return {"raw": ""}
    if re.search(r"user-login", text, re.I) and re.search(r"self\.location", text, re.I):
        raise AuthError("会话已失效，请重新登录")
    if text.startswith("<") or re.search(r"(?:self\.)?location(?:\.href)?\s*=", text, re.I):
        redirect = re.search(r"(?:self\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)", text, re.I)
        return {"raw": text[:1000], "locate": redirect.group(1) if redirect else text[:1000]}
    try:
        return unwrap(text)
    except AuthError:
        raise
    except ApiError:
        # ZenTao write actions may return an HTML/JavaScript redirect instead of JSON.
        return {"raw": text[:1000], "locate": text}


def product_bugs(client: Client, product_id: int) -> list[dict[str, Any]]:
    candidates = [
        ("bug", "browse", product_id, 0, "all", 0, "id_desc", 0, 200, 1),
        ("bug", "browse", product_id, 0, "unclosed", 0, "id_desc", 0, 200, 1),
    ]
    for parts in candidates:
        try:
            data = client.get(*parts)
        except ApiError:
            continue
        if isinstance(data, dict):
            items = records(data.get("bugs"))
            if items:
                return items
    return []


def cmd_bug_create(args: argparse.Namespace) -> int:
    _session, client = require_client()
    if not args.yes:
        raise ApiError("Creating a bug changes ZenTao state. Re-run with --yes after confirming the fields.")
    uploads = upload_paths(args.file)
    image_uploads = [path for path in uploads if is_image_file(path)]
    regular_uploads = [path for path in uploads if not is_image_file(path)]
    uid = uuid.uuid4().hex if image_uploads else ""
    editor_images = [upload_editor_image(client, path, uid) for path in image_uploads]
    fields: dict[str, Any] = {
        "product": args.product, "module": args.module or 0, "project": args.project or 0,
        "execution": args.execution or 0, "story": args.story or 0, "title": args.title,
        "steps": steps_with_inline_images(args.steps or args.description or "", editor_images),
        "severity": args.severity, "pri": args.pri,
        "type": args.type, "os": args.os, "browser": args.browser, "assignedTo": args.assigned_to,
        "deadline": args.deadline, "keywords": args.keywords, "mailto": args.mailto,
        "openedBuild": args.opened_build, "status": "active",
    }
    if uid:
        fields["uid"] = uid
    if regular_uploads:
        fields["labels[]"] = [path.stem for path in regular_uploads]
    before_ids = {
        str(item.get("id") or "")
        for item in product_bugs(client, args.product)
        if str(item.get("title") or "") == args.title
    }
    submit_path = f"bug-create-{args.product}-0-0.json"
    if uploads:
        raw = request_multipart_raw(client, submit_path, fields, regular_uploads)
    else:
        raw = client.request_raw(submit_path, {
            str(key): str(value) for key, value in fields.items() if value is not None
        })
    response = mutation_response(raw)
    bug_id = locate_id(response, "bug")
    if not bug_id:
        matches = [
            item for item in product_bugs(client, args.product)
            if str(item.get("title") or "") == args.title
            and str(item.get("id") or "") not in before_ids
        ]
        if matches:
            bug_id = str(max(matches, key=lambda item: int(str(item.get("id") or "0"))).get("id") or "")
    if not bug_id:
        raise ApiError("Bug creation returned without a bug ID; verify it in ZenTao before retrying.")
    created = bug_detail(client, bug_id)
    if str(created.get("id") or "") != bug_id or str(created.get("title") or "") != args.title:
        raise ApiError(f"Bug {bug_id} could not be verified after creation; verify it in ZenTao before retrying.")
    attached = records(created.get("files"))
    if regular_uploads and len(attached) < len(regular_uploads):
        raise ApiError(
            f"Bug {bug_id} was created, but only {len(attached)} of {len(regular_uploads)} attachments could be verified. "
            "Do not retry creation; inspect the Bug in ZenTao."
        )
    saved_steps = str(created.get("steps") or "")
    missing_images = []
    for image in editor_images:
        image_path = urllib.parse.urlparse(image["url"]).path or image["url"]
        if image["url"] not in saved_steps and image_path not in saved_steps:
            missing_images.append(image["name"])
    visible_ids = {str(item.get("id") or "") for item in attached}
    visible_images = [image["name"] for image in editor_images if image.get("id") in visible_ids]
    if missing_images or visible_images:
        reason = "inline image was not saved" if missing_images else "inline image still appears as an attachment"
        raise ApiError(
            f"Bug {bug_id} was created, but {reason}. Do not retry creation; inspect the Bug in ZenTao."
        )
    payload = {
        "created": True,
        "bugID": bug_id,
        "title": args.title,
        "status": created.get("status"),
        "attachments": [path.name for path in regular_uploads],
        "inlineImages": editor_images,
        "response": response,
    }
    output(args.format, payload, lambda: f"Bug #{bug_id} created: {args.title}\n")
    return 0


def cmd_bug_close(args: argparse.Namespace) -> int:
    _session, client = require_client()
    bug_id = str(args.bug_id).strip()
    before = bug_detail(client, bug_id)
    if not before or str(before.get("id") or "") != bug_id:
        raise ApiError(f"Bug {bug_id} was not found.")
    if before.get("status") == "closed":
        payload = {"bugID": bug_id, "changed": False, "status": "closed", "title": before.get("title") or ""}
        output(args.format, payload, lambda: f"Bug #{bug_id} is already closed.\n")
        return 0
    if not args.yes:
        raise ApiError("Closing a bug changes ZenTao state. Re-run with --yes after confirming the bug ID.")
    uploads = upload_paths(args.file)
    if any(not is_image_file(path) for path in uploads):
        raise ApiError("Bug close comment images must be image files (png, jpg, jpeg, gif, bmp, webp).")
    editor_images = [upload_editor_image(client, path, uuid.uuid4().hex, "bug", bug_id) for path in uploads]
    comment = comment_with_inline_images(args.comment or "Closed via Codex", editor_images)
    responses: dict[str, Any] = {}
    current = before
    if current.get("status") == "active":
        resolve_raw = client.request_raw(f"bug-resolve-{bug_id}.json", {
            "resolution": args.resolution,
            "resolvedBuild": "trunk",
            "resolvedDate": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "assignedTo": str(current.get("openedBy") or ""),
            "comment": comment,
        })
        responses["resolve"] = mutation_response(resolve_raw)
        current = bug_detail(client, bug_id)
        if current.get("status") not in ("resolved", "closed"):
            raise ApiError(
                f"Resolve request returned but bug {bug_id} status is {current.get('status') or 'unknown'}; close was not attempted."
            )
    if current.get("status") != "closed":
        close_raw = client.request_raw(f"bug-close-{bug_id}.json", {"comment": comment})
        responses["close"] = mutation_response(close_raw)
    after = bug_detail(client, bug_id)
    if after.get("status") != "closed":
        raise ApiError(f"Close request returned but bug {bug_id} status is {after.get('status') or 'unknown'}.")
    payload = {"bugID": bug_id, "changed": True, "status": "closed", "title": after.get("title") or "", "responses": responses}
    output(args.format, payload, lambda: f"Bug #{bug_id} closed: {after.get('title') or ''}\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    session = load_session()
    if not session.get("zentaosid"):
        if args.format == "json":
            dump_json({"loggedIn": False, "server": DEFAULT_SERVER})
            return 2
        return print_not_logged_in()
    client = Client(session["server"], session["zentaosid"])
    try:
        client.request("my.json")
        live = True
    except AuthError:
        live = False
    payload = {
        "loggedIn": live,
        "server": session.get("server"),
        "account": session.get("account"),
        "realname": session.get("realname"),
        "loginTime": session.get("loginTime"),
        "cli": VERSION,
    }
    if args.format == "json":
        dump_json(payload)
        return 0 if live else 2
    if not live:
        print("会话已失效。", file=sys.stderr)
        return print_not_logged_in()
    who = session.get("realname") or session.get("account")
    print(f"已登录: {who} ({session.get('account')})")
    print(f"地址: {session.get('server')}")
    print(f"登录时间: {session.get('loginTime')}")
    print(f"CLI: {VERSION}")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    _session, client = require_client()
    path = args.path.strip()
    if not path.endswith(".json") and not path.startswith("http"):
        last = path.split("/")[-1]
        if "." not in last:
            path = path + ".json"
    data = client.request(path)
    output(args.format, data, lambda: json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return 0


def view_or_list(args: argparse.Namespace, kind: str) -> int:
    session, client = require_client()
    ident = getattr(args, "id", None)
    as_list = bool(getattr(args, "list", False) or ident is None)
    if ident and not as_list:
        data = client.get(kind, "view", ident)
        renderer = {
            "bug": lambda: format_bug(data if isinstance(data, dict) else {}, session["server"]),
            "story": lambda: format_story(data if isinstance(data, dict) else {}, session["server"]),
            "task": lambda: format_task(data if isinstance(data, dict) else {}, session["server"]),
            "product": lambda: format_product(data if isinstance(data, dict) else {}, session["server"]),
            "project": lambda: format_project(data if isinstance(data, dict) else {}, session["server"]),
        }[kind]
        output(args.format, data, renderer)
        return 0

    page = getattr(args, "page", 1) or 1
    limit = getattr(args, "limit", 50) or 50
    browse_type = getattr(args, "type", None) or "assignedTo"
    product_id = getattr(args, "product", None)
    project_id = getattr(args, "project", None)
    title = browse_type
    if kind == "bug":
        if product_id:
            _parts, data = client.try_get(
                [
                    (
                        "bug",
                        "browse",
                        product_id,
                        0,
                        "unclosed" if browse_type == "assignedTo" else browse_type,
                        0,
                        "id_desc",
                        0,
                        limit,
                        page,
                    ),
                    ("bug", "browse", product_id),
                ]
            )
            title = f"产品 {product_id}"
        else:
            data = client.get("my", "bug", browse_type, "id_desc", 0, limit, page)
    elif kind == "story":
        if product_id:
            data = client.get(
                "story",
                "browse",
                product_id,
                0,
                "unclosed" if browse_type == "assignedTo" else browse_type,
                0,
                "id_desc",
                0,
                limit,
                page,
            )
            title = f"产品 {product_id}"
        else:
            data = client.get("my", "story", browse_type, "id_desc", 0, limit, page)
    elif kind == "task":
        if project_id:
            data = client.get(
                "project",
                "task",
                project_id,
                "unclosed" if browse_type == "assignedTo" else browse_type,
                0,
                "id_desc",
                0,
                limit,
                page,
            )
            title = f"迭代 {project_id}"
        else:
            data = client.get("my", "task", browse_type, "id_desc", 0, limit, page)
    elif kind == "product":
        _parts, data = client.try_get([("product", "index"), ("product", "all"), ("product", "browse")])
        title = "全部"
    else:
        _parts, data = client.try_get([("project", "browse"), ("project", "index"), ("my", "project")])
        title = "全部"
    if not isinstance(data, dict):
        data = {"data": data}
    output(args.format, data, lambda: format_list(kind, data, str(title)))
    return 0


def cmd_my(args: argparse.Namespace) -> int:
    session, client = require_client()
    bugs = client.get("my", "bug", "assignedTo", "id_desc", 0, args.limit, 1)
    stories = client.get("my", "story", "assignedTo", "id_desc", 0, args.limit, 1)
    tasks = client.get("my", "task", "assignedTo", "id_desc", 0, args.limit, 1)
    payload = {"bugs": bugs, "stories": stories, "tasks": tasks, "account": session.get("account")}
    if args.format == "json":
        dump_json(payload)
        return 0
    who = session.get("realname") or session.get("account")
    print(f"# 我的地盘 / {who}\n")
    print(format_list("bug", bugs if isinstance(bugs, dict) else {}, "指派给我"))
    print(format_list("story", stories if isinstance(stories, dict) else {}, "指派给我"))
    print(format_list("task", tasks if isinstance(tasks, dict) else {}, "指派给我"))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    session, client = require_client()
    query = args.query.strip()
    module = args.module
    if query.isdigit() and module in (None, "auto"):
        for kind, mod in (("bug", "bug"), ("story", "story"), ("task", "task")):
            try:
                data = client.get(mod, "view", query)
            except (ApiError, AuthError):
                continue
            obj = data.get(kind) if isinstance(data, dict) and isinstance(data.get(kind), dict) else data
            if isinstance(obj, dict) and str(obj.get("id") or "") == query:
                renderer = {
                    "bug": lambda d=data: format_bug(d if isinstance(d, dict) else {}, session["server"]),
                    "story": lambda d=data: format_story(d if isinstance(d, dict) else {}, session["server"]),
                    "task": lambda d=data: format_task(d if isinstance(d, dict) else {}, session["server"]),
                }[kind]
                output(args.format, data, renderer)
                return 0
    kinds = [module] if module in ("bug", "story", "task") else ["bug", "story", "task"]
    found: dict[str, list[dict[str, Any]]] = {}
    q = query.lower()
    for kind in kinds:
        data = client.get("my", kind, "assignedTo", "id_desc", 0, args.limit, 1)
        blob_keys = ("id", "title", "name", "keywords")
        items = records(first_present(data if isinstance(data, dict) else {}, [kind + "s", kind]) or [])
        matched = []
        for item in items:
            blob = " ".join(str(item.get(k) or "") for k in blob_keys).lower()
            if q in blob:
                matched.append(item)
        found[kind] = matched
    if args.format == "json":
        dump_json({"query": query, "result": found})
        return 0
    any_hit = False
    for kind, items in found.items():
        print(format_list(kind, {kind + "s": items}, f"搜索 {query}"))
        if items:
            any_hit = True
    if not any_hit:
        print("没有匹配结果。当前搜索范围是「指派给我」的列表，不是全库。")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--format", choices=["md", "json"], default="md", help="输出格式")
    common.add_argument("--timeout", type=int, default=TIMEOUT)
    parser = argparse.ArgumentParser(
        prog="zentao11",
        description="禅道 11.x CLI（网页 JSON 接口，适用于 ihr360 11.3）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", parents=[common], help="登录禅道")
    p_login.add_argument("-s", "--server", default=DEFAULT_SERVER)
    p_login.add_argument("-u", "--user")
    p_login.add_argument("-p", "--password")
    p_login.set_defaults(func=cmd_login)

    p_logout = sub.add_parser("logout", parents=[common], help="退出登录")
    p_logout.set_defaults(func=cmd_logout)

    p_bug_create = sub.add_parser("bug-create", parents=[common], help="create a Bug")
    p_bug_create.add_argument("--product", type=int, required=True)
    p_bug_create.add_argument("--title", required=True)
    p_bug_create.add_argument("--module", type=int)
    p_bug_create.add_argument("--project", type=int)
    p_bug_create.add_argument("--execution", type=int)
    p_bug_create.add_argument("--story", type=int)
    p_bug_create.add_argument("--description", default="")
    p_bug_create.add_argument("--steps", default="")
    p_bug_create.add_argument("--severity", choices=["1", "2", "3", "4"], default="3")
    p_bug_create.add_argument("--pri", choices=["1", "2", "3", "4"], default="3")
    p_bug_create.add_argument("--type", default="codeerror")
    p_bug_create.add_argument("--os", default="all")
    p_bug_create.add_argument("--browser", default="all")
    p_bug_create.add_argument("--assigned-to", default="")
    p_bug_create.add_argument("--deadline", default="")
    p_bug_create.add_argument("--keywords", default="")
    p_bug_create.add_argument("--mailto", default="")
    p_bug_create.add_argument("--opened-build", default="trunk")
    p_bug_create.add_argument(
        "--file",
        action="append",
        default=[],
        help="attachment path; repeat --file to upload multiple files",
    )
    p_bug_create.add_argument("--yes", action="store_true")
    p_bug_create.set_defaults(func=cmd_bug_create)

    p_bug_close = sub.add_parser("bug-close", parents=[common], help="close a Bug by ID")
    p_bug_close.add_argument("bug_id")
    p_bug_close.add_argument("--comment", default="")
    p_bug_close.add_argument("--resolution", default="fixed")
    p_bug_close.add_argument(
        "--file",
        action="append",
        default=[],
        help="image to embed in the close comment; repeat --file for multiple images",
    )
    p_bug_close.add_argument("--yes", action="store_true")
    p_bug_close.set_defaults(func=cmd_bug_close)

    p_status = sub.add_parser("status", parents=[common], help="查看登录状态")
    p_status.set_defaults(func=cmd_status)

    p_get = sub.add_parser("get", parents=[common], help="请求任意 PATH_INFO，例如 bug-view-123")
    p_get.add_argument("path")
    p_get.set_defaults(func=cmd_get)

    for name, help_text in (
        ("bug", "查看或列出 Bug"),
        ("story", "查看或列出需求"),
        ("task", "查看或列出任务"),
    ):
        p = sub.add_parser(name, parents=[common], help=help_text)
        p.add_argument("id", nargs="?")
        p.add_argument("--list", action="store_true")
        p.add_argument("--type", default="assignedTo")
        p.add_argument("--page", type=int, default=1)
        p.add_argument("--limit", type=int, default=50)
        if name in ("bug", "story"):
            p.add_argument("--product", type=int)
        if name == "task":
            p.add_argument("--project", type=int)
        p.set_defaults(func=lambda a, kind=name: view_or_list(a, kind))

    p_product = sub.add_parser("product", parents=[common], help="查看或列出产品")
    p_product.add_argument("id", nargs="?")
    p_product.add_argument("--list", action="store_true")
    p_product.set_defaults(func=lambda a: view_or_list(a, "product"))

    p_project = sub.add_parser("project", parents=[common], help="查看或列出项目/迭代")
    p_project.add_argument("id", nargs="?")
    p_project.add_argument("--list", action="store_true")
    p_project.set_defaults(func=lambda a: view_or_list(a, "project"))

    p_my = sub.add_parser("my", parents=[common], help="我的 Bug/需求/任务总览")
    p_my.add_argument("--limit", type=int, default=20)
    p_my.set_defaults(func=cmd_my)

    p_search = sub.add_parser("search", parents=[common], help="按 ID 精确查找，或在指派给我的列表里搜标题")
    p_search.add_argument("query")
    p_search.add_argument("--module", choices=["auto", "bug", "story", "task"], default="auto")
    p_search.add_argument("--limit", type=int, default=100)
    p_search.set_defaults(func=cmd_search)

    p_ver = sub.add_parser("version", parents=[common], help="显示版本")
    p_ver.set_defaults(func=lambda a: (print(f"zentao11 {VERSION}"), 0)[-1])
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except AuthError as exc:
        if str(exc) and str(exc) != "未登录":
            print(f"Error: {exc}", file=sys.stderr)
        return print_not_logged_in()
    except ApiError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if getattr(args, "command", "") == "login":
            print("提示：账号必须是禅道用户名，不是姓名。", file=sys.stderr)
            print("提示：请运行 zentao11 login -u 你的账号 ，在密码提示处输入，不要加 -p。", file=sys.stderr)
            print("提示：PowerShell 的 -p 会改写带 $ 的密码。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
