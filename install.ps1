[CmdletBinding()]
param(
    [switch]$SkipPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$packageRoot = $PSScriptRoot
$skillNames = @('zentao11', 'zentao-task-manager')
foreach ($skillName in $skillNames) {
    $sourceSkill = Join-Path $packageRoot $skillName
    if (-not (Test-Path -LiteralPath (Join-Path $sourceSkill 'SKILL.md'))) {
        throw "Invalid package: $skillName/SKILL.md was not found."
    }
}

$codexHome = if ($env:CODEX_HOME) {
    [System.IO.Path]::GetFullPath($env:CODEX_HOME)
} else {
    Join-Path $HOME '.codex'
}

$skillsDir = Join-Path $codexHome 'skills'
New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null

foreach ($skillName in $skillNames) {
    $sourceSkill = Join-Path $packageRoot $skillName
    $destination = Join-Path $skillsDir $skillName
    if (Test-Path -LiteralPath $destination) {
        $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backupDir = Join-Path $codexHome 'backups'
        New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
        $backup = Join-Path $backupDir "$skillName-$timestamp"
        Move-Item -LiteralPath $destination -Destination $backup
        Write-Host "Existing skill backed up to: $backup"
    }
    Copy-Item -LiteralPath $sourceSkill -Destination $destination -Recurse -Force
}

$binDir = Join-Path $codexHome 'bin'
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$shimPath = Join-Path $binDir 'zentao11.cmd'
$shim = @'
@echo off
setlocal
if defined CODEX_HOME (
  set "ZENTAO11_CODEX_HOME=%CODEX_HOME%"
) else (
  set "ZENTAO11_CODEX_HOME=%USERPROFILE%\.codex"
)
where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%ZENTAO11_CODEX_HOME%\skills\zentao11\scripts\zentao11.py" %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if not errorlevel 1 (
  python "%ZENTAO11_CODEX_HOME%\skills\zentao11\scripts\zentao11.py" %*
  exit /b %ERRORLEVEL%
)
echo Python 3 was not found. Install Python 3 and try again. 1>&2
exit /b 1
'@
Set-Content -LiteralPath $shimPath -Value $shim -Encoding ASCII

$taskShimPath = Join-Path $binDir 'zentao-task-manager.cmd'
$taskShim = @'
@echo off
setlocal
if defined CODEX_HOME (
  set "ZENTAO11_CODEX_HOME=%CODEX_HOME%"
) else (
  set "ZENTAO11_CODEX_HOME=%USERPROFILE%\.codex"
)
where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%ZENTAO11_CODEX_HOME%\skills\zentao-task-manager\scripts\zentao_task_manager.py" %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if not errorlevel 1 (
  python "%ZENTAO11_CODEX_HOME%\skills\zentao-task-manager\scripts\zentao_task_manager.py" %*
  exit /b %ERRORLEVEL%
)
echo Python 3 was not found. Install Python 3 and try again. 1>&2
exit /b 1
'@
Set-Content -LiteralPath $taskShimPath -Value $taskShim -Encoding ASCII

if (-not $SkipPath) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $pathParts = @($userPath -split ';' | Where-Object { $_ })
    $alreadyPresent = $pathParts | Where-Object {
        $_.TrimEnd('\') -ieq $binDir.TrimEnd('\')
    }
    if (-not $alreadyPresent) {
        $newUserPath = (@($pathParts) + $binDir) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $newUserPath, 'User')
    }
    if (($env:Path -split ';') -notcontains $binDir) {
        $env:Path = "$binDir;$env:Path"
    }
}

$cliScript = Join-Path (Join-Path $skillsDir 'zentao11') 'scripts\zentao11.py'
$taskScript = Join-Path (Join-Path $skillsDir 'zentao-task-manager') 'scripts\zentao_task_manager.py'
$python = Get-Command python -ErrorAction SilentlyContinue
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    & $pyLauncher.Source -3 $cliScript version --format json
} elseif ($python) {
    & $python.Source $cliScript version --format json
} else {
    throw 'Python 3 was not found. Install Python 3, then run this installer again.'
}
if ($LASTEXITCODE -ne 0) {
    throw "zentao11 verification failed with exit code $LASTEXITCODE."
}
if ($pyLauncher) {
    & $pyLauncher.Source -3 $taskScript status --format json
} elseif ($python) {
    & $python.Source $taskScript status --format json
} else {
    throw 'Python 3 was not found. Install Python 3, then run this installer again.'
}
if ($LASTEXITCODE -notin @(0, 2)) {
    throw "zentao-task-manager verification failed with exit code $LASTEXITCODE."
}

Write-Host ''
Write-Host "Installed Codex skills: $($skillNames -join ', ')"
Write-Host "Installed CLI command: $shimPath"
Write-Host "Installed CLI command: $taskShimPath"
Write-Host 'Open a new Codex task (or restart Codex) to refresh skill discovery.'
Write-Host 'Then run: zentao11 login -u YOUR_ACCOUNT'
