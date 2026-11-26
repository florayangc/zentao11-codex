# zentao11 Codex + CLI 安装包

适用于公司禅道 11.3。安装后，Codex 可以调用 `$zentao11` 和 `$zentao-task-manager`，终端可以直接运行 `zentao11` 与 `zentao-task-manager`。

## 环境要求

- Windows 10/11 或 macOS 12 及以上版本
- Python 3.9 或更高版本
- 能访问 `https://zentao.ihr360.com/zentao`

## 安装

将项目下载到本机后，在项目根目录运行对应安装器。

Windows：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

macOS：

```bash
chmod +x ./install.sh
./install.sh
```

安装器会：

1. 将两个 Skill 安装到 `${CODEX_HOME}/skills`，未配置 `CODEX_HOME` 时使用用户目录下的 `.codex/skills`。
2. 在 `${CODEX_HOME}/bin` 创建 `zentao11` 和 `zentao-task-manager` 命令。
3. 将该目录加入当前用户的 `PATH`。已有 Skill 会先备份到 `.codex/backups`。

不希望修改用户 `PATH` 时，Windows 使用 `-SkipPath`，macOS 使用 `--skip-path`。

安装完成后，新建一个 Codex 任务或重启 Codex，让 Skill 列表刷新。

## 登录

每位用户在自己的终端登录：

```bash
zentao11 login -u 账号
zentao11 status
```

会话保存在 `~/.config/zentao11/session.json`。项目不包含任何账号、密码或登录会话。

## 每日钉钉提醒

登录后初始化一次：

```bash
python3 -X utf8 -B ~/.codex/skills/zentao11/scripts/zentao_remind.py setup
```

Windows 没有 `python3` 命令时使用：

```powershell
py -3 -X utf8 -B "$env:USERPROFILE\.codex\skills\zentao11\scripts\zentao_remind.py" setup
```

`setup` 会询问禅道地址、账号、密码、钉钉 Webhook 和可选加签密钥，并创建每天 10:00 执行的提醒：

- Windows 使用 DPAPI 保存禅道密码，并通过任务计划程序运行。
- macOS 使用登录 Keychain 保存禅道密码，并通过 `launchd` 运行。
- Webhook、加签密钥和其他配置保存在 `~/.config/zentao11`，文件权限仅允许当前用户读取。

提醒范围是当前账号被指派且未完成的任务和 Bug。任务的“已完成”“已关闭”和“已取消”不再提醒；Bug 处于 `resolved` 时仍会提醒，直到关闭。

个人禅道可以通过 `ZENTAO11_CONFIG_DIR` 使用独立配置目录：

```bash
export ZENTAO11_CONFIG_DIR="$HOME/.config/zentao11-private"
```

PowerShell 对应写法：

```powershell
$env:ZENTAO11_CONFIG_DIR="$HOME\.config\zentao11-private"
```

## 任务完成与关闭规则

用户提出“关闭任务”时，先检查任务状态和创建人，并展示完成确认草案；未完成任务默认按预计工时完成，用户确认后才执行。当前账号创建且已完成的任务才会关闭。别人创建并指派给当前账号的任务不能关闭，此类请求只完成任务，不执行关闭。

创建任务时如果关联已有禅道需求，任务优先级默认继承需求优先级；用户明确指定任务优先级时，以用户指定值为准。
