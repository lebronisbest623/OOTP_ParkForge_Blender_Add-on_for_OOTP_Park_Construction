import os
import re
import shutil
from pathlib import Path

import bpy


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return cleaned or "texture"


def export_ootp_obj_package(output_dir: str, base_name: str = "ootp_export") -> dict:
    out_dir = Path(output_dir)
    baked_dir = out_dir / "baked"
    out_dir.mkdir(parents=True, exist_ok=True)
    baked_dir.mkdir(parents=True, exist_ok=True)

    obj_path = out_dir / f"{base_name}.obj"
    mtl_path = out_dir / f"{base_name}.mtl"

    for target in [obj_path, mtl_path]:
        if target.exists():
            target.unlink()

    bpy.ops.wm.obj_export(
        filepath=str(obj_path),
        export_selected_objects=False,
        export_animation=False,
        export_materials=True,
        export_uv=True,
        export_normals=True,
        export_triangulated_mesh=False,
        path_mode="AUTO",
    )

    if not mtl_path.exists():
        raise RuntimeError(f"Expected MTL not found: {mtl_path}")

    copied = {}
    rewritten_lines = []

    for line in mtl_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped.startswith(("map_Kd ", "map_d ", "map_Ka ", "map_Ke ")):
            rewritten_lines.append(line)
            continue

        key, source = stripped.split(" ", 1)
        source = source.strip()
        source_path = Path(source)
        if not source_path.exists():
            rewritten_lines.append(line)
            continue

        if source_path not in copied:
            safe_stem = _safe_name(source_path.stem)
            ext = source_path.suffix.lower() or ".png"
            baked_name = f"{safe_stem}{ext}"
            baked_path = baked_dir / baked_name
            counter = 1
            while baked_path.exists() and baked_path.resolve() != source_path.resolve():
                baked_name = f"{safe_stem}_{counter}{ext}"
                baked_path = baked_dir / baked_name
                counter += 1
            if not baked_path.exists():
                shutil.copy2(source_path, baked_path)
            copied[source_path] = baked_name

        rewritten_lines.append(f"{key} baked/{copied[source_path]}")

    mtl_path.write_text("\n".join(rewritten_lines) + "\n", encoding="utf-8")

    return {
        "obj": str(obj_path),
        "mtl": str(mtl_path),
        "baked_dir": str(baked_dir),
        "copied_texture_count": len(copied),
        "copied_textures": [str(p) for p in copied.keys()],
    }
