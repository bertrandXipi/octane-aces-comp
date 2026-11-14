#!/usr/bin/env python3
"""Vérifie qu'une frame rendue par le batch == calcul de référence CPU (numpy + OCIO CPU).

  python scripts/conformite.py demo/reglages_demo.json demo/rendu_exr 450
"""
import os
import sys

import numpy as np
import OpenEXR

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from comp import color  # noqa: E402
from comp.batch import Sources  # noqa: E402
from comp.settings import CompSettings  # noqa: E402


def maskw(raw, m):
    if not m.objects:
        return np.ones_like(raw)
    v = 1 - raw if m.invert else raw
    return 1 + (v - 1) * m.mix


def reference(s, plate, cg, masks):
    P = {k: v.astype(np.float32) for k, v in cg.passes.items()}
    masks = masks.astype(np.float32)
    mcg, mrf, msh = (maskw(masks[..., i], m) for i, m in enumerate((s.cg_mask, s.reflection_mask, s.shadow_mask)))
    c = P["beauty"][..., :3].copy()
    if "diffuse" in P:
        c += (s.diffuse_gain - 1) * P["diffuse"][..., :3]
    if "reflection" in P:
        c += (s.reflection_gain * np.array(s.reflection_tint) - 1) * P["reflection"][..., :3] * mrf[..., None]
    c *= s.cg_gain * mcg[..., None]
    a = np.clip(P["beauty"][..., 3] * mcg, 0, 1)
    bg = color.to_working(plate.astype(np.float32).copy(), s.plate_space) * 2 ** s.plate_exposure
    sh = np.zeros_like(a)
    if "shadow" in P:
        sh = P["shadow"][..., :3].mean(-1)
        sh = 1 - sh if s.shadow_invert else sh
        sh = np.clip(sh * s.shadow_density * msh, 0, 1)
    bgs = bg + (bg * np.array(s.shadow_color) - bg) * sh[..., None]
    return c + bgs * (1 - a)[..., None]


def main(settings, out_dir, frame):
    s = CompSettings.load(settings)
    src = Sources(s)
    plate, cg, masks = src.frame(frame)
    ref = reference(s, plate, cg, masks)
    f = OpenEXR.File(os.path.join(out_dir, f"comp.{frame + 1:04d}.exr"), separate_channels=True)
    got = np.stack([f.channels()[c].pixels.astype(np.float32) for c in "RGB"], -1)
    d_scene = np.abs(got - ref) / np.maximum(np.abs(ref), 1e-3)
    disp_ref = color.display_cpu(ref, s.display, s.view)
    disp_got = color.display_cpu(got, s.display, s.view)
    d_disp = np.abs(disp_ref - disp_got)
    print(f"frame {frame + 1} : écart relatif scène max {d_scene.max():.2e} (EXR half), "
          f"écart affiché max {d_disp.max() * 255:.3f}/255")
    ok = d_disp.max() * 255 < 1.0
    print("CONFORME" if ok else "ÉCART")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], int(sys.argv[3]) - 1 if len(sys.argv) > 3 else 0))
