---
name: zentao-task-manager
description: "Manage iHR360 ZenTao tasks through the bundled web-session CLI. Use when the user asks to list assigned ZenTao tasks with deadline countdowns, close a task by ID, create a task by matching existing ZenTao options, or read a Wiki task-splitting table and prepare ZenTao task drafts without submitting them."
---

# ZenTao Task Manager

## Overview

Use the bundled script to query the current user's assigned tasks, close a task by ID, and create a task after matching existing ZenTao options. This skill reuses the `zentao11` session file at `~/.config/zentao11/session.json`; never ask the user to paste a password into chat.

## Prerequisites

Before querying or closing tasks, check login status:

```shell
zentao-task-manager status
```

If not logged in, stop and tell the user to run the original login command locally:

```shell
zentao11 login -u USER_ACCOUNT
```

Do not use official `zentao-cli` or REST API 2.0. The company instance is ZenTao 11.3 and this skill uses PATH_INFO `.json` endpoints.

公司常用检查命令：

```shell
zentao11 login -u 账号
zentao11 status
zentao11 task --list --type assignedTo --format json --limit 200
zentao11 bug --list --type assignedTo --format json --limit 200
```

个人禅道每条命令前设置：

Windows PowerShell 使用 `$env:ZENTAO11_CONFIG_DIR="$HOME\.config\zentao11-private"`；macOS shell 使用 `export ZENTAO11_CONFIG_DIR="$HOME/.config/zentao11-private"`。

## List Tasks

When the user asks for current tasks or task deadline countdowns, run:

```shell
zentao-task-manager list --format json
```

Return a concise Chinese summary using these columns exactly: `ID`, `P`, `所属迭代`, `任务名称`, `创建`, `指派`, `由谁完成`, `预计`, `消耗`, `剩余`, `截止`, `状态`, `剩余天数`. Interpret negative `剩余天数` as overdue, zero as due today, and positive values as days remaining.

Useful options:

- `--limit 50`: maximum tasks to request.
- `--today YYYY-MM-DD`: override today's date only for testing or explicit user-provided date.
- `--format md`: direct Markdown table output.

## Complete And Close Task

When the user asks to close a task, first inspect the task detail. The operation must follow this state machine:

- If the task is not completed, show a completion draft first. The default completion hours are the task's estimated hours. Do not write until the user confirms the draft.
- If the current account created the task and it is already completed, it may be closed.
- If the task was created by another account and assigned to the current account, the current account cannot close it. Treat a request to close it as a request to complete it only; never call the close endpoint.
- If the current account created a task that is not completed, complete it first and then close it.

The draft is the confirmation popup in the CLI workflow:

```shell
zentao-task-manager close TASK_ID --dry-run --format json
```

After the user explicitly confirms the displayed completion hours and next action, submit:

```shell
zentao-task-manager close TASK_ID --yes --format json
```

Override the default completion hours only when the user gives a different value:

```shell
zentao-task-manager close TASK_ID --consumed 3 --yes --format json
```

To complete without a close request, use:

```shell
zentao-task-manager finish TASK_ID --dry-run --format json
zentao-task-manager finish TASK_ID --yes --format json
```

## Create Task

Creating a task is a state-changing operation. Always run `create-draft` first and present the draft to the user. Submit with `create --yes` only after the user confirms. User corrections override all inferred values.

Match these fields only against existing ZenTao options: `所属迭代`, `任务类型`, `所属模块`, `指派给谁`, `相关需求`. If any requested option cannot be found or matches multiple candidates, stop and ask the user to clarify; do not create new iterations, types, modules, users, or stories.

任务通常基于禅道已有需求创建。指定 `--story` 后，创建草案会读取该需求的 `pri`，并将任务 P 默认设置为需求优先级；如果用户明确指定 `--pri`，以用户指定的优先级为准。需求没有可用优先级时停止创建，不使用猜测值。

Draft command:

```shell
zentao-task-manager create-draft --execution "迭代名称或ID" --type "任务类型" --module "模块" --assigned-to "用户" --story "需求" --name "任务名称" --desc "任务描述" --estimate 2 --est-started 2026-09-04 --deadline 2026-09-05 --format json
```

Confirmed submit command:

```shell
zentao-task-manager create --execution "迭代名称或ID" --type "任务类型" --module "模块" --assigned-to "用户" --story "需求" --name "任务名称" --desc "任务描述" --estimate 2 --est-started 2026-09-04 --deadline 2026-09-05 --yes --format json
```

Use `--dry-run` with `create` for a non-mutating validation of the final payload.

## Read Wiki Task Splits (Draft Only)

When the user supplies a project/iteration name together with a Wiki URL and asks to import or prepare its tasks, use the browser skill to read the authenticated Wiki page. Do not use the ZenTao CLI `get` command for the Wiki URL: that command is for ZenTao PATH_INFO and does not parse Confluence pages.

A leading `/` selects the Wiki-to-ZenTao simulation workflow. Parse the first token after `/` as the project/iteration name and the URL as the source document; the URL is not a ZenTao command argument.

For the standard研发任务拆分 template used by pages such as `P1768 AI寻聘助手V2.1 任务拆分`:

1. Identify the task table by the headers `职能（前端/后端/测试）`, `任务模块`, `任务名称（必填）`, `需求编号（必填）`, `预计开始`, `预计结束`, `使用AI后工时(d or h都可)（必填）`, `负责人（必填）`, and `备注`. Ignore the schedule table above it and any blank trailing rows.
2. Preserve row order. Forward-fill values represented by merged/blank cells for `职能`, `任务模块`, `需求编号`, and `负责人`; do not forward-fill `任务名称`.
3. Convert each non-empty task-name row into one draft. Normalize `0.5h`, `1h`, and `1d` to ZenTao estimated hours (8 hours per day); keep the original value in the description when conversion is needed.
4. Put the Wiki row's task name in `--name`. Put the function, module, Wiki URL, AI-use description, and remarks in `--desc`. Use the supplied project/iteration name (or the ZenTao project link in the page) as `--execution`, and the row owner as `--assigned-to` after matching them against existing ZenTao options.
5. Resolve the Wiki `需求编号` against existing ZenTao stories by ID, title, or displayed code. Do not assume a value such as `P1768` is a numeric story ID: on many pages it is the project code, while the actual ZenTao story has a different numeric ID. If the value does not match and the selected iteration has exactly one story, it may be resolved to that single story; otherwise ask for clarification.
6. A blank required value, an ambiguous match, or a task row with no task name is a validation error. Report the row number and stop the affected draft; do not invent a task name, user, module, story, or iteration.
7. For the slash form (`/项目名 WikiURL`), simulate the direct-create flow automatically: build the same payload that `create --yes` would submit, but do not send it. Return each item as `simulated: would-create` with the exact matched IDs and fields. Do not ask for a second confirmation in simulation mode.

The Wiki slash flow is intentionally non-mutating for this skill package. It may read ZenTao forms and existing options, but it must not POST `task-create-*`, claim that a task was created, or return task IDs as if they were real. Use `simulated: would-create` and clearly separate validation errors from simulated successes. A future live-import mode must be introduced as a separately named, explicitly confirmed operation; never silently switch the slash simulation to live writes.

## 钉钉提醒

提醒范围是“指派给当前登录人且未关闭”。Bug 的 `resolved` 状态仍属于未关闭；已逾期项目进入全部待办，同时单独提醒截止日前 2 天至今天（含今天）的项目。每天 10:00 发送一条钉钉消息：Windows 使用任务计划程序，macOS 使用 `launchd`。

首次配置可在项目根目录运行：

Windows 运行 `py -3 -X utf8 -B "$HOME\.codex\skills\zentao11\scripts\zentao_remind.py" setup`；macOS 运行 `python3 -X utf8 -B "$HOME/.codex/skills/zentao11/scripts/zentao_remind.py" setup`。

`setup` 会使用 Windows DPAPI 或 macOS Keychain 保存禅道密码，在本机配置目录保存钉钉机器人配置，创建系统定时任务并立即发送一条测试提醒。钉钉自定义机器人只能发群消息；要发给自己，需要建立自己的群并添加机器人。密码、Webhook 和密钥禁止进入对话或仓库。

## Resources

Read `references/task-endpoints.md` only when changing the script or diagnosing an endpoint failure.

The executable script is `scripts/zentao_task_manager.py`.
