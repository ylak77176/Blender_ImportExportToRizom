bl_info = {
    "name": "Rizom Bridge - Import/Export to RizomUV",
    "author": "Ylak",
    "version": (2, 2),
    "blender": (2, 93, 0),
    "location": "View3D > Sidebar > Rizom  |  Edit > Preferences > Add-ons",
    "description": "Import/Export an OBJ between Blender and RizomUV, with configurable folder paths and timestamped backups",
    "warning": "",
    "category": "Import-Export",
}

import bpy
import subprocess
import os
import shutil
import glob
from datetime import datetime
import xml.etree.ElementTree as ET
from bpy.types import AddonPreferences, Operator, Panel
from bpy.props import StringProperty, IntProperty


# ---------------------------------------------------------------------------
# Constants & Helpers
# ---------------------------------------------------------------------------

EXCHANGE_FILE_NAME = "RizomBridge_Exchange.obj"


def get_addon_dir():
    """Folder this add-on lives in (works because this is a package, not a text-block)."""
    return os.path.dirname(os.path.abspath(__file__))


def default_config_path():
    return os.path.join(get_addon_dir(), "config.xml")


def get_prefs(context):
    return context.preferences.addons[__name__].preferences


def normalize(path):
    return path.replace("\\", "/") if path else path


def get_full_obj_path(export_dir):
    """Combines the user-defined folder path with the hardcoded exchange file name."""
    if not export_dir:
        return ""
    return os.path.join(export_dir, EXCHANGE_FILE_NAME)


def get_backup_dir(export_dir):
    """Returns the backup directory path inside the export folder."""
    if not export_dir:
        return ""
    return os.path.join(export_dir, "Backups")


def manage_backups(backup_dir, max_backups):
    """Keeps only the newest N backup files, deletes the oldest."""
    if not os.path.exists(backup_dir):
        return
        
    search_pattern = os.path.join(backup_dir, "Backup_*.obj")
    backups = glob.glob(search_pattern)
    
    backups.sort(key=os.path.getmtime)
    
    while len(backups) >= max_backups:
        oldest_file = backups.pop(0)
        try:
            os.remove(oldest_file)
        except Exception as e:
            print(f"Rizom Bridge: Failed to delete old backup {oldest_file}: {e}")


# ---------------------------------------------------------------------------
# Preferences (Edit > Preferences > Add-ons > Rizom Bridge)
# ---------------------------------------------------------------------------

class RizomBridgePreferences(AddonPreferences):
    bl_idname = __name__

    export_dir: StringProperty(
        name="Export Folder",
        description="Path to the directory where the exchange .obj file and backups will be saved",
        subtype='DIR_PATH',
        default=r"C:\BlenderToRizom",
    )

    rizomuv_exe: StringProperty(
        name="RizomUV Executable",
        description="Path to rizomuv.exe",
        subtype='FILE_PATH',
        default="",
    )

    max_backups: IntProperty(
        name="Max Backups",
        description="Maximum number of historical timestamped OBJ files to keep in the backup folder",
        default=15,
        min=1,
        max=500
    )

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="Paths & Settings", icon='FILE_FOLDER')
        box.prop(self, "export_dir")
        box.prop(self, "rizomuv_exe")
        box.prop(self, "max_backups")

        row = layout.row(align=True)
        row.operator("rizombridge.load_config_xml", icon='IMPORT')
        row.operator("rizombridge.save_config_xml", icon='EXPORT')

        # Inform the user what the generated file will be called
        full_path = get_full_obj_path(self.export_dir)
        box.label(text=f"Target File: {full_path}" if self.export_dir else "Target File: Not configured", icon='LINKED')

        layout.label(text=f"config.xml location: {default_config_path()}", icon='INFO')


# ---------------------------------------------------------------------------
# config.xml load / save operators
# ---------------------------------------------------------------------------

class RIZOMBRIDGE_OT_load_config_xml(Operator):
    """Load paths from config.xml into the fields above"""
    bl_idname = "rizombridge.load_config_xml"
    bl_label = "Load from config.xml"

    def execute(self, context):
        config_path = default_config_path()
        if not os.path.exists(config_path):
            self.report({'ERROR'}, f"No config.xml found at: {config_path}")
            return {'CANCELLED'}

        try:
            tree = ET.parse(config_path)
            root = tree.getroot()
            export_dir = root.find("./paths/export_dir")
            rizomuv_exe = root.find("./paths/rizomuv_exe")
            max_backups = root.find("./settings/max_backups")

            prefs = get_prefs(context)
            if export_dir is not None and export_dir.text:
                prefs.export_dir = export_dir.text.strip()
            if rizomuv_exe is not None and rizomuv_exe.text:
                prefs.rizomuv_exe = rizomuv_exe.text.strip()
            if max_backups is not None and max_backups.text:
                try:
                    prefs.max_backups = int(max_backups.text.strip())
                except ValueError:
                    pass

            self.report({'INFO'}, "Loaded configuration from config.xml")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to read config.xml: {e}")
            return {'CANCELLED'}


class RIZOMBRIDGE_OT_save_config_xml(Operator):
    """Save the fields above into config.xml"""
    bl_idname = "rizombridge.save_config_xml"
    bl_label = "Save to config.xml"

    def execute(self, context):
        prefs = get_prefs(context)
        config_path = default_config_path()

        root = ET.Element("config")
        paths = ET.SubElement(root, "paths")
        export_dir_el = ET.SubElement(paths, "export_dir")
        export_dir_el.text = prefs.export_dir
        rizomuv_exe_el = ET.SubElement(paths, "rizomuv_exe")
        rizomuv_exe_el.text = prefs.rizomuv_exe

        settings = ET.SubElement(root, "settings")
        max_backups_el = ET.SubElement(settings, "max_backups")
        max_backups_el.text = str(prefs.max_backups)

        try:
            tree = ET.ElementTree(root)
            ET.indent(tree, space="    ")  
            tree.write(config_path, encoding="utf-8", xml_declaration=True)
            self.report({'INFO'}, f"Saved config.xml to: {config_path}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to write config.xml: {e}")
            return {'CANCELLED'}


# ---------------------------------------------------------------------------
# Import / Export / System operators
# ---------------------------------------------------------------------------

class RIZOMBRIDGE_OT_import_obj(Operator):
    """Import the OBJ file from the configured folder"""
    bl_idname = "import.autoexport_rizom_obj"
    bl_label = "Import from RizomUV"

    def execute(self, context):
        prefs = get_prefs(context)
        file_path = normalize(get_full_obj_path(prefs.export_dir))

        if not file_path or not prefs.export_dir:
            self.report({'ERROR'}, "Export folder path is not set. Check the add-on preferences.")
            return {'CANCELLED'}

        if not os.path.exists(file_path):
            self.report({'ERROR'}, f"No exchange file found to import at: {file_path}")
            return {'CANCELLED'}

        try:
            bpy.ops.wm.obj_import(filepath=file_path)
            self.report({'INFO'}, f"Successfully imported: {file_path}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to import .obj file: {e}")
            return {'CANCELLED'}


class RIZOMBRIDGE_OT_export_and_open(Operator):
    """Export the selected object to OBJ, backup previous run, and open it in RizomUV"""
    bl_idname = "object.export_and_open_rizomuv"
    bl_label = "Export and Open in RizomUV"

    def execute(self, context):
        prefs = get_prefs(context)
        export_dir = prefs.export_dir
        rizomuv_path = prefs.rizomuv_exe
        export_path = get_full_obj_path(export_dir)

        if not export_dir:
            self.report({'ERROR'}, "Export folder path is not set. Check the add-on preferences.")
            return {'CANCELLED'}
        if not rizomuv_path:
            self.report({'ERROR'}, "RizomUV executable path is not set. Check the add-on preferences.")
            return {'CANCELLED'}
        if not context.selected_objects:
            self.report({'ERROR'}, "No object selected to export.")
            return {'CANCELLED'}

        if export_dir and not os.path.exists(export_dir):
            try:
                os.makedirs(export_dir)
            except Exception as e:
                self.report({'ERROR'}, f"Could not create folder '{export_dir}': {e}")
                return {'CANCELLED'}

        # 1. Handle Backup Before Overwriting Existing OBJ Data
        if os.path.exists(export_path):
            backup_dir = get_backup_dir(export_dir)
            if not os.path.exists(backup_dir):
                os.makedirs(backup_dir)
            
            manage_backups(backup_dir, prefs.max_backups)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_filename = f"Backup_{timestamp}.obj"
            backup_file_path = os.path.join(backup_dir, backup_filename)
            
            try:
                shutil.copy2(export_path, backup_file_path)
            except Exception as e:
                self.report({'WARNING'}, f"Could not create historical backup file: {e}")

        # 2. Run standard Export
        try:
            bpy.ops.wm.obj_export(
                filepath=export_path,
                export_selected_objects=True,
                export_materials=False,
                export_uv=True,
                export_animation=False,
            )
            subprocess.Popen([rizomuv_path, export_path])
            self.report({'INFO'}, f"Exported and opened in RizomUV: {export_path}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to export/open RizomUV: {e}")
            return {'CANCELLED'}


class RIZOMBRIDGE_OT_open_backup_folder(Operator):
    """Open the historical backup folder in your native OS file explorer"""
    bl_idname = "rizombridge.open_backup_folder"
    bl_label = "Open Backup Folder"
    
    def execute(self, context):
        prefs = get_prefs(context)
        backup_dir = get_backup_dir(prefs.export_dir)
        
        if not backup_dir:
            self.report({'ERROR'}, "Export Folder is empty. Configure it in Preferences first.")
            return {'CANCELLED'}
            
        if not os.path.exists(backup_dir):
            try:
                os.makedirs(backup_dir)
            except Exception as e:
                self.report({'ERROR'}, f"Could not open/create folder directory: {e}")
                return {'CANCELLED'}
                
        try:
            bpy.ops.wm.path_open(filepath=backup_dir)
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to view directory: {e}")
            return {'CANCELLED'}


# ---------------------------------------------------------------------------
# Sidebar panel (N-panel in the 3D Viewport)
# ---------------------------------------------------------------------------

class RIZOMBRIDGE_PT_panel(Panel):
    bl_label = "Rizom Bridge"
    bl_idname = "RIZOMBRIDGE_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Rizom"

    def draw(self, context):
        layout = self.layout
        
        col = layout.column(align=True)
        col.operator("import.autoexport_rizom_obj", icon='IMPORT')
        col.operator("object.export_and_open_rizomuv", icon='EXPORT')
        
        layout.separator()
        layout.operator("rizombridge.open_backup_folder", icon='FILE_FOLDER')


# ---------------------------------------------------------------------------
# Register / Unregister
# ---------------------------------------------------------------------------

classes = (
    RizomBridgePreferences,
    RIZOMBRIDGE_OT_load_config_xml,
    RIZOMBRIDGE_OT_save_config_xml,
    RIZOMBRIDGE_OT_import_obj,
    RIZOMBRIDGE_OT_export_and_open,
    RIZOMBRIDGE_OT_open_backup_folder,
    RIZOMBRIDGE_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    # Auto-load config.xml on first enable, if present and prefs are empty
    try:
        addon_prefs = bpy.context.preferences.addons[__name__].preferences
        config_path = default_config_path()
        if os.path.exists(config_path) and not addon_prefs.export_dir:
            tree = ET.parse(config_path)
            root = tree.getroot()
            export_dir = root.find("./paths/export_dir")
            rizomuv_exe = root.find("./paths/rizomuv_exe")
            max_backups = root.find("./settings/max_backups")
            
            if export_dir is not None and export_dir.text:
                addon_prefs.export_dir = export_dir.text.strip()
            if rizomuv_exe is not None and rizomuv_exe.text:
                addon_prefs.rizomuv_exe = rizomuv_exe.text.strip()
            if max_backups is not None and max_backups.text:
                try:
                    addon_prefs.max_backups = int(max_backups.text.strip())
                except ValueError:
                    pass
    except Exception:
        pass  


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()