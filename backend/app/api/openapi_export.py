"""Write the API's OpenAPI schema as JSON (input for the frontend's generated types).

Usage: python -m app.api.openapi_export [path]   (default: stdout). No server or DB needed.
"""

import json
import sys

from app.main import app


def openapi_json() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = openapi_json()
    if args:
        with open(args[0], "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
