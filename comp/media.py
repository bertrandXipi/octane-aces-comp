"""Chargement des médias : séquences EXR multi-passes Octane (+ Cryptomatte) et rush F-Log2."""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np
import OpenEXR

from . import color
from .crypto import CryptoLayer, parse_layers

ROLE_KEYWORDS = {
    "beauty": ("beauty", "rgba", "combined", "main"),
    "diffuse": ("diffuse", "diff"),
    "reflection": ("reflection", "specular", "refl", "spec"),
    "shadow": ("shadow",),
}
IMAGE_EXT = (".exr", ".tif", ".tiff", ".png", ".dpx", ".jpg", ".jpeg")


# --------------------------------------------------------------------------- séquences
def expand_sequence(path: str) -> list[str]:
    """Accepte un fichier, un dossier, un motif ####, %04d ou glob -> liste triée de fichiers."""
    path = os.path.expanduser(path)
    if os.path.isdir(path):
        files = []
        for ext in IMAGE_EXT:
            files += glob.glob(os.path.join(path, f"*{ext}"))
        return sorted(files)
    if "#" in path:
        path = re.sub(r"#+", "*", path)
    elif re.search(r"%0?\d*d", path):
        path = re.sub(r"%0?\d*d", "*", path)
    if any(c in path for c in "*?["):
        return sorted(glob.glob(path))
    if os.path.isfile(path):
        # fichier isolé d'une séquence numérotée -> on prend toute la séquence
        m = re.match(r"^(.*?)(\d+)(\.[^.]+)$", path)
        if m:
            seq = sorted(glob.glob(f"{glob.escape(m.group(1))}{'[0-9]' * len(m.group(2))}{m.group(3)}"))
            if len(seq) > 1:
                return seq
        return [path]
    return []


# --------------------------------------------------------------------------- EXR Octane
def read_exr_channels(path: str) -> tuple[dict, dict[str, np.ndarray]]:
    f = OpenEXR.File(path, separate_channels=True)
    header, chans = {}, {}
    for part in f.parts:
        header.update(part.header)
        pname = part.name() if callable(getattr(part, "name", None)) else ""
        for name, ch in part.channels.items():
            key = name
            if pname and "." not in name and len(f.parts) > 1:
                key = f"{pname}.{name}"
            chans[key] = ch.pixels
    return header, chans


def group_layers(chans: dict[str, np.ndarray]) -> dict[str, dict[str, np.ndarray]]:
    layers: dict[str, dict[str, np.ndarray]] = {}
    for name, px in chans.items():
        layer, _, c = name.rpartition(".")
        layers.setdefault(layer, {})[c.upper()] = px
    return layers


def layer_rgba(layer: dict[str, np.ndarray], alpha_default: float = 1.0) -> np.ndarray:
    """Couche -> HxWx4. Reste en half si l'EXR est en half (moitié moins de mémoire et d'upload GPU)."""
    keys = list(layer)
    first = layer[keys[0]]
    dt = np.float16 if all(v.dtype == np.float16 for v in layer.values()) else np.float32
    h, w = first.shape
    rgb = [layer.get(c, layer.get("Y", first)) for c in "RGB"]
    a = layer.get("A")
    if a is None:
        a = np.full((h, w), alpha_default, dt)
    return np.stack([*rgb, a], -1).astype(dt, copy=False)


def detect_roles(layer_names: list[str]) -> dict[str, str]:
    roles = {}
    lower = {n: n.lower() for n in layer_names}
    for role, kws in ROLE_KEYWORDS.items():
        for kw in kws:
            hit = [n for n in layer_names if kw in lower[n] and not lower[n].startswith("crypto")]
            if hit:
                roles[role] = sorted(hit, key=len)[0]
                break
    if "beauty" not in roles and "" in layer_names:
        roles["beauty"] = ""
    return roles


@dataclass
class CGFrame:
    width: int
    height: int
    passes: dict[str, np.ndarray]           # rôle -> HxWx4 float32 en ACEScg
    crypto: list[CryptoLayer] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)


def load_cg_frame(path: str, input_space: str = color.WORKING, roles: dict | None = None) -> CGFrame:
    header, chans = read_exr_channels(path)
    crypto = parse_layers(header, chans)
    crypto_prefixes = tuple(c.name for c in crypto)
    plain = {k: v for k, v in chans.items() if not k.startswith(crypto_prefixes) or not crypto_prefixes}
    layers = group_layers(plain)
    found = detect_roles(list(layers))
    if roles:
        found.update({k: v for k, v in roles.items() if v is not None and v in layers})
    passes = {}
    for role, lname in found.items():
        rgba = layer_rgba(layers[lname], 1.0 if role == "beauty" else 0.0)
        if role != "shadow" and input_space != color.WORKING:  # le shadow est un masque, pas une couleur
            conv = color.to_working(rgba[..., :3].astype(np.float32), input_space)
            rgba = np.concatenate([conv, rgba[..., 3:].astype(np.float32)], -1).astype(rgba.dtype)
        passes[role] = rgba
    h, w = next(iter(chans.values())).shape
    if "beauty" not in passes:
        raise ValueError(f"Pas de passe Beauty trouvée dans {os.path.basename(path)} (couches : {sorted(layers)})")
    return CGFrame(w, h, passes, crypto, sorted(layers))


class CGSequence:
    def __init__(self, path: str, input_space: str = color.WORKING, roles: dict | None = None, cache: int = 24):
        self.files = expand_sequence(path)
        if not self.files:
            raise FileNotFoundError(f"Aucun EXR trouvé : {path}")
        self.input_space, self.roles = input_space, roles or {}
        self._cache: OrderedDict[int, CGFrame] = OrderedDict()
        self._cache_n = cache
        self._lock = threading.Lock()

    def __len__(self):
        return len(self.files)

    def frame(self, i: int) -> CGFrame:
        i = max(0, min(i, len(self.files) - 1))
        with self._lock:
            if i in self._cache:
                self._cache.move_to_end(i)
                return self._cache[i]
        fr = load_cg_frame(self.files[i], self.input_space, self.roles)  # hors verrou : décodage parallèle
        with self._lock:
            self._cache[i] = fr
            while len(self._cache) > self._cache_n:
                self._cache.popitem(last=False)
        return fr


# --------------------------------------------------------------------------- rush F-Log2
def ffprobe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=width,height,r_frame_rate,nb_read_packets,nb_frames",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    n = int(s.get("nb_read_packets") or s.get("nb_frames") or 0)
    return {"width": int(s["width"]), "height": int(s["height"]), "fps": float(num) / float(den), "frames": n}


def read_image_rgb(path: str) -> np.ndarray:
    """Image fixe -> HxWx3 float32 (valeurs code 0-1 ou linéaire pour EXR)."""
    if path.lower().endswith(".exr"):
        f = OpenEXR.File(path, separate_channels=True)
        layers = group_layers({k: v.pixels for k, v in f.channels().items()})
        lay = layers.get("") or next(iter(layers.values()))
        return layer_rgba(lay)[..., :3]
    info = ffprobe(path)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint16).reshape(info["height"], info["width"], 3)
    return a.astype(np.float32) / 65535.0


class _VideoStream:
    """Décodage ffmpeg séquentiel, relancé avec seek si on saute dans le temps."""

    def __init__(self, path: str):
        self.path = path
        self.info = ffprobe(path)
        self.proc = None
        self.next = -1

    def _start(self, i: int):
        self.close()
        t = i / self.info["fps"]
        self.proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.6f}", "-i", self.path,
             "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10 ** 8)
        self.next = i

    def read(self, i: int) -> np.ndarray:
        if self.proc is None or i != self.next:
            self._start(i)
        w, h = self.info["width"], self.info["height"]
        n = w * h * 6
        buf = self.proc.stdout.read(n)
        if len(buf) < n:
            raise IndexError(f"frame {i} hors du rush")
        self.next = i + 1
        return np.frombuffer(buf, np.uint16).reshape(h, w, 3).astype(np.float32) / 65535.0

    def close(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None


class PlateSequence:
    """Rush (vidéo MOV/MP4/MXF, séquence d'images ou image fixe), converti en ACEScg si convert."""

    def __init__(self, path: str, input_space: str = color.FLOG2, cache: int = 24, convert: bool = True):
        self.convert = convert  # False : valeurs code brutes, la conversion se fait sur GPU
        self.path = os.path.expanduser(path)
        self.input_space = input_space
        ext = os.path.splitext(self.path)[1].lower()
        self.video = None
        if os.path.isfile(self.path) and ext not in IMAGE_EXT:
            self.video = _VideoStream(self.path)
            self.n = self.video.info["frames"]
            self.fps = self.video.info["fps"]
        else:
            self.files = expand_sequence(self.path)
            if not self.files:
                raise FileNotFoundError(f"Rush introuvable : {path}")
            self.n = len(self.files)
            self.fps = 24.0
        self.half = False  # True côté viewer (mémoire / upload) ; le batch garde du float32
        self._cache: OrderedDict[int, np.ndarray] = OrderedDict()
        self._cache_n = cache
        self._lock = threading.RLock()

    def __len__(self):
        return self.n

    def frame(self, i: int) -> np.ndarray:
        with self._lock:
            return self._frame(i)

    def _frame(self, i: int) -> np.ndarray:
        i = max(0, min(i, self.n - 1))
        if i in self._cache:
            self._cache.move_to_end(i)
            return self._cache[i]
        raw = self.video.read(i) if self.video else read_image_rgb(self.files[i])
        rgb = np.ascontiguousarray(raw, np.float32)
        if self.convert:
            rgb = color.to_working(rgb.copy(), self.input_space)
        if self.half:
            rgb = rgb.astype(np.float16)
        self._cache[i] = rgb
        while len(self._cache) > self._cache_n:
            self._cache.popitem(last=False)
        return rgb

    def close(self):
        if self.video:
            self.video.close()
