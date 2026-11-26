#!/usr/bin/env pwsh
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }
$scriptPath = [System.IO.Path]::Combine($codexHome, 'skills', 'zentao11', 'scripts', 'zentao11.py')
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    & $pyLauncher.Source -3 $scriptPath @args
} else {
    python $scriptPath @args
}
exit $LASTEXITCODE
