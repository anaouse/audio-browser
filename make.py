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


def remove_old_build():
    print("Cleaning old build files...")

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)

    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    if SPEC_FILE.exists():
        SPEC_FILE.unlink()


def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa

        print("PyInstaller already installed")
    except ImportError:
        print("Installing PyInstaller...")
        run([sys.executable, "-m", "pip", "install", "pyinstaller"])


def build():
    print("Building executable...")

    datas = []

    # audio folder
    if (ROOT / "audio").exists():
        datas.append("audio;audio")

    # styles.py
    if (ROOT / "styles.py").exists():
        datas.append("styles.py;.")

    # sound_path.json
    if (ROOT / "sound_path.json").exists():
        datas.append("sound_path.json;.")

    ICON_FILE = ROOT / "icon.ico"

    data_args = []

    for d in datas:
        data_args += ["--add-data", d]

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
        *data_args,
        str(MAIN_FILE),
    ]

    run(cmd)

    print("\nBuild success!")
    print(f"\nEXE location:\n{DIST_DIR / (APP_NAME + '.exe')}")


if __name__ == "__main__":
    ensure_pyinstaller()
    remove_old_build()
    build()
