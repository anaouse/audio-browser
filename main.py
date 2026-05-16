import json
import sys
from collections import OrderedDict
from pathlib import Path

import vlc
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
)
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from styles import (
    BARK,
    FERN_GREEN,
    GOLD_SPORE,
    LICHEN,
    MOSS_LEAF,
    MOSS_MID,
    PARCHMENT,
    context_menu_stylesheet,
    dark_palette,
    make_leaf_icon,
    status_stylesheet,
    subtitle_label_stylesheet,
    title_label_stylesheet,
    tree_frame_stylesheet,
    tree_stylesheet,
    window_stylesheet,
)


def get_config_dir() -> Path:
    """Config directory: script dir when run as .py, exe dir when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _is_vlc_format(path: str) -> bool:
    """Return True if the file should use VLC (opus/m4a) instead of just_playback."""
    return Path(path).suffix.lower() in (".opus", ".m4a")


class VlcPlayback:
    """Thin wrapper around vlc.MediaPlayer matching just_playback's interface."""

    def __init__(self, path: str):
        self._player = vlc.MediaPlayer(path)

    def play(self):
        self._player.play()

    def stop(self):
        self._player.stop()


class Header(QWidget):
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


# LRU Playback Cache
class PlaybackLRUCache:
    def __init__(self, max_size: int = 20):
        self._max_size = max_size
        self._cache: OrderedDict[str, Playback | VlcPlayback] = OrderedDict()

    def get(self, path: str) -> Playback | VlcPlayback | None:
        if path in self._cache:
            self._cache.move_to_end(path)
            return self._cache[path]
        return None

    def put(self, path: str, pb: Playback | VlcPlayback) -> None:
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
    finished = pyqtSignal(str, object)

    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def run(self):
        try:
            if _is_vlc_format(self._path):
                pb = VlcPlayback(self._path)
            else:
                pb = Playback(self._path)
        except Exception:
            pb = None
        self.finished.emit(self._path, pb)


# Draggable TreeWidget
class DraggableTreeWidget(QTreeWidget):
    LONG_PRESS_MS = 500
    DRAG_THRESHOLD = 8
    HOVER_DEBOUNCE_MS = 120

    add_folder_requested = pyqtSignal()

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

        # Right-click context menu on empty space
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(tree_stylesheet())

    # Mouse events
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

    # Hover debounce
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

    # Drag
    def _on_long_press(self):
        if self._press_item is not None and not self._drag_started:
            self._start_drag(self._press_item)

    # Context menu
    def _on_context_menu(self, pos: QPoint):
        """Right-click on empty tree area → 'Add Audio Folder...'"""
        if self.itemAt(pos) is not None:
            return  # clicked on an item, no menu for now

        menu = QMenu(self)
        menu.setStyleSheet(context_menu_stylesheet())
        action = menu.addAction("  📁  Add Audio Folder...")
        action.triggered.connect(self.add_folder_requested.emit)
        menu.exec(self.viewport().mapToGlobal(pos))

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


# Status bar widget
class StatusLeaf(QFrame):
    """Slim footer showing currently playing file."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setStyleSheet(status_stylesheet())
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


# Main window
class AudioBrowserApp(QMainWindow):
    CACHE_SIZE = 20

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio Browser")
        self.setWindowIcon(make_leaf_icon(64))
        self.resize(620, 580)
        self.setMinimumSize(400, 340)

        # Global window style
        self.setStyleSheet(window_stylesheet())

        self._cache = PlaybackLRUCache(max_size=self.CACHE_SIZE)

        # Audio directories — load saved + default ./audio, user can add more
        self._config_file = get_config_dir() / "sound_path.json"
        self._audio_dirs: list[Path] = []

        # 1. Restore previously saved folders
        saved_paths = self._load_saved_paths()
        for saved in saved_paths:
            resolved = saved.resolve()
            if not any(d.resolve() == resolved for d in self._audio_dirs):
                self._audio_dirs.append(resolved)

        # 2. Always include ./audio (relative to cwd) if it exists
        default_dir = Path("./audio").resolve()
        if default_dir.exists() and default_dir.is_dir():
            if not any(d.resolve() == default_dir for d in self._audio_dirs):
                self._audio_dirs.append(default_dir)

        # Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Decorative header
        header = Header()
        root_layout.addWidget(header)

        # Title label over header (layered via absolute positioning inside header)
        title_label = QLabel("  Audio Browser", header)
        title_label.setGeometry(16, 0, 300, header.height())
        title_label.setStyleSheet(title_label_stylesheet())
        subtitle = QLabel("drag · drop · play", header)
        subtitle.setGeometry(20, 36, 200, 24)
        subtitle.setStyleSheet(subtitle_label_stylesheet())

        # Tree in a thin inset frame
        tree_frame = QFrame()
        tree_frame.setStyleSheet(tree_frame_stylesheet())
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

        # Populate & connect
        self._populate_tree()
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.add_folder_requested.connect(self._on_add_folder)
        self._current_playback: Playback | VlcPlayback | None = None

    # Tree population
    def _populate_tree(self):
        """Rebuild the tree from self._audio_dirs."""
        self.tree.clear()

        if not self._audio_dirs:
            root_item = QTreeWidgetItem(self.tree, ["🌳  No audio folders"])
            f = root_item.font(0)
            f.setBold(True)
            f.setPointSize(13)
            root_item.setFont(0, f)
            root_item.setForeground(0, QColor(GOLD_SPORE))

            hint = QTreeWidgetItem(
                root_item, ["  Right-click empty space → Add Audio Folder"]
            )
            hint_f = hint.font(0)
            hint_f.setItalic(True)
            hint.setFont(0, hint_f)
            hint.setForeground(0, QColor(BARK))
            root_item.setExpanded(True)
            return

        for audio_dir in self._audio_dirs:
            root_item = QTreeWidgetItem(self.tree, [f"🌳  {audio_dir.name}/"])
            font = root_item.font(0)
            font.setBold(True)
            font.setPointSize(13)
            root_item.setFont(0, font)
            root_item.setForeground(0, QColor(GOLD_SPORE))

            if not audio_dir.exists() or not audio_dir.is_dir():
                error_item = QTreeWidgetItem(root_item, ["  Directory not found"])
                f = error_item.font(0)
                f.setItalic(True)
                error_item.setFont(0, f)
                error_item.setForeground(0, QColor(BARK))
                root_item.setExpanded(True)
                continue

            root_files = sorted(
                list(audio_dir.glob("*.wav"))
                + list(audio_dir.glob("*.mp3"))
                + list(audio_dir.glob("*.opus"))
                + list(audio_dir.glob("*.m4a"))
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
                        list(sub_dir.glob("*.wav"))
                        + list(sub_dir.glob("*.mp3"))
                        + list(sub_dir.glob("*.opus"))
                        + list(sub_dir.glob("*.m4a"))
                    )
                    for fp in sub_files:
                        self._add_leaf(sub_node, fp)

            root_item.setExpanded(True)

    def _add_leaf(self, parent: QTreeWidgetItem, file_path: Path):
        leaf = QTreeWidgetItem(parent, [f"   ♪  {file_path.name}"])
        leaf.setData(0, Qt.ItemDataRole.UserRole + 1, str(file_path))
        leaf.setForeground(0, QColor(PARCHMENT))

    # Add folder via file dialog
    def _on_add_folder(self):
        """Open a folder picker, then add the chosen directory to the tree."""
        chosen = QFileDialog.getExistingDirectory(
            self, "Select Audio Folder", str(Path.home())
        )
        if not chosen:
            return  # user cancelled

        folder_path = Path(chosen).resolve()

        # Avoid duplicates
        for existing in self._audio_dirs:
            if existing.resolve() == folder_path:
                return  # already in the list

        self._audio_dirs.append(folder_path)
        self._save_audio_dirs()
        self._populate_tree()

    # Persistence
    def _load_saved_paths(self) -> list[Path]:
        """Read absolute folder paths from sound_path.json (one per line)."""
        try:
            if not self._config_file.exists():
                return []
            data = json.loads(self._config_file.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            return [Path(p) for p in data if isinstance(p, str) and Path(p).is_dir()]
        except Exception:
            return []

    def _save_audio_dirs(self) -> None:
        """Persist added folder paths (excluding ./audio) to sound_path.json."""
        cwd_resolved = Path("./audio").resolve()
        # Only save paths added via the dialog (not the default ./audio)
        to_save = [str(d) for d in self._audio_dirs if d.resolve() != cwd_resolved]
        try:
            self._config_file.write_text(
                json.dumps(to_save, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # Playback
    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int):
        path: str | None = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if not path:
            return

        pb = self._cache.get(path)
        if pb is None:
            try:
                if _is_vlc_format(path):
                    pb = VlcPlayback(path)
                else:
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


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Force dark palette so native widgets don't bleed light colours
    app.setPalette(dark_palette())
    app.setWindowIcon(make_leaf_icon(64))  # taskbar + alt-tab thumbnail

    window = AudioBrowserApp()
    window.show()
    sys.exit(app.exec())
