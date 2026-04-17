# OOTP ParkForge

Blender add-on for importing, editing, and exporting OOTP ballpark packages built around `AB.POD.2.0`.

This repository is intentionally organized as a Blender-installable add-on package first, and a GitHub-friendly project second. The package root stays flat so Blender can install it directly, while supporting docs and release tooling live in dedicated folders.

## Warning

**Edited POD exports are not fully stable yet. Re-importing an edited POD into OOTP can cause the game to exit immediately without warning.**

Please respect OOTP's original assets. This tool does not grant any rights to
game-owned or third-party assets. Edited assets should be used only with OOTP
and only where you have the necessary rights to do so. Do not redistribute
extracted or edited game assets unless you are permitted to do so.

Always work in a safe staging folder first.

- Do not overwrite a live OOTP stadium folder while testing.
- Keep backups of the original `.pod`, `.pfx`, `.ootp3d`, `.prk`, and `textures/` files.
- Validate exported packages outside the live game install before copying anything into OOTP.
- Expect some stadiums to need template-specific or material-specific adjustments.

## What It Does

- Import OOTP stadium `.pod` files into Blender
- Decode bundled `.ktx` textures with `CompressonatorCLI`
- Rebuild Blender scenes into OOTP-friendly `POD + pfx + textures` packages
- Copy template sidecars such as `.ootp3d` and `.prk` during export
- Expose import/export from both Blender's File menu and the `ParkForge` sidebar

## Current Scope

- Scene, mesh, node, texture, and material parsing
- UV import and export
- Fresh scene export using a template POD as the structural base
- Material packaging with generated fallback textures
- Quick export progress reporting in Blender

## Current Limits

- No animation import
- No skinning import
- No patch-mode UI yet; export currently rebuilds a fresh POD package
- Transform handling is best-effort and may still need stadium-specific tuning
- Blender procedural materials are exported as source textures, not live shader graphs

## Repository Layout

```text
io_scene_ootp_pod/
  __init__.py
  *.py
  README.md
  THIRD_PARTY_NOTICES.md
  .gitignore
  LICENSE
  blender_manifest.toml
  docs/
    ARCHITECTURE.md
    RELEASE.md
  tools/
    build_release.ps1
```

## Install In Blender

1. Download this repository as a ZIP from GitHub, or use the packaged zip produced by `tools/build_release.ps1`.
2. In Blender, go to `Edit > Preferences > Add-ons > Install from Disk...`.
3. Select the downloaded repository ZIP or packaged addon ZIP directly.
4. Enable `OOTP ParkForge`.

For Blender 4.x, this repository includes a `blender_manifest.toml`, so the standard GitHub "Download ZIP" archive can be installed directly without repacking.

Menu entries:

- `File > Import > OOTP ParkForge POD (.pod)`
- `File > Export > OOTP ParkForge Package (.pod)`

Sidebar:

- `3D View > N > ParkForge`

## CompressonatorCLI

ParkForge can find `compressonatorcli.exe` in several ways:

- Sidebar `CLI Override`
- Add-on preferences path
- `OOTP_POD_COMPRESSONATOR` or `COMPRESSONATORCLI_PATH`
- System `PATH`
- Common local bundle locations such as `tools/compressonatorcli`

If stock OOTP imports appear pink, ParkForge is usually missing a valid `CompressonatorCLI` runtime.

## Export Workflow

1. Import or build your stadium scene in Blender
2. Set `Template POD` to a known-good OOTP stadium POD
3. Export to a writable staging folder such as `Documents` or a repo-local `dist` folder
4. Keep the exported POD filename the same as the template stadium filename when sidecars are copied
5. Review the generated package
6. Copy the result into a live OOTP stadium folder only after validation

Example:

- Template: `yankee_stadium.pod`
- Safe export name: `yankee_stadium.pod`
- Risky export name: `parkforge_export.pod`

If the POD filename and template stadium name do not match, OOTP can crash while loading the package.

ParkForge intentionally blocks direct export into protected Windows install paths like `Program Files` to avoid half-written packages and confusing permission failures.

## Development Notes

- Keep the package root Blender-friendly
- Put project docs in `docs/`
- Put repeatable packaging or release helpers in `tools/`
- Avoid generated files in source control

More detail:

- [Architecture](docs/ARCHITECTURE.md)
- [Release Process](docs/RELEASE.md)
