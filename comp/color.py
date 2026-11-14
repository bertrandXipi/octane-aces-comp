"""Pipeline couleur OCIO v2 : config ACES studio + F-Log2 F-Gamut, processeurs CPU et shader GPU."""
from __future__ import annotations

import functools
import numpy as np
import PyOpenColorIO as ocio

WORKING = "ACEScg"
FLOG2 = "F-Log2 F-Gamut"

# Fujifilm F-Log2 Data Sheet v1.0 (in = réflectance linéaire, out = valeur code 0-1)
FLOG2_A, FLOG2_B = 5.555556, 0.064829
FLOG2_C, FLOG2_D = 0.245281, 0.384316
FLOG2_CUT1 = 0.000889


def flog2_encode(x: np.ndarray) -> np.ndarray:
    """Référence analytique (pour tests / génération de démo)."""
    e, f = 8.799461, 0.092864
    return np.where(x >= FLOG2_CUT1, FLOG2_C * np.log10(FLOG2_A * x + FLOG2_B) + FLOG2_D, e * x + f)


def _add_flog2(cfg: ocio.Config) -> None:
    if cfg.getColorSpace(FLOG2) is not None:
        return
    log = ocio.LogCameraTransform(
        base=10.0,
        logSideSlope=[FLOG2_C] * 3, logSideOffset=[FLOG2_D] * 3,
        linSideSlope=[FLOG2_A] * 3, linSideOffset=[FLOG2_B] * 3,
        linSideBreak=[FLOG2_CUT1] * 3,
        direction=ocio.TRANSFORM_DIR_INVERSE,
    )
    # F-Gamut = primaires Rec.2020, blanc D65
    gamut = ocio.ColorSpaceTransform(src="Linear Rec.2020", dst=cfg.getCanonicalName("ACES2065-1") or "ACES2065-1")
    cs = ocio.ColorSpace(
        referenceSpace=ocio.REFERENCE_SPACE_SCENE,
        name=FLOG2,
        family="Input/Fujifilm",
        encoding="log",
        description="Fujifilm F-Log2 curve with F-Gamut (Rec.2020) primaries",
        toReference=ocio.GroupTransform([log, gamut]),
    )
    cs.addAlias("flog2_fgamut")
    cfg.addColorSpace(cs)


@functools.lru_cache(maxsize=1)
def config() -> ocio.Config:
    cfg = ocio.Config.CreateFromBuiltinConfig("studio-config-latest")
    _add_flog2(cfg)
    cfg.validate()
    ocio.SetCurrentConfig(cfg)
    return cfg


def input_spaces() -> list[str]:
    return [cs.getName() for cs in config().getColorSpaces()
            if cs.getReferenceSpaceType() == ocio.REFERENCE_SPACE_SCENE]


def displays() -> list[str]:
    return list(config().getDisplays())


def views(display: str) -> list[str]:
    return list(config().getViews(display))


def default_display_view() -> tuple[str, str]:
    d = config().getDefaultDisplay()
    return d, config().getDefaultView(d)


@functools.lru_cache(maxsize=32)
def _cpu(src: str, dst: str):
    return config().getProcessor(src, dst).getOptimizedCPUProcessor(ocio.OPTIMIZATION_DEFAULT)


def to_working(rgb: np.ndarray, src: str) -> np.ndarray:
    """Convertit un tableau float32 HxWx3 (ou x4) vers ACEScg, sur place si possible."""
    if src == WORKING:
        return rgb
    a = np.ascontiguousarray(rgb, dtype=np.float32)
    proc = _cpu(src, WORKING)
    if a.shape[-1] == 4:
        proc.applyRGBA(a)
    else:
        proc.applyRGB(a)
    return a


@functools.lru_cache(maxsize=16)
def _display_proc(display: str, view: str):
    t = ocio.DisplayViewTransform(src=WORKING, display=display, view=view)
    return config().getProcessor(t)


def display_cpu(rgb: np.ndarray, display: str, view: str) -> np.ndarray:
    a = np.ascontiguousarray(rgb[..., :3], dtype=np.float32).copy()
    _display_proc(display, view).getDefaultCPUProcessor().applyRGB(a)
    return a


def from_working_cpu(rgb: np.ndarray, dst: str) -> np.ndarray:
    a = np.ascontiguousarray(rgb[..., :3], dtype=np.float32).copy()
    if dst != WORKING:
        _cpu(WORKING, dst).applyRGB(a)
    return a


class GpuDisplay:
    """Shader GLSL OCIO (ACEScg -> display/view) + ses LUT, prêt pour ModernGL."""

    def __init__(self, display: str, view: str):
        proc = _display_proc(display, view).getDefaultGPUProcessor()
        desc = ocio.GpuShaderDesc.CreateShaderDesc(
            language=ocio.GPU_LANGUAGE_GLSL_4_0, functionName="OCIODisplay", resourcePrefix="ocio_")
        desc.setAllowTexture1D(False)  # ModernGL n'a pas de textures 1D -> 2D
        proc.extractGpuShaderInfo(desc)
        self.glsl = desc.getShaderText()
        self.textures = []  # (sampler, kind, size, channels, data, linear)
        for t in desc.getTextures():
            ch = 1 if t.channel == ocio.GpuShaderDesc.TEXTURE_RED_CHANNEL else 3
            self.textures.append((t.samplerName, "2d", (t.width, t.height), ch,
                                  np.asarray(t.getValues(), np.float32),
                                  t.interpolation != ocio.INTERP_NEAREST))
        for t in desc.get3DTextures():
            n = t.edgeLen
            self.textures.append((t.samplerName, "3d", (n, n, n), 3,
                                  np.asarray(t.getValues(), np.float32),
                                  t.interpolation != ocio.INTERP_NEAREST))
        self.uniforms = list(desc.getUniforms())
