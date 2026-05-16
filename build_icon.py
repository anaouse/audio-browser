import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from styles import make_leaf_icon

if __name__ == "__main__":
    app = QApplication(sys.argv)

    icon = make_leaf_icon(256)

    output = Path("icon.ico")

    sizes = [16, 24, 32, 48, 64, 128, 256]

    pixmaps = [icon.pixmap(s, s) for s in sizes]

    pixmaps[0].save(str(output), "ICO")

    print(f"saved: {output.resolve()}")
