# vNext Boundary: runtime/python

SYSTEM = "runtime"
NODE = "runtime/python"
OWNS = "Python runtime/interpreter environment contracts"
MUST_NOT_OWN = "generic process supervisor"
AUTHORITY = "python_environment"

# This module is intentionally declarative. Concrete behavior belongs in runtime/providers.
