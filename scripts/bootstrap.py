"""Create the persistent plugin venv and install the local bbwatch package."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT / "src"))

from bbwatch.platform import venv_executable, venv_python


def main() -> int:
    data = Path(os.environ["CLAUDE_PLUGIN_DATA"]).expanduser()
    data.mkdir(parents=True, exist_ok=True)
    venv = data / ".venv"
    python = venv_python(venv)
    if not python.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)

    pip = venv_executable(venv, "pip")
    subprocess.run(
        [str(pip), "install", "-q", "--upgrade", "pip"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run([str(pip), "install", "-q", str(ROOT)], check=True)
    subprocess.run(
        [str(pip), "install", "-q", "--no-deps", "--force-reinstall", str(ROOT)],
        check=True,
    )

    username = os.environ.get("BBWATCH_USERNAME")
    password = os.environ.get("BBWATCH_PASSWORD")
    if username and password:
        subprocess.run(
            [str(venv_executable(venv, "bbwatch")), "setup", "--stdin"],
            input=f"{username}\n{password}\n",
            text=True,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    print(f"bbwatch runtime ready: {venv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
