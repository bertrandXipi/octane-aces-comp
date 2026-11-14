"""Cryptomatte (spec Psyop 1.2) : hash MurmurHash3, manifest, extraction de masques."""
from __future__ import annotations

import json
import struct
import numpy as np


def mmh3_32(data: bytes, seed: int = 0) -> int:
    c1, c2 = 0xCC9E2D51, 0x1B873593
    h = seed & 0xFFFFFFFF
    n = len(data) // 4
    for i in range(n):
        k = struct.unpack_from("<I", data, i * 4)[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    tail = data[n * 4:]
    k = 0
    for i, b in enumerate(tail):
        k |= b << (8 * i)
    if tail:
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def name_to_hash(name: str) -> int:
    """Hash uint32 avec correction d'exposant (pas de NaN/Inf/dénormal), comme la spec."""
    h = mmh3_32(name.encode("utf-8"))
    exp = (h >> 23) & 0xFF
    if exp in (0, 255):
        h ^= 1 << 23
    return h


def hash_to_float(h: int) -> float:
    return struct.unpack("<f", struct.pack("<I", h))[0]


def hex_to_float(hx: str) -> np.float32:
    return np.uint32(int(hx, 16)).view(np.float32)


class CryptoLayer:
    """Un type Cryptomatte (CryptoObject, CryptoMaterial...) avec ses rangs ID/coverage."""

    def __init__(self, name: str, manifest: dict[str, str], ranks: list[tuple[np.ndarray, np.ndarray]]):
        self.name = name
        self.manifest = manifest  # nom -> hex
        self.ranks = ranks        # [(id float32 HxW, coverage HxW), ...]

    @property
    def names(self) -> list[str]:
        return sorted(self.manifest)

    def mask(self, selected: list[str]) -> np.ndarray:
        h, w = self.ranks[0][0].shape
        m = np.zeros((h, w), np.float32)
        ids = [hex_to_float(self.manifest[n]) for n in selected if n in self.manifest]
        if not ids:
            return m
        sel = np.array(ids, np.float32)
        for idc, cov in self.ranks:
            hit = np.isin(idc.view(np.uint32), sel.view(np.uint32))
            m += np.where(hit, cov, 0.0).astype(np.float32)
        return np.clip(m, 0.0, 1.0)

    def pick(self, x: int, y: int) -> str | None:
        """Nom de l'objet dominant au pixel (x, y)."""
        best, cov_best = None, 0.0
        inv = {np.uint32(int(v, 16)): k for k, v in self.manifest.items()}
        for idc, cov in self.ranks:
            c = float(cov[y, x])
            if c > cov_best:
                n = inv.get(np.float32(idc[y, x]).view(np.uint32))
                if n:
                    best, cov_best = n, c
        return best


def parse_layers(header: dict, channels: dict[str, np.ndarray]) -> list[CryptoLayer]:
    """Retrouve les couches Cryptomatte depuis l'en-tête EXR (cryptomatte/<key>/name...)."""
    metas: dict[str, dict[str, str]] = {}
    for k, v in header.items():
        if k.startswith("cryptomatte/"):
            _, key, field = k.split("/", 2)
            metas.setdefault(key, {})[field] = v if isinstance(v, str) else str(v)
    layers = []
    for meta in metas.values():
        name = meta.get("name")
        if not name:
            continue
        manifest = {}
        if meta.get("manifest"):
            try:
                manifest = json.loads(meta["manifest"])
            except json.JSONDecodeError:
                manifest = {}
        ranks = []
        i = 0
        while f"{name}{i:02d}.R" in channels or f"{name}{i:02d}.r" in channels:
            p = f"{name}{i:02d}."
            def g(c):
                return channels.get(p + c, channels.get(p + c.lower()))
            for idc, cov in (("R", "G"), ("B", "A")):
                a, b = g(idc), g(cov)
                if a is not None and b is not None:
                    ranks.append((np.asarray(a, np.float32), np.asarray(b, np.float32)))
            i += 1
        if ranks:
            if not manifest:  # pas de manifest : IDs anonymes, nommés par leur hex
                vals = np.unique(np.concatenate([r[0].ravel() for r in ranks]).view(np.uint32))
                manifest = {f"id_{v:08x}": f"{v:08x}" for v in vals if v}
            layers.append(CryptoLayer(name, manifest, ranks))
    return layers
