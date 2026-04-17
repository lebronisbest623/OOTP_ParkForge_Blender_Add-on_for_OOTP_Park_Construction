from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path


START_TAG_MASK = 0x00000000
END_TAG_MASK = 0x80000000
TAG_MASK = 0x80000000
FORMAT_VERSION = "AB.POD.2.0"


class PODIdentifiers(IntEnum):
    PODFormatVersion = 1000
    Scene = 1001
    ExportOptions = 1002
    FileHistory = 1003

    e_sceneClearColor = 2000
    e_sceneAmbientColor = 2001
    e_sceneNumCameras = 2002
    e_sceneNumLights = 2003
    e_sceneNumMeshes = 2004
    e_sceneNumNodes = 2005
    e_sceneNumMeshNodes = 2006
    e_sceneNumTextures = 2007
    e_sceneNumMaterials = 2008
    e_sceneNumFrames = 2009
    e_sceneCamera = 2010
    e_sceneLight = 2011
    e_sceneMesh = 2012
    e_sceneNode = 2013
    e_sceneTexture = 2014
    e_sceneMaterial = 2015
    e_sceneFlags = 2016
    e_sceneFPS = 2017
    e_sceneUserData = 2018
    e_sceneUnits = 2019

    e_materialName = 3000
    e_materialDiffuseTextureIndex = 3001
    e_materialOpacity = 3002
    e_materialAmbientColor = 3003
    e_materialDiffuseColor = 3004
    e_materialSpecularColor = 3005
    e_materialShininess = 3006
    e_materialPfxFilename = 3007
    e_materialEffectName = 3008
    e_materialSecondaryTextureIndex = 3009

    e_textureFilename = 4000

    e_nodeIndex = 5000
    e_nodeName = 5001
    e_nodeMaterialIndex = 5002
    e_nodeParentIndex = 5003
    e_nodePosition = 5004
    e_nodeRotation = 5005
    e_nodeScale = 5006
    e_nodeAnimationPosition = 5007
    e_nodeAnimationRotation = 5008
    e_nodeAnimationScale = 5009
    e_nodeMatrix = 5010
    e_nodeAnimationMatrix = 5011
    e_nodeAnimationFlags = 5012
    e_nodeAnimationPositionIndex = 5013
    e_nodeAnimationRotationIndex = 5014
    e_nodeAnimationScaleIndex = 5015
    e_nodeAnimationMatrixIndex = 5016
    e_nodeUserData = 5017

    e_meshNumVertices = 6000
    e_meshNumFaces = 6001
    e_meshNumUVWChannels = 6002
    e_meshVertexIndexList = 6003
    e_meshStripLength = 6004
    e_meshNumStrips = 6005
    e_meshVertexList = 6006
    e_meshNormalList = 6007
    e_meshTangentList = 6008
    e_meshBinormalList = 6009
    e_meshUVWList = 6010
    e_meshVertexColorList = 6011
    e_meshBoneIndexList = 6012
    e_meshBoneWeightList = 6013
    e_meshInterleavedDataList = 6014
    e_meshBoneBatchIndexList = 6015
    e_meshNumBoneIndicesPerBatch = 6016
    e_meshBoneOffsetPerBatch = 6017
    e_meshMaxNumBonesPerBatch = 6018
    e_meshNumBoneBatches = 6019
    e_meshUnpackMatrix = 6020

    e_blockDataType = 9000
    e_blockNumComponents = 9001
    e_blockStride = 9002
    e_blockData = 9003


class DataType(IntEnum):
    NoneType = 0
    Float32 = 1
    Int32 = 2
    UInt16 = 3
    RGBA = 4
    ARGB = 5
    D3DCOLOR = 6
    UBYTE4 = 7
    DEC3N = 8
    Fixed16_16 = 9
    UInt8 = 10
    Int16 = 11
    Int16Norm = 12
    Int8 = 13
    Int8Norm = 14
    UInt8Norm = 15
    UInt16Norm = 16
    UInt32 = 17
    ABGR = 18
    Float16 = 19


STRUCT_MAP = {
    DataType.Float32: ("f", 4),
    DataType.Int32: ("i", 4),
    DataType.UInt16: ("H", 2),
    DataType.UInt32: ("I", 4),
    DataType.UInt8: ("B", 1),
    DataType.Int16: ("h", 2),
    DataType.Int8: ("b", 1),
    DataType.Float16: ("e", 2),
}


@dataclass
class PODAttribute:
    data_type: int
    num_components: int
    stride: int
    raw_data: bytes | None = None
    offset: int = 0


@dataclass
class PODMesh:
    name: str = ""
    num_vertices: int = 0
    num_faces: int = 0
    interleaved_data: bytes | None = None
    vertices: PODAttribute | None = None
    normals: PODAttribute | None = None
    uvs: list[PODAttribute] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)


@dataclass
class PODNode:
    name: str = ""
    object_index: int = -1
    material_index: int = -1
    parent_index: int = -1
    translation: tuple[float, float, float] | None = None
    rotation_xyzw: tuple[float, float, float, float] | None = None
    scale: tuple[float, float, float] | None = None
    matrix: tuple[float, ...] | None = None


@dataclass
class PODTexture:
    filename: str = ""


@dataclass
class PODMaterial:
    name: str = ""
    diffuse_texture_index: int = -1


@dataclass
class PODScene:
    clear_color: tuple[float, float, float] | None = None
    ambient_color: tuple[float, float, float] | None = None
    fps: float = 30.0
    meshes: list[PODMesh] = field(default_factory=list)
    nodes: list[PODNode] = field(default_factory=list)
    textures: list[PODTexture] = field(default_factory=list)
    materials: list[PODMaterial] = field(default_factory=list)


class PODParseError(RuntimeError):
    pass


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def remaining(self) -> int:
        return len(self.data) - self.offset

    def skip(self, amount: int) -> None:
        self.offset += amount

    def read(self, size: int) -> bytes:
        if self.offset + size > len(self.data):
            raise PODParseError(f"Unexpected EOF at {self.offset:#x}")
        chunk = self.data[self.offset:self.offset + size]
        self.offset += size
        return chunk

    def read_u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def read_i32(self) -> int:
        return struct.unpack("<i", self.read(4))[0]

    def read_f32_array(self, count: int) -> tuple[float, ...]:
        return struct.unpack("<" + ("f" * count), self.read(4 * count))

    def read_tag(self) -> tuple[int, int]:
        return self.read_u32(), self.read_u32()


def _read_string(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def _decode_scalar_array(raw: bytes, data_type: int) -> list[int | float]:
    fmt_info = STRUCT_MAP.get(data_type)
    if not fmt_info:
        raise PODParseError(f"Unsupported POD data type {data_type}")
    fmt_char, item_size = fmt_info
    count = len(raw) // item_size
    if len(raw) % item_size:
        raise PODParseError(f"Raw block size {len(raw)} does not align for POD data type {data_type}")
    return list(struct.unpack("<" + (fmt_char * count), raw))


def _decode_attribute(attr: PODAttribute, num_vertices: int) -> list[tuple[float, ...]]:
    if attr.raw_data is None:
        return []

    fmt_info = STRUCT_MAP.get(attr.data_type)
    if not fmt_info:
        raise PODParseError(f"Unsupported attribute data type {attr.data_type}")

    fmt_char, item_size = fmt_info
    stride = attr.stride or (item_size * attr.num_components)
    values: list[tuple[float, ...]] = []
    for idx in range(num_vertices):
        start = attr.offset + idx * stride
        end = start + (attr.num_components * item_size)
        if end > len(attr.raw_data):
            break
        chunk = attr.raw_data[start:end]
        item = struct.unpack("<" + (fmt_char * attr.num_components), chunk)
        values.append(tuple(float(v) for v in item))
    return values


def _parse_vertex_data_block(reader: Reader, end_tag: int, interleaved_blob: bytes | None) -> PODAttribute | None:
    data_type = None
    num_components = 0
    stride = 0
    raw_data: bytes | None = None
    offset = 0

    while True:
        ident, length = reader.read_tag()
        if ident == end_tag:
            if data_type is None or num_components == 0:
                return None
            return PODAttribute(
                data_type=data_type,
                num_components=num_components,
                stride=stride,
                raw_data=raw_data if raw_data is not None else interleaved_blob,
                offset=offset,
            )

        if ident == PODIdentifiers.e_blockDataType:
            data_type = reader.read_u32()
        elif ident == PODIdentifiers.e_blockNumComponents:
            num_components = reader.read_u32()
        elif ident == PODIdentifiers.e_blockStride:
            stride = reader.read_u32()
        elif ident == PODIdentifiers.e_blockData:
            if interleaved_blob is None:
                raw_data = reader.read(length)
            else:
                if length >= 4:
                    offset = reader.read_u32()
                    if length > 4:
                        reader.skip(length - 4)
                else:
                    raw_data = reader.read(length)
        else:
            reader.skip(length)


def _parse_index_block(reader: Reader, end_tag: int) -> list[int]:
    data_type = None
    raw_data = b""
    while True:
        ident, length = reader.read_tag()
        if ident == end_tag:
            if data_type is None or not raw_data:
                return []
            return [int(v) for v in _decode_scalar_array(raw_data, data_type)]
        if ident == PODIdentifiers.e_blockDataType:
            data_type = reader.read_u32()
        elif ident == PODIdentifiers.e_blockData:
            raw_data = reader.read(length)
        else:
            reader.skip(length)


def _parse_mesh_block(reader: Reader) -> PODMesh:
    mesh = PODMesh()
    while True:
        ident, length = reader.read_tag()
        if ident == (PODIdentifiers.e_sceneMesh | END_TAG_MASK):
            return mesh
        if ident == PODIdentifiers.e_meshNumVertices:
            mesh.num_vertices = reader.read_u32()
        elif ident == PODIdentifiers.e_meshNumFaces:
            mesh.num_faces = reader.read_u32()
        elif ident == PODIdentifiers.e_meshInterleavedDataList:
            mesh.interleaved_data = reader.read(length)
        elif ident == PODIdentifiers.e_meshVertexIndexList:
            mesh.indices = _parse_index_block(reader, PODIdentifiers.e_meshVertexIndexList | END_TAG_MASK)
        elif ident == PODIdentifiers.e_meshVertexList:
            mesh.vertices = _parse_vertex_data_block(reader, PODIdentifiers.e_meshVertexList | END_TAG_MASK, mesh.interleaved_data)
        elif ident == PODIdentifiers.e_meshNormalList:
            mesh.normals = _parse_vertex_data_block(reader, PODIdentifiers.e_meshNormalList | END_TAG_MASK, mesh.interleaved_data)
        elif ident == PODIdentifiers.e_meshUVWList:
            uv = _parse_vertex_data_block(reader, PODIdentifiers.e_meshUVWList | END_TAG_MASK, mesh.interleaved_data)
            if uv:
                mesh.uvs.append(uv)
        else:
            reader.skip(length)


def _parse_node_block(reader: Reader) -> PODNode:
    node = PODNode()
    while True:
        ident, length = reader.read_tag()
        if ident == (PODIdentifiers.e_sceneNode | END_TAG_MASK):
            return node
        if ident == PODIdentifiers.e_nodeIndex:
            node.object_index = reader.read_u32()
        elif ident == PODIdentifiers.e_nodeName:
            node.name = _read_string(reader.read(length))
        elif ident == PODIdentifiers.e_nodeMaterialIndex:
            node.material_index = reader.read_i32()
        elif ident == PODIdentifiers.e_nodeParentIndex:
            node.parent_index = reader.read_i32()
        elif ident == PODIdentifiers.e_nodePosition:
            node.translation = tuple(reader.read_f32_array(3))
        elif ident == PODIdentifiers.e_nodeRotation:
            node.rotation_xyzw = tuple(reader.read_f32_array(4))
        elif ident == PODIdentifiers.e_nodeScale:
            node.scale = tuple(reader.read_f32_array(3))
        elif ident == PODIdentifiers.e_nodeMatrix:
            node.matrix = tuple(reader.read_f32_array(16))
        elif ident == PODIdentifiers.e_nodeAnimationPosition:
            values = reader.read_f32_array(length // 4)
            if len(values) >= 3:
                node.translation = tuple(values[:3])
        elif ident == PODIdentifiers.e_nodeAnimationRotation:
            values = reader.read_f32_array(length // 4)
            if len(values) >= 4:
                node.rotation_xyzw = tuple(values[:4])
        elif ident == PODIdentifiers.e_nodeAnimationScale:
            values = reader.read_f32_array(length // 4)
            if len(values) >= 3:
                node.scale = tuple(values[:3])
        elif ident == PODIdentifiers.e_nodeAnimationMatrix:
            values = reader.read_f32_array(length // 4)
            if len(values) >= 16:
                node.matrix = tuple(values[:16])
        else:
            reader.skip(length)


def _parse_texture_block(reader: Reader) -> PODTexture:
    tex = PODTexture()
    while True:
        ident, length = reader.read_tag()
        if ident == (PODIdentifiers.e_sceneTexture | END_TAG_MASK):
            return tex
        if ident == PODIdentifiers.e_textureFilename:
            tex.filename = _read_string(reader.read(length))
        else:
            reader.skip(length)


def _parse_material_block(reader: Reader) -> PODMaterial:
    material = PODMaterial()
    while True:
        ident, length = reader.read_tag()
        if ident == (PODIdentifiers.e_sceneMaterial | END_TAG_MASK):
            return material
        if ident == PODIdentifiers.e_materialName:
            material.name = _read_string(reader.read(length))
        elif ident == PODIdentifiers.e_materialDiffuseTextureIndex:
            material.diffuse_texture_index = reader.read_i32()
        else:
            reader.skip(length)


def _skip_block(reader: Reader, end_tag: int) -> None:
    while True:
        ident, length = reader.read_tag()
        if ident == end_tag:
            return
        reader.skip(length)


def _parse_scene_block(reader: Reader) -> PODScene:
    scene = PODScene()
    while True:
        ident, length = reader.read_tag()
        if ident == (PODIdentifiers.Scene | END_TAG_MASK):
            return scene
        if ident == PODIdentifiers.e_sceneClearColor:
            vals = reader.read_f32_array(3)
            scene.clear_color = tuple(vals)
        elif ident == PODIdentifiers.e_sceneAmbientColor:
            vals = reader.read_f32_array(3)
            scene.ambient_color = tuple(vals)
        elif ident == PODIdentifiers.e_sceneFPS:
            scene.fps = float(reader.read_u32())
        elif ident == PODIdentifiers.e_sceneMesh:
            scene.meshes.append(_parse_mesh_block(reader))
        elif ident == PODIdentifiers.e_sceneNode:
            scene.nodes.append(_parse_node_block(reader))
        elif ident == PODIdentifiers.e_sceneTexture:
            scene.textures.append(_parse_texture_block(reader))
        elif ident == PODIdentifiers.e_sceneMaterial:
            scene.materials.append(_parse_material_block(reader))
        else:
            reader.skip(length)


def parse_pod_bytes(data: bytes) -> PODScene:
    reader = Reader(data)
    while reader.remaining() >= 8:
        ident, length = reader.read_tag()
        if ident == PODIdentifiers.PODFormatVersion:
            version = _read_string(reader.read(length))
            if version != FORMAT_VERSION:
                raise PODParseError(f"Unsupported POD version '{version}'")
        elif ident == (PODIdentifiers.PODFormatVersion | END_TAG_MASK):
            continue
        elif ident == PODIdentifiers.ExportOptions:
            reader.skip(length)
        elif ident == (PODIdentifiers.ExportOptions | END_TAG_MASK):
            continue
        elif ident == PODIdentifiers.FileHistory:
            reader.skip(length)
        elif ident == (PODIdentifiers.FileHistory | END_TAG_MASK):
            continue
        elif ident == PODIdentifiers.Scene:
            return _parse_scene_block(reader)
        else:
            reader.skip(length)
    raise PODParseError("No Scene block found in POD")


def parse_pod(path: str | Path) -> PODScene:
    return parse_pod_bytes(Path(path).read_bytes())


def mesh_vertices(mesh: PODMesh) -> list[tuple[float, float, float]]:
    if not mesh.vertices:
        return []
    values = _decode_attribute(mesh.vertices, mesh.num_vertices)
    return [tuple(v[:3]) for v in values]


def mesh_normals(mesh: PODMesh) -> list[tuple[float, float, float]]:
    if not mesh.normals:
        return []
    values = _decode_attribute(mesh.normals, mesh.num_vertices)
    return [tuple(v[:3]) for v in values]


def mesh_uvs(mesh: PODMesh, uv_index: int = 0) -> list[tuple[float, float]]:
    if uv_index >= len(mesh.uvs):
        return []
    values = _decode_attribute(mesh.uvs[uv_index], mesh.num_vertices)
    return [tuple(v[:2]) for v in values]

