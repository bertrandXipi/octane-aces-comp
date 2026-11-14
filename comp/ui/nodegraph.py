"""Node Graph : représentation du script de compositing (nœuds déplaçables, sélection = panneau)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsScene, QGraphicsView

NODE_W, NODE_H = 116, 26

# nom, classe, couleur, position, sous-titre
NODES = [
    ("Read1", "Read", "#9a9a9a", (0, 0), "F-Log2 rush"),
    ("Read2", "Read", "#9a9a9a", (300, 0), "Octane EXR"),
    ("Cryptomatte1", "Cryptomatte", "#7f9a45", (470, 55), ""),
    ("Recompose1", "Recompose", "#6c9a6a", (300, 100), "diffuse · reflets"),
    ("ShadowCatcher1", "ShadowCatcher", "#8a7d9e", (0, 100), "densité ombres"),
    ("Merge1", "Merge", "#4f78a8", (150, 175), "over"),
    ("OCIODisplay1", "OCIODisplay", "#6c9a6a", (150, 235), ""),
    ("Viewer1", "Viewer", "#b8945a", (150, 290), ""),
]
EDGES = [  # source, destination, étiquette d'entrée
    ("Read1", "ShadowCatcher1", ""), ("Read2", "Recompose1", ""), ("Read2", "Cryptomatte1", ""),
    ("Cryptomatte1", "Recompose1", "mask"), ("Cryptomatte1", "ShadowCatcher1", "mask"),
    ("Cryptomatte1", "Merge1", "mask"),
    ("Recompose1", "Merge1", "A"), ("ShadowCatcher1", "Merge1", "B"),
    ("Merge1", "OCIODisplay1", ""), ("OCIODisplay1", "Viewer1", ""),
]


class NodeItem(QGraphicsObject):
    moved = Signal()

    def __init__(self, name, klass, color, subtitle):
        super().__init__()
        self.name, self.klass, self.color, self.subtitle = name, klass, QColor(color), subtitle
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setCursor(Qt.PointingHandCursor)
        self.disabled = False

    def boundingRect(self):
        return QRectF(-3, -3, NODE_W + 6, NODE_H + 20)

    def paint(self, p: QPainter, opt, widget=None):
        r = QRectF(0, 0, NODE_W, NODE_H)
        c = self.color.darker(160) if self.disabled else self.color
        grad_top, grad_bot = c.lighter(112), c.darker(115)
        path = QPainterPath()
        path.addRoundedRect(r, 3, 3)
        from PySide6.QtGui import QLinearGradient
        g = QLinearGradient(0, 0, 0, NODE_H)
        g.setColorAt(0, grad_top)
        g.setColorAt(1, grad_bot)
        p.fillPath(path, QBrush(g))
        sel = self.isSelected()
        p.setPen(QPen(QColor("#f0962e") if sel else QColor("#151515"), 2 if sel else 1))
        p.drawPath(path)
        p.setPen(QColor("#111111"))
        f = QFont("Helvetica Neue", 10)
        f.setBold(True)
        p.setFont(f)
        p.drawText(r, Qt.AlignCenter, self.name)
        if self.subtitle:
            p.setPen(QColor("#8f8f8f"))
            p.setFont(QFont("Helvetica Neue", 8))
            p.drawText(QRectF(-20, NODE_H + 1, NODE_W + 40, 14), Qt.AlignHCenter | Qt.AlignTop, self.subtitle)

    def top(self):
        return self.pos() + QPointF(NODE_W / 2, 0)

    def bottom(self):
        return self.pos() + QPointF(NODE_W / 2, NODE_H)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.moved.emit()
        return super().itemChange(change, value)


class EdgeItem(QGraphicsPathItem):
    def __init__(self, src: NodeItem, dst: NodeItem, label: str):
        super().__init__()
        self.src, self.dst, self.label = src, dst, label
        self.setZValue(-1)
        dashed = label == "mask"
        pen = QPen(QColor("#7d7d7d") if not dashed else QColor("#6e7f4a"), 1.2)
        if dashed:
            pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        src.moved.connect(self.update_path)
        dst.moved.connect(self.update_path)
        self.update_path()

    def update_path(self):
        a = self.src.bottom()
        b = self.dst.top()
        if self.label == "mask":
            b = self.dst.pos() + QPointF(NODE_W, NODE_H / 2)
        elif self.label == "A":
            b = b + QPointF(18, 0)
        elif self.label == "B":
            b = b - QPointF(18, 0)
        path = QPainterPath(a)
        path.lineTo(b)
        # flèche vers l'entrée
        v = b - a
        ln = max((v.x() ** 2 + v.y() ** 2) ** 0.5, 1e-3)
        u = QPointF(v.x() / ln, v.y() / ln)
        n = QPointF(-u.y(), u.x())
        tip = b
        poly = QPolygonF([tip, tip - u * 8 + n * 3.5, tip - u * 8 - n * 3.5, tip])
        path.addPolygon(poly)
        self.setPath(path)
        self._mid = a + v * 0.82

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        super().paint(p, opt, widget)
        p.setBrush(self.pen().color())
        if self.label in ("A", "B", "mask"):
            p.setPen(QColor("#a0a0a0"))
            p.setFont(QFont("Helvetica Neue", 8))
            p.drawText(self._mid + QPointF(4, 0), self.label)


class NodeGraph(QGraphicsView):
    selected = Signal(str)

    def __init__(self):
        super().__init__()
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setBackgroundBrush(QColor("#2f2f2f"))
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.nodes: dict[str, NodeItem] = {}
        for name, klass, color, (x, y), sub in NODES:
            it = NodeItem(name, klass, color, sub)
            it.setPos(x, y)
            self.scene_.addItem(it)
            self.nodes[name] = it
        for s, d, lbl in EDGES:
            self.scene_.addItem(EdgeItem(self.nodes[s], self.nodes[d], lbl))
        self.scene_.setSceneRect(-3000, -3000, 6000, 6000)
        self.scene_.selectionChanged.connect(self._sel)
        self._pan = None

    def drawBackground(self, p: QPainter, rect: QRectF):
        super().drawBackground(p, rect)
        p.setPen(QPen(QColor("#353535"), 1))
        step = 40
        x = int(rect.left()) - int(rect.left()) % step
        while x < rect.right():
            p.drawLine(x, int(rect.top()), x, int(rect.bottom()))
            x += step
        y = int(rect.top()) - int(rect.top()) % step
        while y < rect.bottom():
            p.drawLine(int(rect.left()), y, int(rect.right()), y)
            y += step

    def _sel(self):
        items = self.scene_.selectedItems()
        if len(items) == 1 and isinstance(items[0], NodeItem):
            self.selected.emit(items[0].name)

    def select(self, name: str):
        self.scene_.blockSignals(True)
        self.scene_.clearSelection()
        if name in self.nodes:
            self.nodes[name].setSelected(True)
        self.scene_.blockSignals(False)
        self.scene_.update()

    def frame_all(self):
        r = self.scene_.itemsBoundingRect().adjusted(-40, -30, 40, 30)
        self.fitInView(r, Qt.KeepAspectRatio)
        k = self.transform().m11()
        if k > 1.3:
            k = 1.3
            self.resetTransform()
            self.scale(k, k)
            self.centerOn(r.center())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.frame_all()

    def wheelEvent(self, e):
        f = 1.0015 ** e.angleDelta().y()
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.scale(f, f)

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and e.modifiers() & Qt.AltModifier):
            self._pan = e.position()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._pan = None
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_F:
            self.frame_all()
        else:
            super().keyPressEvent(e)
