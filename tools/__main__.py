"""Try tools by hand against the database in DATABASE_URL.

python -m tools --list
python -m tools sentiment_trend --json '{"ticker": "NVDA", "days": 7}'
"""

import argparse
import json
import sys

from tools import default_registry


def main() -> int:
    registry = default_registry()
    parser = argparse.ArgumentParser(prog="python -m tools")
    parser.add_argument("tool", nargs="?", choices=registry.names(), help="tool to call")
    parser.add_argument("--json", default="{}", help="tool arguments as a JSON object")
    parser.add_argument("--list", action="store_true", help="print every tool's schema")
    args = parser.parse_args()

    if args.list or not args.tool:
        print(json.dumps(registry.schemas(), indent=2))
        return 0
    try:
        arguments = json.loads(args.json)
    except json.JSONDecodeError as exc:
        parser.error(f"--json is not valid JSON: {exc}")
    result = registry.call(args.tool, arguments)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
