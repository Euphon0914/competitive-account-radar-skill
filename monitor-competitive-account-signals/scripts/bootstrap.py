"""Create an isolated runtime for a monitoring project."""
from __future__ import annotations
import argparse
import subprocess
import sys
import venv
from pathlib import Path
from typing import Callable, Sequence

def interpreter_for(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

def bootstrap(project: Path, runner: Callable[[Sequence[str]], object] | None = None) -> Path:
    venv_dir = Path(project) / ".competitive-radar" / "venv"
    interpreter = interpreter_for(venv_dir)
    marker = venv_dir.parent / ".bootstrap-managed"
    if not interpreter.exists():
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        marker.write_text("competitive-account-radar\n", encoding="utf-8")
    else:
        resolved_root = venv_dir.resolve()
        resolved_interpreter = interpreter.resolve()
        if not marker.is_file() or not (venv_dir / "pyvenv.cfg").is_file() or not resolved_interpreter.is_relative_to(resolved_root):
            raise RuntimeError("refusing an unmanaged or externally linked monitoring virtual environment")
    requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
    (runner or (lambda args: subprocess.run(args, check=True)))([str(interpreter), "-m", "pip", "install", "--requirement", str(requirements)])
    return interpreter

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the isolated Competitive Account Radar runtime")
    parser.add_argument("--project", type=Path, required=True)
    print(bootstrap(parser.parse_args(argv).project))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
