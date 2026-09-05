from __future__ import annotations

import ast
from pathlib import Path

from .source_index import source_tree

from .source_scan import SourceInvariantViolation, violation


def audit_participant_binding_invariants(root: Path) -> list[SourceInvariantViolation]:
    rows: list[SourceInvariantViolation] = []
    contracts = root / "noetrium_platform" / "capabilities" / "participant" / "core" / "api" / "contracts.py"
    if contracts.exists():
        tree = source_tree(contracts)
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "RuntimeParticipant":
                rows.append(violation(root, contracts, "participant_combined_runtime_forbidden", node.lineno, "combined RuntimeParticipant abstraction is forbidden; implementation and session runtime identities are separate"))
            if not isinstance(node, ast.ClassDef) or node.name != "ParticipantRuntimeBinding":
                continue
            fields = {
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
            }
            required = {"role", "implementation", "runtime", "configuration_digest"}
            missing = sorted(required - fields)
            if missing:
                rows.append(violation(root, contracts, "participant_runtime_binding_identity", node.lineno, f"ParticipantRuntimeBinding is missing independent frozen identity fields: {missing}"))

    method_contracts = root / "noetrium_platform" / "capabilities" / "participant" / "method" / "api" / "ports.py"
    method_runtime = root / "noetrium_platform" / "capabilities" / "participant" / "method" / "runtime" / "endpoint.py"
    if method_contracts.exists():
        contract_tree = source_tree(method_contracts)
        contract_classes = {node.name: node for node in contract_tree.body if isinstance(node, ast.ClassDef)}
        if "MethodRuntimeBinding" not in contract_classes:
            rows.append(violation(root, method_contracts, "method_runtime_identity_separation", 1, "MethodRuntimeBinding missing; implementation and runtime identities must not be folded into MethodIdentity"))
    if method_runtime.exists():
        tree = source_tree(method_runtime)
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        endpoint = classes.get("MethodRuntimeEndpoint")
        if endpoint is not None:
            identity_prop = next((node for node in endpoint.body if isinstance(node, ast.FunctionDef) and node.name == "identity"), None)
            if identity_prop is not None:
                text = ast.unparse(identity_prop)
                if "binding.digest" in text or "canonical_digest" in text:
                    rows.append(violation(root, method_runtime, "method_runtime_identity_separation", identity_prop.lineno, "MethodRuntimeEndpoint.identity must expose scientific implementation identity, not a runtime-bound digest"))
    return rows


__all__ = ["audit_participant_binding_invariants"]
