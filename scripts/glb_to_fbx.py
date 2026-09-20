"""Convert a GLB written by `tesla-model-extract unreal` to FBX with Blender.

    blender --background --python scripts/glb_to_fbx.py -- input.glb output.fbx

Blender 3.6+ (its glTF importer handles KHR_texture_transform, clearcoat and emissive strength). Animations are baked
into the FBX so every closure clip survives as an action; the node hierarchy is kept as empties + meshes.
"""

from __future__ import annotations

import sys

import bpy  # type: ignore[import-not-found]


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(argv) != 2:
        raise SystemExit("usage: blender --background --python scripts/glb_to_fbx.py -- input.glb output.fbx")
    src, dst = argv
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=src, import_shading="NORMALS", guess_original_bind_pose=False)
    bpy.ops.export_scene.fbx(
        filepath=dst,
        use_selection=False,
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_ALL",
        bake_space_transform=True,
        object_types={"EMPTY", "MESH", "ARMATURE"},
        mesh_smooth_type="FACE",
        bake_anim=True,
        bake_anim_use_all_actions=True,
        bake_anim_use_nla_strips=False,
        bake_anim_force_startend_keying=True,
        add_leaf_bones=False,
        path_mode="COPY",
        embed_textures=True,
        axis_forward="-Z",
        axis_up="Y",
    )
    print(f"wrote {dst}")


if __name__ == "__main__":
    main()
