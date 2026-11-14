"""Knobs (contrôles de propriétés) et panneaux de nœuds."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                               QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget)

LABEL_W = 96


def _label(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("KnobLabel")
    l.setFixedWidth(LABEL_W)
    l.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return l


class FloatKnob(QWidget):
    changed = Signal(float)

    def __init__(self, lo: float, hi: float, default: float, decimals: int = 3, hard=(-1e6, 1e6)):
        super().__init__()
        self.lo, self.hi, self.default = lo, hi, default
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(*hard)
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(10 ** -min(decimals, 2))
        self.spin.setFixedWidth(58)
        self.spin.setKeyboardTracking(False)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimumWidth(40)
        self.slider.setRange(0, 1000)
        self.slider.setToolTip("Double-clic : valeur par défaut")
        lay.addWidget(self.spin)
        lay.addWidget(self.slider, 1)
        self.spin.valueChanged.connect(self._from_spin)
        self.slider.valueChanged.connect(self._from_slider)
        self.slider.mouseDoubleClickEvent = lambda e: self.set_value(self.default, emit=True)
        self._lock = False
        self.set_value(default)

    def _from_spin(self, v):
        if self._lock:
            return
        self._sync_slider(v)
        self.changed.emit(float(v))

    def _from_slider(self, i):
        if self._lock:
            return
        v = self.lo + (self.hi - self.lo) * i / 1000.0
        self._lock = True
        self.spin.setValue(v)
        self._lock = False
        self.changed.emit(float(self.spin.value()))

    def _sync_slider(self, v):
        self._lock = True
        self.slider.setValue(int(round((min(max(v, self.lo), self.hi) - self.lo) / (self.hi - self.lo) * 1000)))
        self._lock = False

    def set_value(self, v: float, emit: bool = False):
        self._lock = True
        self.spin.setValue(v)
        self._lock = False
        self._sync_slider(v)
        if emit:
            self.changed.emit(float(v))

    def value(self) -> float:
        return float(self.spin.value())


class ColorKnob(QWidget):
    changed = Signal(list)

    def __init__(self, default):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.spins = []
        for c in "rgb":
            s = QDoubleSpinBox()
            s.setRange(0, 16)
            s.setDecimals(3)
            s.setSingleStep(0.01)
            s.setFixedWidth(56)
            s.setKeyboardTracking(False)
            s.setToolTip(c.upper())
            s.valueChanged.connect(self._emit)
            lay.addWidget(s)
            self.spins.append(s)
        self.swatch = QToolButton()
        self.swatch.setFixedSize(22, 18)
        self.swatch.clicked.connect(self._pick)
        lay.addWidget(self.swatch)
        lay.addStretch(1)
        self._lock = False
        self.set_value(default)

    def _emit(self):
        if not self._lock:
            self._paint()
            self.changed.emit(self.value())

    def _paint(self):
        r, g, b = (int(min(max(v, 0), 1) ** (1 / 2.2) * 255) for v in self.value())
        self.swatch.setStyleSheet(f"background: rgb({r},{g},{b}); border: 1px solid #111;")

    def _pick(self):
        r, g, b = (min(max(v, 0), 1) ** (1 / 2.2) for v in self.value())
        c = QColorDialog.getColor(QColor.fromRgbF(r, g, b), self, "Couleur (encodée sRGB, stockée linéaire)")
        if c.isValid():
            self.set_value([c.redF() ** 2.2, c.greenF() ** 2.2, c.blueF() ** 2.2], emit=True)

    def set_value(self, v, emit=False):
        self._lock = True
        for s, x in zip(self.spins, v):
            s.setValue(float(x))
        self._lock = False
        self._paint()
        if emit:
            self.changed.emit(self.value())

    def value(self):
        return [float(s.value()) for s in self.spins]


class FileKnob(QWidget):
    changed = Signal(str)

    def __init__(self, caption: str, filt: str, directory_ok: bool = False):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("chemin, motif #### ou dossier")
        self.edit.setMinimumWidth(60)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.edit.text().strip()))
        b = QToolButton()
        b.setText("…")
        b.setToolTip(caption)
        b.clicked.connect(self._browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(b)
        if directory_ok:
            d = QToolButton()
            d.setText("dir")
            d.setToolTip("Choisir un dossier de séquence")
            d.clicked.connect(self._browse_dir)
            lay.addWidget(d)
        self.caption, self.filt = caption, filt

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, self.caption, self.edit.text(), self.filt)
        if p:
            self.set_value(p, emit=True)

    def _browse_dir(self):
        p = QFileDialog.getExistingDirectory(self, self.caption, self.edit.text())
        if p:
            self.set_value(p, emit=True)

    def set_value(self, v: str, emit=False):
        self.edit.setText(v)
        if emit:
            self.changed.emit(v)


class MatteKnob(QWidget):
    """Sélection d'objets Cryptomatte : puces, menu d'ajout, pipette, inversion."""
    changed = Signal()
    pick_toggled = Signal(object, bool)

    def __init__(self):
        super().__init__()
        self.objects: list[str] = []
        self.available: list[str] = []
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(3)
        self.pick = QToolButton()
        self.pick.setText("Pipette")
        self.pick.setCheckable(True)
        self.pick.setToolTip("Clic dans le viewer : ajoute l'objet · Maj+clic : retire")
        self.pick.toggled.connect(lambda on: self.pick_toggled.emit(self, on))
        self.add = QToolButton()
        self.add.setText("Ajouter")
        self.add.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(self)
        self.add.setMenu(self.menu)
        self.menu.aboutToShow.connect(self._fill_menu)
        clear = QToolButton()
        clear.setText("Vider")
        clear.clicked.connect(lambda: self.set_objects([], emit=True))
        self.invert = QCheckBox("inv.")
        self.invert.setToolTip("Inverser le masque")
        self.invert.toggled.connect(lambda _: self.changed.emit())
        for b in (self.pick, self.add, clear):
            b.setStyleSheet("padding: 2px 7px;")
        row.addWidget(self.pick)
        row.addWidget(self.add)
        row.addWidget(clear)
        row.addSpacing(6)
        row.addWidget(self.invert)
        row.addStretch(1)
        v.addLayout(row)
        self.chips = QLabel()
        self.chips.setWordWrap(True)
        self.chips.setTextFormat(Qt.RichText)
        v.addWidget(self.chips)
        self._render()

    def _fill_menu(self):
        self.menu.clear()
        if not self.available:
            a = self.menu.addAction("(aucune Cryptomatte chargée)")
            a.setEnabled(False)
        for n in self.available:
            a = self.menu.addAction(n)
            a.setCheckable(True)
            a.setChecked(n in self.objects)
            a.toggled.connect(lambda on, n=n: self.toggle(n, on))

    def toggle(self, name: str, on: bool):
        objs = [o for o in self.objects if o != name] + ([name] if on else [])
        self.set_objects(objs, emit=True)

    def set_objects(self, objs, emit=False):
        self.objects = list(dict.fromkeys(objs))
        self._render()
        if emit:
            self.changed.emit()

    def _render(self):
        if not self.objects:
            self.chips.setText(f"<span style='color:#7a7a7a'>aucun objet — réglage appliqué partout</span>")
        else:
            chip = ("<span style='background:#4a3d2c;color:#ffd7a8;'>&nbsp;{}&nbsp;</span>")
            self.chips.setText("&nbsp; ".join(chip.format(o) for o in self.objects))


class NodePanel(QWidget):
    """Panneau de propriétés d'un nœud (en-tête nom + classe, grille de knobs)."""

    def __init__(self, name: str, klass: str, color: str):
        super().__init__()
        self.name = name
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 10)
        v.setSpacing(6)
        head = QWidget()
        head.setObjectName("NodeHeader")
        h = QHBoxLayout(head)
        h.setContentsMargins(6, 3, 6, 3)
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background:{color}; border-radius:5px; border:1px solid #111;")
        t = QLabel(name)
        t.setObjectName("NodeTitle")
        k = QLabel(klass)
        k.setObjectName("NodeClass")
        self.toggle = QToolButton()
        self.toggle.setText("▾")
        self.toggle.setFixedWidth(22)
        self.toggle.setToolTip("Replier / déplier")
        self.toggle.setObjectName("NodeToggle")
        self.toggle.clicked.connect(self._collapse)
        h.addWidget(dot)
        h.addWidget(t)
        h.addStretch(1)
        h.addWidget(k)
        h.addWidget(self.toggle)
        v.addWidget(head)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 2, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(5)
        self.grid.setColumnStretch(1, 1)
        v.addWidget(self.body)
        self.row = 0

    def _collapse(self):
        vis = not self.body.isVisible()
        self.body.setVisible(vis)
        self.toggle.setText("▾" if vis else "▸")

    def expand(self):
        self.body.setVisible(True)
        self.toggle.setText("▾")

    def add(self, label: str, w: QWidget, tip: str = ""):
        l = _label(label)
        if tip:
            l.setToolTip(tip)
            w.setToolTip(tip)
        self.grid.addWidget(l, self.row, 0, Qt.AlignTop if isinstance(w, MatteKnob) else Qt.AlignVCenter)
        self.grid.addWidget(w, self.row, 1)
        self.row += 1
        return w

    def section(self, text: str):
        l = QLabel(text.upper())
        l.setObjectName("SectionLabel")
        self.grid.addWidget(l, self.row, 0, 1, 2)
        self.row += 1

    def info(self, label: str) -> QLabel:
        l = QLabel("—")
        l.setStyleSheet("color:#8a8a8a;")
        l.setWordWrap(True)
        return self.add(label, l)


def combo(items, current=None, on_change: Callable | None = None) -> QComboBox:
    c = QComboBox()
    c.addItems(items)
    if current is not None and current in items:
        c.setCurrentText(current)
    if on_change:
        c.currentTextChanged.connect(on_change)
    return c


def checkbox(text, value=False, on_change=None) -> QCheckBox:
    c = QCheckBox(text)
    c.setChecked(value)
    if on_change:
        c.toggled.connect(on_change)
    return c


def spin_int(lo, hi, v, on_change=None) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(v)
    s.setFixedWidth(64)
    s.setKeyboardTracking(False)
    if on_change:
        s.valueChanged.connect(on_change)
    return s


def button(text, on_click=None, checkable=False) -> QPushButton:
    b = QPushButton(text)
    b.setCheckable(checkable)
    if on_click:
        b.clicked.connect(on_click)
    return b
