from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

import bpy


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a subtle starfield background in Blender.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--star-texture", required=True)
    parser.add_argument("--seconds", type=float, default=4.0)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--seed", type=int, default=20260705)
    parser.add_argument("--blend", default="")
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    return parser.parse_args(argv)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _look_at(obj: bpy.types.Object, target: tuple[float, float, float]) -> None:
    dx = target[0] - obj.location.x
    dy = target[1] - obj.location.y
    dz = target[2] - obj.location.z
    direction = mathutils.Vector((dx, dy, dz))
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _emission_material(name: str, color: tuple[float, float, float, float], strength: float) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    for node in nodes:
        nodes.remove(node)
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = strength
    mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def _transparent_principled_material(
    name: str,
    color: tuple[float, float, float, float],
    alpha: float,
) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.blend_method = "BLEND"
    if hasattr(mat, "use_screen_refraction"):
        mat.use_screen_refraction = False
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Alpha"].default_value = alpha
        bsdf.inputs["Roughness"].default_value = 1.0
        bsdf.inputs["Metallic"].default_value = 0.0
    mat.show_transparent_back = True
    return mat


def _starfield_material(star_texture: Path) -> bpy.types.Material:
    mat = bpy.data.materials.new("nasa_starfield_emission")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    for node in nodes:
        nodes.remove(node)
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = bpy.data.images.load(str(star_texture))
    emission.inputs["Strength"].default_value = 1.35
    mat.node_tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
    mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def _add_plane(name: str, z: float, width: float, height: float, mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, z), rotation=(0, 0, 0))
    obj = bpy.context.object
    obj.name = name
    obj.scale = (width, height, 1)
    obj.data.materials.append(mat)
    return obj


def _add_real_texture_plate(star_texture: Path) -> None:
    mat = _starfield_material(star_texture)
    plate = _add_plane("real_nasa_starfield_plate", -78, 180, 104, mat)
    plate.rotation_euler[2] = 0


def _add_soft_color_plates() -> None:
    _add_plane(
        "readability_lift",
        -18,
        120,
        72,
        _transparent_principled_material("readability_lift_material", (0.58, 0.64, 0.78, 1), 0.20),
    )
    for idx, (x, y, z, sx, sy, color, alpha) in enumerate(
        [
            (-18.5, 8.0, -28, 72, 24, (0.70, 0.63, 1.0, 1), 0.045),
            (19.0, -7.2, -33, 78, 28, (0.35, 0.72, 1.0, 1), 0.04),
            (1.5, -13.2, -42, 88, 24, (1.0, 0.75, 0.42, 1), 0.025),
        ]
    ):
        obj = _add_plane(
            f"soft_haze_{idx}",
            z,
            sx,
            sy,
            _transparent_principled_material(f"soft_haze_material_{idx}", color, alpha),
        )
        obj.location.x = x
        obj.location.y = y
        obj.rotation_euler[2] = math.radians(-3 + idx * 4)


def _add_3d_stars(seed: int) -> None:
    rng = random.Random(seed)
    cold = _emission_material("near_star_cold", (0.74, 0.86, 1.0, 1), 2.6)
    white = _emission_material("near_star_white", (1.0, 0.98, 0.92, 1), 2.35)
    warm = _emission_material("near_star_warm", (1.0, 0.77, 0.48, 1), 2.0)
    materials = [cold, white, white, white, warm]
    mesh = None
    for idx in range(360):
        z = -rng.uniform(11, 58)
        spread = abs(z) * 0.54
        x = rng.uniform(-spread, spread)
        y = rng.uniform(-spread * 0.55, spread * 0.55)
        radius = rng.choice([0.018, 0.022, 0.026, 0.032, 0.044])
        if rng.random() < 0.035:
            radius *= 2.2
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=radius, location=(x, y, z))
        obj = bpy.context.object
        obj.name = f"near_star_{idx:03d}"
        if mesh is None:
            mesh = obj.data
        obj.data.materials.append(rng.choice(materials))
        obj.hide_select = True


def _setup_camera(frame_end: int, fps: int) -> None:
    bpy.ops.object.camera_add(location=(0, 0, 0), rotation=(0, 0, 0))
    camera = bpy.context.object
    camera.data.lens = 36
    camera.data.dof.use_dof = True
    camera.data.dof.focus_distance = 46
    camera.data.dof.aperture_fstop = 7.5
    bpy.context.scene.camera = camera
    for frame, loc in [
        (1, (0.0, 0.0, 0.0)),
        (frame_end, (0.055, -0.025, -0.36)),
    ]:
        bpy.context.scene.frame_set(frame)
        camera.location = loc
        _look_at(camera, (0.0, 0.0, -36.0))
        camera.keyframe_insert(data_path="location")
        camera.keyframe_insert(data_path="rotation_euler")
    if camera.animation_data and camera.animation_data.action:
        fcurves = getattr(camera.animation_data.action, "fcurves", None)
        if not fcurves:
            return
        for fcurve in fcurves:
            for keyframe in fcurve.keyframe_points:
                keyframe.interpolation = "BEZIER"


def _setup_scene(args: argparse.Namespace) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    if hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = 48
    if hasattr(scene.eevee, "use_gtao"):
        scene.eevee.use_gtao = True
    if hasattr(scene.eevee, "gtao_distance"):
        scene.eevee.gtao_distance = 3
    if hasattr(scene.eevee, "gtao_factor"):
        scene.eevee.gtao_factor = 0.25
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.height
    scene.render.fps = args.fps
    scene.frame_start = 1
    scene.frame_end = max(2, int(round(args.seconds * args.fps)))
    scene.render.film_transparent = False
    scene.world = bpy.data.worlds.new("starfield_world") if not scene.world else scene.world
    scene.world.color = (0.33, 0.36, 0.46)
    scene.view_settings.view_transform = "Filmic"
    scene.view_settings.look = "Medium High Contrast"
    scene.view_settings.exposure = -0.18
    scene.view_settings.gamma = 1.0

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = output.with_suffix("")
    frames_dir = frames_dir.parent / f"{frames_dir.name}_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(frames_dir / "frame_")
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"


def main() -> None:
    args = _args()
    star_texture = Path(args.star_texture)
    if not star_texture.exists():
        raise FileNotFoundError(star_texture)
    _clear_scene()
    _setup_scene(args)
    _add_real_texture_plate(star_texture)
    _add_soft_color_plates()
    _add_3d_stars(args.seed)
    _setup_camera(bpy.context.scene.frame_end, args.fps)
    if args.blend:
        blend = Path(args.blend)
        blend.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.render.render(animation=True)


if __name__ == "__main__":
    import mathutils

    main()
