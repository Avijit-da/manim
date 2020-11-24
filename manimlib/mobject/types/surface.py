import numpy as np
import moderngl

from manimlib.constants import *
from manimlib.mobject.mobject import Mobject
from manimlib.utils.bezier import integer_interpolate
from manimlib.utils.bezier import interpolate
from manimlib.utils.color import color_to_rgba
from manimlib.utils.color import rgb_to_color
from manimlib.utils.config_ops import digest_config
from manimlib.utils.images import get_full_raster_image_path
from manimlib.utils.space_ops import normalize_along_axis


class ParametricSurface(Mobject):
    CONFIG = {
        "u_range": (0, 1),
        "v_range": (0, 1),
        # Resolution counts number of points sampled, which for
        # each coordinate is one more than the the number of rows/columns
        # of approximating squares
        "resolution": (101, 101),
        "color": GREY,
        "opacity": 1.0,
        "gloss": 0.3,
        "shadow": 0.4,
        "prefered_creation_axis": 1,
        # For du and dv steps.  Much smaller and numerical error
        # can crop up in the shaders.
        "epsilon": 1e-5,
        "render_primative": moderngl.TRIANGLES,
        "depth_test": True,
        "vert_shader_file": "surface_vert.glsl",
        "frag_shader_file": "surface_frag.glsl",
        "shader_dtype": [
            ('point', np.float32, (3,)),
            ('du_point', np.float32, (3,)),
            ('dv_point', np.float32, (3,)),
            ('color', np.float32, (4,)),
        ]
    }

    def __init__(self, uv_func, **kwargs):
        digest_config(self, kwargs)
        self.uv_func = uv_func
        self.compute_triangle_indices()
        super().__init__(**kwargs)
        self.sort_faces_back_to_front()

    def init_points(self):
        dim = self.dim
        nu, nv = self.resolution
        u_range = np.linspace(*self.u_range, nu)
        v_range = np.linspace(*self.v_range, nv)

        # Get three lists:
        # - Points generated by pure uv values
        # - Those generated by values nudged by du
        # - Those generated by values nudged by dv
        point_lists = []
        for (du, dv) in [(0, 0), (self.epsilon, 0), (0, self.epsilon)]:
            uv_grid = np.array([[[u + du, v + dv] for v in v_range] for u in u_range])
            point_grid = np.apply_along_axis(lambda p: self.uv_func(*p), 2, uv_grid)
            point_lists.append(point_grid.reshape((nu * nv, dim)))
        # Rather than tracking normal vectors, the points list will hold on to the
        # infinitesimal nudged values alongside the original values.  This way, one
        # can perform all the manipulations they'd like to the surface, and normals
        # are still easily recoverable.
        self.points = np.vstack(point_lists)

    def compute_triangle_indices(self):
        # TODO, if there is an event which changes
        # the resolution of the surface, make sure
        # this is called.
        nu, nv = self.resolution
        if nu == 0 or nv == 0:
            self.triangle_indices = np.zeros(0, dtype=int)
            return
        index_grid = np.arange(nu * nv).reshape((nu, nv))
        indices = np.zeros(6 * (nu - 1) * (nv - 1), dtype=int)
        indices[0::6] = index_grid[:-1, :-1].flatten()  # Top left
        indices[1::6] = index_grid[+1:, :-1].flatten()  # Bottom left
        indices[2::6] = index_grid[:-1, +1:].flatten()  # Top right
        indices[3::6] = index_grid[:-1, +1:].flatten()  # Top right
        indices[4::6] = index_grid[+1:, :-1].flatten()  # Bottom left
        indices[5::6] = index_grid[+1:, +1:].flatten()  # Bottom right
        self.triangle_indices = indices

    def get_triangle_indices(self):
        return self.triangle_indices

    def init_colors(self):
        self.rgbas = np.zeros((1, 4))
        self.set_color(self.color, self.opacity)

    def get_surface_points_and_nudged_points(self):
        k = len(self.points) // 3
        return self.points[:k], self.points[k:2 * k], self.points[2 * k:]

    def get_unit_normals(self):
        s_points, du_points, dv_points = self.get_surface_points_and_nudged_points()
        normals = np.cross(
            (du_points - s_points) / self.epsilon,
            (dv_points - s_points) / self.epsilon,
        )
        return normalize_along_axis(normals, 1)

    def set_color(self, color, opacity=1.0, family=True):
        # TODO, allow for multiple colors
        rgba = color_to_rgba(color, opacity)
        mobs = self.get_family() if family else [self]
        for mob in mobs:
            mob.rgbas[:] = rgba
        return self

    def get_color(self):
        return rgb_to_color(self.rgbas[0, :3])

    def set_opacity(self, opacity, family=True):
        mobs = self.get_family() if family else [self]
        for mob in mobs:
            mob.rgbas[:, 3] = opacity
        return self

    def interpolate_color(self, mobject1, mobject2, alpha):
        self.rgbas = interpolate(mobject1.rgbas, mobject2.rgbas, alpha)
        return self

    def pointwise_become_partial(self, smobject, a, b, axis=None):
        if axis is None:
            axis = self.prefered_creation_axis
        assert(isinstance(smobject, ParametricSurface))
        self.points[:] = smobject.points[:]
        if a <= 0 and b >= 1:
            return self

        nu, nv = smobject.resolution
        self.points[:] = np.vstack([
            self.get_partial_points_array(arr, a, b, (nu, nv, 3), axis=axis)
            for arr in self.get_surface_points_and_nudged_points()
        ])
        return self

    def get_partial_points_array(self, points, a, b, resolution, axis):
        nu, nv = resolution[:2]
        points = points.reshape(resolution)
        max_index = resolution[axis] - 1
        lower_index, lower_residue = integer_interpolate(0, max_index, a)
        upper_index, upper_residue = integer_interpolate(0, max_index, b)
        if axis == 0:
            points[:lower_index] = interpolate(points[lower_index], points[lower_index + 1], lower_residue)
            points[upper_index:] = interpolate(points[upper_index], points[upper_index + 1], upper_residue)
        else:
            tuples = [
                (points[:, :lower_index], lower_index, lower_residue),
                (points[:, upper_index:], upper_index, upper_residue),
            ]
            for to_change, index, residue in tuples:
                col = interpolate(points[:, index], points[:, index + 1], residue)
                to_change[:] = col.reshape((nu, 1, *resolution[2:]))
        return points.reshape((nu * nv, *resolution[2:]))

    def sort_faces_back_to_front(self, vect=OUT):
        tri_is = self.triangle_indices
        indices = list(range(len(tri_is) // 3))
        indices.sort(key=lambda i: np.dot(self.points[tri_is[3 * i]], vect))
        for k in range(3):
            tri_is[k::3] = tri_is[k::3][indices]
        return self

    # For shaders
    def get_shader_data(self):
        s_points, du_points, dv_points = self.get_surface_points_and_nudged_points()
        data = self.get_blank_shader_data_array(len(s_points))
        data["point"] = s_points
        data["du_point"] = du_points
        data["dv_point"] = dv_points
        self.fill_in_shader_color_info(data)
        return data

    def fill_in_shader_color_info(self, data):
        data["color"] = self.rgbas
        return data

    def get_shader_vert_indices(self):
        return self.get_triangle_indices()


class SGroup(ParametricSurface):
    CONFIG = {
        "resolution": (0, 0),
    }

    def __init__(self, *parametric_surfaces, **kwargs):
        super().__init__(uv_func=None, **kwargs)
        self.add(*parametric_surfaces)

    def init_points(self):
        self.points = np.zeros((0, 3))


class TexturedSurface(ParametricSurface):
    CONFIG = {
        "vert_shader_file": "textured_surface_vert.glsl",
        "frag_shader_file": "textured_surface_frag.glsl",
        "shader_dtype": [
            ('point', np.float32, (3,)),
            ('du_point', np.float32, (3,)),
            ('dv_point', np.float32, (3,)),
            ('im_coords', np.float32, (2,)),
            ('opacity', np.float32, (1,)),
        ]
    }

    def __init__(self, uv_surface, image_file, dark_image_file=None, **kwargs):
        if not isinstance(uv_surface, ParametricSurface):
            raise Exception("uv_surface must be of type ParametricSurface")
        # Set texture information
        if dark_image_file is None:
            dark_image_file = image_file
            self.num_textures = 1
        else:
            self.num_textures = 2
        self.texture_paths = {
            "LightTexture": get_full_raster_image_path(image_file),
            "DarkTexture": get_full_raster_image_path(dark_image_file),
        }

        self.uv_surface = uv_surface
        self.uv_func = uv_surface.uv_func
        self.u_range = uv_surface.u_range
        self.v_range = uv_surface.v_range
        self.resolution = uv_surface.resolution
        super().__init__(self.uv_func, **kwargs)

    def init_points(self):
        self.points = self.uv_surface.points
        # Init im_coords
        nu, nv = self.uv_surface.resolution
        u_range = np.linspace(0, 1, nu)
        v_range = np.linspace(1, 0, nv)  # Reverse y-direction
        uv_grid = np.array([[u, v] for u in u_range for v in v_range])
        self.im_coords = uv_grid

    def init_colors(self):
        self.opacity = self.uv_surface.rgbas[:, 3]
        self.gloss = self.uv_surface.gloss

    def interpolate_color(self, mobject1, mobject2, alpha):
        # TODO, handle multiple textures
        self.opacity = interpolate(mobject1.opacity, mobject2.opacity, alpha)
        return self

    def set_opacity(self, opacity, family=True):
        self.opacity = opacity
        if family:
            for sm in self.submobjects:
                sm.set_opacity(opacity, family)
        return self

    def pointwise_become_partial(self, tsmobject, a, b, axis=1):
        super().pointwise_become_partial(tsmobject, a, b, axis)
        self.im_coords[:] = tsmobject.im_coords
        if a <= 0 and b >= 1:
            return self
        nu, nv = tsmobject.resolution
        self.im_coords[:] = self.get_partial_points_array(self.im_coords, a, b, (nu, nv, 2), axis)
        return self

    def get_shader_uniforms(self):
        result = super().get_shader_uniforms()
        result["num_textures"] = self.num_textures
        return result

    def fill_in_shader_color_info(self, data):
        data["im_coords"] = self.im_coords
        data["opacity"] = self.opacity
        return data