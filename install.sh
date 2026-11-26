#!/bin/sh
set -eu

skip_path=0
if [ "${1:-}" = "--skip-path" ]; then
  skip_path=1
elif [ "$#" -gt 0 ]; then
  echo "Usage: ./install.sh [--skip-path]" >&2
  exit 2
fi

package_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
codex_home=${CODEX_HOME:-"$HOME/.codex"}
skills_dir="$codex_home/skills"
bin_dir="$codex_home/bin"
timestamp=$(date +%Y%m%d-%H%M%S)

command -v python3 >/dev/null 2>&1 || {
  echo "Python 3 was not found. Install Python 3 and try again." >&2
  exit 1
}

mkdir -p "$skills_dir" "$bin_dir"
for skill_name in zentao11 zentao-task-manager; do
  source_skill="$package_root/$skill_name"
  destination="$skills_dir/$skill_name"
  [ -f "$source_skill/SKILL.md" ] || {
    echo "Invalid package: $skill_name/SKILL.md was not found." >&2
    exit 1
  }
  if [ -e "$destination" ]; then
    backup_dir="$codex_home/backups"
    mkdir -p "$backup_dir"
    backup="$backup_dir/$skill_name-$timestamp"
    mv "$destination" "$backup"
    echo "Existing skill backed up to: $backup"
  fi
  cp -R "$source_skill" "$destination"
done

cp "$package_root/zentao11/scripts/zentao11" "$bin_dir/zentao11"
cp "$package_root/zentao-task-manager/scripts/zentao-task-manager" "$bin_dir/zentao-task-manager"
chmod +x "$bin_dir/zentao11" "$bin_dir/zentao-task-manager"

if [ "$skip_path" -eq 0 ]; then
  case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *)
      case "${SHELL:-}" in
        */zsh) profile="$HOME/.zprofile" ;;
        *) profile="$HOME/.profile" ;;
      esac
      path_line="export PATH=\"$bin_dir:\$PATH\""
      if [ ! -f "$profile" ] || ! grep -F -- "$path_line" "$profile" >/dev/null 2>&1; then
        printf '\n# zentao11 Codex CLI\n%s\n' "$path_line" >> "$profile"
      fi
      export PATH="$bin_dir:$PATH"
      echo "Added $bin_dir to PATH in $profile"
      ;;
  esac
fi

python3 -X utf8 -B "$skills_dir/zentao11/scripts/zentao11.py" version --format json
status=0
python3 -X utf8 -B "$skills_dir/zentao-task-manager/scripts/zentao_task_manager.py" status --format json || status=$?
if [ "$status" -ne 0 ] && [ "$status" -ne 2 ]; then
  echo "zentao-task-manager verification failed with exit code $status." >&2
  exit "$status"
fi

echo
echo "Installed Codex skills: zentao11, zentao-task-manager"
echo "Installed CLI command: $bin_dir/zentao11"
echo "Installed CLI command: $bin_dir/zentao-task-manager"
echo "Open a new Codex task (or restart Codex) to refresh skill discovery."
echo "Then run: zentao11 login -u YOUR_ACCOUNT"
