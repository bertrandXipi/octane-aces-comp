#!/usr/bin/env python3
"""Génère un jeu de test : rush F-Log2 (ProRes 4444 XQ + TIFF 16 bits) et séquence EXR
multi-passes « façon Octane » (Beauty, Diffuse, Reflection, Shadow, CryptoObject).

  python scripts/make_demo.py --frames 900 --size 640x360 --out demo
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import OpenEXR

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from comp import color  # noqa: E402
from comp.crypto import hash_to_float, mmh3_32, name_to_hash  # noqa: E402

OBJECTS = ["sphere_chrome", "sphere_rouge", "shadow_catcher"]
FPS = 24


def sky(d):
    """Environnement simple (ACEScg) en fonction d'une direction normalisée."""
    t = np.clip(d[..., 1] * 0.5 + 0.5, 0, 1)[..., None]
    horizon = np.array([0.9, 0.8, 0.65], np.float32)
    zenith = np.array([0.18, 0.32, 0.75], np.float32)
    ground = np.array([0.08, 0.07, 0.05], np.float32)
    c = np.where(d[..., 1:2] > 0, horizon + (zenith - horizon) * t ** 0.6, ground)
    sun = np.array([0.4, 0.6, -0.7], np.float32)
    sun /= np.linalg.norm(sun)
    s = np.clip((d * sun).sum(-1), 0, 1) ** 400 * 40
    return c + s[..., None] * np.array([1.0, 0.92, 0.8], np.float32)


def plate_frame(i, w, h):
    """Fond « filmé » : ciel + sol texturé qui défile, en ACEScg linéaire."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = x / w, y / h
    out = np.zeros((h, w, 3), np.float32)
    horizon = 0.42
    sky_t = np.clip(v / horizon, 0, 1)[..., None]
    out[:] = np.array([0.25, 0.4, 0.85]) * (1 - sky_t) + np.array([0.95, 0.85, 0.7]) * sky_t
    g = v > horizon
    depth = 1.0 / np.maximum(v - horizon, 1e-3)
    gx = (u - 0.5) * depth * 1.6 + i * 0.02
    gz = depth * 0.8 + i * 0.05
    chk = ((np.floor(gx) + np.floor(gz)) % 2).astype(np.float32)
    noise = 0.5 + 0.5 * np.sin(gx * 7.1) * np.sin(gz * 5.3)
    base = np.stack([0.28 + 0.06 * chk, 0.24 + 0.05 * chk, 0.17 + 0.03 * chk], -1) * (0.85 + 0.3 * noise[..., None])
    fog = np.clip((depth - 2) / 30, 0, 1)[..., None]
    ground = base * (1 - fog) + np.array([0.9, 0.82, 0.7]) * fog
    out[g] = ground[g]
    out *= 1.0 + 0.03 * np.sin(i * 0.05)  # légère respiration d'expo
    return out.astype(np.float32)


def cg_frame(i, w, h):
    """Rendu analytique de deux sphères + shadow catcher."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    aspect = w / h
    px = ((x + 0.5) / w * 2 - 1) * aspect
    py = 1 - (y + 0.5) / h * 2
    ro = np.array([0, 0.6, 4.0], np.float32)
    rd = np.stack([px * 0.5, py * 0.5 - 0.1, -np.ones_like(px)], -1)
    rd /= np.linalg.norm(rd, axis=-1, keepdims=True)
    t = i / FPS
    spheres = [
        ("sphere_chrome", np.array([-0.9 + 0.5 * np.sin(t * 0.7), 0.0, 0.2], np.float32), 0.75,
         np.array([0.6, 0.6, 0.62]), 0.9),
        ("sphere_rouge", np.array([1.0, -0.2, -0.6 + 0.6 * np.cos(t * 0.5)], np.float32), 0.55,
         np.array([0.7, 0.08, 0.05]), 0.25),
    ]
    floor_y = -0.75
    best_t = np.full((h, w), np.inf, np.float32)
    obj = np.full((h, w), -1, np.int32)
    for k, (_, c, r, _, _) in enumerate(spheres):
        oc = ro - c
        b = (rd * oc).sum(-1)
        cc = (oc * oc).sum() - r * r
        disc = b * b - cc
        th = -b - np.sqrt(np.maximum(disc, 0))
        hit = (disc > 0) & (th > 0) & (th < best_t)
        best_t[hit] = th[hit]
        obj[hit] = k
    diffuse = np.zeros((h, w, 3), np.float32)
    refl = np.zeros((h, w, 3), np.float32)
    alpha = np.zeros((h, w), np.float32)
    light = np.array([0.4, 0.8, 0.5], np.float32)
    light /= np.linalg.norm(light)
    for k, (_, c, r, alb, ks) in enumerate(spheres):
        m = obj == k
        p = ro + rd * np.where(np.isfinite(best_t), best_t, 0)[..., None]
        n = (p - c) / r
        lam = np.clip((n * light).sum(-1), 0, 1) * 1.4 + 0.15
        rv = rd - 2 * (rd * n).sum(-1, keepdims=True) * n
        fres = ks + (1 - ks) * (1 - np.clip(-(rd * n).sum(-1), 0, 1)) ** 5
        d = alb * lam[..., None] * (1 - ks)
        rr = sky(rv) * fres[..., None]
        diffuse[m] = d[m]
        refl[m] = rr[m]
        # anti-aliasing du bord : distance rayon/centre
        oc = ro - c
        b = (rd * oc).sum(-1)
        dist = np.sqrt(np.maximum((oc * oc).sum() - b * b, 0))
        edge = np.clip((r - dist) / (1.5 * 2 / h) + 0.5, 0, 1)
        alpha = np.where(m, edge, np.maximum(alpha, np.where(obj == -1, edge * (b < 0), 0)))
    # ombres portées sur le sol (shadow catcher)
    tf = (floor_y - ro[1]) / np.minimum(rd[..., 1], -1e-4)
    pf = ro + rd * tf[..., None]
    floor_hit = (rd[..., 1] < 0) & (tf > 0)
    shadow = np.zeros((h, w), np.float32)
    for _, c, r, _, _ in spheres:
        hgt = c[1] - floor_y
        sc = c[[0, 2]] - light[[0, 2]] / light[1] * hgt
        dd = np.sqrt((pf[..., 0] - sc[0]) ** 2 + (pf[..., 2] - sc[1]) ** 2)
        shadow = np.maximum(shadow, np.clip(1 - (dd - r * 0.5) / (r * 0.9), 0, 1) ** 1.5 * 0.85)
        ao = np.clip(1 - np.sqrt((pf[..., 0] - c[0]) ** 2 + (pf[..., 2] - c[2]) ** 2) / (r * 1.3), 0, 1)
        shadow = np.maximum(shadow, ao ** 2)
    shadow *= floor_hit * (1 - alpha)
    beauty = diffuse + refl
    # Cryptomatte : rang 0 = objet devant, rang 1 = ce qu'il y a derrière (bord)
    ids = np.array([hash_to_float(name_to_hash(n)) for n in OBJECTS], np.float32)
    catcher = floor_hit
    id0 = np.where(obj >= 0, ids[np.maximum(obj, 0)], np.where(catcher, ids[2], 0)).astype(np.float32)
    cov0 = np.where(obj >= 0, alpha, catcher.astype(np.float32)).astype(np.float32)
    id1 = np.where((obj >= 0) & catcher, ids[2], 0).astype(np.float32)
    cov1 = np.where(obj >= 0, 1 - alpha, 0) * catcher
    beauty *= alpha[..., None]
    diffuse *= alpha[..., None]
    refl *= alpha[..., None]
    return beauty, alpha, diffuse, refl, shadow, (id0, cov0.astype(np.float32), id1, cov1.astype(np.float32))


def write_exr(path, w, h, i):
    beauty, alpha, diffuse, refl, shadow, (id0, c0, id1, c1) = cg_frame(i, w, h)
    hf = np.float16
    ch = {"R": beauty[..., 0].astype(hf), "G": beauty[..., 1].astype(hf), "B": beauty[..., 2].astype(hf),
          "A": alpha.astype(hf)}
    for name, img in (("Diffuse", diffuse), ("Reflection", refl)):
        for j, c in enumerate("RGB"):
            ch[f"{name}.{c}"] = img[..., j].astype(hf)
    for c in "RGB":
        ch[f"Shadow.{c}"] = shadow.astype(hf)
    ch["CryptoObject00.R"], ch["CryptoObject00.G"] = id0, c0
    ch["CryptoObject00.B"], ch["CryptoObject00.A"] = id1, c1
    key = f"{mmh3_32(b'CryptoObject'):08x}"[:7]
    manifest = {n: f"{name_to_hash(n):08x}" for n in OBJECTS}
    header = {
        "compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage,
        f"cryptomatte/{key}/name": "CryptoObject",
        f"cryptomatte/{key}/hash": "MurmurHash3_32",
        f"cryptomatte/{key}/conversion": "uint32_to_float32",
        f"cryptomatte/{key}/manifest": json.dumps(manifest),
        "octane/renderer": "demo synthétique (comp-octane-aces)",
    }
    OpenEXR.File(header, ch).write(path)


def _exr_job(args):
    path, w, h, i = args
    if not os.path.exists(path):
        write_exr(path, w, h, i)
    return i


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=900)
    ap.add_argument("--size", default="640x360")
    ap.add_argument("--out", default="demo")
    a = ap.parse_args()
    w, h = map(int, a.size.split("x"))
    os.makedirs(os.path.join(a.out, "cg"), exist_ok=True)

    # Rush F-Log2 : ACEScg -> F-Log2 F-Gamut (valeurs code) -> ProRes 4444 XQ 12 bits
    mov = os.path.join(a.out, "plate_flog2.mov")
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{w}x{h}",
         "-r", str(FPS), "-i", "-", "-c:v", "prores_ks", "-profile:v", "5", "-pix_fmt", "yuv444p12le",
         "-vendor", "apl0", mov], stdin=subprocess.PIPE)
    for i in range(a.frames):
        lin = plate_frame(i, w, h)
        code = color.from_working_cpu(lin, color.FLOG2)
        ff.stdin.write((np.clip(code, 0, 1) * 65535 + 0.5).astype("<u2").tobytes())
        if i == 0:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb48le",
                            "-s", f"{w}x{h}", "-i", "-", os.path.join(a.out, "plate_flog2_0001.tif")],
                           input=(np.clip(code, 0, 1) * 65535 + 0.5).astype("<u2").tobytes(), check=True)
    ff.stdin.close()
    ff.wait()
    print(f"rush : {mov}")

    jobs = [(os.path.join(a.out, "cg", f"octane.{i + 1:04d}.exr"), w, h, i) for i in range(a.frames)]
    with ProcessPoolExecutor() as ex:
        for n, _ in enumerate(ex.map(_exr_job, jobs, chunksize=8)):
            if n % 100 == 0:
                print(f"EXR {n}/{a.frames}", flush=True)
    print(f"CG : {a.out}/cg/octane.####.exr")


if __name__ == "__main__":
    main()
