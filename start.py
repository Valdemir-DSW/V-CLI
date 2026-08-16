"""
Script de Inicializacao - V CLI
Verifica tudo antes de abrir a aplicacao
"""

import sys
import ctypes
import subprocess
import importlib
from pathlib import Path
from tkinter import Tk, messagebox


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
        root = Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        pass


def _ask_yes_no(title: str, message: str) -> bool:
    try:
        root = Tk()
        root.withdraw()
        answer = messagebox.askyesno(title, message)
        root.destroy()
        return bool(answer)
    except Exception:
        return False


def _ensure_python_dependencies() -> bool:
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

    if not main_py.exists():
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
        return bool(main_module.run() == 0)
    except Exception as e:
        _show_error_dialog("V CLI", f"Erro ao iniciar aplicativo:\n{e}")
        return False


if __name__ == "__main__":
    _hide_console_window()
    success = verify_and_start()
    if not success:
        sys.exit(1)
