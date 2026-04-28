from __future__ import annotations

import json
import os
import re
import shutil
import struct
import zlib
from pathlib import Path

import bpy
from mathutils import Vector

from .pod_fresh_builder import build_fresh_pod_from_scene_json
from .pod_material_package import _write_png_rgba, build_material_package


class PODExportError(RuntimeError):
    pass


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip())
    return cleaned or "Object"


def _iter_target_objects(context: bpy.types.Context, selected_only: bool) -> list[bpy.types.Object]:
    def _is_exportable_mesh(obj: bpy.types.Object) -> bool:
        if obj.type != "MESH":
            return False
        if obj.name.endswith("BACKUP") or "_BACKUP" in obj.name:
            return False
        if obj.hide_render:
            return False
        if obj.hide_get():
            return False
        try:
            if not obj.visible_get():
                return False
        except Exception:
            pass
        return True

    if selected_only and context.selected_objects:
        return [obj for obj in context.selected_objects if _is_exportable_mesh(obj)]
    return [obj for obj in context.scene.objects if _is_exportable_mesh(obj)]


def _emit_progress(progress_cb, fraction: float, message: str) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(max(0.0, min(1.0, fraction)), message)
    except Exception:
        pass


def _normalized_path(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except Exception:
        return os.path.normcase(str(path))


def _is_protected_output_dir(path: Path) -> bool:
    text = _normalized_path(path)
    protected_roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("SystemRoot", r"C:\Windows")),
    ]
    for root in protected_roots:
        root_text = _normalized_path(root)
        if text == root_text or text.startswith(root_text + os.sep):
            return True
    return False


def _recommended_safe_output_dir() -> Path:
    return Path.home() / "Documents" / "ParkForge_exports"


def _ensure_export_location_is_safe(output_pod: Path, template_pod: Path) -> None:
    # Local workflow override: allow direct export into the live OOTP models folder.
    # The caller intentionally controls the destination and accepts overwrite risk.
    return


def _ensure_output_name_matches_template(output_pod: Path, template_pod: Path, copy_template_sidecars: bool) -> None:
    # Local workflow override: allow exporting with an arbitrary stadium/package name.
    # Matching-stem sidecars are renamed during copy so the package remains self-consistent.
    return


def _resolve_image_path(image: bpy.types.Image, generated_dir: Path) -> Path | None:
    raw = image.filepath_raw or image.filepath
    if raw:
        resolved = Path(bpy.path.abspath(raw))
        if resolved.exists():
            return resolved

    generated_dir.mkdir(parents=True, exist_ok=True)
    out_path = generated_dir / f"{_safe_name(image.name)}.png"
    try:
        image.save_render(filepath=str(out_path))
        if out_path.exists():
            return out_path
    except Exception:
        pass
    return None


_IMAGE_ROLE_PRIORITY = {
    "generic": 0,
    "auxiliary": 0,
    "diffuse": 1,
    "emissive": 1,
    "ground": 2,
    "ground_secondary": 2,
    "secondary": 3,
    "lightmap": 3,
    "shadow": 3,
    "secondary_night": 4,
}


def _append_resolved_image_entry(images: list[dict], seen: set[str], image: bpy.types.Image, generated_dir: Path, role: str) -> None:
    image_path = _resolve_image_path(image, generated_dir)
    if image_path is None:
        return
    key = str(image_path).lower()
    if key in seen:
        for entry in images:
            if str(entry.get("filepath", "")).lower() == key:
                old_role = str(entry.get("role", "generic"))
                if _IMAGE_ROLE_PRIORITY.get(role, 0) > _IMAGE_ROLE_PRIORITY.get(old_role, 0):
                    entry["role"] = role
                break
        return
    seen.add(key)
    images.append({"name": image.name, "filepath": str(image_path), "role": role})



def _iter_upstream_nodes_from_socket(socket, seen_nodes: set | None = None):
    if socket is None:
        return
    if seen_nodes is None:
        seen_nodes = set()
    for link in getattr(socket, "links", []):
        from_node = getattr(link, "from_node", None)
        if from_node is None or from_node in seen_nodes:
            continue
        seen_nodes.add(from_node)
        yield from_node
        for input_socket in getattr(from_node, "inputs", []):
            if getattr(input_socket, "is_linked", False):
                yield from _iter_upstream_nodes_from_socket(input_socket, seen_nodes)



def _material_output_nodes(material: bpy.types.Material) -> list[bpy.types.Node]:
    if not material.use_nodes or not material.node_tree:
        return []
    outputs = [
        node for node in material.node_tree.nodes
        if node.type == "OUTPUT_MATERIAL" and getattr(node, "is_active_output", False)
    ]
    if outputs:
        return outputs
    return [node for node in material.node_tree.nodes if node.type == "OUTPUT_MATERIAL"]



def _image_role_from_identity(node_name: str, image_name: str, image_path: str) -> str:
    text = f"{node_name} {image_name} {image_path}".lower()
    if any(token in text for token in ("lightmap", "shadow", "_lm", " lm", "lm_", "stand_lighting")):
        return "lightmap"
    if any(token in text for token in ("rough", "roughness", "normal", "metal", "metallic", "disp", "displacement", "ao", "spec", "gloss")):
        return "auxiliary"
    return "generic"



def _collect_socket_image_entries(socket, generated_dir: Path, images: list[dict], seen: set[str], role: str) -> None:
    for node in _iter_upstream_nodes_from_socket(socket):
        if node.type == "TEX_IMAGE" and getattr(node, "image", None):
            node_role = role
            if role in {"diffuse", "generic"}:
                detected_role = _image_role_from_identity(
                    node.name,
                    node.image.name,
                    str(node.image.filepath_raw or node.image.filepath or ""),
                )
                if detected_role != "generic":
                    node_role = detected_role
            _append_resolved_image_entry(images, seen, node.image, generated_dir, node_role)



def _material_image_override(material: bpy.types.Material, attr_name: str):
    image = getattr(material, attr_name, None)
    if image is not None:
        return image
    return None


def _has_image_role(images: list[dict], *roles: str) -> bool:
    wanted = {role.lower() for role in roles}
    for entry in images:
        if str(entry.get("role", "")).lower() in wanted and entry.get("filepath"):
            return True
    return False


def _material_explicit_image_entries(material: bpy.types.Material, generated_dir: Path, images: list[dict], seen: set[str]) -> None:
    primary = _material_image_override(material, "ootp_primary_image")
    secondary = _material_image_override(material, "ootp_secondary_image")
    secondary_night = _material_image_override(material, "ootp_secondary_night_image")
    if primary is not None and not _has_image_role(images, "diffuse", "emissive", "generic"):
        _append_resolved_image_entry(images, seen, primary, generated_dir, "diffuse")
    if secondary is not None and not _has_image_role(images, "secondary", "lightmap", "shadow"):
        _append_resolved_image_entry(images, seen, secondary, generated_dir, "secondary")
    if secondary_night is not None and not _has_image_role(images, "secondary_night"):
        _append_resolved_image_entry(images, seen, secondary_night, generated_dir, "secondary_night")


def _material_image_entries(material: bpy.types.Material, generated_dir: Path) -> list[dict]:
    if not material.use_nodes or not material.node_tree:
        return []

    images: list[dict] = []
    seen: set[str] = set()

    for output in _material_output_nodes(material):
        surface_socket = output.inputs.get("Surface")
        for node in _iter_upstream_nodes_from_socket(surface_socket):
            if node.type == "BSDF_PRINCIPLED":
                _collect_socket_image_entries(node.inputs.get("Base Color"), generated_dir, images, seen, "diffuse")
                emission_socket = node.inputs.get("Emission Color") or node.inputs.get("Emission")
                _collect_socket_image_entries(emission_socket, generated_dir, images, seen, "emissive")
            elif node.type == "EMISSION":
                _collect_socket_image_entries(node.inputs.get("Color"), generated_dir, images, seen, "emissive")

    for node in material.node_tree.nodes:
        if node.type != "TEX_IMAGE" or not getattr(node, "image", None):
            continue
        role = _image_role_from_identity(node.name, node.image.name, str(node.image.filepath_raw or node.image.filepath or ""))
        _append_resolved_image_entry(images, seen, node.image, generated_dir, role)
    # Explicit export image fields are fallback slots. The live Blender node
    # graph should win when the artist has changed a material texture, otherwise
    # stale pointer properties can export an older cached texture.
    _material_explicit_image_entries(material, generated_dir, images, seen)
    return images


def _principled_base_rgba(material: bpy.types.Material) -> tuple[float, float, float, float]:
    if material.use_nodes and material.node_tree:
        for node in material.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                value = node.inputs["Base Color"].default_value
                return float(value[0]), float(value[1]), float(value[2]), float(value[3])
        for node in material.node_tree.nodes:
            if node.type == "EMISSION":
                value = node.inputs["Color"].default_value
                return float(value[0]), float(value[1]), float(value[2]), float(value[3])
    value = material.diffuse_color
    return float(value[0]), float(value[1]), float(value[2]), float(value[3])


def _write_flat_texture(path: Path, rgba: tuple[float, float, float, float]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)

    def as_byte(v: float) -> int:
        return max(0, min(255, int(round(v * 255.0))))

    r, g, b, a = (as_byte(max(0.0, min(1.0, float(v)))) for v in rgba)
    pixel = bytes((r, g, b, a))
    _write_png_rgba(path, 8, 8, [pixel * 8 for _ in range(8)])
    return path


def _fallback_material_texture(material: bpy.types.Material, generated_dir: Path) -> Path:
    color = _principled_base_rgba(material)
    out_path = generated_dir / f"{_safe_name(material.name)}.png"
    return _write_flat_texture(out_path, color)


def _mesh_uv_layer_names(mesh: bpy.types.Mesh) -> tuple[str | None, str | None]:
    if not mesh.uv_layers:
        return None, None
    uv0 = None
    for layer in mesh.uv_layers:
        if getattr(layer, "active_render", False):
            uv0 = layer.name
            break
    if uv0 is None:
        active_index = getattr(mesh.uv_layers, "active_index", 0)
        if 0 <= active_index < len(mesh.uv_layers):
            uv0 = mesh.uv_layers[active_index].name
        else:
            uv0 = mesh.uv_layers[0].name
    uv1 = None
    for layer in mesh.uv_layers:
        if layer.name != uv0:
            uv1 = layer.name
            break
    return uv0, uv1


def _world_normal(obj: bpy.types.Object, normal: Vector) -> Vector:
    return (obj.matrix_world.to_3x3().inverted().transposed() @ normal).normalized()


def _material_has_emission(material: bpy.types.Material) -> bool:
    if not material.use_nodes or not material.node_tree:
        return False
    for node in material.node_tree.nodes:
        if node.type == "EMISSION":
            return True
        if node.type == "BSDF_PRINCIPLED":
            socket = node.inputs.get("Emission Strength")
            if socket and float(socket.default_value) > 0.0:
                return True
    return False


def _template_semantic_name(name: str) -> str:
    normalized = name.lower().strip()
    normalized = re.sub(r"\.\d{3}$", "", normalized)
    return normalized


def _material_string_override(material: bpy.types.Material, attr_name: str) -> str | None:
    value = getattr(material, attr_name, None)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    value = material.get(attr_name)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _material_bool_override(material: bpy.types.Material, attr_name: str) -> bool | None:
    value = getattr(material, attr_name, None)
    if isinstance(value, bool):
        return value
    value = material.get(attr_name)
    if isinstance(value, bool):
        return value
    return None


def _material_float_override(material: bpy.types.Material, attr_name: str) -> float | None:
    value = getattr(material, attr_name, None)
    if isinstance(value, (int, float)):
        return float(value)
    value = material.get(attr_name)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _material_export_mode_override(material: bpy.types.Material) -> str | None:
    value = _material_string_override(material, "ootp_export_mode")
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized == "auto":
        return None
    valid = {
        "ground",
        "stock_background",
        "stock_lighting",
        "opaque_shadow",
        "alpha_shadow",
        "alpha_blend",
        "emissive",
    }
    if normalized in valid:
        return normalized
    return None


def _material_template_name_override(material: bpy.types.Material) -> str | None:
    return _material_string_override(material, "ootp_template_material_name")


def _material_blend_mode(material: bpy.types.Material) -> str:
    override = _material_export_mode_override(material)
    if override:
        return override
    name = _template_semantic_name(material.name)
    if name == "ground":
        return "ground"
    if name == "background":
        return "stock_background"
    if name == "stand_lighting":
        return "stock_lighting"
    if name == "mat_fencestripe_yellowsafety":
        return "opaque_shadow"
    if name == "mat_scoreboardhousing_darkgraymetal":
        return "opaque_shadow"
    if name == "mat_concrete_blackexterior":
        return "opaque_shadow"
    if (
        name.startswith("spectator")
        or "spectator" in name
        or "attendance" in name
        or "audience" in name
        or "seating" in name
    ):
        return "alpha_shadow"
    if name.startswith("ootp_scoreboard") or name == "screen":
        return "emissive"
    blend = getattr(material, "blend_method", "OPAQUE")
    if blend == "CLIP":
        return "alpha_shadow"
    if blend == "HASHED":
        return "alpha_shadow"
    if blend == "BLEND":
        return "alpha_blend"
    if _material_has_emission(material):
        return "emissive"
    return "opaque_shadow"


def _auto_uv_from_world(world_co: Vector, world_no: Vector, scale: float = 0.1) -> tuple[float, float]:
    """Synthesize export UVs when a mesh has no authored UVMap.

    Many blockout meshes still lack UVs entirely. Without this, UV0/UV1 both
    collapse to (0, 0), which makes textured materials look like a flat
    fallback in OOTP. We project by dominant normal axis in world space so the
    exporter stays non-destructive while producing usable tiled coordinates.
    """
    nx, ny, nz = abs(world_no.x), abs(world_no.y), abs(world_no.z)
    if nz >= nx and nz >= ny:
        return float(world_co.x * scale), float(world_co.y * scale)
    if nx >= ny:
        return float(world_co.y * scale), float(world_co.z * scale)
    return float(world_co.x * scale), float(world_co.z * scale)


def _uv_layer_is_degenerate(mesh: bpy.types.Mesh, uv_layer: bpy.types.MeshUVLoopLayer | None) -> bool:
    """Return True if a UV layer exists but effectively contains no usable variation."""
    if uv_layer is None or not mesh.loops:
        return True
    min_u = min_v = float("inf")
    max_u = max_v = float("-inf")
    first = None
    varied = False
    for item in uv_layer.data:
        u = float(item.uv.x)
        v = float(item.uv.y)
        if first is None:
            first = (u, v)
        elif not varied and (abs(u - first[0]) > 1e-6 or abs(v - first[1]) > 1e-6):
            varied = True
        if u < min_u:
            min_u = u
        if v < min_v:
            min_v = v
        if u > max_u:
            max_u = u
        if v > max_v:
            max_v = v
    return (not varied) or (abs(max_u - min_u) < 1e-6 and abs(max_v - min_v) < 1e-6)


def _uv_layer_is_degenerate_on_loops(uv_layer: bpy.types.MeshUVLoopLayer | None, loop_indices: list[int]) -> bool:
    """Return True if a UV layer is effectively dead for one material's loop subset."""
    if uv_layer is None or not loop_indices:
        return True
    min_u = min_v = float("inf")
    max_u = max_v = float("-inf")
    first = None
    varied = False
    for loop_index in loop_indices:
        uv = uv_layer.data[loop_index].uv
        u = float(uv.x)
        v = float(uv.y)
        if first is None:
            first = (u, v)
        elif not varied and (abs(u - first[0]) > 1e-6 or abs(v - first[1]) > 1e-6):
            varied = True
        if u < min_u:
            min_u = u
        if v < min_v:
            min_v = v
        if u > max_u:
            max_u = u
        if v > max_v:
            max_v = v
    return (not varied) or (abs(max_u - min_u) < 1e-6 and abs(max_v - min_v) < 1e-6)


AUTO_SPLIT_MAX_TRIS = 30000


def _split_mesh_record_by_triangle_budget(record: dict, max_tris: int = AUTO_SPLIT_MAX_TRIS) -> list[dict]:
    """Split one oversized material submesh into several spatial chunks.

    OOTP appears to render very large single-material submeshes unreliably at runtime.
    Keep the same material assignment, but partition the mesh record into smaller chunks
    before writing the POD scene.
    """
    indices = record.get("indices", [])
    tri_count = len(indices) // 3
    if tri_count <= max_tris:
        return [record]

    vertices = record["vertices"]
    normals = record["normals"]
    uv0 = record["uv0"]
    uv1 = record["uv1"]

    tris: list[tuple[tuple[int, int, int], tuple[float, float, float]]] = []
    for i in range(0, len(indices), 3):
        tri = (int(indices[i]), int(indices[i + 1]), int(indices[i + 2]))
        va = vertices[tri[0]]
        vb = vertices[tri[1]]
        vc = vertices[tri[2]]
        centroid = (
            (va[0] + vb[0] + vc[0]) / 3.0,
            (va[1] + vb[1] + vc[1]) / 3.0,
            (va[2] + vb[2] + vc[2]) / 3.0,
        )
        tris.append((tri, centroid))

    buckets: list[list[tuple[tuple[int, int, int], tuple[float, float, float]]]] = []

    def recurse(items: list[tuple[tuple[int, int, int], tuple[float, float, float]]], depth: int = 0) -> None:
        if len(items) <= max_tris or depth >= 12:
            buckets.append(items)
            return

        mins = [min(item[1][axis] for item in items) for axis in range(3)]
        maxs = [max(item[1][axis] for item in items) for axis in range(3)]
        axis = max(range(3), key=lambda idx: maxs[idx] - mins[idx])
        ordered = sorted(items, key=lambda item: item[1][axis])
        mid = len(ordered) // 2
        left = ordered[:mid]
        right = ordered[mid:]
        if not left or not right:
            buckets.append(items)
            return
        recurse(left, depth + 1)
        recurse(right, depth + 1)

    recurse(tris)
    if len(buckets) <= 1:
        return [record]

    out: list[dict] = []
    width = max(2, len(str(len(buckets))))
    for index, bucket in enumerate(buckets, start=1):
        local_map: dict[int, int] = {}
        part = {
            "name": f"{record['name']}__part_{index:0{width}d}",
            "material_name": record["material_name"],
            "vertices": [],
            "normals": [],
            "uv0": [],
            "uv1": [],
            "indices": [],
        }
        for tri, _ in bucket:
            for old_idx in tri:
                if old_idx not in local_map:
                    local_map[old_idx] = len(part["vertices"])
                    part["vertices"].append(vertices[old_idx])
                    part["normals"].append(normals[old_idx])
                    part["uv0"].append(uv0[old_idx])
                    part["uv1"].append(uv1[old_idx])
                part["indices"].append(local_map[old_idx])
        out.append(part)
    return out


def _collect_scene_meshes(context: bpy.types.Context, objects: list[bpy.types.Object]) -> tuple[list[dict], list[dict]]:
    depsgraph = context.evaluated_depsgraph_get()
    meshes_out: list[dict] = []
    material_rows: list[dict] = []
    seen_material_names: set[str] = set()

    for obj in objects:
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        if mesh is None:
            continue
        try:
            mesh.calc_loop_triangles()
            uv0_name, uv1_name = _mesh_uv_layer_names(mesh)
            uv0_layer = mesh.uv_layers.get(uv0_name) if uv0_name else None
            uv1_layer = mesh.uv_layers.get(uv1_name) if uv1_name else None
            grouped: dict[int, dict] = {}
            material_loop_indices: dict[int, list[int]] = {}
            # Per-group vertex deduplication: key = (pos, normal, uv0, uv1) → index
            vert_keys: dict[int, dict[tuple, int]] = {}

            for tri in mesh.loop_triangles:
                mat_index = tri.material_index if tri.material_index < len(obj.material_slots) else 0
                material_loop_indices.setdefault(mat_index, []).extend(tri.loops)
                slot = obj.material_slots[mat_index] if mat_index < len(obj.material_slots) else None
                material = slot.material if slot else None
                material_name = material.name if material else "Material"
                if mat_index not in grouped:
                    grouped[mat_index] = {
                        "name": f"{_safe_name(obj.name)}__{_safe_name(material_name)}",
                        "material_name": material_name,
                        "vertices": [],
                        "normals": [],
                        "uv0": [],
                        "uv1": [],
                        "indices": [],
                    }
                    vert_keys[mat_index] = {}

            uv_layers_by_mat: dict[int, tuple[bpy.types.MeshUVLoopLayer | None, bpy.types.MeshUVLoopLayer | None]] = {}
            world_uv_by_mat: dict[int, tuple[bool, float]] = {}
            for mat_index, loop_indices in material_loop_indices.items():
                mat_uv0 = uv0_layer
                mat_uv1 = uv1_layer
                slot = obj.material_slots[mat_index] if mat_index < len(obj.material_slots) else None
                material = slot.material if slot else None
                force_world_uv = False
                world_uv_scale = 0.1
                if material is not None:
                    force_world_uv = bool(_material_bool_override(material, "ootp_force_world_uv") or False)
                    scale_override = _material_float_override(material, "ootp_world_uv_scale")
                    if scale_override and scale_override > 0:
                        world_uv_scale = float(scale_override)
                if _uv_layer_is_degenerate_on_loops(mat_uv0, loop_indices):
                    mat_uv0 = None
                if _uv_layer_is_degenerate_on_loops(mat_uv1, loop_indices):
                    mat_uv1 = None
                if force_world_uv:
                    mat_uv0 = None
                    mat_uv1 = None
                uv_layers_by_mat[mat_index] = (mat_uv0, mat_uv1)
                world_uv_by_mat[mat_index] = (force_world_uv, world_uv_scale)

            for tri in mesh.loop_triangles:
                mat_index = tri.material_index if tri.material_index < len(obj.material_slots) else 0
                record = grouped[mat_index]
                key_map = vert_keys[mat_index]
                mat_uv0, mat_uv1 = uv_layers_by_mat.get(mat_index, (None, None))
                _, world_uv_scale = world_uv_by_mat.get(mat_index, (False, 0.1))

                for loop_index in tri.loops:
                    loop = mesh.loops[loop_index]
                    vertex_index = loop.vertex_index
                    vertex = mesh.vertices[vertex_index]
                    world_co = obj.matrix_world @ vertex.co

                    # Preserve split/custom normals so hard edges survive even when
                    # many surfaces share the same atlas material.
                    loop_normal = getattr(loop, "normal", None)
                    source_normal = loop_normal if loop_normal is not None else vertex.normal
                    world_no = _world_normal(obj, source_normal)

                    uv0_val = (
                        tuple(mat_uv0.data[loop_index].uv)
                        if mat_uv0
                        else _auto_uv_from_world(world_co, world_no, scale=world_uv_scale)
                    )
                    uv1_val = tuple(mat_uv1.data[loop_index].uv) if mat_uv1 else uv0_val

                    # Round to float32 precision for reliable deduplication of shared verts.
                    key: tuple = (
                        round(world_co.x, 6), round(world_co.y, 6), round(world_co.z, 6),
                        round(world_no.x, 6), round(world_no.y, 6), round(world_no.z, 6),
                        round(float(uv0_val[0]), 6), round(float(uv0_val[1]), 6),
                        round(float(uv1_val[0]), 6), round(float(uv1_val[1]), 6),
                    )

                    if key not in key_map:
                        key_map[key] = len(record["vertices"])
                        record["vertices"].append([float(world_co.x), float(world_co.y), float(world_co.z)])
                        record["normals"].append([float(world_no.x), float(world_no.y), float(world_no.z)])
                        record["uv0"].append([float(uv0_val[0]), float(uv0_val[1])])
                        record["uv1"].append([float(uv1_val[0]), float(uv1_val[1])])

                    record["indices"].append(key_map[key])

            for mat_index, record in grouped.items():
                if record["indices"]:
                    meshes_out.extend(_split_mesh_record_by_triangle_budget(record))
                slot = obj.material_slots[mat_index] if mat_index < len(obj.material_slots) else None
                material = slot.material if slot else None
                if material and material.name not in seen_material_names:
                    seen_material_names.add(material.name)
                    material_rows.append({"object": obj.name, "material": material.name, "images": []})
        finally:
            eval_obj.to_mesh_clear()

    return meshes_out, material_rows


def _hydrate_material_rows(context: bpy.types.Context, material_rows: list[dict], output_dir: Path) -> list[dict]:
    generated_dir = output_dir / "generated_source_textures"
    hydrated: list[dict] = []
    for row in material_rows:
        material = bpy.data.materials.get(row["material"])
        if material is None:
            continue
        images = _material_image_entries(material, generated_dir)
        if not images:
            fallback = _fallback_material_texture(material, generated_dir)
            images = [{"name": fallback.name, "filepath": str(fallback)}]
        hydrated.append({
            "object": row["object"],
            "material": row["material"],
            "template_material_name": _material_template_name_override(material) or _template_semantic_name(material.name),
            "blend_mode": _material_blend_mode(material),
            "alpha_discard_threshold": _material_float_override(material, "ootp_alpha_discard_threshold"),
            "images": images,
        })
    return hydrated


def export_current_scene_to_pod_package(
    context: bpy.types.Context,
    output_pod_path: str | Path,
    template_pod_path: str | Path,
    *,
    selected_only: bool = False,
    copy_template_sidecars: bool = True,
    progress_cb=None,
    debug_output: bool = False,
) -> dict:
    output_pod = Path(output_pod_path)
    template_pod = Path(template_pod_path)

    _emit_progress(progress_cb, 0.02, "Validating export inputs")

    if not template_pod.exists():
        raise PODExportError(f"Template POD not found: {template_pod}")

    _ensure_export_location_is_safe(output_pod, template_pod)
    _ensure_output_name_matches_template(output_pod, template_pod, copy_template_sidecars)

    target_objects = _iter_target_objects(context, selected_only)
    if not target_objects:
        raise PODExportError("No mesh objects found to export")

    output_dir = output_pod.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    _emit_progress(progress_cb, 0.10, "Copying template sidecars")
    if copy_template_sidecars:
        for item in template_pod.parent.iterdir():
            if item.resolve() == template_pod.resolve():
                continue
            dest_name = item.name
            if item.stem == template_pod.stem:
                dest_name = f"{output_pod.stem}{item.suffix}"
            dest = output_dir / dest_name
            if item.is_dir():
                if item.name.lower() == "textures":
                    continue
                if not dest.exists():
                    shutil.copytree(item, dest)
            else:
                if item.suffix.lower() == ".pfx":
                    continue
                if not dest.exists():
                    shutil.copy2(item, dest)

    _emit_progress(progress_cb, 0.28, "Collecting scene meshes")
    meshes, raw_material_rows = _collect_scene_meshes(context, target_objects)
    scene_json = {"meshes": meshes}

    _emit_progress(progress_cb, 0.42, "Resolving material textures")
    hydrated_rows = _hydrate_material_rows(context, raw_material_rows, output_dir)

    if debug_output:
        raw_dump_path = output_dir / f"{output_pod.stem}_material_dump_raw.json"
        raw_dump_path.write_text(json.dumps(hydrated_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    _emit_progress(progress_cb, 0.58, "Building material package")
    # Pass data directly — no intermediate file required.
    material_plan = build_material_package(hydrated_rows, output_dir)
    material_name_map = {row["source_material_name"]: row["name"] for row in material_plan["materials"]}

    for mesh in scene_json["meshes"]:
        mesh["material_name"] = material_name_map.get(mesh["material_name"], mesh["material_name"])

    merged_scene = {
        "meshes": scene_json["meshes"],
        "materials": material_plan["materials"],
    }

    if debug_output:
        merged_scene_path = output_dir / f"{output_pod.stem}_scene_with_materials.json"
        merged_scene_path.write_text(json.dumps(merged_scene, ensure_ascii=False, indent=2), encoding="utf-8")

    _emit_progress(progress_cb, 0.78, "Building POD scene")

    # Write the merged scene to a temp file for build_fresh_pod_from_scene_json.
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(merged_scene, tmp, ensure_ascii=False)
        tmp_path = Path(tmp.name)

    try:
        build_result = build_fresh_pod_from_scene_json(template_pod, tmp_path, output_pod)
    finally:
        tmp_path.unlink(missing_ok=True)

    manifest = {
        "output_pod": str(output_pod),
        "template_pod": str(template_pod),
        "selected_only": selected_only,
        "copy_template_sidecars": copy_template_sidecars,
        "mesh_count": len(merged_scene["meshes"]),
        "material_count": len(merged_scene["materials"]),
        "build_result": build_result,
    }
    manifest_path = output_dir / f"{output_pod.stem}_export_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    _emit_progress(progress_cb, 1.0, "Export complete")
    return manifest
