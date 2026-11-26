# zentao11 Installation Package

When the user asks to install this package on Windows, run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1` from this directory. Verify that the installer reports CLI version `1.0.0`.

The installer copies only the bundled skill. Keep each user's session in `%USERPROFILE%\.config\zentao11`; never copy a session file between users. After installation, tell the user to open a new Codex task or restart Codex, then run `zentao11 login -u THEIR_ACCOUNT` in their own terminal.

