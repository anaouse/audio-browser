import sys
from pathlib import Path

from just_playback import Playback
from PyQt6.QtCore import Qt, QTimer, QPoint, QMimeData, QUrl
from PyQt6.QtGui import QColor, QFont, QDrag
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


# ── 长按拖拽 TreeWidget ──────────────────────────────────────────────────────
class DraggableTreeWidget(QTreeWidget):
    """
    重写鼠标事件：
    - 长按(500ms)或拖动距离超过阈值 → 启动文件拖放
    - 短按/点击 → 保留原有的 itemClicked 信号（播放音频）
    """
    LONG_PRESS_MS = 500
    DRAG_THRESHOLD = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_pos: QPoint | None = None
        self._press_item: QTreeWidgetItem | None = None
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.timeout.connect(self._on_long_press)
        self._drag_started = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.pos()
            self._drag_started = False
            item = self.itemAt(event.pos())
            if item and item.data(0, Qt.ItemDataRole.UserRole) is not None:
                self._press_item = item
                self._long_press_timer.start(self.LONG_PRESS_MS)
            else:
                self._press_item = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
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

    def mouseReleaseEvent(self, event):
        self._long_press_timer.stop()
        self._press_item = None
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _on_long_press(self):
        """长按计时器触发 → 启动拖拽"""
        if self._press_item is not None and not self._drag_started:
            self._start_drag(self._press_item)

    def _start_drag(self, item: QTreeWidgetItem):
        file_path: str = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if not file_path:
            return
        self._drag_started = True

        mime = QMimeData()
        file_url = QUrl.fromLocalFile(str(Path(file_path).resolve()))
        mime.setUrls([file_url])

        drag = QDrag(self)
        drag.setMimeData(mime)

        drag.exec(Qt.DropAction.CopyAction)


class AudioBrowserApp(QMainWindow):
    """A PyQt6 GUI app to browse and play wav files."""

    def __init__(self):
        super().__init__()

        # 相当于 Textual 中的 TITLE
        self.setWindowTitle("Audio File Browser")
        self.resize(600, 500)

        # 设置中心窗口和布局
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # 相当于 Textual 的 Tree
        self.tree = DraggableTreeWidget()
        self.tree.setHeaderHidden(True)  # 隐藏表头
        layout.addWidget(self.tree)

        # 初始化树形数据 (相当于 Textual 的 on_mount)
        self.populate_tree()

        # 信号绑定：itemActivated 相当于 NodeSelected
        # 桌面端通常习惯“双击”或按“回车”来执行动作（而不是单击）
        # 如果你希望单击就能播放，可以把 itemActivated 替换为 itemClicked
        self.tree.itemClicked.connect(self.on_tree_node_selected)

    def populate_tree(self):
        audio_dir = Path("./audio")

        # 创建根节点: audio/
        root_item = QTreeWidgetItem(self.tree, ["audio/"])
        font = root_item.font(0)
        font.setBold(True)
        root_item.setFont(0, font)
        root_item.setForeground(0, QColor("#D79921"))  # [bold yellow] 效果

        # 检查目录是否存在
        if not audio_dir.exists() or not audio_dir.is_dir():
            error_item = QTreeWidgetItem(root_item, ["No ./audio directory found"])
            font = error_item.font(0)
            font.setItalic(True)
            error_item.setFont(0, font)
            error_item.setForeground(0, QColor("gray"))  # [dim italic] 效果
            root_item.setExpanded(True)
            return

        # 1. 预加载根目录下的 .wav 文件
        for file_path in sorted(audio_dir.glob("*.wav")):
            self.add_wav_leaf(root_item, file_path)

        # 2. 加载子目录及其 .wav 文件
        for sub_dir in sorted(audio_dir.iterdir()):
            if sub_dir.is_dir():
                sub_node = QTreeWidgetItem(root_item, [f"{sub_dir.name}/"])
                font = sub_node.font(0)
                font.setBold(True)
                sub_node.setFont(0, font)
                sub_node.setForeground(0, QColor("#268BD2"))  # [bold blue] 效果

                for wav_file in sorted(sub_dir.glob("*.wav")):
                    self.add_wav_leaf(sub_node, wav_file)

        # 展开根节点 (相当于取消 collapse)
        root_item.setExpanded(True)

    def add_wav_leaf(self, parent_node: QTreeWidgetItem, file_path: Path):
        """辅助方法：将 Playback 对象绑定到叶子节点并显示"""
        pb = Playback(str(file_path))
        leaf = QTreeWidgetItem(parent_node, [f"♪ {file_path.name}"])

        # 注意：桌面 GUI 有浅色/深色主题，写死 #e4e4e4(近白色) 会导致浅色模式下看不清文字。
        # 因此这里省略了颜色指定，让它跟随系统默认文本颜色即可。

        # 相当于 Textual 的 data=pb
        # 将 Python 对象保存在 UserRole，文件路径保存在 UserRole+1（供拖拽使用）
        leaf.setData(0, Qt.ItemDataRole.UserRole, pb)
        leaf.setData(0, Qt.ItemDataRole.UserRole + 1, str(file_path))

    def on_tree_node_selected(self, item: QTreeWidgetItem, column: int):
        """Play the WAV file when a leaf node is activated (Double-click/Enter)."""
        # 从 UserRole 获取保存的 Playback 对象
        node_data = item.data(0, Qt.ItemDataRole.UserRole)

        if isinstance(node_data, Playback):
            node_data.play()


if __name__ == "__main__":
    # PyQt 应用必须有 QApplication 实例
    app = QApplication(sys.argv)

    window = AudioBrowserApp()
    window.show()

    # 退出绑定
    sys.exit(app.exec())
