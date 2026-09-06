from __future__ import annotations

import json

from noetrium_platform.capabilities.environment.category.composition import (
    default_environment_category_catalog,
)


def main() -> None:
    catalog = default_environment_category_catalog()
    document = {
        "categories": [
            {
                "id": category.category_id.value,
                "implementations": [
                    {
                        "id": implementation.implementation_id,
                        "status": implementation.status.value,
                    }
                    for implementation in catalog.implementations(category.category_id)
                ],
            }
            for category in catalog.categories()
        ],
        "non_environment_concepts": [
            "benchmark",
            "replay",
            "synthetic",
            "tool",
            "multi_agent",
        ],
    }
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
