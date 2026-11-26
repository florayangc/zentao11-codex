#!/usr/bin/env pwsh
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    & $pyLauncher.Source -3 (Join-Path $codexHome 'skills\zentao11\scripts\zentao11.py') @args
} else {
    python (Join-Path $codexHome 'skills\zentao11\scripts\zentao11.py') @args
}
exit $LASTEXITCODE
