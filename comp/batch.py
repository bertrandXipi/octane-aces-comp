"""Rendu batch offscreen : même moteur GPU que le viewer, réglages lus dans le JSON.

  python -m comp.batch reglages.json -o rendu/ --format exr
  python -m comp.batch reglages.json -o rendu/comp.mov --format prores
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import moderngl
import numpy as np
import OpenEXR

from . import color
from .engine import Engine, ViewParams, crypto_masks
from .media import CGSequence, PlateSequence
from .settings import CompSettings


class Sources:
    """Rush + séquence CG d'après les réglages ; donne les données d'une frame de sortie."""

    def __init__(self, s: CompSettings):
        self.s = s
        self.plate = PlateSequence(s.plate_path, s.plate_space, convert=False) if s.plate_path else None
        self.cg = CGSequence(s.cg_path, s.cg_space) if s.cg_path else None
        if not (self.plate or self.cg):
            raise ValueError("Ni rush ni séquence CG dans les réglages")

    def __len__(self):
        return len(self.plate) if self.plate else len(self.cg)

    @property
    def fps(self):
        return self.plate.fps if self.plate else 24.0

    def cg_frame(self, i):
        if not self.cg:
            return None
        j = i + self.s.frame_offset
        return self.cg.frame(j) if 0 <= j < len(self.cg) else None

    def frame(self, i):
        cg = self.cg_frame(i)
        return (self.plate.frame(i) if self.plate else None), cg, crypto_masks(cg, self.s)

    def close(self):
        if self.plate:
            self.plate.close()


def write_exr(path: str, rgba: np.ndarray, space: str):
    hf = np.float16
    ch = {c: rgba[..., i].astype(hf) for i, c in enumerate("RGBA"[:rgba.shape[-1]])}
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage,
              "colorspace": space, "software": "comp-octane-aces"}
    OpenEXR.File(header, ch).write(path)


def _ffmpeg_writer(fmt: str, out: str, w: int, h: int, fps: float):
    args = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb48le",
            "-s", f"{w}x{h}", "-r", f"{fps:g}", "-i", "-"]
    if fmt == "prores":
        args += ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuv444p10le", "-vendor", "apl0",
                 "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", out]
    else:
        args += ["-f", "image2", "-start_number", "1", out]
    return subprocess.Popen(args, stdin=subprocess.PIPE)


def render(settings_path: str, out: str, fmt: str = "exr", start: int = 0, end: int | None = None,
           bake_display: bool = False, quiet: bool = False) -> dict:
    s = CompSettings.load(settings_path)
    src = Sources(s)
    n = len(src)
    end = n if end is None else min(end, n)
    ctx = moderngl.create_standalone_context(require=410)
    eng = Engine(ctx)
    eng.set_display(s.display, s.view)
    eng.set_input(s.plate_space)
    view = ViewParams()

    w = h = 0
    ff = None
    pool = ThreadPoolExecutor(4)
    pending = []
    if fmt == "exr":
        os.makedirs(out, exist_ok=True)
    elif fmt == "tiff":
        os.makedirs(out, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    # préchargement des EXR CG en parallèle (le décodage EXR libère le GIL)
    if src.cg:
        def warm(i):
            j = i + s.frame_offset
            if 0 <= j < len(src.cg):
                src.cg.frame(j)
        src.cg._cache_n = 64
    t0 = time.time()
    for i in range(start, end):
        if src.cg and i % 16 == 0:
            list(pool.map(warm, range(i, min(i + 16, end))))
        plate, cg, masks = src.frame(i)
        eng.upload(plate, cg, masks)
        eng.render_comp(s)
        w, h = eng.size
        if fmt == "exr":
            img = eng.read_display(view) if bake_display else eng.read_scene()
            space = f"{s.display} / {s.view}" if bake_display else color.WORKING
            pending.append(pool.submit(write_exr, os.path.join(out, f"comp.{i + 1:04d}.exr"), img, space))
            if len(pending) > 16:
                pending.pop(0).result()
        else:
            if ff is None:
                target = out if fmt == "prores" else os.path.join(out, "comp.%04d.tif")
                ff = _ffmpeg_writer(fmt, target, w, h, src.fps)
            disp = eng.read_display(view)
            ff.stdin.write((np.clip(disp, 0, 1) * 65535 + 0.5).astype("<u2").tobytes())
        if not quiet and (i - start) % 50 == 0:
            el = time.time() - t0
            fps = (i - start + 1) / el if el else 0
            print(f"frame {i + 1}/{end}  {fps:5.1f} i/s", flush=True)
    for p in pending:
        p.result()
    if ff:
        ff.stdin.close()
        ff.wait()
    src.close()
    el = time.time() - t0
    stats = {"frames": end - start, "seconds": round(el, 2), "fps": round((end - start) / el, 2),
             "size": [w, h], "format": fmt, "out": out}
    if not quiet:
        print(f"OK {stats['frames']} frames en {stats['seconds']} s ({stats['fps']} i/s) -> {out}")
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(prog="comp-batch", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("settings", help="fichier JSON de réglages (enregistré depuis l'application)")
    ap.add_argument("-o", "--out", required=True, help="dossier (exr/tiff) ou fichier .mov (prores)")
    ap.add_argument("-f", "--format", choices=["exr", "tiff", "prores"], default="exr")
    ap.add_argument("--start", type=int, default=0, help="première frame (index 0)")
    ap.add_argument("--end", type=int, default=None, help="frame de fin exclue")
    ap.add_argument("--bake-display", action="store_true",
                    help="EXR : appliquer l'Output Transform (sinon ACEScg linéaire)")
    a = ap.parse_args(argv)
    render(a.settings, a.out, a.format, a.start, a.end, a.bake_display)


if __name__ == "__main__":
    sys.exit(main())
