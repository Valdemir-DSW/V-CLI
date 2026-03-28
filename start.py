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


def verify_and_start():
    """Verifica ambiente e inicia V CLI."""
    base_dir = Path(__file__).resolve().parent
    cli_path = base_dir / "arduino-cli.exe"
    config_file = base_dir / "cli.yaml"
    main_py = base_dir / "main.py"

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
        _show_error_dialog("V CLI", "Arquivo main.py nao encontrado.")
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

    try:
        main_module = importlib.import_module("main")
        app = main_module.VCliApp()
        app.mainloop()
        return True
    except Exception as e:
        _show_error_dialog("V CLI", f"Erro ao iniciar aplicativo:\n{e}")
        return False


if __name__ == "__main__":
    _hide_console_window()
    success = verify_and_start()
    if not success:
        sys.exit(1)
