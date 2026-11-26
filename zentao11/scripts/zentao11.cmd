@echo off
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
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
python "%ZENTAO11_CODEX_HOME%\skills\zentao11\scripts\zentao11.py" %*
