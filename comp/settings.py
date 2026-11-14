"""Réglages du compositing, sérialisés en JSON (aller-retour exact)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

from . import color

VERSION = 1


@dataclass
class MaskSettings:
    layer: str = ""                       # couche Cryptomatte (CryptoObject, CryptoMaterial…)
    objects: list[str] = field(default_factory=list)
    invert: bool = False
    mix: float = 1.0                      # 0 = réglage partout, 1 = réglage limité au masque


@dataclass
class CompSettings:
    # Médias
    plate_path: str = ""
    plate_space: str = color.FLOG2
    cg_path: str = ""
    cg_space: str = color.WORKING
    frame_offset: int = 0                 # décalage CG par rapport au rush
    # Plate
    plate_exposure: float = 0.0           # stops
    # CG / recomposition
    cg_gain: float = 1.0
    diffuse_gain: float = 1.0
    reflection_gain: float = 1.0          # « balance reflets »
    reflection_tint: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    reflection_mask: MaskSettings = field(default_factory=MaskSettings)
    # Ombres
    shadow_density: float = 1.0
    shadow_color: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    shadow_invert: bool = False           # si le pass vaut 1 hors ombre
    shadow_mask: MaskSettings = field(default_factory=MaskSettings)
    # Matte global du CG
    cg_mask: MaskSettings = field(default_factory=MaskSettings)
    # Sortie
    display: str = ""
    view: str = ""

    def __post_init__(self):
        if not self.display:
            self.display, self.view = color.default_display_view()

    # ---------------------------------------------------------------- JSON
    def to_dict(self) -> dict:
        return {"version": VERSION, **asdict(self)}

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
            f.write("\n")

    @classmethod
    def from_dict(cls, d: dict) -> "CompSettings":
        d = dict(d)
        d.pop("version", None)
        known = {f.name for f in fields(cls)}
        kw = {}
        for k, v in d.items():
            if k not in known:
                continue
            if k.endswith("_mask") and isinstance(v, dict):
                mk = {f.name for f in fields(MaskSettings)}
                v = MaskSettings(**{a: b for a, b in v.items() if a in mk})
            kw[k] = v
        return cls(**kw)

    @classmethod
    def load(cls, path: str) -> "CompSettings":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))
