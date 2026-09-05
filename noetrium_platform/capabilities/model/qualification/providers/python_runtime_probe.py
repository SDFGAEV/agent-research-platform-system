"""Read-only framework, CUDA and model-config probes through runtime/python."""

from __future__ import annotations

from pathlib import Path

from noetrium_platform.infrastructure.lifecycle.python.api import (
    EnvironmentCommandResult,
    PythonEnvironmentExecutionPort,
)
from noetrium_platform.capabilities.model.qualification.api import (
    QualificationRuntimeProbePort,
    RuntimeCheckReceipt,
)
from noetrium_platform.foundation.kernel.kernel import canonical_digest


_MAX_RUNTIME_OUTPUT_PREVIEW = 4096


_BACKEND_IMPORT = """
import importlib, importlib.metadata, json, sys
backend = sys.argv[1]
modules = {"vllm": ("vllm",), "sglang": ("sglang", "sgl_kernel")}[backend]
for module in modules:
    importlib.import_module(module)
versions = {}
for package in modules:
    try:
        versions[package] = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        versions[package] = None
print(json.dumps({"backend": backend, "versions": versions}, sort_keys=True))
""".strip()

_CUDA_RUNTIME = """
import json, sys, torch
if not torch.cuda.is_available():
    raise SystemExit("torch.cuda.is_available() is false")
rows = ["%d.%d" % torch.cuda.get_device_capability(index) for index in range(torch.cuda.device_count())]
required = int(sys.argv[1])
if len(rows) < required:
    raise SystemExit("visible CUDA device count is below tensor_parallel")
print(json.dumps({"torch_cuda": torch.version.cuda, "device_count": len(rows), "capabilities": rows}, sort_keys=True))
""".strip()

_MODEL_CONFIG = """
import json, sys
from pathlib import Path
config = Path(sys.argv[1]) / "config.json"
data = json.loads(config.read_text("utf-8"))
if not data.get("model_type"):
    raise SystemExit("model config has no model_type")
print(json.dumps({"model_type": data["model_type"], "config": str(config)}, sort_keys=True))
""".strip()


class PythonEnvironmentRuntimeProbe(QualificationRuntimeProbePort):
    """Run only bounded read-only checks inside a managed Python environment."""

    def __init__(self, execution: PythonEnvironmentExecutionPort) -> None:
        self._execution = execution

    def probe(
        self,
        environment_id: str,
        backend: str,
        model_path: Path,
        tensor_parallel: int,
    ) -> tuple[RuntimeCheckReceipt, ...]:
        checks = [
            (
                "backend-import",
                ("-c", _BACKEND_IMPORT, backend),
            ),
                ("cuda-runtime", ("-c", _CUDA_RUNTIME, str(tensor_parallel))),
            ("model-config", ("-c", _MODEL_CONFIG, model_path.as_posix())),
        ]
        return tuple(
            self._receipt(
                name,
                self._execution.run(environment_id, *argv),
            )
            for name, argv in checks
        )

    @staticmethod
    def _receipt(check: str, result: EnvironmentCommandResult) -> RuntimeCheckReceipt:
        return RuntimeCheckReceipt(
            check=check,
            command_digest=canonical_digest(result.argv),
            return_code=result.returncode,
            stdout_digest=canonical_digest(result.stdout),
            stderr_digest=canonical_digest(result.stderr),
            stdout_preview=_preview(result.stdout),
            stderr_preview=_preview(result.stderr),
        )


def _preview(value: str) -> str:
    if len(value) <= _MAX_RUNTIME_OUTPUT_PREVIEW:
        return value
    return value[:_MAX_RUNTIME_OUTPUT_PREVIEW] + "...<truncated>"


__all__ = ["PythonEnvironmentRuntimeProbe"]
