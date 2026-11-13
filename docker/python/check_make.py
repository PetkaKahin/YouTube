"""Standalone Make regression check; requires GNU Make, invokes no real Docker commands."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase


def main() -> None:
    check = TestCase()
    make = shutil.which("make")
    if make is None:
        raise SystemExit("GNU Make is required for this check.")
    makefile = Path(__file__).resolve().parents[2] / "Makefile"
    with TemporaryDirectory() as work:
        directory = Path(work)
        capture = directory / "capture"
        result_file = directory / "arguments.json"
        marker = directory / "must-not-exist"
        capture.write_text(
            "#!" + sys.executable + "\nimport json, os, sys\nfrom pathlib import Path\n"
            'Path(os.environ["CAPTURE_FILE"]).write_text(json.dumps(sys.argv[1:]))\n'
        )
        capture.chmod(0o755)
        environment = {
            **os.environ,
            "CAPTURE_FILE": str(result_file),
            "EMAIL": "user@example.com",
            "ROLE": "future-role",
        }

        def invoke(goals: list[str], changes: dict[str, str] | None = None):
            result_file.unlink(missing_ok=True)
            return subprocess.run(  # noqa: S603 -- controlled Makefile and generated test arguments
                [make, "-f", str(makefile), *goals, "DOCKER_RUN=" + str(capture)],
                env={**environment, **(changes or {})},
                capture_output=True,
                text=True,
                check=False,
            )

        emails = [
            "user@example.com",
            "cash$money@example.com",
            "$(shell touch " + str(marker) + ")@example.com",
            "a'\"; touch " + str(marker) + "; #@example.com",
        ]
        for email in emails:
            for action in ("get", "set"):
                result = invoke(["role", action, "EMAIL=" + email])
                check.assertEqual(result.returncode, 0, result.stderr)
                expected = [
                    "dev",
                    "exec",
                    "-T",
                    "python",
                    "python",
                    "-m",
                    "app.cli",
                    "role",
                    action,
                    "--",
                    email,
                ]
                if action == "set":
                    expected.append("future-role")
                check.assertEqual(json.loads(result_file.read_text()), expected)
                check.assertFalse(marker.exists())

        for variable in ("EMAIL", "ROLE"):
            payload = "$(shell touch " + str(marker) + ")"
            result = invoke(["role", "set", variable + "=" + payload])
            check.assertEqual(result.returncode, 0, result.stderr)
            check.assertIn(payload, json.loads(result_file.read_text()))
            check.assertFalse(marker.exists())

        result = invoke(["role", "get", "ROLE_ACTION=set"])
        check.assertEqual(result.returncode, 0, result.stderr)
        check.assertEqual(json.loads(result_file.read_text())[8], "get")
        for goals in (
            ["role"],
            ["role", "unknown"],
            ["role", "get", "set"],
            ["test", "role", "get"],
            ["get"],
            ["set"],
            ["role-admin"],
        ):
            result = invoke(goals)
            check.assertNotEqual(result.returncode, 0, goals)
            check.assertFalse(result_file.exists(), goals)
        for action, missing in (("get", "EMAIL"), ("set", "EMAIL"), ("set", "ROLE")):
            result = invoke(["role", action], {missing: ""})
            check.assertNotEqual(result.returncode, 0)
            check.assertFalse(result_file.exists())
        result = invoke(["test"])
        check.assertEqual(result.returncode, 0, result.stderr)
        check.assertEqual(
            json.loads(result_file.read_text()), ["dev", "exec", "-T", "python", "pytest"]
        )
    print("Make get/set, validation, literal arguments and injection checks passed.")


if __name__ == "__main__":
    main()
