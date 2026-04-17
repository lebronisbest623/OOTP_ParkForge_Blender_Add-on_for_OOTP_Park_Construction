from __future__ import annotations

bl_info = {
    "name": "OOTP ParkForge",
    "author": "Codex",
    "version": (0, 2, 1),
    "blender": (4, 0, 0),
    "location": "File > Import / Export > OOTP ParkForge",
    "description": "Import OOTP AB.POD.2.0 ballparks and export Blender scenes back to OOTP stadium packages",
    "category": "Import-Export",
}

import os
from pathlib import Path

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import pod_exporter, pod_importer


ADDON_PACKAGE = __package__ or Path(__file__).resolve().parent.name
ADDON_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = ADDON_DIR.parents[2]
DEFAULT_EXPORT_DIR = WORKSPACE_ROOT / "temp" / "parkforge_exports"


def _iter_ootp_install_roots() -> list[Path]:
    roots: list[Path] = []
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam" / "steamapps" / "common" / "Out of the Park Baseball 27",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Steam" / "steamapps" / "common" / "Out of the Park Baseball 27",
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\Out of the Park Baseball 27"),
        Path(r"C:\Program Files\Steam\steamapps\common\Out of the Park Baseball 27"),
    ]
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            roots.append(candidate)
    return roots


def _find_default_template_pod() -> Path | None:
    preferred_relatives = [
        Path("data") / "ballparks" / "models" / "american_family_field" / "american_family_field.pod",
        Path("data") / "ballparks" / "models" / "busan" / "busan.pod",
    ]
    for root in _iter_ootp_install_roots():
        for relative in preferred_relatives:
            candidate = root / relative
            if candidate.exists():
                return candidate
        models_dir = root / "data" / "ballparks" / "models"
        if not models_dir.exists():
            continue
        for candidate in sorted(models_dir.rglob("*.pod")):
            if candidate.is_file():
                return candidate
    return None


def _resolve_template_pod_path(raw_path: str, context: bpy.types.Context | None = None) -> Path:
    explicit = Path(raw_path.strip()) if raw_path and raw_path.strip() else None
    if explicit and explicit.exists():
        return explicit

    if context is not None:
        imported = context.scene.get("ootp_pod_last_imported_template_path")
        if imported:
            imported_path = Path(str(imported))
            if imported_path.exists():
                return imported_path

    auto = _find_default_template_pod()
    if auto is not None:
        return auto

    if explicit:
        raise FileNotFoundError(f"Template POD not found: {explicit}")
    raise FileNotFoundError(
        "No template POD specified and no stock OOTP template could be auto-detected. "
        "Install OOTP 27 or set Template POD manually."
    )


def _normalize_output_pod_path(output_path: str | Path, template_pod: Path) -> Path:
    output = Path(output_path)
    suffix = output.suffix if output.suffix.lower() == ".pod" else ".pod"
    return output.with_name(f"{template_pod.stem}{suffix}")


class OOTP_POD_AddonPreferences(AddonPreferences):
    bl_idname = ADDON_PACKAGE

    compressonator_cli_path: StringProperty(
        name="CompressonatorCLI",
        description="Optional path to compressonatorcli.exe for decoding OOTP KTX textures during import",
        subtype="FILE_PATH",
    )

    def draw(self, _context):
        layout = self.layout
        layout.label(text="Optional texture decode tool")
        layout.prop(self, "compressonator_cli_path")


class IMPORT_SCENE_OT_ootp_pod(Operator, ImportHelper):
    bl_idname = "import_scene.ootp_pod"
    bl_label = "Import OOTP ParkForge POD"
    bl_options = {"UNDO"}

    filename_ext = ".pod"
    filter_glob: StringProperty(default="*.pod", options={"HIDDEN"})
    create_parent_empties: BoolProperty(
        name="Create Empty Nodes",
        description="Create Blender empties for POD nodes that do not directly reference a mesh",
        default=True,
    )

    def execute(self, context):
        return pod_importer.import_pod(context, self.filepath, self.create_parent_empties)


class SCENE_OT_ootp_pick_template_pod(Operator, ImportHelper):
    bl_idname = "scene.ootp_pick_template_pod"
    bl_label = "Choose Template POD"

    filename_ext = ".pod"
    filter_glob: StringProperty(default="*.pod", options={"HIDDEN"})

    def execute(self, context):
        context.scene.ootp_pod_template_path = self.filepath
        try:
            template_pod = _resolve_template_pod_path(self.filepath, context)
            current_output = getattr(context.scene, "ootp_pod_export_path", "").strip()
            if current_output:
                context.scene.ootp_pod_export_path = str(_normalize_output_pod_path(current_output, template_pod))
        except Exception:
            pass
        return {"FINISHED"}


class EXPORT_SCENE_OT_ootp_pod_package(Operator, ExportHelper):
    bl_idname = "export_scene.ootp_pod_package"
    bl_label = "Export OOTP ParkForge Package"

    filename_ext = ".pod"
    filter_glob: StringProperty(default="*.pod", options={"HIDDEN"})
    template_pod: StringProperty(
        name="Template POD",
        description="Existing OOTP POD used as the structural template for the exported package",
        subtype="FILE_PATH",
    )
    selected_only: BoolProperty(
        name="Selected Objects Only",
        description="Export only currently selected mesh objects",
        default=False,
    )
    copy_template_sidecars: BoolProperty(
        name="Copy Template Sidecars",
        description="Copy .ootp3d, .prk and other sidecar files from the template folder into the export folder",
        default=True,
    )

    def execute(self, context):
        wm = context.window_manager
        try:
            resolved_template = _resolve_template_pod_path(self.template_pod, context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self.template_pod = str(resolved_template)
        context.scene.ootp_pod_template_path = self.template_pod
        normalized_output = _normalize_output_pod_path(self.filepath, resolved_template)
        self.filepath = str(normalized_output)
        context.scene.ootp_pod_export_path = self.filepath

        def progress_cb(fraction: float, message: str):
            step = int(fraction * 10000)
            wm.progress_update(step)
            if hasattr(context.workspace, "status_text_set"):
                context.workspace.status_text_set(text=f"ParkForge: {message}")

        wm.progress_begin(0, 10000)
        try:
            result = pod_exporter.export_current_scene_to_pod_package(
                context,
                self.filepath,
                resolved_template,
                selected_only=self.selected_only,
                copy_template_sidecars=self.copy_template_sidecars,
                progress_cb=progress_cb,
            )
        except Exception as exc:
            wm.progress_end()
            if hasattr(context.workspace, "status_text_set"):
                context.workspace.status_text_set(text=None)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        wm.progress_end()
        if hasattr(context.workspace, "status_text_set"):
            context.workspace.status_text_set(text=None)

        self.report(
            {"INFO"},
            f"Exported {result['mesh_count']} meshes / {result['material_count']} materials to {result['output_pod']}",
        )
        return {"FINISHED"}


class EXPORT_SCENE_OT_ootp_pod_package_quick(Operator):
    bl_idname = "export_scene.ootp_pod_package_quick"
    bl_label = "Quick Export OOTP ParkForge Package"

    def execute(self, context):
        scene = context.scene
        wm = context.window_manager
        output_path = getattr(scene, "ootp_pod_export_path", "").strip()
        template_path = getattr(scene, "ootp_pod_template_path", "").strip()

        if not output_path:
            self.report({"ERROR"}, "Set Output POD Path in the OOTP ParkForge sidebar first")
            return {"CANCELLED"}
        try:
            resolved_template = _resolve_template_pod_path(template_path, context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        scene.ootp_pod_template_path = str(resolved_template)
        output_path = str(_normalize_output_pod_path(output_path, resolved_template))
        scene.ootp_pod_export_path = output_path

        def progress_cb(fraction: float, message: str):
            step = int(fraction * 10000)
            wm.progress_update(step)
            if hasattr(context.workspace, "status_text_set"):
                context.workspace.status_text_set(text=f"ParkForge: {message}")

        wm.progress_begin(0, 10000)
        try:
            result = pod_exporter.export_current_scene_to_pod_package(
                context,
                output_path,
                resolved_template,
                selected_only=getattr(scene, "ootp_pod_selected_only", False),
                copy_template_sidecars=getattr(scene, "ootp_pod_copy_sidecars", True),
                progress_cb=progress_cb,
            )
        except Exception as exc:
            wm.progress_end()
            if hasattr(context.workspace, "status_text_set"):
                context.workspace.status_text_set(text=None)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        wm.progress_end()
        if hasattr(context.workspace, "status_text_set"):
            context.workspace.status_text_set(text=None)

        self.report(
            {"INFO"},
            f"Exported {result['mesh_count']} meshes / {result['material_count']} materials",
        )
        return {"FINISHED"}


class VIEW3D_PT_ootp_pod_tools(Panel):
    bl_label = "OOTP ParkForge"
    bl_idname = "VIEW3D_PT_ootp_pod_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ParkForge"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        prefs = pod_importer.addon_preferences()
        auto_template = _find_default_template_pod()
        imported_template = scene.get("ootp_pod_last_imported_template_path")

        col = layout.column(align=True)
        col.operator(IMPORT_SCENE_OT_ootp_pod.bl_idname, text="Import Stadium", icon="IMPORT")

        col.separator()
        template_text = getattr(scene, "ootp_pod_template_path", "").strip()
        row = col.row(align=True)
        if template_text:
            row.label(text=Path(template_text).name, icon="FILE")
        else:
            row.label(text="No template selected", icon="FILE")
        row.operator(SCENE_OT_ootp_pick_template_pod.bl_idname, text="Choose Template", icon="FILEBROWSER")
        if not template_text:
            if imported_template and Path(str(imported_template)).exists():
                col.label(text=f"Auto: {Path(str(imported_template)).name}", icon="FILE_TICK")
            elif auto_template is not None:
                col.label(text=f"Auto: {auto_template.name}", icon="FILE_TICK")
        col.prop(scene, "ootp_pod_export_path", text="Output")
        col.prop(scene, "ootp_pod_selected_only", text="Selected Only")
        col.label(text="Use a temp/Documents folder for export", icon="INFO")
        col.operator(EXPORT_SCENE_OT_ootp_pod_package_quick.bl_idname, text="Quick Export", icon="EXPORT")

        op = col.operator(EXPORT_SCENE_OT_ootp_pod_package.bl_idname, text="Export As...", icon="FILEBROWSER")
        op.template_pod = getattr(scene, "ootp_pod_template_path", "")
        op.selected_only = getattr(scene, "ootp_pod_selected_only", False)
        op.copy_template_sidecars = getattr(scene, "ootp_pod_copy_sidecars", True)

        if prefs:
            col.separator()
            cli_path = pod_importer.find_compressonator_cli()
            row = col.row()
            row.alert = cli_path is None
            row.label(text="CompressonatorCLI: Found" if cli_path else "CompressonatorCLI: Missing")
            col.prop(scene, "ootp_pod_compressonator_cli_path", text="CLI Override")


def menu_func_import(self, _context):
    self.layout.operator(IMPORT_SCENE_OT_ootp_pod.bl_idname, text="OOTP ParkForge POD (.pod)")


def menu_func_export(self, _context):
    self.layout.operator(EXPORT_SCENE_OT_ootp_pod_package.bl_idname, text="OOTP ParkForge Package (.pod)")


classes = (
    OOTP_POD_AddonPreferences,
    IMPORT_SCENE_OT_ootp_pod,
    SCENE_OT_ootp_pick_template_pod,
    EXPORT_SCENE_OT_ootp_pod_package,
    EXPORT_SCENE_OT_ootp_pod_package_quick,
    VIEW3D_PT_ootp_pod_tools,
)


def register():
    bpy.types.Scene.ootp_pod_template_path = StringProperty(
        name="Template POD",
        description="Existing OOTP stadium POD used as the export template",
        subtype="FILE_PATH",
    )
    bpy.types.Scene.ootp_pod_export_path = StringProperty(
        name="Output POD",
        description="Where to write the exported OOTP POD file",
        subtype="FILE_PATH",
        default=str(DEFAULT_EXPORT_DIR / "parkforge_export.pod"),
    )
    bpy.types.Scene.ootp_pod_selected_only = BoolProperty(
        name="Selected Only",
        description="Export only selected mesh objects",
        default=False,
    )
    bpy.types.Scene.ootp_pod_copy_sidecars = BoolProperty(
        name="Copy Sidecars",
        description="Copy template folder sidecar files to the export folder",
        default=True,
    )
    bpy.types.Scene.ootp_pod_compressonator_cli_path = StringProperty(
        name="CompressonatorCLI",
        description="Optional per-scene override path to compressonatorcli.exe for KTX decoding",
        subtype="FILE_PATH",
    )

    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.ootp_pod_compressonator_cli_path
    del bpy.types.Scene.ootp_pod_copy_sidecars
    del bpy.types.Scene.ootp_pod_selected_only
    del bpy.types.Scene.ootp_pod_export_path
    del bpy.types.Scene.ootp_pod_template_path


if __name__ == "__main__":
    register()
