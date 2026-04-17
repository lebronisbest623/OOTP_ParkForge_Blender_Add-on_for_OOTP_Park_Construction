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
    if selected_only and context.selected_objects:
        return [obj for obj in context.selected_objects if obj.type == "MESH"]
    return [obj for obj in context.scene.objects if obj.type == "MESH"]


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
    output_dir = output_pod.parent
    if _is_protected_output_dir(output_dir):
        safe_dir = _recommended_safe_output_dir()
        raise PODExportError(
            "Refusing to export directly into a protected folder. "
            f"Current output is under '{output_dir}'. "
            f"Export to a writable staging folder like '{safe_dir}', then copy the finished package into OOTP."
        )

    if _normalized_path(output_dir) == _normalized_path(template_pod.parent) and _is_protected_output_dir(template_pod.parent):
        safe_dir = _recommended_safe_output_dir()
        raise PODExportError(
            "Template folder is inside a protected OOTP install directory. "
            f"Export to a staging folder like '{safe_dir}' instead of writing back into the live game folder."
        )


def _ensure_output_name_matches_template(output_pod: Path, template_pod: Path, copy_template_sidecars: bool) -> None:
    if output_pod.stem == template_pod.stem:
        return
    if not copy_template_sidecars:
        return
    raise PODExportError(
        "Output POD name must match the template stadium name when template sidecars are copied. "
        f"Template is '{template_pod.stem}.pod' but output is '{output_pod.name}'. "
        f"Use '{template_pod.stem}.pod' as the export filename or disable template sidecar copying."
    )


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


def _material_image_entries(material: bpy.types.Material, generated_dir: Path) -> list[dict]:
    if not material.use_nodes or not material.node_tree:
        return []

    images: list[dict] = []
    seen: set[str] = set()
    for node in material.node_tree.nodes:
        if node.type != "TEX_IMAGE" or not getattr(node, "image", None):
            continue
        image = node.image
        image_path = _resolve_image_path(image, generated_dir)
        if image_path is None:
            continue
        key = str(image_path).lower()
        if key in seen:
            continue
        seen.add(key)
        images.append({"name": image.name, "filepath": str(image_path)})
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


def _material_blend_mode(material: bpy.types.Material) -> str:
    name = _template_semantic_name(material.name)
    if name == "ground":
        return "ground"
    if name == "background":
        return "stock_background"
    if name == "stand_lighting":
        return "stock_lighting"
    if (
        name.startswith("spectator")
        or "spectator" in name
        or "attendance" in name
        or "audience" in name
        or "seating" in name
    ):
        return "alpha_shadow"
    if name.startswith("ootp_scoreboard") or "scoreboard" in name or name == "screen":
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
            # Per-group vertex deduplication: key = (pos, normal, uv0, uv1) → index
            vert_keys: dict[int, dict[tuple, int]] = {}

            for tri in mesh.loop_triangles:
                mat_index = tri.material_index if tri.material_index < len(obj.material_slots) else 0
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

                record = grouped[mat_index]
                key_map = vert_keys[mat_index]

                for loop_index in tri.loops:
                    vertex_index = mesh.loops[loop_index].vertex_index
                    vertex = mesh.vertices[vertex_index]
                    world_co = obj.matrix_world @ vertex.co
                    world_no = _world_normal(obj, vertex.normal)

                    uv0_val = tuple(uv0_layer.data[loop_index].uv) if uv0_layer else (0.0, 0.0)
                    uv1_val = tuple(uv1_layer.data[loop_index].uv) if uv1_layer else uv0_val

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
                    meshes_out.append(record)
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
            "template_material_name": _template_semantic_name(material.name),
            "blend_mode": _material_blend_mode(material),
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
            dest = output_dir / item.name
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
