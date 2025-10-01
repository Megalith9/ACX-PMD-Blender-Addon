bl_info = {
    "name": "Import Ace Combat .PMD (from Noesis script)",
    "author": "Converted by ChatGPT (based on user's Noesis script)",
    "version": (1, 0),
    "blender": (2, 80, 0),
    "location": "File > Import",
    "description": "Import .PMD files (Ace Combat X: Skies of Deception) converted from a Noesis loader",
    "category": "Import-Export",
}

import bpy
import struct
import os
from mathutils import Matrix, Vector
from math import radians
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty
from bpy.types import Operator

# ---------------------------
# Binary helper
# ---------------------------
class BinReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.size = len(data)
    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        elif whence == 2:
            self.pos = self.size + offset
    def tell(self):
        return self.pos
    def read(self, fmt):
        if isinstance(fmt, int):
            end = self.pos + fmt
            b = self.data[self.pos:end]
            self.pos = end
            return b
        else:
            size = struct.calcsize(fmt)
            end = self.pos + size
            chunk = self.data[self.pos:end]
            self.pos = end
            return struct.unpack(fmt, chunk)
    def readBytes(self, n):
        return self.read(n)
    def getSize(self):
        return self.size

def read_cstring(b):
    if not b:
        return ""
    i = b.find(b'\x00')
    if i >= 0:
        return b[:i].decode('shift_jis', errors='ignore') if b else ""
    try:
        return b.decode('utf8', errors='ignore')
    except:
        return b.decode('latin1', errors='ignore')

# ---------------------------
# Core parser & Blender builder
# ---------------------------
def import_pmd(filepath):
    with open(filepath, 'rb') as f:
        data = f.read()
    br = BinReader(data)

    if data[:4] != b'PMD.':
        raise Exception("Not a PMD file (missing 'PMD.' header)")

    br.seek(9)
    num_bones = br.read('<H')[0]
    br.seek(32)
    inf = br.read('<8i')
    nameOfs = inf[0]
    matxOfs = inf[1]
    meshOfs = inf[3]

    bones = []
    br.seek(nameOfs)
    for i in range(num_bones):
        parent = br.read('<b')[0]
        rawname = br.readBytes(11)
        name = read_cstring(rawname)
        bones.append({
            "index": i,
            "name": name if name else ("bone_%d" % i),
            "parent": parent,
            "matrix": None
        })

    br.seek(matxOfs)
    for i in range(num_bones):
        vals = br.read('<16f')
        mat = Matrix((
            (vals[0], vals[1], vals[2], vals[3]),
            (vals[4], vals[5], vals[6], vals[7]),
            (vals[8], vals[9], vals[10], vals[11]),
            (vals[12], vals[13], vals[14], vals[15]),
        ))
        bones[i]["matrix"] = mat
        _ = br.read('<16f')

    br.seek(meshOfs)
    all_mesh_vertices = []
    mesh_count = 0
    while br.tell() < br.getSize():
        curPos = br.tell()
        try:
            minf = br.read('<3I3H2B')
        except:
            break
        magic = minf[0]
        if magic != 931:
            break
        sizeHead = minf[1]
        sizeBlock = minf[2]
        stride_bytes = br.readBytes(8)
        if len(stride_bytes) < 8:
            break
        stride = stride_bytes[-1]
        br.seek(4, 1)
        count_unsh = (sizeHead - 32) // 2
        unks = []
        for _ in range(count_unsh):
            try:
                v = br.read('<H')[0]
            except:
                v = 0
            unks.append(v)

        mesh_name = "mesh_%d" % mesh_count
        vertices = []
        uvs = []
        faces = []
        vert_global_index = 0
        for idx in range(0, len(unks), 2):
            poly = unks[idx]
            num = unks[idx+1] if (idx+1) < len(unks) else 0
            for strip_i in range(num):
                vbuf = br.readBytes(poly * stride)
                strip_verts = []
                strip_uvs = []
                for vi in range(poly):
                    base = vi * stride
                    u = v = 0.0
                    px = py = pz = 0.0
                    if base + 4 + 8 <= len(vbuf):
                        u, v = struct.unpack_from('<2f', vbuf, base + 4)
                    if base + 16 + 12 <= len(vbuf):
                        px, py, pz = struct.unpack_from('<3f', vbuf, base + 16)
                    strip_verts.append((px, py, pz))
                    strip_uvs.append((u, 1.0 - v))
                start_index = vert_global_index
                for sv in strip_verts:
                    vertices.append(sv)
                    vert_global_index += 1
                for suv in strip_uvs:
                    uvs.append(suv)
                n = len(strip_verts)
                for i in range(n - 2):
                    if i % 2 == 0:
                        tri = (start_index + i, start_index + i + 1, start_index + i + 2)
                    else:
                        tri = (start_index + i + 2, start_index + i + 1, start_index + i)
                    faces.append(tri)

        mesh_count += 1
        all_mesh_vertices.append({
            "name": mesh_name,
            "verts": vertices,
            "uvs": uvs,
            "faces": faces
        })
        br.seek(curPos + sizeBlock)

    created_mesh_objects = []
    for mesh_data in all_mesh_vertices:
        name = mesh_data["name"]
        verts = mesh_data["verts"]
        faces = mesh_data["faces"]
        uvs = mesh_data["uvs"]

        mesh = bpy.data.meshes.new(name)
        mesh_obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(mesh_obj)

        if verts:
            mesh.from_pydata(verts, [], faces)
            mesh.update()
        if uvs:
            if not mesh.uv_layers:
                mesh.uv_layers.new(name='UVMap')
            uv_layer = mesh.uv_layers.active.data
            for loop_idx, loop in enumerate(mesh.loops):
                vid = loop.vertex_index
                if vid < len(uvs):
                    uv_layer[loop_idx].uv = Vector(uvs[vid])
        created_mesh_objects.append(mesh_obj)

    arm_name = os.path.splitext(os.path.basename(filepath))[0] + "_arm"
    arm_data = bpy.data.armatures.new(arm_name)
    arm_obj = bpy.data.objects.new(arm_name, arm_data)
    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = arm_data.edit_bones
    created_bones = []
    for b in bones:
        eb = edit_bones.new(b["name"])
        mat = b["matrix"] if b["matrix"] is not None else Matrix.Identity(4)
        loc = mat.to_translation()
        eb.head = (loc.x, loc.y, loc.z)
        eb.tail = (loc.x, loc.y + 0.1, loc.z)
        created_bones.append(eb)

    for i, b in enumerate(bones):
        parent_idx = b["parent"]
        if parent_idx is not None and parent_idx >= 0 and parent_idx < len(created_bones):
            created_bones[i].parent = created_bones[parent_idx]

    bpy.ops.object.mode_set(mode='OBJECT')

    for obj in created_mesh_objects:
        mod = obj.modifiers.new(name="Armature", type='ARMATURE')
        mod.object = arm_obj

    # ---------------------------
    # Apply coordinate correction (+90° X and +90° Z)
    # ---------------------------
    rot_x = Matrix.Rotation(radians(90), 4, 'X')
    rot_z = Matrix.Rotation(radians(90), 4, 'Z')
    transform = rot_z @ rot_x

    for obj in created_mesh_objects:
        obj.matrix_world = transform @ obj.matrix_world
    arm_obj.matrix_world = transform @ arm_obj.matrix_world

    return created_mesh_objects, arm_obj

# ---------------------------
# Blender operator
# ---------------------------
class ImportPMD(Operator, ImportHelper):
    bl_idname = "import_scene.ace_pmd"
    bl_label = "Import Ace Combat PMD"
    filename_ext = ".pmd"
    filter_glob: StringProperty(default="*.pmd", options={'HIDDEN'})

    def execute(self, context):
        try:
            objs, arm = import_pmd(self.filepath)
            self.report({'INFO'}, f"Imported {len(objs)} mesh objects and armature '{arm.name}'.")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to import PMD: {str(e)}")
            return {'CANCELLED'}

# ---------------------------
# Registration
# ---------------------------
def menu_func_import(self, context):
    self.layout.operator(ImportPMD.bl_idname, text="Ace Combat PMD (.pmd)")

def register():
    bpy.utils.register_class(ImportPMD)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    bpy.utils.unregister_class(ImportPMD)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)

if __name__ == "__main__":
    register()
