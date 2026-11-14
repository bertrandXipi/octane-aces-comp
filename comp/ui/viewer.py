"""Viewport OpenGL (QOpenGLWidget + ModernGL) : zoom/pan, wipe A/B, pipette, lecture pixel."""
from __future__ import annotations

import time

import moderngl
import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ..engine import Engine, ViewParams
from ..settings import CompSettings


class Viewer(QOpenGLWidget):
    ready = Signal()
    hover = Signal(int, int, object)      # x, y image, rgba scene ou None
    picked = Signal(int, int, bool)       # x, y image, shift
    frame_time = Signal(float)            # ms GPU+affichage

    def __init__(self, settings: CompSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.view = ViewParams()
        self.engine: Engine | None = None
        self.ctx = None
        self.zoom = 1.0
        self.pan = QPointF(0, 0)
        self.fit_pending = True
        self.pick_mode = False
        self.dirty = True
        self._drag = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(320, 200)

    # ------------------------------------------------------------ GL
    def initializeGL(self):
        self.ctx = moderngl.create_context(require=410)
        self.engine = Engine(self.ctx)
        self.engine.set_display(self.settings.display, self.settings.view)
        self.engine.set_input(self.settings.plate_space)
        self.ready.emit()

    def dpr(self):
        return self.devicePixelRatioF()

    def image_rect_px(self):
        """Rectangle de l'image en pixels physiques (gauche, haut, droite, bas)."""
        iw, ih = self.engine.size if self.engine else (16, 16)
        W, H = self.width() * self.dpr(), self.height() * self.dpr()
        cx, cy = W / 2 + self.pan.x(), H / 2 + self.pan.y()
        return cx - iw * self.zoom / 2, cy - ih * self.zoom / 2, cx + iw * self.zoom / 2, cy + ih * self.zoom / 2

    def fit(self):
        if not self.engine:
            return
        iw, ih = self.engine.size
        W, H = self.width() * self.dpr(), self.height() * self.dpr()
        self.zoom = min(W / iw, H / ih) * 0.94
        self.pan = QPointF(0, 0)
        self.update()

    def one_to_one(self):
        self.zoom = 1.0
        self.pan = QPointF(0, 0)
        self.update()

    def paintGL(self):
        if not self.engine:
            return
        t0 = time.perf_counter()
        if self.fit_pending and self.engine.has.get("beauty") or self.fit_pending and self.engine.has.get("plate"):
            self.fit_pending = False
            iw, ih = self.engine.size
            W, H = self.width() * self.dpr(), self.height() * self.dpr()
            self.zoom = min(W / iw, H / ih) * 0.94
        fbo = self.ctx.detect_framebuffer(self.defaultFramebufferObject())
        W, H = int(self.width() * self.dpr()), int(self.height() * self.dpr())
        fbo.use()
        fbo.viewport = (0, 0, W, H)
        self.ctx.clear(0.102, 0.102, 0.102, 1.0)
        has_media = self.engine.has.get("beauty") or self.engine.has.get("plate")
        if has_media:
            self.engine.render_frame(self.settings, self.view, b_neutral=self.view.mode != 0)
            l, t, r, b = self.image_rect_px()
            rect = (2 * l / W - 1, 1 - 2 * b / H, 2 * r / W - 1, 1 - 2 * t / H)
            self.engine.render_view(fbo, (0, 0, W, H), rect, self.view)
        self.ctx.finish()
        self.frame_time.emit((time.perf_counter() - t0) * 1000)
        self._overlay(has_media)

    def _overlay(self, has_media):
        """Traits seulement : le texte QPainter sur un contexte partagé avec ModernGL
        sort brouillé (cache de glyphes), les textes vont dans la barre d'info."""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        d = self.dpr()
        if has_media:
            l, t, r, b = (v / d for v in self.image_rect_px())
            p.setPen(QPen(QColor(90, 90, 90), 1))
            p.drawRect(int(l) - 1, int(t) - 1, int(r - l) + 1, int(b - t) + 1)
            if self.view.mode == 2:
                x = l + (r - l) * self.view.wipe
                p.setPen(QPen(QColor(240, 150, 46), 1))
                p.drawLine(int(x), int(t) - 14, int(x), int(b) + 14)
                p.setBrush(QColor(240, 150, 46))
                p.drawEllipse(QPointF(x, (t + b) / 2), 5, 5)
        if self.pick_mode:
            p.setPen(QPen(QColor(240, 150, 46), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(1, 1, self.width() - 2, self.height() - 2)
        p.end()

    # ------------------------------------------------------------ souris
    def to_image(self, pos) -> tuple[float, float]:
        l, t, r, b = self.image_rect_px()
        d = self.dpr()
        return (pos.x() * d - l) / self.zoom, (pos.y() * d - t) / self.zoom

    def read_pixel(self, x: int, y: int):
        if not self.engine or self.engine.fbo_a is None:
            return None
        iw, ih = self.engine.size
        if not (0 <= x < iw and 0 <= y < ih):
            return None
        self.makeCurrent()
        raw = self.engine.fbo_a.read(viewport=(x, ih - 1 - y, 1, 1), components=4, dtype="f4")
        self.doneCurrent()
        return np.frombuffer(raw, "f4").copy()

    def wheelEvent(self, e):
        f = 1.0015 ** e.angleDelta().y()
        d = self.dpr()
        W, H = self.width() * d, self.height() * d
        mx, my = e.position().x() * d - W / 2, e.position().y() * d - H / 2
        nz = min(max(self.zoom * f, 0.05), 64)
        f = nz / self.zoom
        self.pan = QPointF(mx - (mx - self.pan.x()) * f, my - (my - self.pan.y()) * f)
        self.zoom = nz
        self.update()

    def mousePressEvent(self, e):
        d = self.dpr()
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and e.modifiers() & Qt.AltModifier):
            self._drag = ("pan", e.position(), QPointF(self.pan))
            return
        if e.button() == Qt.LeftButton:
            x, y = self.to_image(e.position())
            if self.pick_mode:
                self.picked.emit(int(x), int(y), bool(e.modifiers() & Qt.ShiftModifier))
                return
            if self.view.mode == 2 and self.engine:
                l, t, r, b = self.image_rect_px()
                wx = l + (r - l) * self.view.wipe
                if abs(e.position().x() * d - wx) < 12 * d:
                    self._drag = ("wipe",)

    def mouseMoveEvent(self, e):
        if self._drag and self._drag[0] == "pan":
            dlt = (e.position() - self._drag[1]) * self.dpr()
            self.pan = self._drag[2] + dlt
            self.update()
        elif self._drag and self._drag[0] == "wipe":
            l, t, r, b = self.image_rect_px()
            self.view.wipe = min(max((e.position().x() * self.dpr() - l) / (r - l), 0.0), 1.0)
            self.update()
        x, y = self.to_image(e.position())
        xi, yi = int(np.floor(x)), int(np.floor(y))
        self.hover.emit(xi, yi, self.read_pixel(xi, yi))

    def mouseReleaseEvent(self, e):
        self._drag = None

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_F:
            self.fit()
        elif k == Qt.Key_H:
            self.one_to_one()
        elif k == Qt.Key_Escape and self.pick_mode:
            self.window().stop_picking()
        else:
            super().keyPressEvent(e)

    def resizeGL(self, w, h):
        self.update()
