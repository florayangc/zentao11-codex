# ZenTao 11.3 task endpoints

This skill reuses the session created by the sibling `zentao11` CLI.

Default server:

```text
https://zentao.ihr360.com/zentao
```

Session file:

```text
%USERPROFILE%\.config\zentao11\session.json
```

Relevant PATH_INFO endpoints:

| Operation | Endpoint |
| --- | --- |
| Current user's assigned tasks | `my-task-assignedTo-id_desc-0-{limit}-{page}.json` |
| Task detail | `task-view-{id}.json` |
| Finish task | `task-finish-{id}.html` |
| Close task | `task-close-{id}.json` |
| Execution list | `execution-all.json`, `execution-browse.json`, `project-execution-0.json` |
| Create task form | Prefer `task-create-{executionId}-0-0.html`; fall back to `task-create-{executionId}.html` |
| Create task submit | Same path as the loaded create form |

The finish operation is a POST to the HTML PATH_INFO endpoint. The script posts `currentConsumed`, `consumed`, `assignedTo`, `realStarted`, `finishedDate`, `status=done`, and `comment`. After the request, it fetches `task-view-{id}.json` and treats `status == done` as the success condition.

The close operation is a POST. The script posts a small form body with `comment`. After the request, it fetches `task-view-{id}.json` and treats `status == closed` as the success condition. A task created by another account must never be closed by this workflow; a close request for that task only completes it.

The create operation must parse the create form first and match user-provided task type, module, assignee, and story against existing options. Do not synthesize missing options. Submit only after explicit confirmation.

ZenTao 18 cloud task creation uses an AJAX `multipart/form-data` POST to `task-create-{executionId}-0-0.html`, with `X-Requested-With: XMLHttpRequest`, `after=toTaskList`, and the page `uid` field when present. Do not submit it as `application/x-www-form-urlencoded` when diagnosing cloud create failures.
