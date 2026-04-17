from __future__ import annotations

import copy
import json
import re
import struct
import sys
from pathlib import Path

try:
    from .pod_dom import PODBlock, PODDocument, get_scene_block, parse_pod_dom
    from .pod_parser import DataType, PODIdentifiers, parse_pod
    from .pod_writer import write_document
except ImportError:
    from pod_dom import PODBlock, PODDocument, get_scene_block, parse_pod_dom
    from pod_parser import DataType, PODIdentifiers, parse_pod
    from pod_writer import write_document


# Interleaved vertex layout: pos(3) + normal(3) + tangent(3) + uv0(2) + uv1(2) = 13 floats × 4 bytes
_VERTEX_STRIDE = (3 + 3 + 3 + 2 + 2) * 4  # 52 bytes

IDENTITY_UNPACK_MATRIX = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)

ZERO_SCALE_PAYLOAD = struct.pack("<fffffff", 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0)
IDENTITY_ROTATION_PAYLOAD = struct.pack("<ffff", 0.0, 0.0, 0.0, -1.0)
ZERO_POSITION_PAYLOAD = struct.pack("<fff", 0.0, 0.0, 0.0)


class PODFreshBuildError(RuntimeError):
    pass


def _blender_to_ootp_xyz(values: tuple[float, float, float] | list[float]) -> tuple[float, float, float]:
    # Blender is Z-up; imported OOTP stadiums indicate OOTP uses Y-up.
    # Preserve handedness by flipping Blender Y into OOTP Z.
    x, y, z = float(values[0]), float(values[1]), float(values[2])
    return (x, z, -y)


def _pack_u32(value: int) -> bytes:
    return struct.pack("<I", value)


def _pack_i32(value: int) -> bytes:
    return struct.pack("<i", value)


def _pack_f32(value: float) -> bytes:
    return struct.pack("<f", value)


def _pack_f32s(*values: float) -> bytes:
    return struct.pack("<" + ("f" * len(values)), *values)


def _pack_cstr(value: str) -> bytes:
    return value.encode("utf-8") + b"\x00"


def _clone_block(block: PODBlock) -> PODBlock:
    return copy.deepcopy(block)


def _raw_block(tag: int, payload: bytes) -> PODBlock:
    return PODBlock(
        tag=tag,
        length=len(payload),
        tag_offset=0,
        payload_offset=0,
        payload_end_offset=len(payload),
        end_tag_offset=0,
        end_length=0,
        payload=payload,
        children=[],
    )


def _container_block(tag: int, children: list[PODBlock]) -> PODBlock:
    return PODBlock(
        tag=tag,
        length=0,
        tag_offset=0,
        payload_offset=0,
        payload_end_offset=0,
        end_tag_offset=0,
        end_length=0,
        payload=b"",
        children=children,
    )


def _material_name_from_block(block: PODBlock) -> str:
    for child in block.children:
        if child.tag == PODIdentifiers.e_materialName:
            return child.payload.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    return ""


def _template_scene_parts(template_doc: PODDocument):
    scene = get_scene_block(template_doc)
    material_blocks = [b for b in scene.children if b.tag == PODIdentifiers.e_sceneMaterial]
    texture_blocks = [b for b in scene.children if b.tag == PODIdentifiers.e_sceneTexture]
    node_block = next((b for b in scene.children if b.tag == PODIdentifiers.e_sceneNode), None)
    return scene, material_blocks, texture_blocks, node_block


def _sanitize_name(name: str, index: int) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    if not base:
        base = f"Mesh_{index:03d}"
    return base[:63]


def _make_attr_block(tag: int, data_type: int, num_components: int, stride: int, data_payload: bytes) -> PODBlock:
    return _container_block(
        tag,
        [
            _raw_block(PODIdentifiers.e_blockDataType, _pack_u32(data_type)),
            _raw_block(PODIdentifiers.e_blockNumComponents, _pack_u32(num_components)),
            _raw_block(PODIdentifiers.e_blockStride, _pack_u32(stride)),
            _raw_block(PODIdentifiers.e_blockData, data_payload),
        ],
    )


def _make_mesh_block(mesh_record: dict) -> PODBlock:
    name = str(mesh_record.get("name", "Mesh"))
    material_name = str(mesh_record.get("material_name", ""))
    vertices = mesh_record["vertices"]
    normals = mesh_record["normals"]
    tangents = mesh_record.get("tangents") or [(1.0, 0.0, 0.0)] * len(vertices)
    uv0 = mesh_record.get("uv0") or [(0.0, 0.0)] * len(vertices)
    uv1 = mesh_record.get("uv1") or uv0
    indices = mesh_record["indices"]

    if not (len(vertices) == len(normals) == len(tangents) == len(uv0) == len(uv1)):
        raise PODFreshBuildError(f"Mesh {name} has mismatched attribute lengths")
    if len(indices) % 3 != 0:
        raise PODFreshBuildError(f"Mesh {name} index count is not divisible by 3")

    interleaved = bytearray()
    flip_lighting_v = material_name.lower() == "stand_lighting"

    for pos, normal, tangent, tex0, tex1 in zip(vertices, normals, tangents, uv0, uv1):
        pos = _blender_to_ootp_xyz(pos)
        normal = _blender_to_ootp_xyz(normal)
        tangent = _blender_to_ootp_xyz(tangent)
        if flip_lighting_v:
            tex0 = (float(tex0[0]), 1.0 - float(tex0[1]))
            tex1 = (float(tex1[0]), 1.0 - float(tex1[1]))
        interleaved.extend(_pack_f32s(
            float(pos[0]), float(pos[1]), float(pos[2]),
            float(normal[0]), float(normal[1]), float(normal[2]),
            float(tangent[0]), float(tangent[1]), float(tangent[2]),
            float(tex0[0]), float(tex0[1]),
            float(tex1[0]), float(tex1[1]),
        ))

    max_index = max(indices) if indices else 0
    if max_index <= 0xFFFF:
        index_type = DataType.UInt16
        index_stride = 2
        index_bytes = struct.pack("<" + ("H" * len(indices)), *[int(i) for i in indices])
    else:
        index_type = DataType.UInt32
        index_stride = 4
        index_bytes = struct.pack("<" + ("I" * len(indices)), *[int(i) for i in indices])

    children = [
        _raw_block(PODIdentifiers.e_meshNumVertices, _pack_u32(len(vertices))),
        _raw_block(PODIdentifiers.e_meshNumFaces, _pack_u32(len(indices) // 3)),
        _raw_block(PODIdentifiers.e_meshNumUVWChannels, _pack_u32(2)),
        _raw_block(PODIdentifiers.e_meshNumStrips, _pack_u32(0)),
        _raw_block(PODIdentifiers.e_meshInterleavedDataList, bytes(interleaved)),
        _raw_block(PODIdentifiers.e_meshMaxNumBonesPerBatch, _pack_u32(0)),
        _raw_block(PODIdentifiers.e_meshNumBoneBatches, _pack_u32(0)),
        _raw_block(PODIdentifiers.e_meshUnpackMatrix, _pack_f32s(*IDENTITY_UNPACK_MATRIX)),
        _make_attr_block(PODIdentifiers.e_meshVertexIndexList, index_type, 1, index_stride, index_bytes),
        _make_attr_block(PODIdentifiers.e_meshVertexList, DataType.Float32, 3, _VERTEX_STRIDE, _pack_u32(0)),
        _make_attr_block(PODIdentifiers.e_meshNormalList, DataType.Float32, 3, _VERTEX_STRIDE, _pack_u32(12)),
        _make_attr_block(PODIdentifiers.e_meshTangentList, DataType.Float32, 3, _VERTEX_STRIDE, _pack_u32(24)),
        _make_attr_block(PODIdentifiers.e_meshBinormalList, DataType.Float32, 0, 0, _pack_u32(0)),
        _make_attr_block(PODIdentifiers.e_meshUVWList, DataType.Float32, 2, _VERTEX_STRIDE, _pack_u32(36)),
        _make_attr_block(PODIdentifiers.e_meshUVWList, DataType.Float32, 2, _VERTEX_STRIDE, _pack_u32(44)),
        _make_attr_block(PODIdentifiers.e_meshVertexColorList, DataType.RGBA, 0, 0, _pack_u32(0)),
        _make_attr_block(PODIdentifiers.e_meshBoneIndexList, DataType.Int32, 0, 0, _pack_u32(0)),
        _make_attr_block(PODIdentifiers.e_meshBoneWeightList, DataType.Float32, 0, 0, _pack_u32(0)),
    ]
    return _container_block(PODIdentifiers.e_sceneMesh, children)


def _make_node_from_template(node_template: PODBlock, name: str, object_index: int, material_index: int) -> PODBlock:
    node = _clone_block(node_template)
    for child in node.children:
        if child.tag == PODIdentifiers.e_nodeIndex:
            child.payload = _pack_u32(object_index)
        elif child.tag == PODIdentifiers.e_nodeName:
            child.payload = _pack_cstr(name)
        elif child.tag == PODIdentifiers.e_nodeMaterialIndex:
            child.payload = _pack_i32(material_index)
        elif child.tag == PODIdentifiers.e_nodeParentIndex:
            child.payload = _pack_i32(-1)
        elif child.tag == PODIdentifiers.e_nodeAnimationPosition:
            child.payload = ZERO_POSITION_PAYLOAD
        elif child.tag == PODIdentifiers.e_nodeAnimationRotation:
            child.payload = IDENTITY_ROTATION_PAYLOAD
        elif child.tag == PODIdentifiers.e_nodeAnimationScale:
            child.payload = ZERO_SCALE_PAYLOAD
        elif child.tag == PODIdentifiers.e_nodeAnimationFlags:
            child.payload = _pack_u32(0)
    return node


def _load_scene_json(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {"meshes": data}
    if not isinstance(data, dict) or "meshes" not in data:
        raise PODFreshBuildError("Scene JSON must be a dict with a 'meshes' key")
    return data


def _scene_texture_block(path_text: str) -> PODBlock:
    return _container_block(
        PODIdentifiers.e_sceneTexture,
        [_raw_block(PODIdentifiers.e_textureFilename, _pack_cstr(path_text))],
    )


def _set_child_payload(block: PODBlock, tag: int, payload: bytes) -> None:
    for child in block.children:
        if child.tag == tag:
            child.payload = payload
            return
    block.children.append(_raw_block(tag, payload))


def _build_material_from_template(
    template_block: PODBlock,
    name: str,
    diffuse_index: int,
    secondary_index: int | None,
    pfx_filename: str | None = None,
    effect_name: str | None = None,
) -> PODBlock:
    block = _clone_block(template_block)
    _set_child_payload(block, PODIdentifiers.e_materialName, _pack_cstr(name))
    _set_child_payload(block, PODIdentifiers.e_materialDiffuseTextureIndex, _pack_i32(diffuse_index))
    _set_child_payload(
        block,
        PODIdentifiers.e_materialSecondaryTextureIndex,
        _pack_i32(-1 if secondary_index is None else secondary_index),
    )
    if pfx_filename is not None:
        _set_child_payload(block, PODIdentifiers.e_materialPfxFilename, _pack_cstr(pfx_filename))
    if effect_name is not None:
        _set_child_payload(block, PODIdentifiers.e_materialEffectName, _pack_cstr(effect_name))
    return block


def _build_material_from_exact_template(
    template_block: PODBlock,
    name: str,
    diffuse_index: int,
    secondary_index: int | None,
) -> PODBlock:
    block = _clone_block(template_block)
    _set_child_payload(block, PODIdentifiers.e_materialName, _pack_cstr(name))
    _set_child_payload(block, PODIdentifiers.e_materialDiffuseTextureIndex, _pack_i32(diffuse_index))
    _set_child_payload(
        block,
        PODIdentifiers.e_materialSecondaryTextureIndex,
        _pack_i32(-1 if secondary_index is None else secondary_index),
    )
    return block


def _build_materials_and_textures_from_spec(
    template_mat_name_map: dict[str, PODBlock],
    fallback_template: PODBlock,
    materials_spec: list[dict],
) -> tuple[list[PODBlock], list[PODBlock], dict[str, int]]:
    mode_to_template = {
        "ground": template_mat_name_map.get("Ground") or fallback_template,
        "opaque": template_mat_name_map.get("Ground") or fallback_template,
        "opaque_shadow": template_mat_name_map.get("Stand") or fallback_template,
        "alpha_shadow": template_mat_name_map.get("Alphatest") or fallback_template,
        "alpha_blend": template_mat_name_map.get("Alphablend") or fallback_template,
        "stock_lighting": template_mat_name_map.get("Stand_Lighting") or fallback_template,
        "emissive": (
            template_mat_name_map.get("ootp_scoreboard_0")
            or template_mat_name_map.get("screen")
            or template_mat_name_map.get("Stand_Lighting")
            or fallback_template
        ),
    }

    texture_index_by_path: dict[str, int] = {}
    texture_blocks: list[PODBlock] = []

    def ensure_texture(path_text: str | None) -> int | None:
        if not path_text:
            return None
        key = str(path_text).replace("\\", "/")
        if key not in texture_index_by_path:
            texture_index_by_path[key] = len(texture_blocks)
            texture_blocks.append(_scene_texture_block(key))
        return texture_index_by_path[key]

    material_blocks: list[PODBlock] = []
    material_index_by_name: dict[str, int] = {}
    for spec in materials_spec:
        name = str(spec["name"])
        source_name = str(spec.get("source_material_name") or name)
        mode = str(spec.get("mode", "opaque_shadow")).lower()
        exact_template = template_mat_name_map.get(source_name) or template_mat_name_map.get(name)
        template = exact_template or mode_to_template.get(mode)
        if template is None:
            raise PODFreshBuildError(f"No template material available for mode '{mode}'")

        diffuse_index = ensure_texture(spec.get("diffuse_path"))
        if diffuse_index is None:
            raise PODFreshBuildError(f"Material {name} has no diffuse_path")
        secondary_index = ensure_texture(spec.get("secondary_path"))

        # When the source material already exists in the template POD, preserve
        # that exact stock material block and only retarget the texture indices.
        # This keeps hidden blend/state flags and stock rendering behavior intact
        # for stadium-sensitive materials like Stand, Spectator, Lighting, and ADs.
        if exact_template is not None:
            material_blocks.append(
                _build_material_from_exact_template(
                    exact_template,
                    name,
                    diffuse_index,
                    secondary_index,
                )
            )
        else:
            material_blocks.append(
                _build_material_from_template(
                    template,
                    name,
                    diffuse_index,
                    secondary_index,
                    pfx_filename=spec.get("pfx_filename"),
                    effect_name=spec.get("effect_name"),
                )
            )
        material_index_by_name[name] = len(material_blocks) - 1

    return material_blocks, texture_blocks, material_index_by_name


def _get_top_level_blocks(template_doc: PODDocument) -> tuple[PODBlock, PODBlock, PODBlock]:
    if len(template_doc.blocks) < 3:
        raise PODFreshBuildError(
            f"Template POD must have at least 3 top-level blocks "
            f"(version/exportoptions/filehistory), got {len(template_doc.blocks)}"
        )
    return template_doc.blocks[0], template_doc.blocks[1], template_doc.blocks[2]


def build_fresh_pod_from_scene_json(
    template_pod_path: str | Path,
    scene_json_path: str | Path,
    output_pod_path: str | Path,
    default_material_index: int = 6,
) -> dict:
    template_doc = parse_pod_dom(template_pod_path)
    template_scene, template_material_blocks, template_texture_blocks, template_node = _template_scene_parts(template_doc)

    # Build name → block map from the DOM directly; no second parse needed.
    template_mat_name_map = {
        _material_name_from_block(b): b
        for b in template_material_blocks
        if _material_name_from_block(b)
    }
    fallback_template = template_material_blocks[0] if template_material_blocks else None

    scene_data = _load_scene_json(scene_json_path)
    scene_meshes = scene_data["meshes"]

    if template_node is None:
        raise PODFreshBuildError("Template POD has no node template")

    materials_spec = scene_data.get("materials")
    if materials_spec:
        if fallback_template is None:
            raise PODFreshBuildError("Template POD has no material blocks to use as template")
        material_blocks, texture_blocks, material_index_by_name = _build_materials_and_textures_from_spec(
            template_mat_name_map, fallback_template, materials_spec
        )
    else:
        material_blocks = [_clone_block(b) for b in template_material_blocks]
        texture_blocks = [_clone_block(b) for b in template_texture_blocks]
        num_template_materials = len(template_material_blocks)
        if default_material_index < 0 or default_material_index >= num_template_materials:
            raise PODFreshBuildError(
                f"default_material_index {default_material_index} out of range "
                f"(0..{num_template_materials - 1})"
            )
        material_index_by_name = {}

    mesh_blocks: list[PODBlock] = []
    node_blocks: list[PODBlock] = []
    mesh_names: list[str] = []
    for mesh_index, mesh_record in enumerate(scene_meshes):
        safe_name = _sanitize_name(str(mesh_record.get("name", "")), mesh_index)
        mesh_names.append(safe_name)
        mesh_blocks.append(_make_mesh_block(mesh_record))
        if materials_spec:
            material_name = mesh_record.get("material_name")
            if not material_name:
                raise PODFreshBuildError(f"Mesh {safe_name} is missing material_name for custom material build")
            if material_name not in material_index_by_name:
                raise PODFreshBuildError(f"Mesh {safe_name} references unknown material_name '{material_name}'")
            material_index = material_index_by_name[material_name]
        else:
            material_index = default_material_index
        node_blocks.append(_make_node_from_template(template_node, safe_name, mesh_index, material_index))

    scene_children = [
        _raw_block(PODIdentifiers.e_sceneUnits, _pack_f32(1.0)),
        _raw_block(PODIdentifiers.e_sceneClearColor, _pack_f32s(0.0, 0.0, 0.0)),
        _raw_block(PODIdentifiers.e_sceneAmbientColor, _pack_f32s(0.0, 0.0, 0.0)),
        _raw_block(PODIdentifiers.e_sceneNumCameras, _pack_u32(0)),
        _raw_block(PODIdentifiers.e_sceneNumLights, _pack_u32(0)),
        _raw_block(PODIdentifiers.e_sceneNumMeshes, _pack_u32(len(mesh_blocks))),
        _raw_block(PODIdentifiers.e_sceneNumNodes, _pack_u32(len(node_blocks))),
        _raw_block(PODIdentifiers.e_sceneNumMeshNodes, _pack_u32(len(node_blocks))),
        _raw_block(PODIdentifiers.e_sceneNumTextures, _pack_u32(len(texture_blocks))),
        _raw_block(PODIdentifiers.e_sceneNumMaterials, _pack_u32(len(material_blocks))),
        _raw_block(PODIdentifiers.e_sceneNumFrames, _pack_u32(0)),
        _raw_block(PODIdentifiers.e_sceneFlags, _pack_u32(0)),
        *material_blocks,
        *mesh_blocks,
        *node_blocks,
        *texture_blocks,
    ]

    version_block, export_options_block, file_history_block = _get_top_level_blocks(template_doc)
    scene_block = _container_block(PODIdentifiers.Scene, scene_children)

    out_path = Path(output_pod_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = PODDocument(
        path=str(out_path),
        data=b"",
        blocks=[
            _clone_block(version_block),
            _clone_block(export_options_block),
            _clone_block(file_history_block),
            scene_block,
        ],
    )
    write_document(doc, out_path)

    reparsed = parse_pod(out_path)
    return {
        "template_pod": str(template_pod_path),
        "scene_json": str(scene_json_path),
        "output_pod": str(out_path),
        "mesh_count": len(scene_meshes),
        "node_count": len(reparsed.nodes),
        "material_count": len(reparsed.materials),
        "texture_count": len(reparsed.textures),
        "mesh_names": mesh_names[:8],
    }


def main(argv: list[str]) -> int:
    if len(argv) not in (4, 5):
        print("usage: pod_fresh_builder.py <template.pod> <scene_meshes.json> <output.pod> [default_material_index]")
        return 2
    material_index = int(argv[4]) if len(argv) == 5 else 6
    result = build_fresh_pod_from_scene_json(argv[1], argv[2], argv[3], material_index)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
