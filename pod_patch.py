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
    from .pod_parser import mesh_vertices, parse_pod
    from .pod_writer import serialize_document
except ImportError:
    from pod_dom import (
        PODDocument,
        PODIdentifiers,
        get_mesh_interleaved_block,
        get_scene_mesh_blocks,
        parse_pod_dom,
    )
    from pod_parser import mesh_vertices, parse_pod
    from pod_writer import serialize_document


class PODPatchError(RuntimeError):
    pass


def patch_mesh_translation(input_path: str | Path, output_path: str | Path, mesh_index: int, delta_xyz: tuple[float, float, float]) -> dict:
    doc = parse_pod_dom(input_path)
    scene = parse_pod(input_path)

    mesh_blocks = get_scene_mesh_blocks(doc)
    if mesh_index < 0 or mesh_index >= len(mesh_blocks):
        raise PODPatchError(f"mesh_index {mesh_index} out of range (0..{len(mesh_blocks)-1})")

    mesh = scene.meshes[mesh_index]
    if not mesh.vertices or not mesh.interleaved_data:
        raise PODPatchError("Only interleaved vertex meshes are supported in this patch")

    interleaved_block = get_mesh_interleaved_block(mesh_blocks[mesh_index])
    if interleaved_block is None:
        raise PODPatchError("Mesh interleaved data block not found")

    stride = mesh.vertices.stride or 12
    offset = mesh.vertices.offset
    raw = bytearray(interleaved_block.payload)
    before_first = mesh_vertices(mesh)[0]

    dx, dy, dz = delta_xyz
    for vertex_idx in range(mesh.num_vertices):
        base = offset + (vertex_idx * stride)
        x, y, z = struct.unpack_from("<fff", raw, base)
        struct.pack_into("<fff", raw, base, x + dx, y + dy, z + dz)

    interleaved_block.payload = bytes(raw)

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(serialize_document(doc))

    reparsed = parse_pod(out_path)
    after_first = mesh_vertices(reparsed.meshes[mesh_index])[0]

    return {
        "input": str(input_path),
        "output": str(output_path),
        "mesh_index": mesh_index,
        "vertex_count": mesh.num_vertices,
        "delta": list(delta_xyz),
        "before_first_vertex": list(before_first),
        "after_first_vertex": list(after_first),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 7:
        print("usage: pod_patch.py <input.pod> <output.pod> <mesh_index> <dx> <dy> <dz>")
        return 2
    result = patch_mesh_translation(
        argv[1],
        argv[2],
        int(argv[3]),
        (float(argv[4]), float(argv[5]), float(argv[6])),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
