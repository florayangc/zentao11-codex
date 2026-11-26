from __future__ import annotations

import importlib.util
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REMINDER_SCRIPT = ROOT / "zentao11" / "scripts" / "zentao_remind.py"


def load_reminder():
    spec = importlib.util.spec_from_file_location("zentao_remind_test", REMINDER_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {REMINDER_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlatformSupportTests(unittest.TestCase):
    def test_launchd_plist_has_daily_schedule_and_config(self):
        reminder = load_reminder()
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            reminder.CONFIG_DIR = root / "config"
            plist_path = reminder._write_launchd_plist(root / "LaunchAgents")
            payload = plistlib.loads(plist_path.read_bytes())

        self.assertEqual(payload["Label"], "com.ihr360.zentao11.remind")
        self.assertEqual(payload["StartCalendarInterval"], {"Hour": 10, "Minute": 0})
        self.assertEqual(payload["ProgramArguments"][-1], "send")
        self.assertEqual(
            payload["EnvironmentVariables"]["ZENTAO11_CONFIG_DIR"],
            str(reminder.CONFIG_DIR),
        )

    def test_keychain_adapter_uses_security_command(self):
        reminder = load_reminder()

        class Result:
            returncode = 0
            stdout = b"secret-value\n"
            stderr = b""

        with patch.object(reminder.sys, "platform", "darwin"), patch.object(
            reminder.subprocess, "run", return_value=Result()
        ) as run:
            reminder._keychain_store("server|account", "password")
            self.assertIn("add-generic-password", run.call_args.args[0])
            self.assertEqual(reminder._keychain_read("server|account"), "secret-value")
            self.assertIn("find-generic-password", run.call_args.args[0])

    def test_posix_launchers_use_lf_shebangs(self):
        paths = [
            ROOT / "install.sh",
            ROOT / "zentao11" / "scripts" / "zentao11",
            ROOT / "zentao-task-manager" / "scripts" / "zentao-task-manager",
        ]
        for path in paths:
            content = path.read_bytes()
            self.assertTrue(content.startswith(b"#!/bin/sh\n"), path)
            self.assertNotIn(b"\r\n", content, path)


if __name__ == "__main__":
    unittest.main()
