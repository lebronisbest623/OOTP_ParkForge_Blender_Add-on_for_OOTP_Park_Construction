from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    from .pod_parser import END_TAG_MASK, TAG_MASK, PODIdentifiers
except ImportError:
    from pod_parser import END_TAG_MASK, TAG_MASK, PODIdentifiers


STRUCTURED_CONTAINER_TAGS = {
    PODIdentifiers.Scene,
    PODIdentifiers.e_sceneCamera,
    PODIdentifiers.e_sceneLight,
    PODIdentifiers.e_sceneMesh,
    PODIdentifiers.e_sceneNode,
    PODIdentifiers.e_sceneTexture,
    PODIdentifiers.e_sceneMaterial,
    PODIdentifiers.e_meshVertexIndexList,
    PODIdentifiers.e_meshVertexList,
    PODIdentifiers.e_meshNormalList,
    PODIdentifiers.e_meshTangentList,
    PODIdentifiers.e_meshBinormalList,
    PODIdentifiers.e_meshUVWList,
    PODIdentifiers.e_meshVertexColorList,
    PODIdentifiers.e_meshBoneIndexList,
    PODIdentifiers.e_meshBoneWeightList,
    PODIdentifiers.e_meshBoneBatchIndexList,
}


TAG_NAMES = {
    PODIdentifiers.PODFormatVersion: "PODFormatVersion",
    PODIdentifiers.Scene: "Scene",
    PODIdentifiers.ExportOptions: "ExportOptions",
    PODIdentifiers.FileHistory: "FileHistory",
    PODIdentifiers.e_sceneCamera: "sceneCamera",
    PODIdentifiers.e_sceneLight: "sceneLight",
    PODIdentifiers.e_sceneMesh: "sceneMesh",
    PODIdentifiers.e_sceneNode: "sceneNode",
    PODIdentifiers.e_sceneTexture: "sceneTexture",
    PODIdentifiers.e_sceneMaterial: "sceneMaterial",
    PODIdentifiers.e_meshVertexIndexList: "meshVertexIndexList",
    PODIdentifiers.e_meshVertexList: "meshVertexList",
    PODIdentifiers.e_meshNormalList: "meshNormalList",
    PODIdentifiers.e_meshTangentList: "meshTangentList",
    PODIdentifiers.e_meshBinormalList: "meshBinormalList",
    PODIdentifiers.e_meshUVWList: "meshUVWList",
    PODIdentifiers.e_meshVertexColorList: "meshVertexColorList",
    PODIdentifiers.e_meshBoneIndexList: "meshBoneIndexList",
    PODIdentifiers.e_meshBoneWeightList: "meshBoneWeightList",
    PODIdentifiers.e_meshBoneBatchIndexList: "meshBoneBatchIndexList",
}


class PODDomError(RuntimeError):
    pass


@dataclass
class PODBlock:
    tag: int
    length: int
    tag_offset: int
    payload_offset: int
    payload_end_offset: int
    end_tag_offset: int | None = None
    end_length: int | None = None
    payload: bytes = b""
    children: list["PODBlock"] = field(default_factory=list)

    @property
    def tag_name(self) -> str:
        return TAG_NAMES.get(self.tag, f"tag_{self.tag}")

    @property
    def has_end_tag(self) -> bool:
        return self.end_tag_offset is not None

    @property
    def end_offset(self) -> int:
        if self.end_tag_offset is not None:
            return self.end_tag_offset + 8
        return self.payload_end_offset

    @property
    def is_structured_container(self) -> bool:
        return self.tag in STRUCTURED_CONTAINER_TAGS

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "tag_name": self.tag_name,
            "length": self.length,
            "tag_offset": self.tag_offset,
            "payload_offset": self.payload_offset,
            "payload_end_offset": self.payload_end_offset,
            "end_tag_offset": self.end_tag_offset,
            "end_length": self.end_length,
            "has_end_tag": self.has_end_tag,
            "payload_size": len(self.payload),
            "children": [child.to_dict() for child in self.children],
        }


@dataclass
class PODDocument:
    path: str
    data: bytes
    blocks: list[PODBlock]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "size": len(self.data),
            "blocks": [block.to_dict() for block in self.blocks],
        }


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def tell(self) -> int:
        return self.offset

    def remaining(self) -> int:
        return len(self.data) - self.offset

    def seek(self, offset: int) -> None:
        """Move to an absolute byte offset."""
        self.offset = offset

    def skip(self, amount: int) -> None:
        """Advance forward by a relative number of bytes."""
        self.offset += amount

    def peek_u32(self, ahead: int = 0) -> int:
        """Read a u32 at current position + ahead without advancing."""
        pos = self.offset + ahead
        return int.from_bytes(self.data[pos:pos + 4], "little", signed=False)

    def read(self, size: int) -> bytes:
        if self.offset + size > len(self.data):
            raise PODDomError(f"Unexpected EOF at 0x{self.offset:X}")
        chunk = self.data[self.offset:self.offset + size]
        self.offset += size
        return chunk

    def read_u32(self) -> int:
        return int.from_bytes(self.read(4), "little", signed=False)


# ---------------------------------------------------------------------------
# Shared DOM helpers (used by pod_patch, pod_patch_from_json, pod_fresh_builder)
# ---------------------------------------------------------------------------

def get_scene_block(doc: PODDocument) -> PODBlock:
    for block in doc.blocks:
        if block.tag == PODIdentifiers.Scene:
            return block
    raise PODDomError("No Scene block found")


def get_scene_mesh_blocks(doc: PODDocument) -> list[PODBlock]:
    scene = get_scene_block(doc)
    return [child for child in scene.children if child.tag == PODIdentifiers.e_sceneMesh]


def get_mesh_interleaved_block(mesh_block: PODBlock) -> PODBlock | None:
    for child in mesh_block.children:
        if child.tag == PODIdentifiers.e_meshInterleavedDataList:
            return child
    return None


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_pod_dom(path: str | Path) -> PODDocument:
    blob = Path(path).read_bytes()
    reader = Reader(blob)
    blocks: list[PODBlock] = []
    while reader.remaining() >= 8:
        block = _parse_block(reader)
        if block is None:
            break
        blocks.append(block)
    return PODDocument(path=str(path), data=blob, blocks=blocks)


def _parse_block(reader: Reader) -> PODBlock | None:
    if reader.remaining() < 8:
        return None

    tag_offset = reader.tell()
    tag_raw = reader.read_u32()
    length = reader.read_u32()

    if tag_raw & TAG_MASK:
        raise PODDomError(f"Unexpected end tag 0x{tag_raw:X} at 0x{tag_offset:X}")

    tag = tag_raw
    if tag in STRUCTURED_CONTAINER_TAGS and length == 0:
        payload_offset = reader.tell()
        children = _parse_blocks_until_end_tag(reader, tag)
        payload_end_offset = reader.tell()
        payload = reader.data[payload_offset:payload_end_offset]
        end_tag_offset, end_length = _consume_matching_end_tag(reader, tag)
        return PODBlock(
            tag=tag,
            length=length,
            tag_offset=tag_offset,
            payload_offset=payload_offset,
            payload_end_offset=payload_end_offset,
            end_tag_offset=end_tag_offset,
            end_length=end_length,
            payload=payload,
            children=children,
        )

    payload_offset = reader.tell()
    payload_end_offset = payload_offset + length
    if payload_end_offset > len(reader.data):
        raise PODDomError(
            f"Tag {tag} at 0x{tag_offset:X} claims payload beyond EOF "
            f"(0x{payload_end_offset:X} > 0x{len(reader.data):X})"
        )
    payload = reader.read(length)
    end_tag_offset, end_length = _consume_matching_end_tag(reader, tag)

    return PODBlock(
        tag=tag,
        length=length,
        tag_offset=tag_offset,
        payload_offset=payload_offset,
        payload_end_offset=payload_end_offset,
        end_tag_offset=end_tag_offset,
        end_length=end_length,
        payload=payload,
        children=[],
    )


def _parse_blocks_until_end_tag(reader: Reader, expected_end_tag: int) -> list[PODBlock]:
    blocks: list[PODBlock] = []
    while reader.remaining() >= 8:
        if _next_is_matching_end_tag(reader, expected_end_tag):
            break
        block = _parse_block(reader)
        if block is None:
            break
        blocks.append(block)
    if not _next_is_matching_end_tag(reader, expected_end_tag):
        raise PODDomError(f"Container {expected_end_tag} missing end tag near 0x{reader.tell():X}")
    return blocks


def _next_is_matching_end_tag(reader: Reader, tag: int) -> bool:
    if reader.remaining() < 8:
        return False
    return reader.peek_u32() == (tag | END_TAG_MASK) and reader.peek_u32(4) == 0


def _consume_matching_end_tag(reader: Reader, tag: int) -> tuple[int, int]:
    if not _next_is_matching_end_tag(reader, tag):
        raise PODDomError(f"Expected end tag for {tag} at 0x{reader.tell():X}")
    end_tag_offset = reader.tell()
    end_tag_raw = reader.read_u32()
    end_length = reader.read_u32()
    if end_tag_raw != (tag | END_TAG_MASK):
        raise PODDomError(f"Mismatched end tag for {tag} at 0x{end_tag_offset:X}")
    return end_tag_offset, end_length
