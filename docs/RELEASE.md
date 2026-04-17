# Release Process

This repository is the source package. Release bundles should be generated, not hand-assembled.

## Recommended Flow

1. Update `bl_info["version"]` in `__init__.py`
2. Run `tools/build_release.ps1`
3. Review the generated `dist/` folder
4. Publish the source repo and attach the generated zip to the GitHub release

## Output Goals

The build script should produce:

- Blender-installable add-on zip
- optional release bundle with runtime dependencies
- checksums for generated artifacts

## Notes

- Keep source control clean; `dist/` is ignored
- Do not export directly into protected OOTP install paths from Blender
- Prefer staging packages first, then copying into live OOTP folders
