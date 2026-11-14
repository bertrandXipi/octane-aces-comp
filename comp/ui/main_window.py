"""Fenêtre principale : Viewer + timeline, Node Graph, Properties."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

import numpy as np
from PySide6.QtCore import QProcess, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QProgressBar, QScrollArea, QSlider, QSplitter, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from .. import color
from ..batch import Sources
from ..engine import crypto_masks
from ..settings import CompSettings
from . import knobs as K
from .nodegraph import NodeGraph
from .viewer import Viewer


# ============================================================================ sources + préchargement
class Loader:
    """Accès aux médias + préchargement en arrière-plan : rush sur un fil (flux ffmpeg
    séquentiel), EXR CG sur plusieurs fils (le décodage EXR libère le GIL)."""

    def __init__(self):
        from concurrent.futures import ThreadPoolExecutor
        self.src: Sources | None = None
        self.want = 0
        self.ahead = 24
        self.stop = False
        self.ev = threading.Event()
        self.pool = ThreadPoolExecutor(3)
        self.gen = 0
        self.masks: dict = {}       # (frame CG, signature des mattes) -> masques
        self.settings = None
        threading.Thread(target=self._run, daemon=True).start()

    def set(self, s: CompSettings):
        self.settings = s
        self.gen += 1
        old, self.src = self.src, None
        if old:
            old.close()
        if s.plate_path or s.cg_path:
            src = Sources(s)
            if src.cg:
                src.cg._cache_n = 64
            if src.plate:
                src.plate._cache_n = 64
                src.plate.half = True
            self.src = src
        self.masks = {}

    def __len__(self):
        return len(self.src) if self.src else 0

    def get(self, i):
        src = self.src
        if not src:
            return None, None
        cg = src.cg_frame(i)
        plate = None
        if src.plate:  # frame déjà en cache : lecture directe, sans attendre le fil du rush
            plate = src.plate._cache.get(i)
            if plate is None:
                plate = src.plate.frame(i)
        return plate, cg

    def cached(self, i) -> bool:
        s = self.src
        if not s:
            return False
        ok = True
        if s.plate:
            ok &= i in s.plate._cache
        if s.cg:
            j = i + s.s.frame_offset
            ok &= (j in s.cg._cache) or not (0 <= j < len(s.cg))
        return ok

    @staticmethod
    def sig(s: CompSettings):
        return tuple((m.layer, tuple(m.objects)) for m in (s.cg_mask, s.reflection_mask, s.shadow_mask))

    def _load_cg(self, src, j):
        cg = src.cg.frame(j)
        key = (j, self.sig(src.s))
        if key not in self.masks:
            self.masks[key] = crypto_masks(cg, src.s)
            if len(self.masks) > 96:
                self.masks.pop(next(iter(self.masks)), None)
        return cg

    def masks_for(self, i, cg):
        src = self.src
        if cg is None or not src:
            return None
        key = (i + src.s.frame_offset, self.sig(src.s))
        m = self.masks.get(key)
        if m is None:
            m = crypto_masks(cg, src.s)
            self.masks[key] = m
        return m

    def prefetch(self, i):
        self.want = i
        self.ev.set()

    def _run(self):
        pending = set()
        while not self.stop:
            self.ev.wait(0.1)
            self.ev.clear()
            src, gen, base = self.src, self.gen, self.want
            if not src:
                continue
            n = len(src)
            frames = [(base + k) % n for k in range(min(self.ahead, n))]
            if src.cg:  # CG en parallèle
                for i in frames:
                    j = i + src.s.frame_offset
                    if 0 <= j < len(src.cg) and j not in src.cg._cache and j not in pending:
                        pending.add(j)
                        f = self.pool.submit(self._load_cg, src, j)
                        f.add_done_callback(lambda _f, j=j: pending.discard(j))
            if src.plate:  # rush dans l'ordre
                for i in frames:
                    if self.want != base or self.gen != gen or self.stop:
                        break
                    if i not in src.plate._cache:
                        try:
                            src.plate.frame(i)
                        except Exception:
                            break


# ============================================================================ dialogue de rendu
class RenderDialog(QDialog):
    def __init__(self, parent, settings: CompSettings, n_frames: int):
        super().__init__(parent)
        self.setWindowTitle("Rendu batch")
        self.settings = settings
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.fmt = K.combo(["exr", "tiff", "prores"], "exr", self._fmt_changed)
        self.out = QLineEdit(os.path.abspath("rendu"))
        browse = QToolButton()
        browse.setText("…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.out, 1)
        row.addWidget(browse)
        self.start = K.spin_int(1, max(n_frames, 1), 1)
        self.end = K.spin_int(1, max(n_frames, 1), max(n_frames, 1))
        rng = QHBoxLayout()
        rng.addWidget(self.start)
        rng.addWidget(QLabel("à"))
        rng.addWidget(self.end)
        rng.addStretch(1)
        self.bake = K.checkbox("EXR : appliquer l'Output Transform (sinon ACEScg linéaire)")
        form.addRow("Format", self.fmt)
        form.addRow("Sortie", row)
        form.addRow("Frames", rng)
        form.addRow("", self.bake)
        lay.addLayout(form)
        self.bar = QProgressBar()
        self.bar.setRange(0, max(n_frames, 1))
        self.log = QLabel("")
        self.log.setStyleSheet("color:#8a8a8a; font-family: Menlo; font-size:10px;")
        lay.addWidget(self.bar)
        lay.addWidget(self.log)
        self.buttons = QDialogButtonBox()
        self.go = self.buttons.addButton("Rendre", QDialogButtonBox.AcceptRole)
        self.buttons.addButton("Fermer", QDialogButtonBox.RejectRole)
        self.go.clicked.connect(self._run)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.proc = None

    def _fmt_changed(self, f):
        p = self.out.text()
        base = os.path.dirname(p) if p.endswith(".mov") else p
        self.out.setText(os.path.join(base, "comp.mov") if f == "prores" else base)
        self.bake.setEnabled(f == "exr")

    def _browse(self):
        if self.fmt.currentText() == "prores":
            p, _ = QFileDialog.getSaveFileName(self, "Fichier ProRes", self.out.text(), "QuickTime (*.mov)")
        else:
            p = QFileDialog.getExistingDirectory(self, "Dossier de sortie", self.out.text())
        if p:
            self.out.setText(p)

    def _run(self):
        if self.proc:
            self.proc.kill()
            return
        tmp = os.path.join(tempfile.gettempdir(), f"comp_render_{os.getpid()}.json")
        self.settings.save(tmp)
        args = ["-m", "comp.batch", tmp, "-o", self.out.text(), "-f", self.fmt.currentText(),
                "--start", str(self.start.value() - 1), "--end", str(self.end.value())]
        if self.bake.isChecked() and self.fmt.currentText() == "exr":
            args.append("--bake-display")
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._done)
        self.bar.setRange(self.start.value() - 1, self.end.value())
        self.go.setText("Arrêter")
        self.proc.start(sys.executable, args)

    def _read(self):
        for line in bytes(self.proc.readAllStandardOutput()).decode(errors="replace").splitlines():
            if line.startswith("frame "):
                try:
                    self.bar.setValue(int(line.split()[1].split("/")[0]))
                except ValueError:
                    pass
            if line.strip():
                self.log.setText(line.strip())

    def _done(self, code, _status):
        self.go.setText("Rendre")
        if code == 0:
            self.bar.setValue(self.bar.maximum())
        else:
            self.log.setText(self.log.text() + f"  (code {code})")
        self.proc = None


# ============================================================================ fenêtre
class MainWindow(QMainWindow):
    frame_changed = Signal(int)

    def __init__(self, settings: CompSettings | None = None, settings_path: str | None = None):
        super().__init__()
        self.s = settings or CompSettings()
        self.settings_path = settings_path
        self.frame = 0
        self.loader = Loader()
        self.cg_cur = None
        self.picking = None
        self.playing = 0
        self._play_t = time.perf_counter()
        self._play_count = 0
        self.setWindowTitle("Octane Comp — ACES")

        self.viewer = Viewer(self.s)
        self.viewer.ready.connect(self._viewer_ready)
        self.viewer.hover.connect(self._hover)
        self.viewer.picked.connect(self._picked)
        self.viewer.frame_time.connect(self._gpu_ms)

        left = QSplitter(Qt.Vertical)
        left.addWidget(self._wrap_tab(self._viewer_pane(), "Viewer1"))
        self.graph = NodeGraph()
        self.graph.selected.connect(self.show_node)
        left.addWidget(self._wrap_tab(self.graph, "Node Graph"))
        left.setStretchFactor(0, 5)
        left.setStretchFactor(1, 2)
        left.setSizes([660, 260])

        main = QSplitter(Qt.Horizontal)
        main.addWidget(left)
        props = self._wrap_tab(self._properties(), "Properties")
        props.setMinimumWidth(400)
        main.addWidget(props)
        main.setStretchFactor(0, 3)
        main.setStretchFactor(1, 1)
        main.setSizes([1100, 400])
        self.setCentralWidget(main)

        self._menus()
        self.status = self.statusBar()
        self.st_color = QLabel()
        self.st_gpu = QLabel()
        self.status.addWidget(self.st_color, 1)
        self.status.addPermanentWidget(self.st_gpu)
        self._update_status()

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.refresh_knobs()
        self._fit_screen()

    def _fit_screen(self):
        """Taille et position adaptées à l'écran (jamais plus grand que la zone utile)."""
        from PySide6.QtGui import QGuiApplication
        g = QGuiApplication.primaryScreen().availableGeometry()
        # marge pour Stage Manager / Dock latéral, que Qt ne retire pas de la zone utile
        w = max(min(1500, g.width() - 240), min(self.minimumSizeHint().width(), g.width()))
        h = min(960, int(g.height() * 0.94))
        self.resize(w, h)
        self.move(g.x() + g.width() - w - 12, g.y() + (g.height() - h) // 2)

    # ------------------------------------------------------------------ construction
    def _wrap_tab(self, w, title):
        t = QTabWidget()
        t.setDocumentMode(True)
        t.addTab(w, title)
        return t

    def _viewer_pane(self):
        pane = QWidget()
        v = QVBoxLayout(pane)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._viewer_bar())
        v.addWidget(self.viewer, 1)
        self.info = QLabel(" ")
        self.info.setObjectName("InfoBar")
        self.info.setFixedHeight(18)
        self.info.setContentsMargins(8, 0, 8, 0)
        v.addWidget(self.info)
        v.addWidget(self._timeline())
        return pane

    def _seg(self, labels, on_change, tips=()):
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        btns = []
        for i, l in enumerate(labels):
            b = QToolButton()
            b.setText(l)
            b.setCheckable(True)
            b.setProperty("seg", True)
            b.setAutoExclusive(True)
            if i < len(tips):
                b.setToolTip(tips[i])
            b.clicked.connect(lambda _=False, i=i: on_change(i))
            h.addWidget(b)
            btns.append(b)
        btns[0].setChecked(True)
        box.buttons = btns
        return box

    def _viewer_bar(self):
        bar = QWidget()
        bar.setObjectName("ViewerBar")
        rows = QVBoxLayout(bar)
        rows.setContentsMargins(6, 3, 6, 3)
        rows.setSpacing(3)
        h, h2 = QHBoxLayout(), QHBoxLayout()   # deux lignes d'outils, comme un viewer de compositing
        h.setSpacing(6)
        h2.setSpacing(6)
        rows.addLayout(h)
        rows.addLayout(h2)
        self.ch = K.combo(["rgba", "red", "green", "blue", "alpha", "luminance"], "rgba",
                          lambda t: self._view("channel", ["rgba", "red", "green", "blue", "alpha", "luminance"].index(t)))
        self.ch.setToolTip("Canal affiché (R, G, B, A comme dans un viewer de compositing)")
        h.addWidget(self.ch)
        self.ab = self._seg(["A", "B", "wipe", "diff"], lambda i: self._view("mode", i),
                            ("Comp réglée", "Référence", "Volet A/B (glisser la ligne)", "Différence |A−B|"))
        h.addWidget(self.ab)
        h.addWidget(QLabel("B"))
        self.bsrc = K.combo(["plate", "CG brut"], "plate",
                            lambda t: self._view("b_source", "plate" if t == "plate" else "raw"))
        self.bsrc.setToolTip("Référence B : rush seul, ou comp sans aucun réglage")
        h.addWidget(self.bsrc)
        h.addWidget(QLabel("matte"))
        self.ov = K.combo(["off", "CG", "reflets", "ombres"], "off",
                          lambda t: self._view("overlay", ["off", "CG", "reflets", "ombres"].index(t) - 1))
        self.ov.setToolTip("Superposer le masque Cryptomatte d'un réglage")
        h.addWidget(self.ov)
        h2.addWidget(QLabel("f/stop"))
        self.expo = K.FloatKnob(-6, 6, 0.0, 2)
        self.expo.setMaximumWidth(280)
        self.expo.changed.connect(lambda v: self._view("exposure", v))
        h2.addWidget(self.expo, 1)
        h2.addSpacing(10)
        h2.addWidget(QLabel("γ"))
        self.gam = K.FloatKnob(0.2, 3.0, 1.0, 2)
        self.gam.setMaximumWidth(240)
        self.gam.changed.connect(lambda v: self._view("gamma", v))
        h2.addWidget(self.gam, 1)
        h2.addStretch(2)
        self.disp = K.combo(color.displays(), self.s.display, self._display_changed)
        self.vw = K.combo(color.views(self.s.display), self.s.view, self._view_changed)
        self.disp.setToolTip("Display OCIO")
        self.vw.setToolTip("View OCIO (Output Transform ACES)")
        for c in (self.ch, self.bsrc, self.ov, self.disp, self.vw):
            c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            c.setMinimumContentsLength(3)
        self.vw.setMinimumContentsLength(14)
        self.disp.setMinimumContentsLength(8)
        for k in (self.expo, self.gam):
            k.slider.setMinimumWidth(24)
        h.addStretch(1)
        h.addWidget(self.disp)
        h.addWidget(self.vw)
        return bar

    def _timeline(self):
        w = QWidget()
        w.setObjectName("Timeline")
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(4)

        def tb(text, tip, fn):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            h.addWidget(b)
            return b

        tb("|◀", "Première frame (Début)", lambda: self.goto(0))
        tb("◀|", "Frame précédente (←)", lambda: self.goto(self.frame - 1))
        self.btn_back = tb("◀", "Lecture arrière (J)", lambda: self.play(-1))
        self.btn_play = tb("▶", "Lecture (L / Espace)", lambda: self.play(1))
        tb("|▶", "Frame suivante (→)", lambda: self.goto(self.frame + 1))
        tb("▶|", "Dernière frame (Fin)", lambda: self.goto(len(self.loader) - 1))
        self.fspin = K.spin_int(1, 1, 1, lambda v: self.goto(v - 1))
        h.addWidget(self.fspin)
        self.tl = QSlider(Qt.Horizontal)
        self.tl.setRange(0, 0)
        self.tl.setTickPosition(QSlider.TicksBelow)
        self.tl.valueChanged.connect(self.goto)
        h.addWidget(self.tl, 1)
        self.rng = QLabel("1 – 1")
        self.rng.setStyleSheet("color:#8a8a8a;")
        h.addWidget(self.rng)
        self.fps_target = K.combo(["24", "25", "30", "48", "50", "60"], "24")
        self.fps_target.setToolTip("Cadence de lecture")
        h.addWidget(self.fps_target)
        self.fps_lbl = QLabel("")
        self.fps_lbl.setFixedWidth(70)
        self.fps_lbl.setStyleSheet("color:#8a8a8a; font-family: Menlo; font-size:10px;")
        h.addWidget(self.fps_lbl)
        return w

    def _properties(self):
        s = self.s
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # le contenu s'adapte à la largeur
        host = QWidget()
        self.props_layout = QVBoxLayout(host)
        self.props_layout.setContentsMargins(0, 0, 0, 0)
        self.props_layout.setSpacing(2)
        self.panels: dict[str, K.NodePanel] = {}
        self.k = {}
        self.mattes: dict[str, K.MatteKnob] = {}
        spaces = color.input_spaces()

        # --- Read1 : rush
        p = self._panel("Read1", "Read", "#9a9a9a")
        self.k["plate_path"] = p.add("file", K.FileKnob("Rush F-Log2", "Vidéo / images (*.mov *.mp4 *.mxf *.tif *.tiff *.png *.dpx *.exr);;Tous (*)"))
        self.k["plate_path"].changed.connect(lambda v: self._media("plate_path", v))
        self.k["plate_space"] = p.add("input transform", K.combo(spaces, s.plate_space, lambda v: self._media("plate_space", v)),
                                      "Espace couleur du rush (converti en ACEScg)")
        self.k["plate_exposure"] = p.add("exposure", K.FloatKnob(-4, 4, 0.0, 2), "Exposition du rush en stops")
        self.plate_info = p.info("info")

        # --- Read2 : EXR Octane
        p = self._panel("Read2", "Read", "#9a9a9a")
        self.k["cg_path"] = p.add("file", K.FileKnob("Passes Octane EXR", "OpenEXR (*.exr);;Tous (*)", directory_ok=True))
        self.k["cg_path"].changed.connect(lambda v: self._media("cg_path", v))
        self.k["cg_space"] = p.add("input transform", K.combo(spaces, s.cg_space, lambda v: self._media("cg_space", v)),
                                   "Espace des passes Octane (ACEScg si le rendu est en ACES)")
        self.k["frame_offset"] = p.add("frame offset", K.spin_int(-10000, 10000, 0, lambda v: self._media("frame_offset", v)),
                                       "Décalage de la séquence CG par rapport au rush")
        self.cg_info = p.info("passes")

        # --- Cryptomatte1
        p = self._panel("Cryptomatte1", "Cryptomatte", "#7f9a45")
        self.crypto_layer = p.add("layer", K.combo([], None, self._crypto_layer))
        self.crypto_info = p.info("manifest")
        self.crypto_hover = p.info("sous le curseur")

        # --- Recompose1
        p = self._panel("Recompose1", "Recompose", "#6c9a6a")
        self.k["cg_gain"] = p.add("gain", K.FloatKnob(0, 4, 1.0), "Gain global du CG")
        self.k["diffuse_gain"] = p.add("diffuse", K.FloatKnob(0, 4, 1.0), "Dosage de la passe Diffuse")
        p.section("reflets")
        self.k["reflection_gain"] = p.add("balance reflets", K.FloatKnob(0, 4, 1.0), "Dosage des reflets (Reflection/Specular)")
        self.k["reflection_tint"] = p.add("teinte", K.ColorKnob([1, 1, 1]))
        self.mattes["reflection_mask"] = p.add("matte", K.MatteKnob(), "Limiter le réglage des reflets à des objets")
        self.k["reflection_mask.mix"] = p.add("mix masque", K.FloatKnob(0, 1, 1.0))

        # --- ShadowCatcher1
        p = self._panel("ShadowCatcher1", "ShadowCatcher", "#8a7d9e")
        self.k["shadow_density"] = p.add("densité", K.FloatKnob(0, 3, 1.0), "Densité des ombres portées sur le rush")
        self.k["shadow_color"] = p.add("couleur", K.ColorKnob([0, 0, 0]), "Couleur de l'ombre (noir = assombrir)")
        self.k["shadow_invert"] = p.add("", K.checkbox("inverser la passe Shadow"))
        self.mattes["shadow_mask"] = p.add("matte", K.MatteKnob())
        self.k["shadow_mask.mix"] = p.add("mix masque", K.FloatKnob(0, 1, 1.0))

        # --- Merge1
        p = self._panel("Merge1", "Merge", "#4f78a8")
        p.add("operation", K.combo(["over"], "over"))
        self.mattes["cg_mask"] = p.add("matte CG", K.MatteKnob(), "N'intégrer que ces objets")
        self.k["cg_mask.mix"] = p.add("mix masque", K.FloatKnob(0, 1, 1.0))

        # --- OCIODisplay1
        p = self._panel("OCIODisplay1", "OCIODisplay", "#6c9a6a")
        cfg = color.config()
        p.info("config").setText(f"{cfg.getName()}\n+ {color.FLOG2}")
        p.info("working").setText(color.WORKING)
        self.disp_info = p.info("display / view")

        self.props_layout.addStretch(1)
        area.setWidget(host)

        # câblage des knobs simples
        for key, w in self.k.items():
            if key in ("plate_path", "cg_path", "plate_space", "cg_space", "frame_offset"):
                continue
            if isinstance(w, K.FloatKnob):
                w.changed.connect(lambda v, key=key: self._set(key, v))
            elif isinstance(w, K.ColorKnob):
                w.changed.connect(lambda v, key=key: self._set(key, v))
            elif hasattr(w, "toggled"):
                w.toggled.connect(lambda v, key=key: self._set(key, bool(v)))
        for key, m in self.mattes.items():
            m.changed.connect(lambda key=key, m=m: self._matte_changed(key, m))
            m.pick_toggled.connect(self._pick_toggled)
        return area

    def _panel(self, name, klass, col):
        p = K.NodePanel(name, klass, col)
        self.panels[name] = p
        self.props_layout.addWidget(p)
        return p

    def _menus(self):
        mb = self.menuBar()
        f = mb.addMenu("Fichier")
        self._act(f, "Lire le rush F-Log2…", "R", lambda: self.k["plate_path"]._browse())
        self._act(f, "Lire les passes Octane (EXR)…", "Shift+R", lambda: self.k["cg_path"]._browse())
        f.addSeparator()
        self._act(f, "Ouvrir les réglages…", QKeySequence.Open, self.open_settings)
        self._act(f, "Enregistrer les réglages", QKeySequence.Save, self.save_settings)
        self._act(f, "Enregistrer les réglages sous…", QKeySequence.SaveAs, lambda: self.save_settings(True))
        f.addSeparator()
        self._act(f, "Rendu batch…", "F7", self.render_dialog)
        v = mb.addMenu("Viewer")
        self._act(v, "Cadrer (F)", None, self.viewer.fit)
        self._act(v, "Taille réelle (H)", None, self.viewer.one_to_one)
        self._act(v, "Vue A", "1", lambda: self._set_mode(0))
        self._act(v, "Vue B", "2", lambda: self._set_mode(1))
        self._act(v, "Wipe A/B", "W", lambda: self._set_mode(2))
        self._act(v, "Différence", "D", lambda: self._set_mode(3))
        v.addSeparator()
        self._act(v, "Bypass OCIO (valeurs scène)", "Shift+O", self._toggle_ocio)
        t = mb.addMenu("Lecture")
        self._act(t, "Lecture / pause", "Space", lambda: self.play(1))
        self._act(t, "Lecture", "L", lambda: self.play(1))
        self._act(t, "Lecture arrière", "J", lambda: self.play(-1))
        self._act(t, "Pause", "K", self.pause)
        self._act(t, "Frame suivante", "Right", lambda: self.goto(self.frame + 1))
        self._act(t, "Frame précédente", "Left", lambda: self.goto(self.frame - 1))

    def _act(self, menu, text, key, fn):
        a = QAction(text, self)
        if key:
            a.setShortcut(QKeySequence(key))
            a.setShortcutContext(Qt.ApplicationShortcut)
        a.triggered.connect(fn)
        menu.addAction(a)
        self.addAction(a)
        return a

    # ------------------------------------------------------------------ réglages
    def _set(self, key: str, value):
        obj = self.s
        parts = key.split(".")
        for p in parts[:-1]:
            obj = getattr(obj, p)
        setattr(obj, parts[-1], value)
        self.viewer.update()

    def _matte_changed(self, key, m: K.MatteKnob):
        ms = getattr(self.s, key)
        ms.objects = list(m.objects)
        ms.invert = m.invert.isChecked()
        ms.layer = self.crypto_layer.currentText()
        self._upload_masks()

    def _crypto_layer(self, name):
        for key in self.mattes:
            getattr(self.s, key).layer = name
        if self.cg_cur:
            lay = next((c for c in self.cg_cur.crypto if c.name == name), None)
            names = lay.names if lay else []
            for m in self.mattes.values():
                m.available = names
            self.crypto_info.setText(f"{len(names)} objets : " + ", ".join(names[:12]) + (" …" if len(names) > 12 else ""))
        self._upload_masks()

    def _media(self, key, value):
        setattr(self.s, key, value)
        if key == "plate_space" and self.viewer.engine:
            self.viewer.makeCurrent()
            self.viewer.engine.set_input(value)
            self.viewer.doneCurrent()
        self.reload_media()

    def refresh_knobs(self):
        """Réglages -> widgets (après chargement JSON)."""
        s = self.s
        for key, w in self.k.items():
            obj = s
            for p in key.split(".")[:-1]:
                obj = getattr(obj, p)
            v = getattr(obj, key.split(".")[-1])
            w.blockSignals(True)
            if isinstance(w, (K.FloatKnob, K.ColorKnob, K.FileKnob)):
                w.set_value(v)
            elif isinstance(w, QComboBox):
                w.setCurrentText(v)
            elif hasattr(w, "setChecked"):
                w.setChecked(bool(v))
            elif hasattr(w, "setValue"):
                w.setValue(int(v))
            w.blockSignals(False)
        for key, m in self.mattes.items():
            ms = getattr(s, key)
            m.set_objects(ms.objects)
            m.invert.blockSignals(True)
            m.invert.setChecked(ms.invert)
            m.invert.blockSignals(False)
        for wdg, val in ((self.disp, s.display), (self.vw, s.view)):
            wdg.blockSignals(True)
            wdg.setCurrentText(val)
            wdg.blockSignals(False)
        self._update_status()

    # ------------------------------------------------------------------ médias / frames
    def _viewer_ready(self):
        # hors d'initializeGL : makeCurrent/doneCurrent imbriqués y casseraient le contexte
        QTimer.singleShot(0, self.reload_media)

    def reload_media(self):
        self.pause()
        try:
            self.loader.set(self.s)
        except Exception as e:
            QMessageBox.warning(self, "Lecture impossible", str(e))
            self.loader.src = None
        n = len(self.loader)
        self.tl.blockSignals(True)
        self.tl.setRange(0, max(n - 1, 0))
        self.tl.setTickInterval(max(n // 30, 1))
        self.tl.blockSignals(False)
        self.fspin.blockSignals(True)
        self.fspin.setRange(1, max(n, 1))
        self.fspin.blockSignals(False)
        self.rng.setText(f"1 – {max(n, 1)}")
        src = self.loader.src
        if src and src.plate:
            p = src.plate
            kind = "vidéo" if p.video else f"{len(p.files)} images"
            self.plate_info.setText(f"{kind} · {len(p)} frames · {p.fps:g} i/s · {p.input_space} → ACEScg")
            self.fps_target.setCurrentText(str(int(round(p.fps))) if str(int(round(p.fps))) in
                                           [self.fps_target.itemText(i) for i in range(self.fps_target.count())] else "24")
        else:
            self.plate_info.setText("—")
        self.viewer.fit_pending = True
        self.frame = min(self.frame, max(n - 1, 0))
        self.show_frame(self.frame, force=True)
        if src and src.cg:
            cg = self.cg_cur
            if cg:
                self.cg_info.setText(f"{len(src.cg)} frames · {cg.width}×{cg.height}\n"
                                     f"couches : {', '.join(l or '(rgba)' for l in cg.layers)}\n"
                                     f"rôles : {', '.join(f'{r}' for r in cg.passes)}")
                layers = [c.name for c in cg.crypto]
                self.crypto_layer.blockSignals(True)
                self.crypto_layer.clear()
                self.crypto_layer.addItems(layers)
                want = self.s.reflection_mask.layer or self.s.shadow_mask.layer or self.s.cg_mask.layer
                if want in layers:
                    self.crypto_layer.setCurrentText(want)
                self.crypto_layer.blockSignals(False)
                self._crypto_layer(self.crypto_layer.currentText())
                if not layers:
                    self.crypto_info.setText("aucune Cryptomatte dans l'EXR")
        else:
            self.cg_info.setText("—")
        self._update_status()

    def show_frame(self, i, force=False):
        n = len(self.loader)
        if n == 0:
            self.viewer.makeCurrent()
            if self.viewer.engine:
                self.viewer.engine.upload(None, None, None)
            self.viewer.doneCurrent()
            self.viewer.update()
            return
        i = max(0, min(i, n - 1))
        if i == self.frame and not force:
            return
        self.frame = i
        try:
            plate, cg = self.loader.get(i)
        except Exception as e:
            self.status.showMessage(f"Erreur frame {i + 1} : {e}", 5000)
            return
        self.cg_cur = cg
        masks = self.loader.masks_for(i, cg)
        self.viewer.makeCurrent()
        self.viewer.engine.upload(plate, cg, masks)
        self.viewer.doneCurrent()
        self.viewer.update()
        for w, v in ((self.tl, i), (self.fspin, i + 1)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self.loader.prefetch(i + 1)
        if not self.viewer.underMouse():
            self.info.setText(self._idle_info())

    def _upload_masks(self):
        if not self.viewer.engine:
            return
        masks = crypto_masks(self.cg_cur, self.s)
        self.viewer.makeCurrent()
        self.viewer.engine._upload("masks", masks, 3)
        self.viewer.doneCurrent()
        self.viewer.update()

    def goto(self, i):
        self.show_frame(int(i))

    # ------------------------------------------------------------------ lecture
    def play(self, direction):
        if self.playing == direction:
            self.pause()
            return
        self.playing = direction
        self._play_t, self._play_count = time.perf_counter(), 0
        self.btn_play.setText("❚❚" if direction > 0 else "▶")
        self.btn_back.setText("❚❚" if direction < 0 else "◀")
        self.timer.start(int(1000 / float(self.fps_target.currentText())))

    def pause(self):
        self.playing = 0
        self.timer.stop()
        self.btn_play.setText("▶")
        self.btn_back.setText("◀")
        self.fps_lbl.setText("")

    def _tick(self):
        n = len(self.loader)
        if not n:
            return self.pause()
        nxt = (self.frame + self.playing) % n
        if not self.loader.cached(nxt):
            self.loader.prefetch(nxt)
            return  # on attend le préchargement (lecture de toutes les frames)
        self.show_frame(nxt)
        self._play_count += 1
        el = time.perf_counter() - self._play_t
        if el > 0.5:
            self.fps_lbl.setText(f"{self._play_count / el:5.1f} i/s")
            self._play_t, self._play_count = time.perf_counter(), 0

    # ------------------------------------------------------------------ viewer
    def _view(self, attr, value):
        setattr(self.viewer.view, attr, value)
        self.viewer.update()
        self.info.setText(self._idle_info())

    def _set_mode(self, i):
        self.ab.buttons[i].setChecked(True)
        self._view("mode", i)

    def _toggle_ocio(self):
        self._view("ocio_on", not self.viewer.view.ocio_on)
        self._update_status()

    def _display_changed(self, d):
        self.vw.blockSignals(True)
        self.vw.clear()
        self.vw.addItems(color.views(d))
        self.vw.blockSignals(False)
        self.s.display = d
        self.s.view = self.vw.currentText()
        self._apply_display()

    def _view_changed(self, v):
        if v:
            self.s.view = v
            self._apply_display()

    def _apply_display(self):
        if self.viewer.engine:
            self.viewer.makeCurrent()
            self.viewer.engine.set_display(self.s.display, self.s.view)
            self.viewer.engine.set_input(self.s.plate_space)
            self.viewer.doneCurrent()
            self.viewer.update()
        self._update_status()

    def _update_status(self):
        s = self.s
        ocio = f"{s.display} · {s.view}" if self.viewer.view.ocio_on else "OCIO bypass (valeurs scène)"
        src = f"{s.plate_space} → {color.WORKING}" if s.plate_path else "pas de rush"
        self.st_color.setText(f"  working {color.WORKING}   ·   rush {src}   ·   CG {s.cg_space}   ·   sortie {ocio}")
        if hasattr(self, "disp_info"):
            self.disp_info.setText(f"{s.display}\n{s.view}")

    def _gpu_ms(self, ms):
        self.st_gpu.setText(f"GPU {ms:.2f} ms  ")

    def _idle_info(self):
        if self.picking:
            return "PIPETTE CRYPTOMATTE  ·  clic : ajouter l'objet  ·  Maj+clic : retirer  ·  Échap : quitter"
        if not len(self.loader):
            return "Aucun média  ·  Fichier ▸ Lire le rush (R)  ·  Fichier ▸ Lire les passes Octane (Maj+R)"
        iw, ih = self.viewer.engine.size if self.viewer.engine else (0, 0)
        ab = ["A", "B", "wipe A | B", "différence"][self.viewer.view.mode]
        return f"{iw}×{ih}  ·  frame {self.frame + 1}/{len(self.loader)}  ·  vue {ab}"

    def _hover(self, x, y, px):
        if px is None:
            self.info.setText(self._idle_info())
            return
        pre = "PIPETTE  ·  " if self.picking else ""
        txt = pre + f"x {x:<5d} y {y:<5d}   R {px[0]:8.4f}  G {px[1]:8.4f}  B {px[2]:8.4f}  A {px[3]:6.3f}   ACEScg"
        name = None
        if self.cg_cur and self.cg_cur.crypto:
            lay = next((c for c in self.cg_cur.crypto if c.name == self.crypto_layer.currentText()), self.cg_cur.crypto[0])
            cw, ch = self.cg_cur.width, self.cg_cur.height
            iw, ih = self.viewer.engine.size
            cx, cy = int(x * cw / iw), int(y * ch / ih)
            if 0 <= cx < cw and 0 <= cy < ch:
                name = lay.pick(cx, cy)
        if name:
            txt += f"   ·   {name}"
        self.crypto_hover.setText(name or "—")
        self.info.setText(txt)

    # ------------------------------------------------------------------ pipette Cryptomatte
    def _pick_toggled(self, matte, on):
        if on:
            for m in self.mattes.values():
                if m is not matte and m.pick.isChecked():
                    m.pick.blockSignals(True)
                    m.pick.setChecked(False)
                    m.pick.blockSignals(False)
            self.picking = matte
            idx = {"cg_mask": 0, "reflection_mask": 1, "shadow_mask": 2}
            key = next(k for k, v in self.mattes.items() if v is matte)
            self.ov.setCurrentIndex(idx[key] + 1)
        else:
            self.picking = None
        self.viewer.pick_mode = self.picking is not None
        self.info.setText(self._idle_info())
        self.viewer.setCursor(Qt.CrossCursor if self.picking else Qt.ArrowCursor)
        self.viewer.update()

    def stop_picking(self):
        if self.picking:
            self.picking.pick.setChecked(False)

    def _picked(self, x, y, shift):
        if not (self.picking and self.cg_cur and self.cg_cur.crypto):
            return
        lay = next((c for c in self.cg_cur.crypto if c.name == self.crypto_layer.currentText()), self.cg_cur.crypto[0])
        iw, ih = self.viewer.engine.size
        cx, cy = int(x * self.cg_cur.width / iw), int(y * self.cg_cur.height / ih)
        if not (0 <= cx < self.cg_cur.width and 0 <= cy < self.cg_cur.height):
            return
        name = lay.pick(cx, cy)
        if name:
            self.picking.toggle(name, not shift)

    # ------------------------------------------------------------------ nœuds
    def show_node(self, name):
        p = self.panels.get(name)
        if not p:
            return
        p.expand()
        area = p.parentWidget().parentWidget().parentWidget()
        if isinstance(area, QScrollArea):
            area.ensureWidgetVisible(p, 0, 0)
            area.verticalScrollBar().setValue(p.y())

    # ------------------------------------------------------------------ fichiers
    def open_settings(self):
        p, _ = QFileDialog.getOpenFileName(self, "Ouvrir les réglages", "", "Réglages (*.json)")
        if p:
            self.load_settings(p)

    def load_settings(self, path):
        new = CompSettings.load(path)
        for f in new.__dataclass_fields__:
            setattr(self.s, f, getattr(new, f))
        self.settings_path = path
        self.refresh_knobs()
        self._apply_display()
        self.reload_media()
        self.setWindowTitle(f"Octane Comp — {os.path.basename(path)}")
        self.status.showMessage(f"Réglages chargés : {path}", 4000)

    def save_settings(self, ask=False):
        if ask or not self.settings_path:
            p, _ = QFileDialog.getSaveFileName(self, "Enregistrer les réglages", self.settings_path or "reglages.json",
                                               "Réglages (*.json)")
            if not p:
                return
            self.settings_path = p
        self.s.save(self.settings_path)
        self.setWindowTitle(f"Octane Comp — {os.path.basename(self.settings_path)}")
        self.status.showMessage(f"Réglages enregistrés : {self.settings_path}", 4000)

    def render_dialog(self):
        RenderDialog(self, self.s, len(self.loader)).exec()

    def closeEvent(self, e):
        self.loader.stop = True
        if self.loader.src:
            self.loader.src.close()
        super().closeEvent(e)
