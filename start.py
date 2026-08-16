"""
Script de Inicializacao - V CLI
Verifica tudo antes de abrir a aplicacao
"""

import sys
import ctypes
import subprocess
import importlib
import json
from pathlib import Path

_single_instance_handle = None


def _is_compiled_runtime() -> bool:
    return bool(globals().get("__compiled__")) or bool(getattr(sys, "frozen", False))

def _hide_console_window():
    """Oculta o console no Windows para execucao silenciosa."""
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def _show_error_dialog(title: str, message: str):
    try:
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 0x10)
    except Exception:
        pass


def _ask_yes_no(title: str, message: str) -> bool:
    try:
        if sys.platform == "win32":
            return bool(ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 0x24) == 6)
    except Exception:
        pass
    return False


def _load_startup_settings(base_dir: Path) -> dict:
    appdata_local = Path(__file__).resolve().parent
    try:
        appdata_local = Path((__import__("os").getenv("LOCALAPPDATA")) or (Path.home() / "AppData" / "Local"))
    except Exception:
        pass
    settings_file = appdata_local / "Arduino15" / "V-CLI" / "settings.json"
    if not settings_file.exists():
        return {}
    try:
        return json.loads(settings_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _acquire_single_instance_lock(enabled: bool = True) -> bool:
    global _single_instance_handle
    if sys.platform != "win32" or not enabled:
        return True
    try:
        mutex_name = "Local\\V_CLI_SINGLE_INSTANCE"
        _single_instance_handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        already_exists = ctypes.windll.kernel32.GetLastError() == 183
        if already_exists:
            _show_error_dialog("V CLI", "O V CLI ja esta aberto. Feche a instancia atual antes de abrir outra.")
            return False
        return True
    except Exception:
        return True


def _ensure_python_dependencies() -> bool:
    if _is_compiled_runtime():
        return True
    required = {
        "PyQt5": "PyQt5",
        "serial": "pyserial",
    }
    missing = []
    for module_name, package_name in required.items():
        try:
            importlib.import_module(module_name)
        except Exception:
            missing.append(package_name)

    if not missing:
        return True

    packages_str = ", ".join(missing)
    if not _ask_yes_no(
        "V CLI",
        (
            "Faltam bibliotecas Python para iniciar a interface Qt 5:\n\n"
            f"{packages_str}\n\n"
            "Deseja instalar automaticamente agora?"
        ),
    ):
        _show_error_dialog(
            "V CLI",
            (
                "Instale manualmente com:\n"
                f"{sys.executable} -m pip install {' '.join(missing)}"
            ),
        )
        return False

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *missing],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if result.returncode != 0:
            msg = (result.stderr or result.stdout or "").strip()
            _show_error_dialog("V CLI", f"Falha ao instalar dependências:\n{msg[:700]}")
            return False
        return True
    except Exception as exc:
        _show_error_dialog("V CLI", f"Erro ao instalar dependências:\n{exc}")
        return False


def verify_and_start():
    """Verifica ambiente e inicia V CLI."""
    base_dir = Path(__file__).resolve().parent
    settings = _load_startup_settings(base_dir)
    if not _acquire_single_instance_lock(bool(settings.get("single_instance", True))):
        return False
    cli_path = base_dir / "arduino-cli.exe"
    config_file = base_dir / "cli.yaml"
    main_py = base_dir / "main_qt5.py"

    if not cli_path.exists():
        _show_error_dialog(
            "V CLI",
            (
                f"Arduino CLI nao encontrado em:\n{cli_path}\n\n"
                "Baixe em:\nhttps://github.com/arduino/arduino-cli/releases"
            ),
        )
        return False

    if not _is_compiled_runtime() and not main_py.exists():
        _show_error_dialog("V CLI", "Arquivo main_qt5.py nao encontrado.")
        return False

    try:
        result = subprocess.run(
            [str(cli_path), "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            msg = (result.stderr or result.stdout or "").strip()
            _show_error_dialog("V CLI", f"Arduino CLI retornou erro:\n{msg[:300]}")
            return False
    except Exception as e:
        _show_error_dialog("V CLI", f"Falha ao testar arduino-cli:\n{e}")
        return False

    _ = config_file.exists()

    if not _ensure_python_dependencies():
        return False

    try:
        main_module = importlib.import_module("main_qt5")
        initial_project = sys.argv[1] if len(sys.argv) > 1 else None
        return bool(main_module.run(initial_project=initial_project) == 0)
    except Exception as e:
        _show_error_dialog("V CLI", f"Erro ao iniciar aplicativo:\n{e}")
        return False


if __name__ == "__main__":
    _hide_console_window()
    success = verify_and_start()
    if not success:
        sys.exit(1)
