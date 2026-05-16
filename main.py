import sys
from collections import OrderedDict
from pathlib import Path

from just_playback import Playback
from PyQt6.QtCore import (
    QMimeData,
    QObject,
    QPoint,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QDrag,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# ─── Palette ────────────────────────────────────────────────────────────────
MOSS_DARK = "#1C2416"  # deepest forest floor
MOSS_MID = "#2D3B22"  # window / panel background
MOSS_LEAF = "#3E5430"  # header / sidebar
FERN_GREEN = "#5A7A3A"  # accent / hover
LICHEN = "#8FAF6A"  # bright accent, highlighted text
CREAM = "#EDE8D5"  # primary text
PARCHMENT = "#C8BFA0"  # secondary text
GOLD_SPORE = "#B89A4A"  # folder colour
BARK = "#6B5B3E"  # subtle separator
DEWDROP = "#A8C8A0"  # currently playing tint


# ─── Leaf / vine decoration widget ──────────────────────────────────────────
class BotanicalHeader(QWidget):
    """Decorative header panel with hand-drawn-style vine SVG paths rendered via QPainter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background gradient
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QColor(MOSS_LEAF))
        grad.setColorAt(1.0, QColor(MOSS_MID))
        p.fillRect(self.rect(), QBrush(grad))

        # Decorative bottom border
        p.setPen(QColor(FERN_GREEN))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

        # Draw vine-like bezier curves
        p.setPen(QColor(FERN_GREEN))
        p.setBrush(Qt.BrushStyle.NoBrush)

        vine = QPainterPath()
        vine.moveTo(0, 48)
        vine.cubicTo(60, 20, 120, 68, 200, 38)
        vine.cubicTo(280, 8, 340, 55, 420, 30)
        vine.cubicTo(500, 5, 560, 50, self.width(), 28)
        p.drawPath(vine)

        # Small leaf dots along vine
        leaf_positions = [40, 120, 200, 300, 400, 500]
        p.setBrush(QColor(LICHEN))
        p.setPen(Qt.PenStyle.NoPen)
        for x in leaf_positions:
            if x < self.width():
                # Tiny ellipse "leaf"
                p.save()
                p.translate(x, 35)
                p.rotate(30)
                p.drawEllipse(-5, -2, 10, 5)
                p.restore()

        p.end()


# ─── LRU Playback Cache (unchanged logic) ───────────────────────────────────
class PlaybackLRUCache:
    def __init__(self, max_size: int = 20):
        self._max_size = max_size
        self._cache: OrderedDict[str, Playback] = OrderedDict()

    def get(self, path: str) -> Playback | None:
        if path in self._cache:
            self._cache.move_to_end(path)
            return self._cache[path]
        return None

    def put(self, path: str, pb: Playback) -> None:
        if path in self._cache:
            self._cache.move_to_end(path)
            return
        self._cache[path] = pb
        self._cache.move_to_end(path)
        if len(self._cache) > self._max_size:
            _, evicted = self._cache.popitem(last=False)
            try:
                evicted.stop()
            except Exception:
                pass

    def __contains__(self, path: str) -> bool:
        return path in self._cache


# ─── Background loader thread (unchanged logic) ─────────────────────────────
class PlaybackLoader(QObject):
    finished = pyqtSignal(str, object)

    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def run(self):
        try:
            pb = Playback(self._path)
        except Exception:
            pb = None
        self.finished.emit(self._path, pb)


# ─── Draggable TreeWidget (unchanged logic, botanical stylesheet) ─────────────
class DraggableTreeWidget(QTreeWidget):
    LONG_PRESS_MS = 500
    DRAG_THRESHOLD = 8
    HOVER_DEBOUNCE_MS = 120

    def __init__(self, cache: PlaybackLRUCache, parent=None):
        super().__init__(parent)
        self._cache = cache
        self._loading: set[str] = set()
        self._threads: list[QThread] = []

        self._press_pos: QPoint | None = None
        self._press_item: QTreeWidgetItem | None = None
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.timeout.connect(self._on_long_press)
        self._drag_started = False

        self.setMouseTracking(True)
        self._hover_debounce = QTimer(self)
        self._hover_debounce.setSingleShot(True)
        self._hover_debounce.timeout.connect(self._on_hover_settled)
        self._pending_path: str | None = None
        self._prefetched_path: str | None = None

        self.setRootIsDecorated(False)  # removes Qt's branch arrow column entirely
        self.setIndentation(16)  # keep visual indent without branch lines

        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(f"""
            QTreeWidget {{
                background-color: {MOSS_MID};
                color: {CREAM};
                border: none;
                outline: none;
                padding: 6px 4px;
                font-size: 13px;
            }}

            QTreeWidget::item {{
                padding: 5px 8px;
                border-radius: 6px;
                margin: 1px 4px;
                color: {PARCHMENT};
            }}

            QTreeWidget::item:hover {{
                background-color: {MOSS_LEAF};
                color: {CREAM};
            }}

            QTreeWidget::item:selected {{
                background-color: {FERN_GREEN};
                color: {CREAM};
            }}

            QTreeWidget::branch,
            QTreeWidget::branch:hover,
            QTreeWidget::branch:selected,
            QTreeWidget::branch:has-siblings,
            QTreeWidget::branch:!has-siblings,
            QTreeWidget::branch:has-siblings:adjoins-item,
            QTreeWidget::branch:has-siblings:!adjoins-item,
            QTreeWidget::branch:!has-siblings:adjoins-item,
            QTreeWidget::branch:!has-siblings:!adjoins-item,
            QTreeWidget::branch:open:has-children,
            QTreeWidget::branch:closed:has-children,
            QTreeWidget::branch:open:has-children:has-siblings,
            QTreeWidget::branch:closed:has-children:has-siblings {{
                background-color: {MOSS_MID};
                border-image: none;
                image: none;
            }}

            QScrollBar:vertical {{
                background: {MOSS_DARK};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {FERN_GREEN};
                border-radius: 4px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar:horizontal {{
                background: {MOSS_DARK};
                height: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:horizontal {{
                background: {FERN_GREEN};
                border-radius: 4px;
            }}
        """)

    # ── Mouse events (logic unchanged) ──────────────────────────────────────
    def mouseMoveEvent(self, event):
        if self._drag_started:
            super().mouseMoveEvent(event)
            return

        item = self.itemAt(event.pos())
        hovered_path: str | None = (
            item.data(0, Qt.ItemDataRole.UserRole + 1) if item else None
        )

        if hovered_path:
            if hovered_path != self._pending_path:
                self._pending_path = hovered_path
                self._hover_debounce.start(self.HOVER_DEBOUNCE_MS)
        else:
            self._hover_debounce.stop()
            self._pending_path = None

        if (
            self._press_item is not None
            and self._press_pos is not None
            and not self._drag_started
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            dist = (event.pos() - self._press_pos).manhattanLength()
            if dist >= self.DRAG_THRESHOLD:
                self._long_press_timer.stop()
                self._start_drag(self._press_item)
                return

        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.pos()
            self._drag_started = False
            item = self.itemAt(event.pos())
            if item and item.data(0, Qt.ItemDataRole.UserRole + 1) is not None:
                self._press_item = item
                self._long_press_timer.start(self.LONG_PRESS_MS)
            else:
                self._press_item = None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._long_press_timer.stop()
        self._press_item = None
        self._press_pos = None
        super().mouseReleaseEvent(event)

    # ── Hover debounce (logic unchanged) ────────────────────────────────────
    def _on_hover_settled(self):
        path = self._pending_path
        if path and path != self._prefetched_path:
            self._prefetched_path = path
            self._prefetch(path)

    def _prefetch(self, path: str):
        if path in self._cache or path in self._loading:
            return
        self._loading.add(path)

        thread = QThread()
        loader = PlaybackLoader(path)
        loader.moveToThread(thread)

        thread.started.connect(loader.run)
        loader.finished.connect(self._on_prefetch_done)
        loader.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        self._threads.append(thread)
        thread.finished.connect(
            lambda t=thread: self._threads.remove(t) if t in self._threads else None
        )
        thread.start()

    def _on_prefetch_done(self, path: str, pb):
        self._loading.discard(path)
        if pb is not None:
            self._cache.put(path, pb)

    # ── Drag (logic unchanged) ───────────────────────────────────────────────
    def _on_long_press(self):
        if self._press_item is not None and not self._drag_started:
            self._start_drag(self._press_item)

    def _start_drag(self, item: QTreeWidgetItem):
        file_path: str | None = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if not file_path:
            return
        self._drag_started = True

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(Path(file_path).resolve()))])

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


# ─── Status bar widget ───────────────────────────────────────────────────────
class StatusLeaf(QFrame):
    """Slim footer showing currently playing file."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {MOSS_DARK};
                border-top: 1px solid {BARK};
            }}
            QLabel {{
                color: {LICHEN};
                font-size: 11px;
                padding: 0 12px;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)

        self._icon = QLabel("🌿")
        self._icon.setFixedWidth(20)
        self._text = QLabel("No track playing")
        layout.addWidget(self._icon)
        layout.addWidget(self._text)
        layout.addStretch()

    def set_track(self, name: str):
        self._text.setText(f"{name}")
        self._icon.setText("🎵")

    def clear_track(self):
        self._text.setText("No track playing")
        self._icon.setText("🌿")


# ─── Main window ─────────────────────────────────────────────────────────────
class AudioBrowserApp(QMainWindow):
    CACHE_SIZE = 20

    def __init__(self):
        super().__init__()
        self.setWindowTitle("🌿  Audio Browser")
        self.resize(620, 580)
        self.setMinimumSize(400, 340)

        # Global window style
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {MOSS_MID};
            }}
            QWidget {{
                background-color: {MOSS_MID};
            }}
        """)

        self._cache = PlaybackLRUCache(max_size=self.CACHE_SIZE)

        # ── Layout ────────────────────────────────────────────────────────
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Decorative header
        header = BotanicalHeader()
        root_layout.addWidget(header)

        # Title label over header (layered via absolute positioning inside header)
        title_label = QLabel("  Audio Browser", header)
        title_label.setGeometry(16, 0, 300, header.height())
        title_label.setStyleSheet(f"""
            QLabel {{
                color: {CREAM};
                font-size: 18px;
                font-weight: bold;
                letter-spacing: 2px;
                background: transparent;
            }}
        """)
        subtitle = QLabel("drag · drop · play", header)
        subtitle.setGeometry(20, 36, 200, 24)
        subtitle.setStyleSheet(f"""
            QLabel {{
                color: {LICHEN};
                font-size: 10px;
                letter-spacing: 3px;
                background: transparent;
            }}
        """)

        # Tree in a thin inset frame
        tree_frame = QFrame()
        tree_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {MOSS_MID};
                border-left: 3px solid {MOSS_LEAF};
                margin: 8px 10px 4px 10px;
                border-radius: 4px;
            }}
        """)
        tree_layout = QVBoxLayout(tree_frame)
        tree_layout.setContentsMargins(0, 0, 0, 0)

        self.tree = DraggableTreeWidget(cache=self._cache)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(20)
        tree_layout.addWidget(self.tree)
        root_layout.addWidget(tree_frame, stretch=1)

        # Status footer
        self._status = StatusLeaf()
        root_layout.addWidget(self._status)

        # ── Populate & connect ─────────────────────────────────────────────
        self._populate_tree()
        self.tree.itemClicked.connect(self._on_item_clicked)
        self._current_playback: Playback | None = None

    # ── Tree population (logic unchanged, botanical colours) ─────────────────
    def _populate_tree(self):
        audio_dir = Path("./audio")

        root_item = QTreeWidgetItem(self.tree, ["🌳  audio/"])
        font = root_item.font(0)
        font.setBold(True)
        font.setPointSize(13)
        root_item.setFont(0, font)
        root_item.setForeground(0, QColor(GOLD_SPORE))

        if not audio_dir.exists() or not audio_dir.is_dir():
            error_item = QTreeWidgetItem(root_item, ["  No ./audio directory found"])
            f = error_item.font(0)
            f.setItalic(True)
            error_item.setFont(0, f)
            error_item.setForeground(0, QColor(BARK))
            root_item.setExpanded(True)
            return

        root_files = sorted(
            list(audio_dir.glob("*.wav")) + list(audio_dir.glob("*.mp3"))
        )
        for fp in root_files:
            self._add_leaf(root_item, fp)

        for sub_dir in sorted(audio_dir.iterdir()):
            if sub_dir.is_dir():
                sub_node = QTreeWidgetItem(root_item, [f"🌿  {sub_dir.name}/"])
                font = sub_node.font(0)
                font.setBold(True)
                font.setPointSize(12)
                sub_node.setFont(0, font)
                sub_node.setForeground(0, QColor(LICHEN))

                sub_files = sorted(
                    list(sub_dir.glob("*.wav")) + list(sub_dir.glob("*.mp3"))
                )
                for fp in sub_files:
                    self._add_leaf(sub_node, fp)

        root_item.setExpanded(True)

    def _add_leaf(self, parent: QTreeWidgetItem, file_path: Path):
        leaf = QTreeWidgetItem(parent, [f"   ♪  {file_path.name}"])
        leaf.setData(0, Qt.ItemDataRole.UserRole + 1, str(file_path))
        leaf.setForeground(0, QColor(PARCHMENT))

    # ── Playback (logic unchanged) ────────────────────────────────────────────
    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int):
        path: str | None = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if not path:
            return

        pb = self._cache.get(path)
        if pb is None:
            try:
                pb = Playback(path)
                self._cache.put(path, pb)
            except Exception:
                return

        if self._current_playback is not None:
            try:
                self._current_playback.stop()
            except Exception:
                pass

        self._current_playback = pb
        pb.play()

        # Update status footer
        self._status.set_track(Path(path).name)


# ─── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Force dark palette so native widgets don't bleed light colours
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(MOSS_MID))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(CREAM))
    palette.setColor(QPalette.ColorRole.Base, QColor(MOSS_DARK))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(MOSS_LEAF))
    palette.setColor(QPalette.ColorRole.Text, QColor(CREAM))
    palette.setColor(QPalette.ColorRole.Button, QColor(MOSS_LEAF))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(CREAM))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(FERN_GREEN))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(CREAM))
    app.setPalette(palette)

    window = AudioBrowserApp()
    window.show()
    sys.exit(app.exec())
