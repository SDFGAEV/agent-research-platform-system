from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from noetrium_platform.infrastructure.lifecycle.python.api import EnvironmentCommandResult
from noetrium_platform.product.operator.maintenance.runtime.management_cli import _require_command_success, main


class ManagementCliTests(unittest.TestCase):
    def test_nonzero_managed_environment_command_is_not_reported_as_ok(self):
        result = EnvironmentCommandResult(("python", "-m", "pip", "check"), 1, "", "missing dependency")
        with self.assertRaisesRegex(RuntimeError, "missing dependency"):
            _require_command_success(result)

    def test_directory_and_model_registry_commands(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            keys = (
                "releases", "runtime", "state", "logs", "model_artifacts",
                "python_environments", "cache", "temp", "locks", "workspaces",
            )
            config = root / "management.json"
            config.write_text(json.dumps({"directories": {key: str(root / key) for key in keys}}), encoding="utf-8")
            self.assertEqual(main(["--config", str(config), "dirs", "init"]), 0)
            model = root / "weights"
            model.mkdir()
            self.assertEqual(main(["--config", str(config), "model", "add", "m1", str(model)]), 0)
            self.assertEqual(main(["--config", str(config), "model", "list"]), 0)
            self.assertEqual(main(["--config", str(config), "model", "inspect", "m1"]), 0)
            self.assertEqual(main(["--config", str(config), "dirs", "entries", "model_artifacts"]), 0)
            self.assertEqual(main(["--config", str(config), "summary"]), 0)
            self.assertEqual(main(["--config", str(config), "controller", "status"]), 0)
            self.assertEqual(main(["--config", str(config), "controller", "run", "--interval-seconds", "0.01", "--max-cycles", "1"]), 0)


if __name__ == "__main__":
    unittest.main()
