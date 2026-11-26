# zentao11 Codex + CLI 安装包

适用于公司禅道 11.3。安装后，Codex 可以自动调用 `$zentao11`，终端可以直接运行 `zentao11`。

## 环境要求

- Windows 10/11
- Python 3.9 或更高版本
- 能访问 `https://zentao.ihr360.com/zentao`

## 使用 Codex 安装

将压缩包解压到工作区，然后告诉 Codex：

```text
安装这个 zentao11 包
```

包内的 `AGENTS.md` 会引导 Codex 执行安装器。安装完成后，新建一个 Codex 任务或重启 Codex，让 skill 列表刷新。

## 手动安装

在解压目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

安装器会：

1. 将 skill 安装到 `$CODEX_HOME\skills\zentao11`，未配置 `CODEX_HOME` 时使用 `%USERPROFILE%\.codex\skills\zentao11`。
2. 在 Codex 的 `bin` 目录创建 `zentao11.cmd`。
3. 将该 `bin` 目录加入当前用户 PATH。已有版本会备份到 Codex 的 `backups` 目录。

不希望修改用户 PATH 时，使用：

```powershell
.\install.ps1 -SkipPath
```

## 登录

安装后由每位用户在自己的终端登录：

```powershell
zentao11 login -u 账号
zentao11 status
```

会话保存在 `%USERPROFILE%\.config\zentao11\session.json`。分发包不包含任何账号、密码或登录会话。

## 每日钉钉提醒

登录后只需初始化一次：

```powershell
py -3 -X utf8 -B zentao11\scripts\zentao_remind.py setup
```

`setup` 会询问禅道地址、账号、密码、钉钉 Webhook 和可选加签密钥，只保存在 `%USERPROFILE%\.config\zentao11`，创建每天 10:00 执行的 `ZentaoDingTalkRemind` 任务，并立即发送一条消息。提醒范围是当前登录人被指派且未完成的任务和 Bug；任务的“已完成”、“已关闭”和“已取消”不再提醒，Bug 处于 `resolved` 仍会提醒，直到关闭。

个人禅道每条命令前设置：

```powershell
$env:ZENTAO11_CONFIG_DIR="$env:USERPROFILE\.config\zentao11-private"
```

## 任务完成与关闭规则

用户提出“关闭任务”时，先检查任务状态和创建人，并展示完成确认草案；未完成任务默认按预计工时完成，用户确认后才执行。当前账号创建且已完成的任务才会关闭。别人创建并指派给当前账号的任务不能关闭，此类请求只完成任务，不执行关闭。

创建任务时如果关联已有禅道需求，任务优先级默认继承需求优先级；用户明确指定任务优先级时，以用户指定值为准。
