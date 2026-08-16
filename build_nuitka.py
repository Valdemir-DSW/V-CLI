import shutil
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
BUILD_DIR = BASE_DIR / "build_nuitka"
GUI_BUILD_DIR = BUILD_DIR / "gui"
CLI_BUILD_DIR = BUILD_DIR / "cli"
RELEASE_DIR = BUILD_DIR / "release"
APP_DIR = RELEASE_DIR / "V-CLI"
APP_EXE_NAME = "V-CLI.exe"
CLI_EXE_NAME = "vcli_cmd.exe"
ICON_COPY_PATH = BUILD_DIR / "vcli_icon.ico"
APP_VERSION = "1.0.0"
APP_ICON_NAME = "vcli_icon.ico"


def run(cmd: list[str]) -> None:
    print("[RUN]", " ".join(str(part) for part in cmd))
    subprocess.run(cmd, cwd=BASE_DIR, check=True)


def clean() -> None:
    for path in [GUI_BUILD_DIR, CLI_BUILD_DIR, RELEASE_DIR]:
        if path.exists():
            print(f"[CLEAN] {path}")
            shutil.rmtree(path)
    if ICON_COPY_PATH.exists():
        ICON_COPY_PATH.unlink()


def ensure_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")


def copy_required_files() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    for name in [
        "arduino-cli.exe",
        "cli.yaml",
        "LICENSE.txt",
        "README.md",
        "project_padrao.png",
    ]:
        src = BASE_DIR / name
        ensure_file(src)
        shutil.copy2(src, APP_DIR / src.name)

    source_icon = BASE_DIR / ".ico"
    if source_icon.exists():
        shutil.copy2(source_icon, APP_DIR / APP_ICON_NAME)

    for folder_name in ["docs", "locales"]:
        src_dir = BASE_DIR / folder_name
        if src_dir.exists():
            target_dir = APP_DIR / folder_name
            shutil.copytree(src_dir, target_dir, dirs_exist_ok=True)

    projects_dir = APP_DIR / "projects"
    projects_dir.mkdir(exist_ok=True)


def build_gui() -> Path:
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyqt5",
        "--windows-console-mode=disable",
        "--output-dir=" + str(GUI_BUILD_DIR),
        "--output-filename=" + APP_EXE_NAME,
        "--company-name=V CLI",
        "--product-name=V CLI",
        "--file-version=" + APP_VERSION,
        "--product-version=" + APP_VERSION,
        "--file-description=V CLI",
        "--copyright=V CLI",
        "--include-module=main_qt5",
        "--include-package=lupa",
        "--include-data-dir=" + str(BASE_DIR / "locales") + "=locales",
        "--include-data-dir=" + str(BASE_DIR / "docs") + "=docs",
        "--include-data-files=" + str(BASE_DIR / ".ico") + "=.ico",
        "--include-data-files=" + str(BASE_DIR / "arduino-cli.exe") + "=arduino-cli.exe",
        "--include-data-files=" + str(BASE_DIR / "cli.yaml") + "=cli.yaml",
        "--include-data-files=" + str(BASE_DIR / "project_padrao.png") + "=project_padrao.png",
        "--include-data-files=" + str(BASE_DIR / "LICENSE.txt") + "=LICENSE.txt",
        "--include-data-files=" + str(BASE_DIR / "README.md") + "=README.md",
        str(BASE_DIR / "start.py"),
    ]
    icon_path = BASE_DIR / ".ico"
    if icon_path.exists():
        ICON_COPY_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icon_path, ICON_COPY_PATH)
        cmd.append("--windows-icon-from-ico=" + str(ICON_COPY_PATH))
    run(cmd)
    dist_dir = GUI_BUILD_DIR / "start.dist"
    ensure_file(dist_dir / APP_EXE_NAME)
    return dist_dir


def build_cli() -> Path:
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--onefile",
        "--assume-yes-for-downloads",
        "--output-dir=" + str(CLI_BUILD_DIR),
        "--output-filename=" + CLI_EXE_NAME,
        "--company-name=V CLI",
        "--product-name=V CLI Command Runner",
        "--file-version=" + APP_VERSION,
        "--product-version=" + APP_VERSION,
        "--file-description=V CLI command runner",
        str(BASE_DIR / "vcli_cmd.py"),
    ]
    run(cmd)
    exe_path = CLI_BUILD_DIR / CLI_EXE_NAME
    ensure_file(exe_path)
    return exe_path


def create_launcher() -> None:
    launcher = APP_DIR / "vcli.cmd"
    launcher.write_text('@echo off\r\n"%~dp0vcli_cmd.exe" %*\r\n', encoding="utf-8")


def assemble_release(gui_dist_dir: Path, cli_exe_path: Path) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(gui_dist_dir, APP_DIR, dirs_exist_ok=True)
    shutil.copy2(cli_exe_path, APP_DIR / CLI_EXE_NAME)
    copy_required_files()
    create_launcher()


def main() -> int:
    clean()
    gui_dist_dir = build_gui()
    cli_exe_path = build_cli()
    assemble_release(gui_dist_dir, cli_exe_path)
    print(f"[OK] Build pronta em: {APP_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
