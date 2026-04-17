from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from .pod_dom import parse_pod_dom
except ImportError:
    from pod_dom import parse_pod_dom


def dump_pod_structure(path: str | Path, output_path: str | Path) -> dict:
    doc = parse_pod_dom(path)
    payload = doc.to_dict()
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "input": str(path),
        "output": str(output_path),
        "top_level_blocks": len(doc.blocks),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: pod_inspect.py <input.pod> <output.json>")
        return 2
    result = dump_pod_structure(argv[1], argv[2])
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
