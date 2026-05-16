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
from PyQt6.QtGui import QColor, QDrag
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


# LRU Playback Cache
class PlaybackLRUCache:
    """
    LRU cache: file_path -> Playback, capped at max_size.
    Evicted entries are stopped automatically to free resources.
    """

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


# Background loader thread
class PlaybackLoader(QObject):
    """Instantiates Playback on a worker thread, emits finished when done."""

    finished = pyqtSignal(str, object)  # (file_path, Playback | None)

    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def run(self):
        try:
            pb = Playback(self._path)
        except Exception:
            pb = None
        self.finished.emit(self._path, pb)


# Draggable TreeWidget with mouse tracking, hover debounce, and prefetch
class DraggableTreeWidget(QTreeWidget):
    """
    Mouse behaviour:
    - setMouseTracking(True)  ->  mouseMoveEvent fires without button held
    - Hover debounce (HOVER_DEBOUNCE_MS): only prefetch after the cursor has
      rested on a node long enough.  Fast scrolling past nodes is ignored.
    - Long-press (LONG_PRESS_MS) or drag distance >= DRAG_THRESHOLD -> file drag
    - Short click -> itemClicked signal (playback handled by AudioBrowserApp)
    """

    LONG_PRESS_MS = 500
    DRAG_THRESHOLD = 8
    HOVER_DEBOUNCE_MS = 120  # ms the cursor must rest before prefetch fires

    def __init__(self, cache: PlaybackLRUCache, parent=None):
        super().__init__(parent)
        self._cache = cache

        self._loading: set[str] = set()
        self._threads: list[QThread] = []

        # drag state
        self._press_pos: QPoint | None = None
        self._press_item: QTreeWidgetItem | None = None
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.timeout.connect(self._on_long_press)
        self._drag_started = False

        # hover / prefetch state
        self.setMouseTracking(True)
        self._hover_debounce = QTimer(self)
        self._hover_debounce.setSingleShot(True)
        self._hover_debounce.timeout.connect(self._on_hover_settled)
        self._pending_path: str | None = None  # candidate waiting for debounce
        self._prefetched_path: str | None = None  # last path actually prefetched

    # Mouse events
    def mouseMoveEvent(self, event):
        if self._drag_started:
            super().mouseMoveEvent(event)
            return

        # Hover debounce logic
        item = self.itemAt(event.pos())
        hovered_path: str | None = (
            item.data(0, Qt.ItemDataRole.UserRole + 1) if item else None
        )

        if hovered_path:
            if hovered_path != self._pending_path:
                # Cursor moved to a different node: restart debounce timer
                self._pending_path = hovered_path
                self._hover_debounce.start(self.HOVER_DEBOUNCE_MS)
            # else: same node, timer already running -> do nothing
        else:
            # Cursor left all nodes: cancel pending prefetch
            self._hover_debounce.stop()
            self._pending_path = None

        # Drag-distance detection while button is held
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

    # Hover debounce callback
    def _on_hover_settled(self):
        """Fires only after the cursor has rested on a node for HOVER_DEBOUNCE_MS."""
        path = self._pending_path
        if path and path != self._prefetched_path:
            self._prefetched_path = path
            self._prefetch(path)

    # Async prefetch
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

    # Drag
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


# Main window
class AudioBrowserApp(QMainWindow):
    """
    Audio file browser with:
    - Zero-cost startup  (no Playback created at launch)
    - Hover-debounced async prefetch into LRU cache
    - Near-zero click latency (cache hit on click)
    """

    CACHE_SIZE = 20

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio File Browser")
        self.resize(600, 500)

        self._cache = PlaybackLRUCache(max_size=self.CACHE_SIZE)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        self.tree = DraggableTreeWidget(cache=self._cache)
        self.tree.setHeaderHidden(True)
        layout.addWidget(self.tree)

        self._populate_tree()
        self.tree.itemClicked.connect(self._on_item_clicked)

        self._current_playback: Playback | None = None

    def _populate_tree(self):
        audio_dir = Path("./audio")

        root_item = QTreeWidgetItem(self.tree, ["audio/"])
        font = root_item.font(0)
        font.setBold(True)
        root_item.setFont(0, font)
        root_item.setForeground(0, QColor("#D79921"))

        if not audio_dir.exists() or not audio_dir.is_dir():
            error_item = QTreeWidgetItem(root_item, ["No ./audio directory found"])
            font = error_item.font(0)
            font.setItalic(True)
            error_item.setFont(0, font)
            error_item.setForeground(0, QColor("gray"))
            root_item.setExpanded(True)
            return

        root_files = sorted(
            list(audio_dir.glob("*.wav")) + list(audio_dir.glob("*.mp3"))
        )
        for fp in root_files:
            self._add_leaf(root_item, fp)

        for sub_dir in sorted(audio_dir.iterdir()):
            if sub_dir.is_dir():
                sub_node = QTreeWidgetItem(root_item, [f"{sub_dir.name}/"])
                font = sub_node.font(0)
                font.setBold(True)
                sub_node.setFont(0, font)
                sub_node.setForeground(0, QColor("#268BD2"))

                sub_files = sorted(
                    list(sub_dir.glob("*.wav")) + list(sub_dir.glob("*.mp3"))
                )
                for fp in sub_files:
                    self._add_leaf(sub_node, fp)

        root_item.setExpanded(True)

    def _add_leaf(self, parent: QTreeWidgetItem, file_path: Path):
        leaf = QTreeWidgetItem(parent, [f"♪ {file_path.name}"])
        leaf.setData(0, Qt.ItemDataRole.UserRole + 1, str(file_path))

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int):
        path: str | None = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if not path:
            return

        pb = self._cache.get(path)
        if pb is None:
            # Rare fallback: hover debounce hasn't fired yet
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


# Entry point
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AudioBrowserApp()
    window.show()
    sys.exit(app.exec())
