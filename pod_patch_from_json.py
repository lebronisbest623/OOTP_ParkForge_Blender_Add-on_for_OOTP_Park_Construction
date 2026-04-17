from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

try:
    from .pod_dom import (
        PODDocument,
        PODIdentifiers,
        get_mesh_interleaved_block,
        get_scene_mesh_blocks,
        parse_pod_dom,
    )
    from .pod_parser import parse_pod
    from .pod_writer import serialize_document
except ImportError:
    from pod_dom import (
        PODDocument,
        PODIdentifiers,
        get_mesh_interleaved_block,
        get_scene_mesh_blocks,
        parse_pod_dom,
    )
    from pod_parser import parse_pod
    from pod_writer import serialize_document


class PODPatchFromJsonError(RuntimeError):
    pass


def patch_mesh_vertices_from_json(
    input_path: str | Path,
    output_path: str | Path,
    mesh_index: int,
    vertices_json_path: str | Path,
) -> dict:
    doc = parse_pod_dom(input_path)
    scene = parse_pod(input_path)

    mesh_blocks = get_scene_mesh_blocks(doc)
    if mesh_index < 0 or mesh_index >= len(mesh_blocks):
        raise PODPatchFromJsonError(f"mesh_index {mesh_index} out of range (0..{len(mesh_blocks)-1})")

    mesh = scene.meshes[mesh_index]
    if not mesh.vertices or not mesh.interleaved_data:
        raise PODPatchFromJsonError("Only interleaved vertex meshes are supported")

    vertices = json.loads(Path(vertices_json_path).read_text(encoding="utf-8"))
    if not isinstance(vertices, list):
        raise PODPatchFromJsonError("Vertex JSON must be a list")
    if len(vertices) != mesh.num_vertices:
        raise PODPatchFromJsonError(
            f"Vertex count mismatch: JSON has {len(vertices)}, mesh expects {mesh.num_vertices}"
        )

    interleaved_block = get_mesh_interleaved_block(mesh_blocks[mesh_index])
    if interleaved_block is None:
        raise PODPatchFromJsonError("Mesh interleaved data block not found")

    stride = mesh.vertices.stride or 12
    offset = mesh.vertices.offset
    raw = bytearray(interleaved_block.payload)

    for vertex_idx, v in enumerate(vertices):
        if not isinstance(v, (list, tuple)) or len(v) < 3:
            raise PODPatchFromJsonError(f"Vertex {vertex_idx} is not a 3-float tuple/list")
        base = offset + (vertex_idx * stride)
        struct.pack_into("<fff", raw, base, float(v[0]), float(v[1]), float(v[2]))

    interleaved_block.payload = bytes(raw)

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(serialize_document(doc))

    return {
        "input": str(input_path),
        "output": str(output_path),
        "mesh_index": mesh_index,
        "vertex_count": mesh.num_vertices,
        "vertices_json": str(vertices_json_path),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print("usage: pod_patch_from_json.py <input.pod> <output.pod> <mesh_index> <vertices.json>")
        return 2
    result = patch_mesh_vertices_from_json(argv[1], argv[2], int(argv[3]), argv[4])
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
