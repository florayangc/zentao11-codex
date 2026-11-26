#!/usr/bin/env python3
"""Task helper for the bundled ZenTao 11.3 web-session CLI."""

from __future__ import annotations

import argparse
import html as html_lib
import importlib.util
import json
import os
import re
import sys
import uuid
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

VERSION = "1.1.0"
COMPLETED_STATUSES = {"done", "closed"}


def configure_stdio() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def find_zentao11_script() -> Path:
    raw = os.environ.get("ZENTAO11_SCRIPT")
    if raw:
        return Path(raw)
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "zentao11" / "scripts" / "zentao11.py",
        Path.home() / ".codex" / "skills" / "zentao11" / "scripts" / "zentao11.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("Cannot find zentao11.py. Install the zentao11 skill or set ZENTAO11_SCRIPT.")


def load_zentao11() -> Any:
    script = find_zentao11_script()
    spec = importlib.util.spec_from_file_location("zentao11_base", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load_zentao11()


class FormOptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inputs: dict[str, str] = {}
        self.selects: dict[str, list[dict[str, str]]] = {}
        self.textareas: dict[str, str] = {}
        self._select_name = ""
        self._option: dict[str, str] | None = None
        self._textarea_name = ""
        self._textarea_data: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {k.lower(): v or "" for k, v in attrs}
        if tag == "input":
            name = attrs_map.get("name", "")
            input_type = attrs_map.get("type", "").lower()
            if input_type in {"checkbox", "radio"} and "checked" not in attrs_map:
                return
            if name:
                self.inputs[name] = attrs_map.get("value", "")
        elif tag == "select":
            name = attrs_map.get("name") or attrs_map.get("id") or ""
            self._select_name = name
            if name:
                self.selects.setdefault(name, [])
        elif tag == "option" and self._select_name:
            self._option = {
                "value": attrs_map.get("value", ""),
                "label": "",
                "selected": "selected" in attrs_map,
            }
        elif tag == "textarea":
            self._textarea_name = attrs_map.get("name", "")
            self._textarea_data = []

    def handle_data(self, data: str) -> None:
        if self._option is not None:
            self._option["label"] += data
        if self._textarea_name:
            self._textarea_data.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self._option is not None:
            if self._select_name:
                item = dict(self._option)
                item["label"] = html_lib.unescape(re.sub(r"\s+", " ", item["label"]).strip())
                self.selects.setdefault(self._select_name, []).append(item)
            self._option = None
        elif tag == "select":
            self._select_name = ""
        elif tag == "textarea":
            if self._textarea_name:
                self.textareas[self._textarea_name] = "".join(self._textarea_data)
            self._textarea_name = ""
            self._textarea_data = []


def clean_text(raw: Any, server: str) -> str:
    text = BASE.html_to_text(raw, server)
    return re.sub(r"\s+", " ", text).strip()


def norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def display_option(option: dict[str, str]) -> str:
    label = option.get("label") or ""
    value = option.get("value") or ""
    if label and value and label != value:
        return f"{label} ({value})"
    return label or value


def match_named_option(
    field: str,
    query: str | None,
    options: list[dict[str, str]],
    *,
    required: bool = True,
) -> tuple[str, str, str | None]:
    if not query:
        if required:
            return "", "", f"{field} 不能为空"
        return "", "", None
    q = norm(query)
    usable = [item for item in options if item.get("value")]
    exact = [item for item in usable if q in {norm(item.get("value")), norm(item.get("label"))}]
    contains = [
        item
        for item in usable
        if not exact and (q in norm(item.get("label")) or q in norm(item.get("value")))
    ]
    matches = exact or contains
    if len(matches) == 1:
        item = matches[0]
        return item.get("value", ""), item.get("label", "") or item.get("value", ""), None
    if not matches:
        allowed = ", ".join(display_option(item) for item in usable[:20])
        return "", "", f"{field} 未在现有列表中匹配到: {query}。可选值: {allowed}"
    allowed = ", ".join(display_option(item) for item in matches[:10])
    return "", "", f"{field} 匹配到多个候选，请说得更精确: {allowed}"


def first_select(form: FormOptionParser, names: list[str]) -> list[dict[str, str]]:
    for name in names:
        if name in form.selects:
            return form.selects[name]
        array_name = name + "[]"
        if array_name in form.selects:
            return form.selects[array_name]
    return []


def has_field(form: FormOptionParser, name: str) -> bool:
    return name in form.inputs or name in form.selects or name in form.textareas


def put_form_value(form: FormOptionParser, payload: dict[str, str | list[str]], name: str, value: str) -> None:
    array_name = name + "[]"
    if has_field(form, array_name):
        payload[array_name] = value
    else:
        payload[name] = value


def extract_uid(page: str) -> str:
    for pattern in (
        r"""(?i)\bkuid\s*[:=]\s*['"]([a-z0-9]+)['"]""",
        r"""(?i)\buid\s*[:=]\s*['"]([a-z0-9]+)['"]""",
    ):
        match = re.search(pattern, page)
        if match:
            return match.group(1)
    for tag in re.findall(r"(?is)<input\b[^>]*>", page):
        if not re.search(r"""(?i)\b(?:name|id)\s*=\s*['"]uid['"]""", tag):
            continue
        match = re.search(r"""(?i)\bvalue\s*=\s*['"]([a-z0-9]+)['"]""", tag)
        if match:
            return html_lib.unescape(match.group(1))
    return ""


def encode_multipart(fields: dict[str, str | list[str]]) -> tuple[bytes, str]:
    boundary = "----CodexZenTaoBoundary" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, raw_value in fields.items():
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        for value in values:
            chunks.append(f"--{boundary}\r\n".encode("utf-8"))
            chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            chunks.append(str(value or "").encode("utf-8"))
            chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def request_multipart_raw(client: Any, path: str, data: dict[str, str | list[str]]) -> bytes:
    url = client._url(path)
    body, boundary = encode_multipart(data)
    headers = {
        "User-Agent": BASE.UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": client._url(path.split("?", 1)[0]),
    }
    req = BASE.urllib.request.Request(url, data=body, headers=headers)
    try:
        with client.opener.open(req, timeout=client.timeout) as resp:
            raw = resp.read()
            client._sync_sid()
            return raw
    except BASE.urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        client._sync_sid()
        if raw:
            return raw
        raise BASE.ApiError(f"HTTP {exc.code} {url}") from exc
    except BASE.urllib.error.URLError as exc:
        raise BASE.ApiError(f"无法访问 {url}: {exc.reason}") from exc


def parse_day(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text or text == "0000-00-00":
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def task_title(task: dict[str, Any]) -> str:
    return str(task.get("name") or task.get("title") or task.get("storyTitle") or "").strip()


def normalize_task(task: dict[str, Any], today: date, users: dict[str, Any]) -> dict[str, Any]:
    deadline = parse_day(task.get("deadline"))
    remaining = None if deadline is None else (deadline - today).days
    return {
        "ID": str(task.get("id") or ""),
        "P": str(task.get("pri") or ""),
        "所属迭代": str(task.get("projectName") or task.get("project") or ""),
        "任务名称": task_title(task),
        "创建": BASE.user_name(users, task.get("openedBy")),
        "指派": BASE.user_name(users, task.get("assignedTo")),
        "由谁完成": BASE.user_name(users, task.get("finishedBy")),
        "预计": str(task.get("estimate") or ""),
        "消耗": str(task.get("consumed") or ""),
        "剩余": str(task.get("left") or ""),
        "截止": deadline.isoformat() if deadline else "",
        "状态": BASE.map_val(BASE.TASK_STATUS, task.get("status")),
        "剩余天数": remaining,
    }


def get_client() -> tuple[dict[str, Any], Any]:
    return BASE.require_client()


def list_tasks(args: argparse.Namespace) -> dict[str, Any]:
    session, client = get_client()
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else date.today()
    data = client.get("my", "task", "assignedTo", "id_desc", 0, args.limit, args.page)
    raw_tasks = BASE.records(data.get("tasks") if isinstance(data, dict) else data)
    users = data.get("users") if isinstance(data, dict) and isinstance(data.get("users"), dict) else {}
    by_id = {str(item.get("id") or ""): item for item in raw_tasks if isinstance(item, dict)}
    account = str(session.get("account") or "")
    for execution in execution_candidates(client):
        try:
            execution_data = client.request(f"execution-task-{execution['value']}-all-0-id_desc.json")
        except Exception:
            continue
        if isinstance(execution_data, dict) and isinstance(execution_data.get("users"), dict):
            users.update(execution_data["users"])
        for item in BASE.records(execution_data.get("tasks") if isinstance(execution_data, dict) else []):
            if not isinstance(item, dict) or str(item.get("assignedTo") or "") != account:
                continue
            item = dict(item)
            item["projectName"] = execution.get("label") or execution.get("value") or item.get("projectName") or ""
            by_id[str(item.get("id") or "")] = item
    raw_tasks = sorted(by_id.values(), key=lambda item: int(str(item.get("id") or "0") or "0"), reverse=True)
    raw_tasks = raw_tasks[: args.limit]
    tasks = [normalize_task(item, today, users) for item in raw_tasks if isinstance(item, dict)]
    return {
        "account": session.get("account"),
        "server": session.get("server"),
        "today": today.isoformat(),
        "count": len(tasks),
        "columns": ["ID", "P", "所属迭代", "任务名称", "创建", "指派", "由谁完成", "预计", "消耗", "剩余", "截止", "状态", "剩余天数"],
        "tasks": tasks,
    }


def task_detail(client: Any, task_id: str) -> dict[str, Any]:
    data = client.get("task", "view", task_id)
    if isinstance(data, dict) and isinstance(data.get("task"), dict):
        return data["task"]
    if isinstance(data, dict):
        return data
    return {}


def story_detail(client: Any, story_id: str) -> dict[str, Any]:
    data = client.get("story", "view", story_id)
    if isinstance(data, dict) and isinstance(data.get("story"), dict):
        return data["story"]
    if isinstance(data, dict):
        return data
    return {}


def story_priority(client: Any, story_id: str) -> tuple[str, str]:
    story = story_detail(client, story_id)
    priority = str(story.get("pri") or "").strip()
    if priority not in {"1", "2", "3", "4"}:
        raise BASE.ApiError(f"相关需求 {story_id} 没有可用的优先级，无法按需求优先级创建任务。")
    return priority, str(story.get("title") or story.get("name") or story_id)


def task_status(task: dict[str, Any]) -> str:
    return str(task.get("status") or "").strip().lower()


def is_completed(task: dict[str, Any]) -> bool:
    return task_status(task) in COMPLETED_STATUSES


def numeric_value(value: Any, default: float = 0) -> float:
    try:
        return float(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def format_hours(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def finish_plan(
    session: dict[str, Any],
    task: dict[str, Any],
    *,
    consumed: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    account = str(session.get("account") or "")
    opened_by = str(task.get("openedBy") or "")
    assigned_to = str(task.get("assignedTo") or "")
    estimate = numeric_value(task.get("estimate"))
    current_consumed = format_hours(estimate) if consumed is None else str(consumed)
    total_consumed = numeric_value(task.get("consumed")) + numeric_value(current_consumed)
    own_task = opened_by == account
    return {
        "taskID": str(task.get("id") or ""),
        "title": task_title(task),
        "account": account,
        "openedBy": opened_by,
        "assignedTo": assigned_to,
        "isOwnTask": own_task,
        "alreadyCompleted": is_completed(task),
        "status": task.get("status") or "",
        "estimate": format_hours(estimate),
        "consumedBefore": str(task.get("consumed") or "0"),
        "currentConsumed": current_consumed,
        "consumedAfter": format_hours(total_consumed),
        "comment": comment or "",
        "nextAction": "close" if own_task else "complete_only",
        "completeEndpoint": f"{session.get('server')}/task-finish-{task.get('id')}.html",
        "closeEndpoint": f"{session.get('server')}/task-close-{task.get('id')}.json",
    }


def finish_task(
    client: Any,
    task: dict[str, Any],
    *,
    consumed: str | None,
    comment: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    estimate = numeric_value(task.get("estimate"))
    current_consumed = format_hours(estimate) if consumed is None else str(consumed)
    total_consumed = numeric_value(task.get("consumed")) + numeric_value(current_consumed)
    real_started = str(task.get("realStarted") or task.get("estStarted") or "").strip()
    if not real_started:
        real_started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = f"task-finish-{str(task.get('id') or '')}.html"
    page = client.request_raw(path).decode("utf-8", "replace")
    form = FormOptionParser()
    form.feed(page)
    data: dict[str, str | list[str]] = {}
    if has_field(form, "currentConsumed"):
        put_form_value(form, data, "currentConsumed", current_consumed)
        put_form_value(form, data, "consumed", format_hours(total_consumed))
    else:
        put_form_value(form, data, "consumed", current_consumed)
    put_form_value(form, data, "assignedTo", str(task.get("assignedTo") or ""))
    put_form_value(form, data, "finishedDate", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    put_form_value(form, data, "comment", comment or "")
    if has_field(form, "realStarted"):
        put_form_value(form, data, "realStarted", real_started)
    if has_field(form, "status"):
        put_form_value(form, data, "status", "done")
    uid = extract_uid(page)
    if uid:
        data["uid"] = uid
    raw = client.request_raw(path, data)
    response = BASE.mutation_response(raw)
    after = task_detail(client, str(task.get("id") or ""))
    if task_status(after) != "done":
        raise BASE.ApiError(
            f"Finish request returned but task {task.get('id')} status is {after.get('status') or 'unknown'}."
        )
    return response, after


def close_task(args: argparse.Namespace) -> dict[str, Any]:
    session, client = get_client()
    task_id = str(args.task_id).strip()
    before = task_detail(client, task_id)
    if not before or str(before.get("id") or "") != task_id:
        raise BASE.ApiError(f"Task {task_id} was not found.")
    account = str(session.get("account") or "")
    assigned_to = str(before.get("assignedTo") or "")
    opened_by = str(before.get("openedBy") or "")
    if assigned_to and assigned_to != account:
        raise BASE.ApiError(f"Task {task_id} is assigned to {assigned_to}, not the current account {account}.")

    plan = finish_plan(
        session,
        before,
        consumed=args.consumed,
        comment=args.comment,
    )
    if task_status(before) == "closed":
        return {
            "taskID": task_id,
            "changed": False,
            "status": "closed",
            "message": "任务已经是关闭状态",
            "title": task_title(before),
        }
    if args.dry_run:
        return {
            "taskID": task_id,
            "changed": False,
            "dryRun": True,
            "status": before.get("status") or "",
            "title": task_title(before),
            "plan": plan,
            "wouldCall": (
                [plan["completeEndpoint"], plan["closeEndpoint"]]
                if not is_completed(before) and opened_by == account
                else [plan["completeEndpoint"]]
                if not is_completed(before)
                else [plan["closeEndpoint"]]
                if opened_by == account
                else []
            ),
        }
    if not args.yes:
        raise BASE.ApiError(
            "关闭任务前必须先确认完成草案。请先运行 close TASK_ID --dry-run，"
            "确认完成工时后再运行 close TASK_ID --yes。"
        )

    comment = args.comment if args.comment is not None else "Closed via Codex"
    responses: dict[str, Any] = {}
    current = before
    if not is_completed(current):
        response, current = finish_task(
            client,
            current,
            consumed=args.consumed,
            comment=comment,
        )
        responses["finish"] = response

    if opened_by != account:
        return {
            "taskID": task_id,
            "changed": bool(responses),
            "status": task_status(current),
            "title": task_title(current),
            "action": "complete_only",
            "message": "任务由其他账号创建，当前账号不能关闭；已按“关闭”请求完成任务，未执行关闭。",
            "completion": plan,
            "responses": responses,
        }

    raw = client.request_raw(BASE.path_info("task", "close", task_id), {"comment": comment})
    responses["close"] = BASE.mutation_response(raw)
    after = task_detail(client, task_id)
    if task_status(after) != "closed":
        raise BASE.ApiError(f"Close request returned but task {task_id} status is {after.get('status') or 'unknown'}.")
    return {
        "taskID": task_id,
        "changed": True,
        "status": "closed",
        "title": task_title(after),
        "action": "complete_then_close" if not is_completed(before) else "close_only",
        "completion": plan if not is_completed(before) else None,
        "responses": responses,
    }


def complete_task_command(args: argparse.Namespace) -> dict[str, Any]:
    session, client = get_client()
    task_id = str(args.task_id).strip()
    before = task_detail(client, task_id)
    if not before or str(before.get("id") or "") != task_id:
        raise BASE.ApiError(f"Task {task_id} was not found.")
    account = str(session.get("account") or "")
    assigned_to = str(before.get("assignedTo") or "")
    if assigned_to and assigned_to != account:
        raise BASE.ApiError(f"Task {task_id} is assigned to {assigned_to}, not the current account {account}.")
    plan = finish_plan(session, before, consumed=args.consumed, comment=args.comment)
    if is_completed(before):
        return {"taskID": task_id, "changed": False, "status": task_status(before), "plan": plan}
    if args.dry_run:
        return {"taskID": task_id, "changed": False, "dryRun": True, "status": task_status(before), "plan": plan}
    if not args.yes:
        raise BASE.ApiError("完成任务会修改禅道数据。请先运行 finish TASK_ID --dry-run，确认后再使用 --yes。")
    response, after = finish_task(
        client,
        before,
        consumed=args.consumed,
        comment=args.comment,
    )
    return {
        "taskID": task_id,
        "changed": True,
        "status": task_status(after),
        "title": task_title(after),
        "plan": plan,
        "response": response,
    }


def execution_candidates(client: Any) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(execution_id: Any, name: Any, project_name: Any = "") -> None:
        eid = str(execution_id or "").strip()
        label = str(name or "").strip().strip("/")
        project = str(project_name or "").strip()
        if not eid or not label or eid in seen:
            return
        seen.add(eid)
        candidates.append({"value": eid, "label": label, "project": project})

    for path in ("execution-all.json", "execution-browse.json", "project-execution-0.json"):
        try:
            data = client.request(path)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for item in BASE.records(data.get("executionStats")):
            add(item.get("id"), item.get("name"), item.get("projectName") or item.get("project"))
        executions = data.get("executions")
        if isinstance(executions, dict):
            for eid, label in executions.items():
                add(eid, label)
        execution = data.get("execution")
        if isinstance(execution, dict):
            add(execution.get("id"), execution.get("name"), execution.get("projectName") or execution.get("project"))
    return candidates


def match_execution(client: Any, query: str) -> tuple[dict[str, str] | None, str | None]:
    candidates = execution_candidates(client)
    value, label, error = match_named_option("所属迭代", query, candidates)
    if error and str(query).strip().isdigit():
        execution_id = str(query).strip()
        try:
            data = client.get("project", "view", execution_id)
        except Exception:
            data = None
        project = data.get("project") if isinstance(data, dict) else None
        if isinstance(project, dict) and str(project.get("id") or "") == execution_id:
            name = str(project.get("name") or "").strip()
            if name:
                return {"id": execution_id, "name": name, "project": name}, None
    if error:
        return None, error
    for item in candidates:
        if item["value"] == value:
            return {"id": value, "name": label, "project": item.get("project", "")}, None
    return None, f"所属迭代未在现有列表中匹配到: {query}"


def load_create_form(client: Any, execution_id: str) -> tuple[FormOptionParser, str, str]:
    paths = [f"task-create-{execution_id}-0-0.html", f"task-create-{execution_id}.html"]
    page = ""
    form_path = paths[-1]
    last_error: Exception | None = None
    for path in paths:
        try:
            page = client.request_raw(path).decode("utf-8", "replace")
            form_path = path
            break
        except Exception as exc:
            last_error = exc
    if not page and last_error:
        raise last_error
    if "user-login" in page and "self.location" in page:
        raise BASE.AuthError("会话已失效，请重新登录")
    form = FormOptionParser()
    form.feed(page)
    return form, page, form_path


def selected_value(options: list[dict[str, str]]) -> str:
    for item in options:
        if item.get("selected") and item.get("value"):
            return item["value"]
    return ""


def build_create_draft(args: argparse.Namespace) -> dict[str, Any]:
    _session, client = get_client()
    errors: list[str] = []
    if not args.execution:
        errors.append("所属迭代不能为空，且必须能匹配到现有迭代")
        execution = None
    else:
        execution, error = match_execution(client, args.execution)
        if error:
            errors.append(error)

    if errors or execution is None:
        return {"ready": False, "errors": errors, "draft": {}, "form": {}}

    form, page, form_path = load_create_form(client, execution["id"])
    type_value, type_label, error = match_named_option("任务类型", args.type, first_select(form, ["type"]))
    if error:
        errors.append(error)
    module_value, module_label, error = match_named_option(
        "所属模块", args.module, first_select(form, ["module"]), required=bool(args.module)
    )
    if error:
        errors.append(error)
    assigned_value, assigned_label, error = match_named_option(
        "指派给谁", args.assigned_to, first_select(form, ["assignedTo"]), required=bool(args.assigned_to)
    )
    if error:
        errors.append(error)
    story_value, story_label, error = match_named_option(
        "相关需求", args.story, first_select(form, ["story"]), required=bool(args.story)
    )
    if error:
        errors.append(error)
    if not args.name:
        errors.append("任务名称不能为空")

    requirement_priority = ""
    requirement_title = ""
    if story_value:
        try:
            requirement_priority, requirement_title = story_priority(client, story_value)
        except BASE.ApiError as exc:
            errors.append(str(exc))

    payload: dict[str, str | list[str]] = {
        "status": "wait",
        "color": "",
        "storyEstimate": "",
        "storyDesc": "",
        "storyPri": requirement_priority,
    }
    put_form_value(form, payload, "module", module_value or selected_value(first_select(form, ["module"])) or "0")
    put_form_value(form, payload, "type", type_value)
    put_form_value(form, payload, "assignedTo", assigned_value)
    put_form_value(form, payload, "story", story_value if args.story else "")
    put_form_value(form, payload, "name", args.name or "")
    put_form_value(form, payload, "desc", args.desc or "")
    put_form_value(form, payload, "estimate", args.estimate or "0")
    put_form_value(form, payload, "estStarted", args.est_started or "")
    put_form_value(form, payload, "deadline", args.deadline or "")
    put_form_value(form, payload, "after", "toTaskList")
    uid = payload.get("uid") or ""
    if not re.fullmatch(r"[A-Za-z0-9]+", uid):
        uid = extract_uid(page)
    if uid:
        payload["uid"] = uid
    task_priority = str(args.pri or requirement_priority or "").strip()
    if task_priority:
        put_form_value(form, payload, "pri", task_priority)

    draft = {
        "所属迭代": {"id": execution["id"], "name": execution["name"], "input": args.execution},
        "任务类型": {"value": type_value, "name": type_label, "input": args.type},
        "所属模块": {"value": module_value or payload["module"], "name": module_label, "input": args.module or ""},
        "指派给谁": {"value": assigned_value, "name": assigned_label, "input": args.assigned_to or ""},
        "相关需求": {"value": story_value or payload["story"], "name": story_label, "input": args.story or ""},
        "任务名称": args.name or "",
        "任务描述": args.desc or "",
        "预计开始": args.est_started or "",
        "截止时间": args.deadline or "",
        "预计": args.estimate or "0",
        "P": task_priority,
        "优先级来源": (
            "用户明确指定"
            if args.pri
            else f"相关需求 #{story_value}：{requirement_title or story_label}"
            if requirement_priority
            else "禅道创建页默认值"
        ),
    }
    return {
        "ready": not errors,
        "errors": errors,
        "draft": draft,
        "submitPath": f"task-create-{execution['id']}-0-0.html",
        "submitPaths": list(dict.fromkeys([f"task-create-{execution['id']}-0-0.html", form_path])),
        "payload": payload if not errors else {},
    }


def find_created_task(client: Any, execution_id: str, name: str) -> dict[str, Any]:
    paths = [
        f"execution-task-{execution_id}-all-0-id_desc.json",
        f"project-task-{execution_id}.json",
    ]
    for path in paths:
        try:
            data = client.request(path)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        tasks = BASE.records(data.get("tasks"))
        for task in tasks:
            if task_title(task) == name and str(task.get("deleted") or "0") == "0":
                return task
    return {}


def create_task(args: argparse.Namespace) -> dict[str, Any]:
    _session, client = get_client()
    draft = build_create_draft(args)
    if not draft["ready"]:
        return draft
    if args.dry_run:
        draft["dryRun"] = True
        return draft
    if not args.yes:
        raise BASE.ApiError("创建任务会修改禅道数据。请先把 create-draft 草案给用户确认，确认后再用 create --yes 提交。")

    raw = request_multipart_raw(client, str(draft["submitPath"]), draft["payload"])
    text = raw.decode("utf-8", "replace").strip()
    response: Any
    if text:
        try:
            response = BASE.unwrap(text)
        except Exception:
            response = {"raw": clean_text(text, str(_session.get("server") or ""))[:1000]}
    else:
        response = {"raw": ""}
    locate = response.get("locate") if isinstance(response, dict) else ""
    task_id = ""
    match = re.search(r"task-view-(\d+)", str(locate))
    if match:
        task_id = match.group(1)
    created = find_created_task(client, draft["draft"]["所属迭代"]["id"], draft["draft"]["任务名称"])
    if not task_id and created:
        task_id = str(created.get("id") or "")
    return {
        "created": bool(task_id or created),
        "taskID": task_id,
        "draft": draft["draft"],
        "task": created,
        "response": response,
    }


def print_create_draft(payload: dict[str, Any]) -> None:
    if not payload.get("ready"):
        print("# 创建任务草案未就绪\n")
        for error in payload.get("errors", []):
            print(f"- {error}")
        return
    print("# 创建任务待确认\n")
    for key, value in payload["draft"].items():
        if isinstance(value, dict):
            shown = value.get("name") or value.get("value") or ""
            if value.get("id"):
                shown = f"{shown} ({value['id']})"
            print(f"- {key}: {shown}")
        else:
            print(f"- {key}: {value}")
    print("\n确认后再执行 `create --yes`。")


def dump_json(payload: Any) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def print_task_table(payload: dict[str, Any]) -> None:
    print(f"# 禅道任务截止倒计时 / {payload['today']}\n")
    columns = payload["columns"]
    print("| " + " | ".join(columns) + " |")
    print("| " + " | ".join("---:" if col in {"P", "预计", "消耗", "剩余", "剩余天数"} else "---" for col in columns) + " |")
    for task in payload["tasks"]:
        remaining = task["剩余天数"]
        remaining_text = "" if remaining is None else str(remaining)
        values = [remaining_text if col == "剩余天数" else str(task.get(col) or "") for col in columns]
        print("| " + " | ".join(values) + " |")


def output(args: argparse.Namespace, payload: Any, md_printer) -> None:
    if args.format == "json":
        dump_json(payload)
    else:
        md_printer(payload)


def cmd_status(args: argparse.Namespace) -> int:
    session = BASE.load_session()
    logged_in = bool(session.get("zentaosid") and session.get("server"))
    payload = {
        "loggedIn": logged_in,
        "server": session.get("server") or BASE.DEFAULT_SERVER,
        "account": session.get("account") or "",
        "cli": VERSION,
    }
    output(args, payload, lambda p: print(json.dumps(p, ensure_ascii=False, indent=2)))
    return 0 if logged_in else 2


def cmd_list(args: argparse.Namespace) -> int:
    payload = list_tasks(args)
    output(args, payload, print_task_table)
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    payload = close_task(args)
    output(
        args,
        payload,
        lambda p: print_finish_draft(p)
        if p.get("dryRun") and p.get("plan")
        else print(
            f"任务 {p['taskID']} {p.get('action') or '检查完成'}: "
            f"{p.get('message') or p.get('title', '')}"
        ),
    )
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    payload = complete_task_command(args)
    output(
        args,
        payload,
        lambda p: print_finish_draft(p)
        if p.get("dryRun") and p.get("plan")
        else print(f"任务 {p['taskID']} 已完成: {p.get('title', '')}"),
    )
    return 0


def print_finish_draft(payload: dict[str, Any]) -> None:
    plan = payload.get("plan") or {}
    print("# 任务完成确认\n")
    print(f"- 任务: {plan.get('taskID')} {plan.get('title', '')}")
    print(f"- 当前状态: {plan.get('status', '')}")
    print(f"- 创建人: {plan.get('openedBy', '')}")
    print(f"- 当前账号: {plan.get('account', '')}")
    print(f"- 之前消耗: {plan.get('consumedBefore', '0')} 工时")
    print(f"- 本次完成工时: {plan.get('currentConsumed', '')} 工时")
    print(f"- 完成后总消耗: {plan.get('consumedAfter', '')} 工时")
    if plan.get("nextAction") == "close":
        print("- 完成后动作: 继续关闭任务")
    else:
        print("- 完成后动作: 仅完成任务，不关闭（任务由其他账号创建）")
    print("\n确认后再执行带 `--yes` 的命令。")


def add_create_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--execution", help="所属迭代名称或 ID")
    parser.add_argument("--type", required=True, help="任务类型，必须匹配创建页现有类型")
    parser.add_argument("--module", help="所属模块，必须匹配创建页现有模块")
    parser.add_argument("--assigned-to", help="指派给谁，必须匹配创建页现有用户")
    parser.add_argument("--story", help="相关需求，必须匹配创建页现有需求")
    parser.add_argument("--name", required=True, help="任务名称")
    parser.add_argument("--desc", default="", help="任务描述")
    parser.add_argument("--estimate", default="0", help="预计工时")
    parser.add_argument("--est-started", help="预计开始，YYYY-MM-DD")
    parser.add_argument("--deadline", help="截止时间，YYYY-MM-DD")
    parser.add_argument("--pri", choices=["1", "2", "3", "4"], help="优先级 P")


def cmd_create_draft(args: argparse.Namespace) -> int:
    payload = build_create_draft(args)
    output(args, payload, print_create_draft)
    return 0 if payload.get("ready") else 1


def cmd_create(args: argparse.Namespace) -> int:
    payload = create_task(args)
    output(args, payload, lambda p: print(f"任务已创建: {p.get('taskID') or '未知 ID'}"))
    return 0 if payload.get("created") or payload.get("dryRun") else 1


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--format", choices=["md", "json"], default="md")
    parser = argparse.ArgumentParser(prog="zentao-task-manager")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", parents=[common], help="check ZenTao login session")
    p_status.set_defaults(func=cmd_status)

    p_list = sub.add_parser("list", parents=[common], help="list assigned tasks with deadline countdown")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--page", type=int, default=1)
    p_list.add_argument("--today", help="override today as YYYY-MM-DD")
    p_list.set_defaults(func=cmd_list)

    p_close = sub.add_parser("close", parents=[common], help="close a ZenTao task by ID")
    p_close.add_argument("task_id")
    p_close.add_argument("--comment")
    p_close.add_argument("--consumed", help="完成任务时的本次消耗工时，默认使用预计工时")
    p_close.add_argument("--dry-run", action="store_true")
    p_close.add_argument("--yes", action="store_true", help="确认完成/关闭流程")
    p_close.set_defaults(func=cmd_close)

    p_finish = sub.add_parser("finish", parents=[common], help="complete a ZenTao task by ID")
    p_finish.add_argument("task_id")
    p_finish.add_argument("--comment")
    p_finish.add_argument("--consumed", help="本次消耗工时，默认使用预计工时")
    p_finish.add_argument("--dry-run", action="store_true")
    p_finish.add_argument("--yes", action="store_true", help="确认完成任务")
    p_finish.set_defaults(func=cmd_finish)

    p_draft = sub.add_parser("create-draft", parents=[common], help="prepare a task creation draft from existing options")
    add_create_args(p_draft)
    p_draft.set_defaults(func=cmd_create_draft)

    p_create = sub.add_parser("create", parents=[common], help="create a task after user confirmation")
    add_create_args(p_create)
    p_create.add_argument("--dry-run", action="store_true")
    p_create.add_argument("--yes", action="store_true", help="confirm the state-changing create operation")
    p_create.set_defaults(func=cmd_create)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except BASE.AuthError as exc:
        if str(exc) and str(exc) != "未登录":
            print(f"Error: {exc}", file=sys.stderr)
        BASE.print_not_logged_in()
        return 2
    except (BASE.ApiError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
