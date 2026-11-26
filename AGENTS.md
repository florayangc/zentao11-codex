# zentao11 Installation Package

Install from this directory with the platform installer:

- Windows: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1`
- macOS: `chmod +x ./install.sh && ./install.sh`

Verify that the installer reports CLI version `1.1.0`. Keep each user's session in `~/.config/zentao11`; never copy credentials or session files between users. After installation, tell the user to open a new Codex task or restart Codex, then run `zentao11 login -u THEIR_ACCOUNT` in their own terminal.
