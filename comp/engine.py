"""Moteur de compositing GPU (ModernGL). Le même code sert au viewer et au rendu batch offscreen."""
from __future__ import annotations

from dataclasses import dataclass

import moderngl
import numpy as np

from . import color
from .media import CGFrame
from .settings import CompSettings, MaskSettings

VERT = """
#version 410 core
in vec2 in_pos;
uniform vec4 rect;      // x0, y0, x1, y1 en NDC
out vec2 uv;
void main() {
    vec2 t = in_pos * 0.5 + 0.5;
    uv = vec2(t.x, 1.0 - t.y);          // les images sont stockées ligne du haut en premier
    gl_Position = vec4(mix(rect.xy, rect.zw, t), 0.0, 1.0);
}
"""

COMP_FRAG_HEAD = """
#version 410 core
"""
COMP_FRAG = """
in vec2 uv;
out vec4 frag;
uniform sampler2D t_plate, t_beauty, t_diffuse, t_refl, t_shadow, t_masks;
uniform bool has_plate, has_cg, has_diffuse, has_refl, has_shadow;
uniform float plate_exp, cg_gain, diffuse_gain, refl_gain, shadow_density;
uniform vec3 refl_tint, shadow_color;
uniform bool shadow_invert;
// masques : x = actif (objets choisis), y = inversé, z = mix
uniform vec3 m_cg, m_refl, m_shadow;

float maskw(float raw, vec3 p) {
    if (p.x < 0.5) return 1.0;
    float v = p.y > 0.5 ? 1.0 - raw : raw;
    return mix(1.0, v, p.z);
}

void main() {
    vec3 bg = has_plate ? OCIOInput(vec4(texture(t_plate, uv).rgb, 1.0)).rgb * exp2(plate_exp) : vec3(0.0);
    if (!has_cg) { frag = vec4(bg, 1.0); return; }
    vec4 b = texture(t_beauty, uv);
    vec3 m = texture(t_masks, uv).rgb;
    float mcg = maskw(m.r, m_cg), mrf = maskw(m.g, m_refl), msh = maskw(m.b, m_shadow);

    vec3 cg = b.rgb;
    if (has_diffuse) cg += (diffuse_gain - 1.0) * texture(t_diffuse, uv).rgb;
    if (has_refl)    cg += (refl_gain * refl_tint - 1.0) * texture(t_refl, uv).rgb * mrf;
    cg *= cg_gain * mcg;
    float a = clamp(b.a * mcg, 0.0, 1.0);

    float s = 0.0;
    if (has_shadow) {
        vec4 sp = texture(t_shadow, uv);
        s = dot(sp.rgb, vec3(1.0 / 3.0));
        if (shadow_invert) s = 1.0 - s;
        s = clamp(s * shadow_density * msh, 0.0, 1.0);
    }
    vec3 bgs = mix(bg, bg * shadow_color, s);
    frag = vec4(cg + bgs * (1.0 - a), max(a, has_plate ? 1.0 : a));
}
"""

VIEW_FRAG_HEAD = """
#version 410 core
in vec2 uv;
out vec4 frag;
uniform sampler2D t_a, t_b, t_masks;
uniform int mode;          // 0 A, 1 B, 2 wipe, 3 différence
uniform float wipe;        // position du wipe (0-1, en x image)
uniform int channel;       // 0 RGB, 1 R, 2 G, 3 B, 4 A, 5 luminance
uniform float exposure, gamma;
uniform int overlay;       // -1 aucun, 0/1/2 masque cg/refl/shadow
uniform bool ocio_on;
"""

VIEW_FRAG_MAIN = """
void main() {
    vec2 fuv = vec2(uv.x, 1.0 - uv.y);   // les FBO sont stockés bas en premier
    vec4 a = texture(t_a, fuv), b = texture(t_b, fuv);
    vec4 c = a;
    if (mode == 1) c = b;
    else if (mode == 2) c = uv.x < wipe ? a : b;
    else if (mode == 3) c = vec4(abs(a.rgb - b.rgb), 1.0);
    c.rgb *= exp2(exposure);
    if (channel == 1) c.rgb = c.rrr;
    else if (channel == 2) c.rgb = c.ggg;
    else if (channel == 3) c.rgb = c.bbb;
    else if (channel == 4) c.rgb = c.aaa;
    else if (channel == 5) c.rgb = vec3(dot(c.rgb, vec3(0.2722287, 0.6740818, 0.0536895)));
    vec4 o = ocio_on ? OCIODisplay(vec4(c.rgb, 1.0)) : vec4(c.rgb, 1.0);
    if (gamma != 1.0) o.rgb = pow(max(o.rgb, 0.0), vec3(1.0 / gamma));
    if (overlay >= 0) {
        float m = texture(t_masks, uv)[overlay];
        o.rgb = mix(o.rgb, vec3(1.0, 0.78, 0.15), 0.45 * m);
    }
    if (mode == 2 && abs(uv.x - wipe) < 0.0015) o.rgb = vec3(0.95, 0.55, 0.1);
    frag = vec4(o.rgb, 1.0);
}
"""


@dataclass
class ViewParams:
    mode: int = 0
    wipe: float = 0.5
    channel: int = 0
    exposure: float = 0.0
    gamma: float = 1.0
    overlay: int = -1
    ocio_on: bool = True
    b_source: str = "plate"     # « plate » ou « raw » (CG sans réglages)


def _mask_vec(m: MaskSettings) -> tuple[float, float, float]:
    return (1.0 if m.objects else 0.0, 1.0 if m.invert else 0.0, float(m.mix))


def crypto_masks(cg: CGFrame | None, s: CompSettings) -> np.ndarray | None:
    """Masques Cryptomatte HxWx3 (r = matte CG, g = reflets, b = ombres)."""
    if cg is None:
        return None
    out = np.zeros((cg.height, cg.width, 3), np.float16)
    by_name = {c.name: c for c in cg.crypto}
    for i, m in enumerate((s.cg_mask, s.reflection_mask, s.shadow_mask)):
        if not m.objects or not cg.crypto:
            continue
        layer = by_name.get(m.layer) or cg.crypto[0]
        out[..., i] = layer.mask(m.objects)
    return out


class Engine:
    def __init__(self, ctx: moderngl.Context):
        self.ctx = ctx
        quad = np.array([-1, -1, 1, -1, -1, 1, 1, 1], "f4")
        self.vbo = ctx.buffer(quad.tobytes())
        self.comp_prog = self.comp_vao = None
        self.in_tex: list[tuple[str, object]] = []
        self.input_key = None
        self.tex: dict[str, moderngl.Texture] = {}
        self.has: dict[str, bool] = {}
        self.size = (16, 16)
        self.fbo_a = self.fbo_b = None
        self.view_prog = self.view_vao = None
        self.lut_tex: list[tuple[str, object]] = []
        self.display_key = None
        self._dummy = ctx.texture((1, 1), 4, np.zeros(4, "f4").tobytes(), dtype="f4")
        self.set_display(*color.default_display_view())
        self.set_input(color.FLOG2)

    # ------------------------------------------------------------ OCIO
    def _luts(self, g, old):
        for _, t in old:
            t.release()
        out = []
        for sampler, kind, size, ch, data, linear in g.textures:
            if kind == "3d":
                t = self.ctx.texture3d(size, ch, data.tobytes(), dtype="f4")
            else:
                t = self.ctx.texture(size, ch, data.tobytes(), dtype="f4")
                t.repeat_x = t.repeat_y = False
            f = moderngl.LINEAR if linear else moderngl.NEAREST
            t.filter = (f, f)
            out.append((sampler, t))
        return out

    def set_input(self, space: str):
        """Transformation d'entrée du rush (-> ACEScg) compilée dans le shader de comp."""
        if self.input_key == space:
            return
        g = color.GpuInput(space)
        prog = self.ctx.program(vertex_shader=VERT, fragment_shader=COMP_FRAG_HEAD + g.glsl + COMP_FRAG)
        self.in_tex = self._luts(g, self.in_tex)
        if self.comp_prog:
            self.comp_vao.release()
            self.comp_prog.release()
        self.comp_prog = prog
        self.comp_vao = self.ctx.vertex_array(prog, [(self.vbo, "2f", "in_pos")])
        self.input_key = space

    def set_display(self, display: str, view: str):
        if self.display_key == (display, view):
            return
        g = color.GpuDisplay(display, view)
        frag = VIEW_FRAG_HEAD + g.glsl + VIEW_FRAG_MAIN
        prog = self.ctx.program(vertex_shader=VERT, fragment_shader=frag)
        for _, t in self.lut_tex:
            t.release()
        self.lut_tex = []
        for sampler, kind, size, ch, data, linear in g.textures:
            if kind == "3d":
                t = self.ctx.texture3d(size, ch, data.tobytes(), dtype="f4")
            else:
                t = self.ctx.texture(size, ch, data.tobytes(), dtype="f4")
                t.repeat_x = t.repeat_y = False
            f = moderngl.LINEAR if linear else moderngl.NEAREST
            t.filter = (f, f)
            self.lut_tex.append((sampler, t))
        if self.view_prog:
            self.view_vao.release()
            self.view_prog.release()
        self.view_prog = prog
        self.view_vao = self.ctx.vertex_array(prog, [(self.vbo, "2f", "in_pos")])
        self.display_key = (display, view)

    # ------------------------------------------------------------ upload
    def _upload(self, name: str, arr: np.ndarray | None, comps: int):
        if arr is None:
            self.has[name] = False
            return
        h, w = arr.shape[:2]
        dt = "f2" if arr.dtype == np.float16 else "f4"
        data = np.ascontiguousarray(arr[..., :comps] if arr.ndim == 3 else arr, dtype=dt)
        t = self.tex.get(name)
        if t is None or t.size != (w, h) or t.components != comps or t.dtype != dt:
            if t:
                t.release()
            t = self.ctx.texture((w, h), comps, dtype=dt)
            t.repeat_x = t.repeat_y = False
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.tex[name] = t
        t.write(data.tobytes())
        self.has[name] = True

    def upload(self, plate: np.ndarray | None, cg: CGFrame | None, masks: np.ndarray | None):
        self._upload("plate", plate, 3)
        p = cg.passes if cg else {}
        self._upload("beauty", p.get("beauty"), 4)
        self._upload("diffuse", p.get("diffuse"), 4)
        self._upload("refl", p.get("reflection"), 4)
        self._upload("shadow", p.get("shadow"), 4)
        self._upload("masks", masks, 3)
        if plate is not None:
            size = (plate.shape[1], plate.shape[0])
        elif cg is not None:
            size = (cg.width, cg.height)
        else:
            size = (16, 16)
        if size != self.size or self.fbo_a is None:
            self.size = size
            for f in (self.fbo_a, self.fbo_b):
                if f:
                    f.color_attachments[0].release()
                    f.release()
            self.fbo_a = self._make_fbo(size)
            self.fbo_b = self._make_fbo(size)

    def _make_fbo(self, size):
        t = self.ctx.texture(size, 4, dtype="f4")
        t.filter = (moderngl.LINEAR, moderngl.LINEAR)
        return self.ctx.framebuffer(color_attachments=[t])

    # ------------------------------------------------------------ rendu
    def _bind(self, prog, name: str, unit: int):
        if name in prog:
            prog[name].value = unit

    def _set(self, prog, name, value):
        if name in prog:
            prog[name].value = value

    def render_comp(self, s: CompSettings, neutral: bool = False, target=None):
        """Compose en ACEScg linéaire dans fbo_a (ou fbo_b si neutral)."""
        fbo = target or (self.fbo_b if neutral else self.fbo_a)
        p = self.comp_prog
        units = {"t_plate": "plate", "t_beauty": "beauty", "t_diffuse": "diffuse",
                 "t_refl": "refl", "t_shadow": "shadow", "t_masks": "masks"}
        for i, (u, n) in enumerate(units.items()):
            (self.tex[n] if self.has.get(n) else self._dummy).use(i)
            self._bind(p, u, i)
        for k, (sampler, t) in enumerate(self.in_tex):
            t.use(len(units) + k)
            self._bind(p, sampler, len(units) + k)
        has_cg = self.has.get("beauty", False)
        self._set(p, "has_plate", self.has.get("plate", False))
        self._set(p, "has_cg", has_cg)
        self._set(p, "has_diffuse", self.has.get("diffuse", False))
        self._set(p, "has_refl", self.has.get("refl", False))
        self._set(p, "has_shadow", self.has.get("shadow", False))
        n = neutral
        self._set(p, "plate_exp", 0.0 if n else s.plate_exposure)
        self._set(p, "cg_gain", 1.0 if n else s.cg_gain)
        self._set(p, "diffuse_gain", 1.0 if n else s.diffuse_gain)
        self._set(p, "refl_gain", 1.0 if n else s.reflection_gain)
        self._set(p, "refl_tint", (1.0, 1.0, 1.0) if n else tuple(s.reflection_tint))
        self._set(p, "shadow_density", 1.0 if n else s.shadow_density)
        self._set(p, "shadow_color", (0.0, 0.0, 0.0) if n else tuple(s.shadow_color))
        self._set(p, "shadow_invert", s.shadow_invert)
        off = (0.0, 0.0, 1.0)
        self._set(p, "m_cg", off if n else _mask_vec(s.cg_mask))
        self._set(p, "m_refl", off if n else _mask_vec(s.reflection_mask))
        self._set(p, "m_shadow", off if n else _mask_vec(s.shadow_mask))
        self._set(p, "rect", (-1.0, -1.0, 1.0, 1.0))
        fbo.use()
        fbo.viewport = (0, 0, *fbo.size)
        self.ctx.disable(moderngl.BLEND)
        self.comp_vao.render(moderngl.TRIANGLE_STRIP)

    def render_view(self, target, viewport, rect, v: ViewParams):
        """Applique l'Output Transform OCIO (+ outils viewer) vers target."""
        p = self.view_prog
        self.fbo_a.color_attachments[0].use(0)
        self.fbo_b.color_attachments[0].use(1)
        (self.tex["masks"] if self.has.get("masks") else self._dummy).use(2)
        self._bind(p, "t_a", 0)
        self._bind(p, "t_b", 1)
        self._bind(p, "t_masks", 2)
        for i, (sampler, t) in enumerate(self.lut_tex):
            t.use(3 + i)
            self._bind(p, sampler, 3 + i)
        self._set(p, "mode", v.mode)
        self._set(p, "wipe", v.wipe)
        self._set(p, "channel", v.channel)
        self._set(p, "exposure", v.exposure)
        self._set(p, "gamma", v.gamma)
        self._set(p, "overlay", v.overlay if self.has.get("masks") else -1)
        self._set(p, "ocio_on", v.ocio_on)
        self._set(p, "rect", rect)
        target.use()
        target.viewport = viewport
        self.view_vao.render(moderngl.TRIANGLE_STRIP)

    def render_frame(self, s: CompSettings, v: ViewParams | None = None, b_neutral: bool = True):
        """Rendu complet : A = comp réglée, B = référence (plate ou CG brut)."""
        v = v or ViewParams()
        self.render_comp(s)
        if v.mode != 0 or b_neutral:
            if v.b_source == "raw":
                self.render_comp(s, neutral=True)
            else:
                saved = self.has.get("beauty")
                self.has["beauty"] = False
                self.render_comp(s, neutral=True)
                self.has["beauty"] = saved

    # ------------------------------------------------------------ lecture
    def read_scene(self) -> np.ndarray:
        w, h = self.size
        raw = self.fbo_a.read(components=4, dtype="f4")
        return np.frombuffer(raw, "f4").reshape(h, w, 4)[::-1].copy()

    def read_display(self, v: ViewParams | None = None) -> np.ndarray:
        v = v or ViewParams()
        w, h = self.size
        if getattr(self, "_out", None) is None or self._out.size != (w, h):
            self._out = self._make_fbo((w, h))
        self.render_view(self._out, (0, 0, w, h), (-1.0, -1.0, 1.0, 1.0), v)
        raw = self._out.read(components=4, dtype="f4")
        return np.frombuffer(raw, "f4").reshape(h, w, 4)[::-1, :, :3].copy()
