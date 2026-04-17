from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

try:
    from .pod_dom import END_TAG_MASK, PODBlock, PODDocument, parse_pod_dom
except ImportError:
    from pod_dom import END_TAG_MASK, PODBlock, PODDocument, parse_pod_dom


def serialize_block(block: PODBlock) -> bytes:
    parts: list[bytes] = []
    parts.append(int(block.tag).to_bytes(4, "little", signed=False))
    if block.children:
        # Container blocks always write length=0; children delimit themselves with end tags.
        parts.append((0).to_bytes(4, "little", signed=False))
        for child in block.children:
            parts.append(serialize_block(child))
    else:
        # Compute length from actual payload so stale block.length can't corrupt output.
        parts.append(len(block.payload).to_bytes(4, "little", signed=False))
        parts.append(block.payload)

    if block.has_end_tag:
        parts.append(int(block.tag | END_TAG_MASK).to_bytes(4, "little", signed=False))
        parts.append(int(block.end_length or 0).to_bytes(4, "little", signed=False))

    return b"".join(parts)


def serialize_document(doc: PODDocument) -> bytes:
    return b"".join(serialize_block(block) for block in doc.blocks)


def write_document(doc: PODDocument, output_path: str | Path) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(serialize_document(doc))
    return out


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_roundtrip(input_path: str | Path, output_path: str | Path) -> dict:
    doc = parse_pod_dom(input_path)
    rebuilt = serialize_document(doc)
    original = Path(input_path).read_bytes()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(rebuilt)

    return {
        "input": str(input_path),
        "output": str(output_path),
        "input_size": len(original),
        "output_size": len(rebuilt),
        "byte_identical": rebuilt == original,
        "input_sha256": sha256_bytes(original),
        "output_sha256": sha256_bytes(rebuilt),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: pod_writer.py <input.pod> <output.pod>")
        return 2
    result = verify_roundtrip(argv[1], argv[2])
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
