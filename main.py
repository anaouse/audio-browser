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

# ---------------------------------------------------------------------------
# LRU Playback Cache
# ---------------------------------------------------------------------------


class PlaybackLRUCache:
    """
    容量上限为 max_size 的 LRU 缓存，存储 file_path -> Playback。
    淘汰时自动 stop() 旧对象，释放资源。
    """

    def __init__(self, max_size: int = 20):
        self._max_size = max_size
        self._cache: OrderedDict[str, Playback] = OrderedDict()

    def get(self, path: str) -> Playback | None:
        if path in self._cache:
            self._cache.move_to_end(path)  # 标记为最近使用
            return self._cache[path]
        return None

    def put(self, path: str, pb: Playback) -> None:
        if path in self._cache:
            self._cache.move_to_end(path)
            return
        self._cache[path] = pb
        self._cache.move_to_end(path)
        if len(self._cache) > self._max_size:
            _, evicted = self._cache.popitem(last=False)  # 弹出最久未使用
            try:
                evicted.stop()
            except Exception:
                pass

    def __contains__(self, path: str) -> bool:
        return path in self._cache


# ---------------------------------------------------------------------------
# Background loader thread
# ---------------------------------------------------------------------------


class PlaybackLoader(QObject):
    """
    在独立线程中实例化 Playback，完成后通过信号通知主线程。
    """

    finished = pyqtSignal(str, object)  # (file_path, Playback)

    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def run(self):
        try:
            pb = Playback(self._path)
        except Exception:
            pb = None
        self.finished.emit(self._path, pb)


# ---------------------------------------------------------------------------
# Draggable TreeWidget with mouse tracking & hover prefetch
# ---------------------------------------------------------------------------


class DraggableTreeWidget(QTreeWidget):
    """
    重写鼠标事件：
    - 开启 setMouseTracking → mouseMoveEvent 持续触发
    - Hover 到音频叶节点 → 后台异步预加载 Playback（写入 LRU 缓存）
    - 长按 (500 ms) 或拖动距离超过阈值 → 启动文件拖放
    - 短按 / 点击 → 保留原有 itemClicked 信号（播放音频）
    """

    LONG_PRESS_MS = 500
    DRAG_THRESHOLD = 8

    def __init__(self, cache: PlaybackLRUCache, parent=None):
        super().__init__(parent)
        self._cache = cache

        # 记录正在后台加载的路径，避免重复提交
        self._loading: set[str] = set()
        # 持有线程引用，防止被 GC
        self._threads: list[QThread] = []

        # 拖拽状态
        self._press_pos: QPoint | None = None
        self._press_item: QTreeWidgetItem | None = None
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.timeout.connect(self._on_long_press)
        self._drag_started = False

        # 开启鼠标追踪（不需要按下按钮也能触发 mouseMoveEvent）
        self.setMouseTracking(True)

        # 当前 hover 的叶节点路径（用于去重）
        self._last_hover_path: str | None = None

    # ------------------------------------------------------------------
    # Hover 预加载
    # ------------------------------------------------------------------

    def mouseMoveEvent(self, event):
        # 拖拽进行中，跳过 hover 逻辑
        if self._drag_started:
            super().mouseMoveEvent(event)
            return

        item = self.itemAt(event.pos())
        if item is not None:
            path: str | None = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if path and path != self._last_hover_path:
                self._last_hover_path = path
                self._prefetch(path)

        # 拖拽距离检测（按下状态）
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

    def _prefetch(self, path: str):
        """如果缓存中没有，则异步加载。"""
        if path in self._cache or path in self._loading:
            return
        self._loading.add(path)

        thread = QThread()
        loader = PlaybackLoader(path)
        loader.moveToThread(thread)

        # 线程启动后执行 run
        thread.started.connect(loader.run)
        # 加载完成后回调
        loader.finished.connect(self._on_prefetch_done)
        # 回调后清理线程
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

    # ------------------------------------------------------------------
    # 鼠标按下 / 释放
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 拖拽
    # ------------------------------------------------------------------

    def _on_long_press(self):
        if self._press_item is not None and not self._drag_started:
            self._start_drag(self._press_item)

    def _start_drag(self, item: QTreeWidgetItem):
        file_path: str | None = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if not file_path:
            return
        self._drag_started = True

        mime = QMimeData()
        file_url = QUrl.fromLocalFile(str(Path(file_path).resolve()))
        mime.setUrls([file_url])

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class AudioBrowserApp(QMainWindow):
    """A PyQt6 GUI app to browse and play wav/mp3 files.

    优化要点：
    - 启动时只遍历目录，不实例化任何 Playback（毫秒级启动）
    - 鼠标 Hover 到叶节点时，后台线程异步预加载 Playback
    - 点击时从 LRU 缓存中直接取出，实现"零延迟"播放
    - LRU 缓存上限 20 个，超出自动 stop() 并淘汰最久未用的
    """

    CACHE_SIZE = 20

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio File Browser")
        self.resize(600, 500)

        # 共享 LRU 缓存
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

    # ------------------------------------------------------------------
    # Tree population（纯文件系统遍历，不创建 Playback）
    # ------------------------------------------------------------------

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

        # 根目录下的音频文件
        root_files = sorted(
            list(audio_dir.glob("*.wav")) + list(audio_dir.glob("*.mp3"))
        )
        for fp in root_files:
            self._add_leaf(root_item, fp)

        # 子目录
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
        """只存文件路径字符串，不创建 Playback。"""
        leaf = QTreeWidgetItem(parent, [f"♪ {file_path.name}"])
        # UserRole+1 存路径（与原版保持一致，DraggableTreeWidget 也读此字段）
        leaf.setData(0, Qt.ItemDataRole.UserRole + 1, str(file_path))

    # ------------------------------------------------------------------
    # 点击播放（从缓存取，缓存未命中则同步加载——正常情况下 hover 已预热）
    # ------------------------------------------------------------------

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int):
        path: str | None = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if not path:
            return

        # 先尝试从缓存取
        pb = self._cache.get(path)
        if pb is None:
            # 极少数情况：hover 还没来得及预热，同步加载
            try:
                pb = Playback(path)
                self._cache.put(path, pb)
            except Exception:
                return

        # 停止当前播放
        if self._current_playback is not None:
            try:
                self._current_playback.stop()
            except Exception:
                pass

        self._current_playback = pb
        pb.play()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AudioBrowserApp()
    window.show()
    sys.exit(app.exec())
