"""Lancement : python -m comp [reglages.json] [--plate RUSH] [--cg EXR] [--screenshot out.png]"""
from __future__ import annotations

import argparse
import os
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="octacomp")
    ap.add_argument("settings", nargs="?", help="réglages JSON à ouvrir")
    ap.add_argument("--plate", help="rush F-Log2 (mov, séquence, image)")
    ap.add_argument("--cg", help="séquence EXR Octane (dossier, motif ####, fichier)")
    ap.add_argument("--frame", type=int, default=1)
    ap.add_argument("--screenshot", help="capture la fenêtre puis quitte (contrôle)")
    ap.add_argument("--node", default="Recompose1", help="nœud sélectionné au démarrage")
    a = ap.parse_args(argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    fmt = QSurfaceFormat()
    fmt.setVersion(4, 1)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Octane Comp")
    import locale
    locale.setlocale(locale.LC_NUMERIC, "C")  # OCIO génère du GLSL avec des points décimaux
    from PySide6.QtCore import QLocale
    QLocale.setDefault(QLocale.c())  # points décimaux dans les champs, comme les outils de compositing
    from .ui.style import QSS
    app.setStyleSheet(QSS)

    from .settings import CompSettings
    from .ui.main_window import MainWindow
    s = CompSettings.load(a.settings) if a.settings else CompSettings()
    if a.plate:
        s.plate_path = os.path.abspath(a.plate)
    if a.cg:
        s.cg_path = os.path.abspath(a.cg)
    w = MainWindow(s, a.settings)
    w.refresh_knobs()
    w.show()
    w.graph.select(a.node)
    w.show_node(a.node)

    if a.screenshot:
        def shot():
            w.goto(a.frame - 1)
            w.viewer.update()
            QTimer.singleShot(600, lambda: (w.grab().save(a.screenshot), app.quit()))
        QTimer.singleShot(1200, shot)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
