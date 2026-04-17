from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
from pathlib import Path

import bpy
from mathutils import Matrix, Quaternion, Vector

try:
    from . import pfx_parser, pod_parser
except ImportError:
    import pfx_parser  # type: ignore
    import pod_parser  # type: ignore


ADDON_PACKAGE = __package__ or Path(__file__).resolve().parent.name
ADDON_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = ADDON_DIR.parents[2]
COMPRESSONATOR_ROOT = WORKSPACE_ROOT / "tools" / "compressonatorcli"
TEXTURE_CACHE_DIR = WORKSPACE_ROOT / "temp" / "pod_texture_cache"
ROOT_AXIS_CORRECTION = Matrix.Rotation(math.radians(90.0), 4, "X")


def _node_matrix(node: pod_parser.PODNode) -> Matrix:
    if node.matrix:
        return Matrix(
            (
                node.matrix[0:4],
                node.matrix[4:8],
                node.matrix[8:12],
                node.matrix[12:16],
            )
        ).transposed()

    loc = Vector(node.translation or (0.0, 0.0, 0.0))
    rot = Quaternion((1.0, 0.0, 0.0, 0.0))
    if node.rotation_xyzw:
        x, y, z, w = node.rotation_xyzw
        rot = Quaternion((w, x, y, z))
    scale_vals = node.scale or (1.0, 1.0, 1.0)
    scale = Matrix.Diagonal((scale_vals[0], scale_vals[1], scale_vals[2], 1.0))
    return Matrix.Translation(loc) @ rot.to_matrix().to_4x4() @ scale


def _ootp_to_blender_xyz(values: tuple[float, float, float] | list[float]) -> tuple[float, float, float]:
    # Kept as a helper for debugging/reference, but import now applies a single
    # scene-level axis correction root so mesh coordinates can stay in raw POD space.
    x, y, z = float(values[0]), float(values[1]), float(values[2])
    return (x, -z, y)


def _is_close_tuple(values, target, eps: float = 1e-4) -> bool:
    return all(abs(float(a) - float(b)) <= eps for a, b in zip(values, target))


def _should_bake_node_transforms(scene: pod_parser.PODScene) -> bool:
    mesh_nodes = [node for node in scene.nodes if 0 <= node.object_index < len(scene.meshes)]
    if not mesh_nodes:
        return True

    if any(node.matrix is not None for node in mesh_nodes):
        return True

    first_rot = mesh_nodes[0].rotation_xyzw or (0.0, 0.0, 0.0, -1.0)
    common_rotation = all(
        _is_close_tuple(node.rotation_xyzw or (0.0, 0.0, 0.0, -1.0), first_rot)
        for node in mesh_nodes
    )
    zero_translation = all(
        _is_close_tuple(node.translation or (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        for node in mesh_nodes
    )
    unit_scale = all(
        _is_close_tuple(node.scale or (1.0, 1.0, 1.0), (1.0, 1.0, 1.0))
        for node in mesh_nodes
    )

    if common_rotation and zero_translation and unit_scale:
        if not _is_close_tuple(first_rot, (0.0, 0.0, 0.0, -1.0)):
            return False

    return True


def _has_shared_mesh_axis_correction(scene: pod_parser.PODScene) -> bool:
    mesh_nodes = [node for node in scene.nodes if 0 <= node.object_index < len(scene.meshes)]
    if not mesh_nodes:
        return False
    if any(node.matrix is not None for node in mesh_nodes):
        return False

    first_rot = mesh_nodes[0].rotation_xyzw or (0.0, 0.0, 0.0, -1.0)
    if _is_close_tuple(first_rot, (0.0, 0.0, 0.0, -1.0)):
        return False

    common_rotation = all(
        _is_close_tuple(node.rotation_xyzw or (0.0, 0.0, 0.0, -1.0), first_rot)
        for node in mesh_nodes
    )
    zero_translation = all(
        _is_close_tuple(node.translation or (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        for node in mesh_nodes
    )
    unit_scale = all(
        _is_close_tuple(node.scale or (1.0, 1.0, 1.0), (1.0, 1.0, 1.0))
        for node in mesh_nodes
    )
    return common_rotation and zero_translation and unit_scale


def addon_preferences():
    try:
        return bpy.context.preferences.addons[ADDON_PACKAGE].preferences
    except Exception:
        return None


def _scene_cli_override() -> Path | None:
    try:
        scene = bpy.context.scene
    except Exception:
        return None
    if not scene:
        return None
    raw = getattr(scene, "ootp_pod_compressonator_cli_path", "")
    if not raw:
        return None
    try:
        return Path(bpy.path.abspath(raw))
    except Exception:
        return Path(raw)


def _iter_ancestor_roots(path: Path, depth: int = 6) -> list[Path]:
    roots: list[Path] = []
    current = path.resolve()
    for _ in range(depth):
        roots.append(current)
        if current.parent == current:
            break
        current = current.parent
    return roots


def _looks_like_compressonator(path: Path) -> bool:
    if not path.exists() or path.name.lower() != "compressonatorcli.exe":
        return False
    sidecars = (
        path.with_name("ktx.dll"),
        path.with_name("qt.conf"),
        path.parent / "license",
    )
    return any(item.exists() for item in sidecars)


def _search_cli_under(root: Path) -> list[Path]:
    patterns = (
        "compressonatorcli.exe",
        "third_party/compressonatorcli/**/compressonatorcli.exe",
        "tools/compressonatorcli/**/compressonatorcli.exe",
        "**/compressonatorcli.exe",
    )
    found: list[Path] = []
    if not root.exists():
        return found
    for pattern in patterns:
        try:
            found.extend(root.glob(pattern))
        except Exception:
            continue
    return found


def find_compressonator_cli() -> Path | None:
    prefs = addon_preferences()
    candidates: list[Path] = []

    scene_override = _scene_cli_override()
    if scene_override:
        candidates.append(scene_override)

    if prefs and getattr(prefs, "compressonator_cli_path", ""):
        candidates.append(Path(prefs.compressonator_cli_path))

    for env_name in ("OOTP_POD_COMPRESSONATOR", "COMPRESSONATORCLI_PATH"):
        env_value = os.environ.get(env_name)
        if env_value:
            candidates.append(Path(env_value))

    for exe_name in ("compressonatorcli.exe", "CompressonatorCLI.exe"):
        which_path = shutil.which(exe_name)
        if which_path:
            candidates.append(Path(which_path))

    addon_search_roots = []
    addon_search_roots.extend(_iter_ancestor_roots(ADDON_DIR, depth=5))
    addon_search_roots.extend(_iter_ancestor_roots(WORKSPACE_ROOT, depth=3))
    addon_search_roots.append(WORKSPACE_ROOT / "temp" / "ootp_pod_addon_distribution")
    addon_search_roots.append(Path.home() / "Downloads")
    for root in addon_search_roots:
        candidates.extend(_search_cli_under(root))

    if COMPRESSONATOR_ROOT.exists():
        candidates.extend(sorted(COMPRESSONATOR_ROOT.rglob("compressonatorcli.exe")))

    documents = Path.home() / "Documents"
    if documents.exists():
        candidates.extend(
            sorted(
                documents.glob(
                    "Out of the Park Developments/OOTP Baseball */saved_games/*/tools/compressonatorcli/**/compressonatorcli.exe"
                )
            )
        )

    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if _looks_like_compressonator(resolved):
            return resolved
    return None


def _decoded_cache_path(texture_path: Path) -> Path:
    stat = texture_path.stat()
    cache_key = f"{texture_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()
    return TEXTURE_CACHE_DIR / f"{texture_path.stem}_{digest}.png"


def _decode_ktx_to_png(texture_path: Path) -> Path | None:
    cache_path = _decoded_cache_path(texture_path)
    if cache_path.exists():
        return cache_path

    cli_path = find_compressonator_cli()
    if cli_path is None:
        return None

    TEXTURE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    command = [str(cli_path), str(texture_path), str(cache_path)]

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception:
        return None

    if completed.returncode != 0 or not cache_path.exists():
        return None
    return cache_path


def _resolve_blender_image_path(texture_path: Path) -> Path | None:
    if texture_path.suffix.lower() == ".ktx":
        decoded = _decode_ktx_to_png(texture_path)
        return decoded if decoded and decoded.exists() else None
    return texture_path


def _resolve_texture_path(base_dir: Path, texture_ref: str) -> Path | None:
    if not texture_ref:
        return None

    raw = Path(texture_ref.replace("\\", "/"))
    candidates: list[Path] = []

    if raw.is_absolute():
        candidates.append(raw)
        candidates.append(base_dir / raw.name)
    else:
        candidates.append(base_dir / raw)
        candidates.append(base_dir / raw.name)
        candidates.append(base_dir / "textures" / raw.name)

    stem = raw.stem if raw.suffix else raw.name
    exts = [".png", ".webp", ".jpg", ".jpeg", ".tga", ".dds", ".ktx"]
    for ext in exts:
        if raw.parent and str(raw.parent) not in (".", ""):
            candidates.append(base_dir / raw.parent / f"{stem}{ext}")
        candidates.append(base_dir / f"{stem}{ext}")
        candidates.append(base_dir / "textures" / f"{stem}{ext}")

    seen = set()
    for candidate in candidates:
        norm = str(candidate).lower()
        if norm in seen:
            continue
        seen.add(norm)
        if candidate.exists():
            return candidate
    return None


def _resolve_pfx_path(base_dir: Path, material_name: str) -> Path | None:
    if not material_name:
        return None
    direct = base_dir / f"{material_name}.pfx"
    if direct.exists():
        return direct

    lowered = material_name.lower()
    for candidate in base_dir.glob("*.pfx"):
        if candidate.stem.lower() == lowered:
            return candidate
    startswith_matches = sorted(candidate for candidate in base_dir.glob("*.pfx") if candidate.stem.lower().startswith(lowered))
    if len(startswith_matches) == 1:
        return startswith_matches[0]
    return None


def _semantic_to_uv_layer_name(semantic: str | None) -> str | None:
    if not semantic:
        return None
    semantic = semantic.upper()
    if semantic == "UV0":
        return "UVMap"
    if semantic == "UV1":
        return "UV1"
    return None


def _load_image_for_blender(path: Path) -> bpy.types.Image:
    blender_tex_path = _resolve_blender_image_path(path)
    if blender_tex_path is None:
        raise FileNotFoundError(str(path))
    return bpy.data.images.load(str(blender_tex_path), check_existing=True)


def _build_material_from_pfx(
    mat: bpy.types.Material,
    base_dir: Path,
    pfx_material: pfx_parser.PFXMaterial,
) -> bool:
    effect = pfx_parser.choose_effect(pfx_material)
    if effect is None:
        return False

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = next(node for node in nodes if node.type == "BSDF_PRINCIPLED")

    sampler_order = pfx_parser.used_sampler_sequence(pfx_material, effect)
    if not sampler_order:
        sampler_order = [uniform_name for uniform_name, semantic in effect.uniforms.items() if semantic.upper().startswith("TEXTURE")]

    sampler_semantics = pfx_parser.sampler_uv_semantics(pfx_material, effect)
    texture_nodes: dict[str, bpy.types.Node] = {}
    chain_socket = None
    node_x = -700
    node_y = 120

    for sampler_name in sampler_order:
        uniform_semantic = effect.uniforms.get(sampler_name, "")
        if not uniform_semantic.upper().startswith("TEXTURE"):
            continue
        try:
            texture_unit = int(uniform_semantic.upper().replace("TEXTURE", ""))
        except ValueError:
            continue

        texture_name = effect.texture_units.get(texture_unit)
        if not texture_name:
            continue
        texture_def = pfx_material.textures.get(texture_name)
        if not texture_def or not texture_def.path:
            continue
        texture_path = _resolve_texture_path(base_dir, texture_def.path)
        if texture_path is None:
            continue

        try:
            image = _load_image_for_blender(texture_path)
        except Exception:
            continue

        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.location = (node_x, node_y)
        tex_node.image = image
        texture_nodes[sampler_name] = tex_node

        uv_layer_name = _semantic_to_uv_layer_name(sampler_semantics.get(sampler_name))
        if uv_layer_name:
            uv_node = nodes.new("ShaderNodeUVMap")
            uv_node.location = (node_x - 220, node_y)
            uv_node.uv_map = uv_layer_name
            links.new(uv_node.outputs["UV"], tex_node.inputs["Vector"])

        if chain_socket is None:
            chain_socket = tex_node.outputs["Color"]
        else:
            mix_node = nodes.new("ShaderNodeMixRGB")
            mix_node.blend_type = "MULTIPLY"
            mix_node.inputs["Fac"].default_value = 1.0
            mix_node.location = (node_x + 220, node_y)
            links.new(chain_socket, mix_node.inputs["Color1"])
            links.new(tex_node.outputs["Color"], mix_node.inputs["Color2"])
            chain_socket = mix_node.outputs["Color"]

        node_y -= 260

    if effect.name.lower() == "grass_new":
        base_node = texture_nodes.get("texUnit1")
        detail_node = texture_nodes.get("texUnit0")
        for node in nodes:
            if node.type == "UVMAP" and getattr(node, "uv_map", "") == "UV1":
                for output in node.outputs:
                    for link in list(output.links):
                        if link.to_node == base_node and link.to_socket.name == "Vector":
                            links.remove(link)
        if base_node is not None:
            uv0_node = nodes.new("ShaderNodeUVMap")
            uv0_node.location = (-920, -120)
            uv0_node.uv_map = "UVMap"
            links.new(uv0_node.outputs["UV"], base_node.inputs["Vector"])
            chain_socket = base_node.outputs["Color"]
            if detail_node is not None:
                preview_mix = nodes.new("ShaderNodeMixRGB")
                preview_mix.blend_type = "MULTIPLY"
                preview_mix.inputs["Fac"].default_value = 0.25
                preview_mix.location = (-120, -260)
                links.new(base_node.outputs["Color"], preview_mix.inputs["Color1"])
                links.new(detail_node.outputs["Color"], preview_mix.inputs["Color2"])
                chain_socket = preview_mix.outputs["Color"]

    if chain_socket is not None:
        links.new(chain_socket, bsdf.inputs["Base Color"])

    alpha_source = pfx_parser.alpha_discard_source(pfx_material, effect)
    if alpha_source:
        sampler_name, threshold = alpha_source
        tex_node = texture_nodes.get(sampler_name)
        if tex_node:
            links.new(tex_node.outputs["Alpha"], bsdf.inputs["Alpha"])
            mat.blend_method = "CLIP"
            mat.alpha_threshold = threshold
            if hasattr(mat, "shadow_method"):
                mat.shadow_method = "CLIP"

    return True


def _build_materials(scene: pod_parser.PODScene, pod_path: Path) -> list[bpy.types.Material]:
    base_dir = pod_path.parent
    built: list[bpy.types.Material] = []

    for index, material in enumerate(scene.materials):
        mat_name = material.name or f"material_{index:03d}"
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        for node in list(nodes):
            if node.type != "OUTPUT_MATERIAL":
                nodes.remove(node)

        output = next(node for node in nodes if node.type == "OUTPUT_MATERIAL")
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

        built_from_pfx = False
        pfx_path = _resolve_pfx_path(base_dir, mat_name)
        if pfx_path:
            try:
                pfx_material = pfx_parser.parse_pfx(pfx_path)
                built_from_pfx = _build_material_from_pfx(mat, base_dir, pfx_material)
            except Exception:
                built_from_pfx = False

        if not built_from_pfx:
            tex_path = None
            if 0 <= material.diffuse_texture_index < len(scene.textures):
                tex_ref = scene.textures[material.diffuse_texture_index].filename
                tex_path = _resolve_texture_path(base_dir, tex_ref)

            if tex_path:
                try:
                    image = _load_image_for_blender(tex_path)
                    tex_node = nodes.new("ShaderNodeTexImage")
                    tex_node.location = (-360, 40)
                    tex_node.image = image
                    links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
                    if "Alpha" in tex_node.outputs:
                        links.new(tex_node.outputs["Alpha"], bsdf.inputs["Alpha"])
                except Exception:
                    pass

        lowered = mat_name.lower()
        if "alphablend" in lowered:
            mat.blend_method = "BLEND"
            if hasattr(mat, "shadow_method"):
                mat.shadow_method = "CLIP"
        elif "alphatest" in lowered:
            mat.blend_method = "CLIP"
            if hasattr(mat, "shadow_method"):
                mat.shadow_method = "CLIP"

        built.append(mat)

    return built


def _build_mesh_data(name: str, mesh: pod_parser.PODMesh) -> bpy.types.Mesh:
    vertices = pod_parser.mesh_vertices(mesh)
    if not vertices:
        raise pod_parser.PODParseError(f"Mesh '{name}' has no readable vertices")

    if mesh.indices:
        faces = [tuple(mesh.indices[i:i + 3]) for i in range(0, len(mesh.indices), 3) if len(mesh.indices[i:i + 3]) == 3]
    else:
        faces = [tuple(range(i, i + 3)) for i in range(0, len(vertices), 3) if i + 2 < len(vertices)]

    blender_mesh = bpy.data.meshes.new(name)
    blender_mesh.from_pydata(vertices, [], faces)
    blender_mesh.update()

    for uv_index, layer_name in ((0, "UVMap"), (1, "UV1")):
        uvs = pod_parser.mesh_uvs(mesh, uv_index)
        if not uvs or not blender_mesh.polygons:
            continue
        uv_layer = blender_mesh.uv_layers.new(name=layer_name)
        loop_uvs = uv_layer.data
        for poly in blender_mesh.polygons:
            for loop_index in poly.loop_indices:
                vert_index = blender_mesh.loops[loop_index].vertex_index
                if vert_index < len(uvs):
                    u, v = uvs[vert_index]
                    loop_uvs[loop_index].uv = (u, 1.0 - v)

    return blender_mesh


def import_pod(context: bpy.types.Context, filepath: str, create_parent_empties: bool = True) -> set[str]:
    pod_path = Path(filepath)
    scene = pod_parser.parse_pod(pod_path)
    collection = bpy.data.collections.new(pod_path.stem)
    context.scene.collection.children.link(collection)
    context.scene["ootp_pod_last_imported_template_path"] = str(pod_path)
    root_obj = bpy.data.objects.new(f"{pod_path.stem}__ROOT", None)
    root_obj.empty_display_type = "PLAIN_AXES"
    root_obj.matrix_local = ROOT_AXIS_CORRECTION
    root_obj["ootp_template_pod_path"] = str(pod_path)
    collection.objects.link(root_obj)
    materials = _build_materials(scene, pod_path)

    mesh_data_blocks: list[bpy.types.Mesh] = []
    for mesh_index, mesh in enumerate(scene.meshes):
        mesh_name = mesh.name or f"mesh_{mesh_index:03d}"
        mesh_data_blocks.append(_build_mesh_data(mesh_name, mesh))

    created_objects: list[bpy.types.Object] = []
    node_objects: list[bpy.types.Object | None] = [None] * len(scene.nodes)

    bake_node_transforms = _should_bake_node_transforms(scene)
    ignore_shared_mesh_axis_correction = _has_shared_mesh_axis_correction(scene)

    if scene.nodes:
        for idx, node in enumerate(scene.nodes):
            object_name = node.name or f"node_{idx:03d}"
            is_mesh_node = 0 <= node.object_index < len(mesh_data_blocks)
            if is_mesh_node:
                mesh_data = mesh_data_blocks[node.object_index]
                if bake_node_transforms and not ignore_shared_mesh_axis_correction:
                    mesh_data = mesh_data.copy()
                    mesh_data.transform(_node_matrix(node))
                    mesh_data.update()
                obj = bpy.data.objects.new(object_name, mesh_data)
            elif create_parent_empties:
                obj = bpy.data.objects.new(object_name, None)
                obj.empty_display_type = "PLAIN_AXES"
            else:
                continue

            collection.objects.link(obj)
            if not is_mesh_node:
                obj.matrix_local = _node_matrix(node)
            elif not ignore_shared_mesh_axis_correction and not bake_node_transforms:
                obj.matrix_local = _node_matrix(node)
            if obj.type == "MESH" and 0 <= node.material_index < len(materials):
                if obj.data.users > 1:
                    obj.data = obj.data.copy()
                obj.data.materials.clear()
                obj.data.materials.append(materials[node.material_index])
            node_objects[idx] = obj
            created_objects.append(obj)

        for idx, node in enumerate(scene.nodes):
            obj = node_objects[idx]
            if obj and 0 <= node.parent_index < len(node_objects):
                parent = node_objects[node.parent_index]
                if parent and (obj.type != "MESH" or (not ignore_shared_mesh_axis_correction and not bake_node_transforms)):
                    obj.parent = parent

        for obj in created_objects:
            if obj.parent is None:
                obj.parent = root_obj
    else:
        for mesh_index, mesh_data in enumerate(mesh_data_blocks):
            obj = bpy.data.objects.new(f"mesh_{mesh_index:03d}", mesh_data)
            collection.objects.link(obj)
            if mesh_index < len(materials):
                obj.data.materials.append(materials[mesh_index])
            obj.parent = root_obj
            created_objects.append(obj)

    if created_objects:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in created_objects:
            obj.select_set(True)
        context.view_layer.objects.active = created_objects[0]

    return {"FINISHED"}
