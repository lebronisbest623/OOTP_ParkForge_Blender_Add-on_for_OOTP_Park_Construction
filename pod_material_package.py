from __future__ import annotations

import json
import re
import shutil
import sys
import struct
import zlib
from pathlib import Path

try:
    from PIL import Image
except Exception:
    Image = None


class PODMaterialPackageError(RuntimeError):
    pass


def _write_png_rgba(path: Path, width: int, height: int, rgba_rows: list[bytes]) -> None:
    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + chunk_type
            + data
            + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + row for row in rgba_rows)
    compressed = zlib.compress(raw, level=9)
    png_bytes = signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
    path.write_bytes(png_bytes)


def _sanitize_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    return base or "Material"


def _canonical_texture_filename(src: Path) -> str:
    stem = src.stem
    ext = src.suffix.lower() or ".png"

    # ParkForge often receives textures from decoded cache files like:
    #   Stand_day_<sha1>.png
    #   Stand_LM_day_<sha1>_01.png
    # Normalize those back to stock-like names so OOTP day/night and
    # weather transitions can still locate the expected texture families.
    stem = re.sub(r"_[0-9a-f]{40}(?:_\d{2})?$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_(\d{2})$", "", stem)
    stem = _sanitize_name(stem)
    return f"{stem}{ext}"


def _copy_texture(src_path: str, textures_dir: Path, used_names: set[str]) -> str:
    src = Path(src_path)
    if not src.exists():
        raise PODMaterialPackageError(f"Texture source not found: {src}")
    candidate = _canonical_texture_filename(src)
    serial = 1
    while candidate.lower() in used_names:
        stem = Path(candidate).stem
        ext = Path(candidate).suffix.lower() or ".png"
        stem = re.sub(r"_\d{2}$", "", stem)
        candidate = f"{stem}_{serial:02d}{ext}"
        serial += 1
    used_names.add(candidate.lower())
    dst = textures_dir / candidate
    shutil.copy2(src, dst)
    return f"textures/{candidate}"


def _write_white_lightmap(textures_dir: Path) -> str:
    name = "_white_lm.png"
    dst = textures_dir / name
    # OOTP multiplies non-emissive materials by the secondary lightmap.
    # Use a tiny, known-good opaque white PNG so "no lightmap" behaves as identity.
    if Image is not None:
        Image.new("RGBA", (1, 1), (255, 255, 255, 255)).save(dst, format="PNG")
    else:
        _write_png_rgba(dst, 1, 1, [bytes((255, 255, 255, 255))])
    return f"textures/{name}"


_PFX_VERTEX_SHADER = """\
[VERTEXSHADER]
\tNAME \t\tc2u_alpha_shadow.vert
\t[GLSL_CODE]
attribute vec4 vPosition;
attribute vec2 vTexCoord;
attribute vec2 vTexCoord1;
varying vec2 v_texcoord;
varying vec2 v_texcoord1;
uniform mat4 p_matrix;
void main(void)
{
\tv_texcoord = vTexCoord;
\tv_texcoord1 = vTexCoord1;
\tgl_Position = p_matrix * vPosition;
}
\t[/GLSL_CODE]
[/VERTEXSHADER]"""

_PFX_FRAGMENT_SHADER_BODY = """\
uniform sampler2D texUnit0;
uniform sampler2D texUnit1;
varying vec2 v_texcoord;
varying vec2 v_texcoord1;
void main(void)
{
%(discard_line)s
\tgl_FragColor = texture2D(texUnit0, v_texcoord) * texture2D(texUnit1, v_texcoord1);
}"""


def _pfx_grass_text(diffuse_rel: str, ground_rel: str, dirt_rel: str) -> str:
    return (
        "[HEADER]\n"
        "\tVERSION\t\t02.00.00.00\n"
        "\tDESCRIPTION lightmap\n"
        "\tCOPYRIGHT\tOOTP\n"
        "[/HEADER]\n\n"
        "[TEXTURE]\n"
        "\tNAME \t\t\tdiffuse_tex\n"
        f"\tPATH\t\t\t{diffuse_rel}\n"
        "\tMINIFICATION\tLINEAR\n"
        "\tMAGNIFICATION\tLINEAR\n"
        "\tMIPMAP\t\t\tNONE\n"
        "[/TEXTURE]\n\n"
        "[TEXTURE]\n"
        "\tNAME \t\t\tground_tex\n"
        f"\tPATH\t\t\t{ground_rel}\n"
        "\tMINIFICATION\tLINEAR\n"
        "\tMAGNIFICATION\tLINEAR\n"
        "\tMIPMAP\t\t\tNONE\n"
        "[/TEXTURE]\n\n"
        "[TEXTURE]\n"
        "\tNAME \t\t\tdirt_tex\n"
        f"\tPATH\t\t\t{dirt_rel}\n"
        "\tMINIFICATION\tLINEAR\n"
        "\tMAGNIFICATION\tLINEAR\n"
        "\tMIPMAP\t\t\tNONE\n"
        "[/TEXTURE]\n\n"
        "[VERTEXSHADER]\n"
        "\tNAME \t\tgrass_new.vert\n"
        "\t[GLSL_CODE]\n"
        "attribute vec4 vPosition;\n"
        "attribute vec2 vTexCoord;\n"
        "attribute vec2 vTexCoord1;\n"
        "varying vec2 v_texcoord;\n"
        "varying vec2 v_texcoord1;\n"
        "uniform mat4 p_matrix;\n"
        "void main(void)\n"
        "{\n"
        "\tv_texcoord = vTexCoord;\n"
        "\tv_texcoord1 = vTexCoord1;\n"
        "\tgl_Position = p_matrix * vPosition;\n"
        "}\n"
        "\t[/GLSL_CODE]\n"
        "[/VERTEXSHADER]\n\n"
        "[FRAGMENTSHADER]\n"
        "\tNAME \t\tgrass_new.frag\n"
        "\t[GLSL_CODE]\n"
        "uniform sampler2D texUnit0;\n"
        "uniform sampler2D texUnit1;\n"
        "varying vec2 v_texcoord;\n"
        "varying vec2 v_texcoord1;\n"
        "void main(void)\n"
        "{\n"
        "\tif(texture2D(texUnit0, v_texcoord)[3]<0.1) discard;\n"
        "\tgl_FragColor = texture2D(texUnit0, v_texcoord) * texture2D(texUnit1, v_texcoord1);\n"
        "}\n"
        "\t[/GLSL_CODE]\n"
        "[/FRAGMENTSHADER]\n\n"
        "[EFFECT]\n"
        "\tNAME \tgrass_new\n"
        "\tUNIFORM p_matrix \t\t\tWORLDVIEWPROJECTION\n"
        "\tUNIFORM\ttexUnit0\t\t\tTEXTURE0\n"
        "\tUNIFORM\ttexUnit1\t\t\tTEXTURE1\n"
        "\tUNIFORM\ttexUnit2\t\t\tTEXTURE2\n"
        "\tATTRIBUTE vPosition\t\t\tPOSITION\n"
        "\tATTRIBUTE vTexCoord\t\t\tUV0\n"
        "\tATTRIBUTE vTexCoord1\t\tUV1\n"
        "\tVERTEXSHADER grass_new.vert\n"
        "\tFRAGMENTSHADER grass_new.frag\n"
        "\tTEXTURE 0 diffuse_tex\n"
        "\tTEXTURE 1 ground_tex\n"
        "\tTEXTURE 2 dirt_tex\n"
        "[/EFFECT]\n"
    )


def _pfx_lightmap_text(diffuse_rel: str, secondary_rel: str, *, alpha_cut: bool, effect_name: str) -> str:
    discard_line = "\tif(texture2D(texUnit0, v_texcoord)[3]<0.1) discard;" if alpha_cut else ""
    frag_body = _PFX_FRAGMENT_SHADER_BODY % {"discard_line": discard_line}
    return (
        "[HEADER]\n"
        "\tVERSION\t\t02.00.00.00\n"
        "\tDESCRIPTION lightmap\n"
        "\tCOPYRIGHT\tOOTP\n"
        "[/HEADER]\n\n"
        "[TEXTURE]\n"
        f"\tNAME \t\t\tdiffuse_tex\n"
        f"\tPATH\t\t\t{diffuse_rel}\n"
        "\tMINIFICATION\tLINEAR\n"
        "\tMAGNIFICATION\tLINEAR\n"
        "\tMIPMAP\t\t\tNONE\n"
        "[/TEXTURE]\n\n"
        "[TEXTURE]\n"
        f"\tNAME \t\t\tshadow_tex\n"
        f"\tPATH\t\t\t{secondary_rel}\n"
        "\tMINIFICATION\tLINEAR\n"
        "\tMAGNIFICATION\tLINEAR\n"
        "\tMIPMAP\t\t\tNONE\n"
        "[/TEXTURE]\n\n"
        + _PFX_VERTEX_SHADER + "\n\n"
        "[FRAGMENTSHADER]\n"
        "\tNAME \t\tc2u_alpha_shadow.frag\n"
        "\t[GLSL_CODE]\n"
        + frag_body + "\n"
        "\t[/GLSL_CODE]\n"
        "[/FRAGMENTSHADER]\n\n"
        "[EFFECT]\n"
        f"\tNAME \t{effect_name}\n"
        "\tUNIFORM p_matrix \t\t\tWORLDVIEWPROJECTION\n"
        "\tUNIFORM\ttexUnit0\t\t\tTEXTURE0\n"
        "\tUNIFORM\ttexUnit1\t\t\tTEXTURE1\n"
        "\tATTRIBUTE vPosition\t\t\tPOSITION\n"
        "\tATTRIBUTE vTexCoord\t\t\tUV0\n"
        "\tATTRIBUTE vTexCoord1\t\tUV1\n"
        "\tVERTEXSHADER c2u_alpha_shadow.vert\n"
        "\tFRAGMENTSHADER c2u_alpha_shadow.frag\n"
        "\tTEXTURE 0 diffuse_tex\n"
        "\tTEXTURE 1 shadow_tex\n"
        "[/EFFECT]\n"
    )


def _pfx_screen_text(diffuse_rel: str) -> str:
    return (
        "[HEADER]\n"
        "\tVERSION\t\t02.00.00.00\n"
        "\tDESCRIPTION simple diffuse\n"
        "\tCOPYRIGHT\tOOTP\n"
        "[/HEADER]\n\n"
        "[TEXTURE]\n"
        "\tNAME \t\t\tdiffuse_tex\n"
        f"\tPATH\t\t\t{diffuse_rel}\n"
        "\tMINIFICATION\tLINEAR\n"
        "\tMAGNIFICATION\tLINEAR\n"
        "\tMIPMAP\t\t\tNONE\n"
        "[/TEXTURE]\n\n"
        "[VERTEXSHADER]\n"
        "\tNAME \t\tc2u.vert\n"
        "\t[GLSL_CODE]\n"
        "attribute vec4 vPosition;\n"
        "attribute vec2 vTexCoord;\n"
        "varying vec2 v_texcoord;\n"
        "uniform mat4 p_matrix;\n"
        "void main(void)\n"
        "{\n"
        "\tv_texcoord = vTexCoord;\n"
        "\tgl_Position = p_matrix * vPosition;\n"
        "}\n"
        "\t[/GLSL_CODE]\n"
        "[/VERTEXSHADER]\n\n"
        "[FRAGMENTSHADER]\n"
        "\tNAME \t\tc2u.frag\n"
        "\t[GLSL_CODE]\n"
        "uniform sampler2D texUnit0;\n"
        "varying vec2 v_texcoord;\n"
        "void main(void)\n"
        "{\n"
        "\tgl_FragColor = texture2D(texUnit0, v_texcoord);\n"
        "}\n"
        "\t[/GLSL_CODE]\n"
        "[/FRAGMENTSHADER]\n\n"
        "[EFFECT]\n"
        "\tNAME \tmaterial_screen\n"
        "\tUNIFORM p_matrix \t\t\tWORLDVIEWPROJECTION\n"
        "\tUNIFORM\ttexUnit0\t\t\tTEXTURE0\n"
        "\tATTRIBUTE vPosition\t\t\tPOSITION\n"
        "\tATTRIBUTE vTexCoord\t\t\tUV0\n"
        "\tVERTEXSHADER c2u.vert\n"
        "\tFRAGMENTSHADER c2u.frag\n"
        "\tTEXTURE 0 diffuse_tex\n"
        "[/EFFECT]\n"
    )


def build_material_package(material_dump: list[dict] | str | Path, output_dir: str | Path) -> dict:
    if isinstance(material_dump, (str, Path)):
        dump = json.loads(Path(material_dump).read_text(encoding="utf-8"))
    else:
        dump = list(material_dump)
    if not isinstance(dump, list):
        raise PODMaterialPackageError("Material dump must be a JSON list")

    out_dir = Path(output_dir)
    textures_dir = out_dir / "textures"
    out_dir.mkdir(parents=True, exist_ok=True)
    textures_dir.mkdir(parents=True, exist_ok=True)

    used_names: set[str] = set()
    white_lm = _write_white_lightmap(textures_dir)
    materials_spec = []
    material_name_by_object: dict[str, str] = {}

    dedup: dict[str, dict] = {}
    for row in dump:
        material_name = row.get("material")
        if not material_name:
            continue
        object_name = row.get("object")
        images = row.get("images") or []
        if material_name not in dedup:
            dedup[material_name] = {
                "object": object_name,
                "material": material_name,
                "blend_mode": row.get("blend_mode", "opaque_shadow"),
                "images": images,
            }
        if object_name and object_name not in material_name_by_object:
            material_name_by_object[object_name] = material_name

    for material_name, row in dedup.items():
        safe_name = _sanitize_name(material_name)
        images = row["images"]
        if not images:
            raise PODMaterialPackageError(f"Material {material_name} has no image textures")
        diffuse_source = images[0]["filepath"]
        diffuse_rel = _copy_texture(diffuse_source, textures_dir, used_names)
        mode = row.get("blend_mode", "opaque_shadow")
        secondary_rel = None
        tertiary_rel = None
        if mode in ("opaque_shadow", "alpha_shadow", "alpha_blend"):
            if len(images) > 1 and images[1].get("filepath"):
                secondary_rel = _copy_texture(images[1]["filepath"], textures_dir, used_names)
            else:
                secondary_rel = white_lm
        pfx_filename = None
        effect_name = None
        if mode == "ground":
            pfx_filename = "ground.pfx"
            effect_name = "grass_new"
            if len(images) > 1 and images[1].get("filepath"):
                secondary_rel = _copy_texture(images[1]["filepath"], textures_dir, used_names)
            else:
                secondary_rel = diffuse_rel
            tertiary_rel = "../misc/grass/dirt.png"
            (out_dir / pfx_filename).write_text(
                _pfx_grass_text(diffuse_rel, secondary_rel, tertiary_rel),
                encoding="utf-8",
            )
        elif mode == "emissive":
            pfx_filename = f"{safe_name}.pfx"
            effect_name = "material_screen"
            secondary_rel = None
            (out_dir / pfx_filename).write_text(
                _pfx_screen_text(diffuse_rel),
                encoding="utf-8",
            )
        elif mode == "stock_lighting":
            # Preserve the exact template material block for Stand_Lighting.
            # Stock stadiums rely on hidden blend/state flags here, and
            # exporting it as a generic alpha-shadow material causes lights to
            # render as dark cards or disappear entirely.
            #
            # OOTP stock parks also reference Stand_Lighting textures by plain
            # filename at the package root rather than through textures/... .
            # Keep a top-level copy and point the material at that plain name
            # so the exported package behaves like the stock stadium.
            plain_name = Path(diffuse_rel).name
            src = textures_dir / plain_name
            dst = out_dir / plain_name
            if src.exists():
                shutil.copy2(src, dst)
            diffuse_rel = plain_name
            secondary_rel = None
        elif mode in ("opaque_shadow", "alpha_shadow", "alpha_blend"):
            pfx_filename = f"{safe_name}.pfx"
            effect_name = "c2u_alpha_shadow" if mode == "alpha_shadow" else "c2u_shadow"
            (out_dir / pfx_filename).write_text(
                _pfx_lightmap_text(
                    diffuse_rel,
                    secondary_rel,
                    alpha_cut=(mode == "alpha_shadow"),
                    effect_name=effect_name,
                ),
                encoding="utf-8",
            )
        materials_spec.append(
            {
                "name": safe_name,
                "source_material_name": material_name,
                "mode": mode,
                "diffuse_path": diffuse_rel,
                "secondary_path": secondary_rel,
                "tertiary_path": tertiary_rel,
                "pfx_filename": pfx_filename,
                "effect_name": effect_name,
            }
        )

    summary = {
        "materials": materials_spec,
        "object_material_map": material_name_by_object,
    }
    (out_dir / "material_plan.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: pod_material_package.py <material_dump.json> <output_dir>")
        return 2
    result = build_material_package(argv[1], argv[2])
    print(json.dumps({"material_count": len(result["materials"]), "output_dir": argv[2]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
