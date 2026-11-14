#!/usr/bin/env python3
"""Prépare la démo navigateur (docs/) à partir des médias de démo :

- docs/media/passes.mp4 : mosaïque 3x3 (tuiles 640x360) décodée en temps réel par le navigateur
    [rush F-Log2 | beauty | diffuse ]
    [reflets     | alpha  | shadow  ]
    [matte obj 0 | obj 1  | obj 2   ]
  Le rush reste en valeurs code F-Log2 ; les passes CG sont encodées en log2 (LOG_MIN..LOG_MAX stops).
- docs/ocio.js : shaders GLSL ES 3.0 générés par OpenColorIO (F-Log2 -> ACEScg, ACEScg -> sRGB ACES 2.0)
  + leurs LUT, et la liste des objets Cryptomatte.

  python scripts/export_web.py demo/reglages_demo.json --frames 360
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys

import numpy as np
import PyOpenColorIO as ocio
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from comp import color  # noqa: E402
from comp.media import CGSequence, PlateSequence  # noqa: E402
from comp.settings import CompSettings  # noqa: E402

TW, TH = 640, 360
LOG_MIN, LOG_MAX = -10.0, 6.0


def resize(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, np.float32)
    if a.shape[:2] == (TH, TW):
        return a
    chans = a[..., None] if a.ndim == 2 else a
    out = np.stack([np.asarray(Image.fromarray(chans[..., c]).resize((TW, TH), Image.BILINEAR))
                    for c in range(chans.shape[-1])], -1)
    return out[..., 0] if a.ndim == 2 else out


def log_enc(x):
    x = np.maximum(x, 0)
    e = (np.log2(np.maximum(x, 2.0 ** LOG_MIN)) - LOG_MIN) / (LOG_MAX - LOG_MIN)
    return np.where(x <= 2.0 ** LOG_MIN, 0.0, np.clip(e, 1.0 / 255, 1.0))


def gray(x):
    return np.repeat(np.clip(x, 0, 1)[..., None], 3, -1)


def to8(x, rng):
    # léger tramage : évite les aplats en escalier après quantification 8 bits
    return np.clip(x * 255 + rng.uniform(-0.5, 0.5, x.shape), 0, 255).round().astype(np.uint8)


def shader(proc, fn, prefix):
    d = ocio.GpuShaderDesc.CreateShaderDesc(language=ocio.GPU_LANGUAGE_GLSL_ES_3_0,
                                            functionName=fn, resourcePrefix=prefix)
    d.setAllowTexture1D(False)
    proc.getDefaultGPUProcessor().extractGpuShaderInfo(d)
    texs = []
    for t in d.getTextures():
        ch = 1 if t.channel == ocio.GpuShaderDesc.TEXTURE_RED_CHANNEL else 3
        texs.append({"sampler": t.samplerName, "w": t.width, "h": t.height, "ch": ch,
                     "linear": t.interpolation != ocio.INTERP_NEAREST,
                     "data": base64.b64encode(np.asarray(t.getValues(), "<f4").tobytes()).decode()})
    if list(d.get3DTextures()):
        raise SystemExit("LUT 3D non gérée dans la démo web")
    return {"glsl": d.getShaderText(), "textures": texs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("settings")
    ap.add_argument("--frames", type=int, default=360)
    ap.add_argument("--out", default="docs")
    a = ap.parse_args()
    s = CompSettings.load(a.settings)
    plate = PlateSequence(s.plate_path, s.plate_space, convert=False)
    cg = CGSequence(s.cg_path, s.cg_space)
    first = cg.frame(0)
    crypto = first.crypto[0]
    objects = crypto.names[:3]
    os.makedirs(os.path.join(a.out, "media"), exist_ok=True)

    cfg = color.config()
    disp, view = s.display, s.view
    meta = {
        "tile": [TW, TH], "grid": [3, 3], "fps": plate.fps, "frames": a.frames,
        "logMin": LOG_MIN, "logMax": LOG_MAX, "objects": objects, "cryptoLayer": crypto.name,
        "display": disp, "view": view, "plateSpace": s.plate_space, "config": cfg.getName(),
        "ocioVersion": ocio.__version__, "source": [first.width, first.height],
        "input": shader(cfg.getProcessor(s.plate_space, color.WORKING), "OCIOInput", "ocioin_"),
        "output": shader(color._display_proc(disp, view), "OCIODisplay", "ocio_"),
        "defaults": {k: v for k, v in s.to_dict().items() if k not in ("plate_path", "cg_path")},
    }
    with open(os.path.join(a.out, "ocio.js"), "w") as f:
        f.write("// Généré par scripts/export_web.py — ne pas éditer\nexport const META = ")
        json.dump(meta, f, ensure_ascii=False)
        f.write(";\n")

    out = os.path.join(a.out, "media", "passes.mp4")
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{TW * 3}x{TH * 3}",
         "-r", f"{plate.fps:g}", "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "15",
         "-pix_fmt", "yuv420p", "-profile:v", "high", "-color_range", "tv", "-colorspace", "bt709",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-movflags", "+faststart", out],
        stdin=subprocess.PIPE)
    rng = np.random.default_rng(1)
    for i in range(a.frames):
        fr = cg.frame(i)
        P = {k: v.astype(np.float32) for k, v in fr.passes.items()}
        tiles = [
            plate.frame(i),
            log_enc(P["beauty"][..., :3]), log_enc(P["diffuse"][..., :3]), log_enc(P["reflection"][..., :3]),
            gray(P["beauty"][..., 3]), gray(P["shadow"][..., :3].mean(-1)),
        ] + [gray(fr.crypto[0].mask([o])) for o in objects]
        tiles += [np.zeros((TH, TW, 3))] * (9 - len(tiles))
        rows = [np.concatenate([resize(t) for t in tiles[r * 3:r * 3 + 3]], 1) for r in range(3)]
        ff.stdin.write(to8(np.concatenate(rows, 0), rng).tobytes())
        if i % 60 == 0:
            print(f"frame {i}/{a.frames}", flush=True)
    ff.stdin.close()
    ff.wait()
    print(f"{out} : {os.path.getsize(out) / 1e6:.1f} Mo")


if __name__ == "__main__":
    main()
