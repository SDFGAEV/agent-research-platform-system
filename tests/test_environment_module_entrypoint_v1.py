from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_module_entrypoint_exposes_product_cli() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "noetrium", "project", "--help"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0
    assert "create" in completed.stdout
    assert "doctor" in completed.stdout
    assert "test" in completed.stdout
    assert completed.stderr == ""
