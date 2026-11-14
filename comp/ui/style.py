"""Thème sombre inspiré des outils de compositing pro (gris neutres, accent orange discret)."""

BG = "#2b2b2b"
PANEL = "#323232"
FIELD = "#1f1f1f"
LINE = "#202020"
TEXT = "#c9c9c9"
DIM = "#8a8a8a"
ACCENT = "#f0962e"
VIEWER_BG = "#1a1a1a"

QSS = f"""
* {{ font-family: "Helvetica Neue"; font-size: 11px; color: {TEXT}; }}
QMainWindow, QWidget {{ background: {BG}; }}
QMenuBar {{ background: {PANEL}; border-bottom: 1px solid {LINE}; padding: 1px 4px; }}
QMenuBar::item {{ padding: 3px 9px; background: transparent; }}
QMenuBar::item:selected {{ background: #454545; }}
QMenu {{ background: {PANEL}; border: 1px solid #1a1a1a; padding: 3px 0; }}
QMenu::item {{ padding: 4px 22px 4px 18px; }}
QMenu::item:selected {{ background: {ACCENT}; color: #1b1b1b; }}
QMenu::separator {{ height: 1px; background: #444; margin: 3px 6px; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {LINE}; color: {DIM}; }}
QSplitter::handle {{ background: {LINE}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical {{ height: 3px; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {LINE}; background: {PANEL}; }}
QTabBar {{ background: {BG}; }}
QTabBar::tab {{ background: #262626; color: {DIM}; padding: 4px 14px; border: 1px solid {LINE};
               border-bottom: none; margin-right: 1px; }}
QTabBar::tab:selected {{ background: {PANEL}; color: #e6e6e6; }}

QLineEdit, QSpinBox, QDoubleSpinBox {{ background: {FIELD}; border: 1px solid #141414; border-radius: 2px;
    padding: 2px 4px; selection-background-color: {ACCENT}; selection-color: #111; }}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QComboBox {{ background: #3d3d3d; border: 1px solid #1a1a1a; border-radius: 2px; padding: 2px 18px 2px 6px; }}
QComboBox:hover {{ background: #464646; }}
QComboBox::drop-down {{ border: none; width: 14px; }}
QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {DIM}; width: 0; height: 0; margin-right: 5px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid #111; selection-background-color: {ACCENT};
    selection-color: #111; outline: none; }}

QPushButton, QToolButton {{ background: #424242; border: 1px solid #1a1a1a; border-radius: 2px; padding: 3px 9px; }}
QPushButton:hover, QToolButton:hover {{ background: #4d4d4d; }}
QPushButton:pressed, QToolButton:pressed {{ background: #2e2e2e; }}
QToolButton:checked, QPushButton:checked {{ background: #5a4730; border-color: {ACCENT}; color: #ffd7a8; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#NodeToggle {{ background: transparent; border: none; padding: 0; }}
QToolButton[seg="true"] {{ border-radius: 0; padding: 2px 8px; }}

QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 12px; height: 12px; background: {FIELD}; border: 1px solid #111; border-radius: 2px; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: #8a5a20; }}

QSlider::groove:horizontal {{ height: 2px; background: #1a1a1a; margin: 0 2px; }}
QSlider::sub-page:horizontal {{ background: #5e5e5e; }}
QSlider::handle:horizontal {{ background: #b9b9b9; width: 6px; height: 12px; margin: -5px 0; border-radius: 1px; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}

QScrollArea {{ border: none; background: {PANEL}; }}
QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #4a4a4a; min-height: 24px; border-radius: 3px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: {BG}; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #4a4a4a; min-width: 24px; border-radius: 3px; margin: 2px; }}

QListWidget {{ background: {FIELD}; border: 1px solid #141414; outline: none; }}
QListWidget::item {{ padding: 2px 4px; }}
QListWidget::item:selected {{ background: #5a4730; color: #ffd7a8; }}

QToolTip {{ background: #1c1c1c; color: {TEXT}; border: 1px solid #444; padding: 4px; }}

#ViewerBar {{ background: {PANEL}; border-bottom: 1px solid {LINE}; }}
#Timeline {{ background: {PANEL}; border-top: 1px solid {LINE}; }}
#InfoBar {{ background: #242424; color: {DIM}; font-family: Menlo, monospace; font-size: 10px; }}
#NodeHeader {{ background: #3a3a3a; border: 1px solid #222; border-radius: 2px; }}
#NodeTitle {{ font-weight: 600; color: #e8e8e8; }}
#NodeClass {{ color: {DIM}; }}
#KnobLabel {{ color: #b0b0b0; }}
#SectionLabel {{ color: {DIM}; font-size: 10px; letter-spacing: 1px; }}
#Chip {{ background: #4a3d2c; border: 1px solid #6d5636; border-radius: 8px; padding: 1px 8px; color: #ffd7a8; }}
"""
