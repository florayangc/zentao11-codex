# ZenTao 11.3 endpoints

Default server: `https://zentao.ihr360.com/zentao`

Auth:

1. `GET api-getsessionid.json` → `sessionName`, `sessionID`
2. `POST user-login.json?zentaosid=SID` with `account`, `password=md5(plain)`, `keepLogin=on`
3. Later requests send cookie `zentaosid=SID`

JSON envelope:

```json
{"status":"success","data":"{...inner json string...}","md5":"..."}
```

Failed login:

```json
{"status":"failed","reason":"登录失败，请检查您的用户名或密码是否填写正确。"}
```

Expired session returns HTML `self.location='/zentao/user-login-....json'`.

PATH_INFO examples:

| Command | Path |
| --- | --- |
| my home | `my.json` |
| my bugs | `my-bug-assignedTo-id_desc-0-50-1.json` |
| my stories | `my-story-assignedTo-id_desc-0-50-1.json` |
| my tasks | `my-task-assignedTo-id_desc-0-50-1.json` |
| bug view | `bug-view-{id}.json` |
| product bugs | `bug-browse-{productId}-0-unclosed-0-id_desc-0-50-1.json` |
| create bug | `POST bug-create-{productId}-0-0.json`; use `multipart/form-data` with repeated `files[]` parts when uploading attachments |
| editor image upload | `POST file-ajaxUpload-{uid}.json?objectType=bug&objectID={bugId}&extra=editor` with multipart field `imgFile`; put the returned URL in `steps` or an action comment so the image is marked `editor` instead of a visible attachment |
| resolve bug | `POST bug-resolve-{bugId}.json` |
| close bug | `POST bug-close-{bugId}.json` |
| story view | `story-view-{id}.json` |
| task view | `task-view-{id}.json` |
| product list | `product-index.json` |
| product view | `product-view-{id}.json` |
| project/sprint view | `project-view-{id}.json` |
| project tasks | `project-task-{id}.json` |

In 11.3, 迭代 is the `project` module (no execution module).

List `type` values: `assignedTo`, `openedBy`, `resolvedBy`, `closedBy`, `finishedBy`.
