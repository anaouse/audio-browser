import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()

APP_NAME = "AudioBrowser"

MAIN_FILE = ROOT / "main.py"

DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"

SPEC_FILE = ROOT / f"{APP_NAME}.spec"


def run(cmd: list[str]):
    print("\n>>>", " ".join(map(str, cmd)))
    subprocess.check_call(cmd)


def build():
    print("Building executable...")

    ICON_FILE = ROOT / "icon.ico"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--windowed",
        "--onefile",
        "--hidden-import=_cffi_backend",
        "--hidden-import=cffi",
        "--icon",
        str(ICON_FILE),
        str(MAIN_FILE),
    ]

    run(cmd)

    print("\nBuild success!")
    print(f"\nEXE location:\n{DIST_DIR / (APP_NAME + '.exe')}")


if __name__ == "__main__":
    # remove_old_build()
    build()
