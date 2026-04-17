from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PFXTexture:
    name: str = ""
    path: str = ""


@dataclass
class PFXShader:
    name: str = ""
    code: str = ""


@dataclass
class PFXEffect:
    name: str = ""
    uniforms: dict[str, str] = field(default_factory=dict)
    attributes: dict[str, str] = field(default_factory=dict)
    vertex_shader: str = ""
    fragment_shader: str = ""
    texture_units: dict[int, str] = field(default_factory=dict)


@dataclass
class PFXMaterial:
    textures: dict[str, PFXTexture] = field(default_factory=dict)
    vertex_shaders: dict[str, PFXShader] = field(default_factory=dict)
    fragment_shaders: dict[str, PFXShader] = field(default_factory=dict)
    effects: dict[str, PFXEffect] = field(default_factory=dict)


class PFXParseError(RuntimeError):
    pass


SECTION_RE = re.compile(r"^\[(?P<name>[A-Z_]+)\]\s*$")
SECTION_END_RE = re.compile(r"^\[/([A-Z_]+)\]\s*$")
TEXTURE_BIND_RE = re.compile(r"^TEXTURE\s+(\d+)\s+(.+)$", re.IGNORECASE)
DECL_RE = re.compile(r"^(NAME|PATH|UNIFORM|ATTRIBUTE|VERTEXSHADER|FRAGMENTSHADER)\s+(.+)$", re.IGNORECASE)

TEX_LOOKUP_RE = re.compile(r"texture(?:2D)?\(\s*(\w+)\s*,\s*([\w\.]+)\s*\)")
VARYING_ASSIGN_RE = re.compile(r"(\w+)\s*=\s*(\w+)\s*;")
ALPHA_DISCARD_RE = re.compile(r"texture(?:2D)?\(\s*(\w+)\s*,\s*([\w\.]+)\s*\)\s*\[\s*3\s*\]\s*<\s*([0-9.]+)")


def _clean(line: str) -> str:
    return line.strip().replace("\t", " ")


def parse_pfx(path: str | Path) -> PFXMaterial:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    material = PFXMaterial()
    idx = 0
    while idx < len(lines):
        line = _clean(lines[idx])
        idx += 1
        if not line:
            continue
        section_match = SECTION_RE.match(line)
        if not section_match:
            continue

        section_name = section_match.group("name").upper()
        block_lines: list[str] = []
        while idx < len(lines):
            current = lines[idx].rstrip("\n")
            idx += 1
            if SECTION_END_RE.match(_clean(current)):
                break
            block_lines.append(current)

        if section_name == "TEXTURE":
            texture = _parse_texture_block(block_lines)
            if texture.name:
                material.textures[texture.name] = texture
        elif section_name == "VERTEXSHADER":
            shader = _parse_shader_block(block_lines)
            if shader.name:
                material.vertex_shaders[shader.name] = shader
        elif section_name == "FRAGMENTSHADER":
            shader = _parse_shader_block(block_lines)
            if shader.name:
                material.fragment_shaders[shader.name] = shader
        elif section_name == "EFFECT":
            effect = _parse_effect_block(block_lines)
            if effect.name:
                material.effects[effect.name] = effect

    return material


def _parse_texture_block(lines: list[str]) -> PFXTexture:
    texture = PFXTexture()
    for raw in lines:
        line = _clean(raw)
        if not line or line.startswith("//"):
            continue
        match = DECL_RE.match(line)
        if not match:
            continue
        key = match.group(1).upper()
        value = match.group(2).strip()
        if key == "NAME":
            texture.name = value
        elif key == "PATH":
            texture.path = value
    return texture


def _parse_shader_block(lines: list[str]) -> PFXShader:
    shader = PFXShader()
    inside_glsl = False
    glsl_lines: list[str] = []
    for raw in lines:
        line = raw.rstrip("\n")
        stripped = _clean(line)
        if stripped.upper() == "[GLSL_CODE]":
            inside_glsl = True
            continue
        if stripped.upper() == "[/GLSL_CODE]":
            inside_glsl = False
            continue
        if inside_glsl:
            glsl_lines.append(line)
            continue
        match = DECL_RE.match(stripped)
        if not match:
            continue
        key = match.group(1).upper()
        value = match.group(2).strip()
        if key == "NAME":
            shader.name = value
    shader.code = "\n".join(glsl_lines)
    return shader


def _parse_effect_block(lines: list[str]) -> PFXEffect:
    effect = PFXEffect()
    for raw in lines:
        line = _clean(raw)
        if not line or line.startswith("//"):
            continue

        tex_match = TEXTURE_BIND_RE.match(line)
        if tex_match:
            effect.texture_units[int(tex_match.group(1))] = tex_match.group(2).strip()
            continue

        match = DECL_RE.match(line)
        if not match:
            continue
        key = match.group(1).upper()
        value = match.group(2).strip()
        if key == "NAME":
            effect.name = value
        elif key == "UNIFORM":
            parts = value.split()
            if len(parts) >= 2:
                effect.uniforms[parts[0]] = parts[1]
        elif key == "ATTRIBUTE":
            parts = value.split()
            if len(parts) >= 2:
                effect.attributes[parts[0]] = parts[1]
        elif key == "VERTEXSHADER":
            effect.vertex_shader = value
        elif key == "FRAGMENTSHADER":
            effect.fragment_shader = value
    return effect


def choose_effect(material: PFXMaterial) -> PFXEffect | None:
    if not material.effects:
        return None
    return next(iter(material.effects.values()))


def used_sampler_sequence(material: PFXMaterial, effect: PFXEffect) -> list[str]:
    shader = material.fragment_shaders.get(effect.fragment_shader)
    if not shader:
        return []
    samplers: list[str] = []
    for sampler_name, _ in TEX_LOOKUP_RE.findall(shader.code):
        if sampler_name not in samplers:
            samplers.append(sampler_name)
    return samplers


def varying_uv_semantics(material: PFXMaterial, effect: PFXEffect) -> dict[str, str]:
    shader = material.vertex_shaders.get(effect.vertex_shader)
    if not shader:
        return {}
    semantics: dict[str, str] = {}
    for varying_name, attribute_name in VARYING_ASSIGN_RE.findall(shader.code):
        semantic = effect.attributes.get(attribute_name)
        if semantic:
            semantics[varying_name] = semantic
    return semantics


def sampler_uv_semantics(material: PFXMaterial, effect: PFXEffect) -> dict[str, str]:
    shader = material.fragment_shaders.get(effect.fragment_shader)
    if not shader:
        return {}
    varying_map = varying_uv_semantics(material, effect)
    mapping: dict[str, str] = {}
    for sampler_name, varying_name in TEX_LOOKUP_RE.findall(shader.code):
        semantic = varying_map.get(varying_name)
        if semantic:
            mapping[sampler_name] = semantic
    return mapping


def alpha_discard_source(material: PFXMaterial, effect: PFXEffect) -> tuple[str, float] | None:
    shader = material.fragment_shaders.get(effect.fragment_shader)
    if not shader:
        return None
    match = ALPHA_DISCARD_RE.search(shader.code)
    if not match:
        return None
    sampler_name = match.group(1)
    threshold = float(match.group(3))
    return sampler_name, threshold
