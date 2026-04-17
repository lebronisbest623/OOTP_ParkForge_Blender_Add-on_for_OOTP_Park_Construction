# Architecture

ParkForge stays as a single Blender add-on package so Blender can install it directly from a zip containing `io_scene_ootp_pod/`.

## Core Modules

- `__init__.py`
  - Blender registration
  - import/export operators
  - sidebar panel
  - runtime tool discovery
- `pod_parser.py`
  - typed POD scene parsing
- `pod_dom.py`
  - raw-preserving POD DOM for safe rewrite work
- `pod_writer.py`
  - byte-preserving POD serialization
- `pod_exporter.py`
  - scene collection and export orchestration
- `pod_fresh_builder.py`
  - fresh POD scene construction from Blender/export JSON
- `pod_material_package.py`
  - `pfx`, textures, and fallback texture generation
- `pfx_parser.py`
  - OOTP/PowerVR-style PFX parsing

## Support / Dev Utilities

- `pod_patch.py`
- `pod_patch_from_json.py`
- `pod_inspect.py`
- `export_ootp_obj_package.py`

These are still worth keeping in the repository, but they are not the primary public UI surface of the add-on.

## Design Rules

- Keep runtime import/export code in package root for Blender compatibility
- Keep docs in `docs/`
- Keep repeatable release helpers in `tools/`
- Do not commit generated release bundles into the source package directory

