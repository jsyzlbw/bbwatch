"""Run offline tests in an owned temporary directory and remove their files afterward."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    print(f"Test VM: {platform.platform()}; Python {platform.python_version()}", flush=True)
    with tempfile.TemporaryDirectory(prefix="bbwatch-ci-tests-") as directory:
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-q",
            "-ra",
            "--basetemp",
            str(Path(directory) / "pytest"),
        ]
        report = os.environ.get("BBWATCH_TEST_REPORT")
        if report:
            command.extend(["--junitxml", report])
        try:
            result = subprocess.run(command, timeout=300, check=False)
        finally:
            print(
                "Test processes finished; removing temporary downloads and databases.", flush=True
            )
    print("Temporary test data removed.", flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
