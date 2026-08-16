import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from cli_backend import CLIBackend


def log(text: str):
    print(text)


def load_app_settings(base_dir: Path) -> dict:
    appdata_local = Path(
        os.getenv("LOCALAPPDATA")
        or (Path.home() / "AppData" / "Local")
    )
    settings_file = appdata_local / "Arduino15" / "V-CLI" / "settings.json"
    try:
        if settings_file.exists():
            return json.loads(settings_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {
        "editor_command": "code",
    }


def resolve_project_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path)


def main():
    parser = argparse.ArgumentParser(description="V CLI command runner")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ["open", "vscode", "compile", "export", "upload", "info"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("project")
        if name == "upload":
            cmd.add_argument("--port", default="")

    args = parser.parse_args()
    base_dir = Path(__file__).resolve().parent
    backend = CLIBackend(str(base_dir), log)
    settings = load_app_settings(base_dir)
    project_path = resolve_project_path(args.project)

    if not project_path.exists():
        print(f"Projeto não encontrado: {project_path}", file=sys.stderr)
        return 2

    config = backend.load_project(str(project_path))
    if not config:
        print("Falha ao carregar project.fuzil", file=sys.stderr)
        return 3

    fqbn = config.get("fqbn", "arduino:avr:uno")

    if args.command == "open":
        subprocess.Popen(["explorer", str(project_path)])
        return 0

    if args.command == "vscode":
        editor = str(settings.get("editor_command", "code") or "code").strip()
        return 0 if backend.open_code_editor(str(project_path), editor=editor) else 4

    if args.command == "compile":
        out, ok, err = backend.compile_action(str(project_path), fqbn, config=config)
        print(out or err)
        return 0 if ok else 5

    if args.command == "export":
        out, ok, err = backend.export_binary_action(str(project_path), fqbn, config=config)
        print(out or err)
        return 0 if ok else 6

    if args.command == "upload":
        port = args.port or config.get("port", "")
        if not port or port == "auto":
            print("Informe --port para upload quando a porta não estiver definida.", file=sys.stderr)
            return 7
        out, ok, err = backend.upload_action(str(project_path), fqbn, port, config=config)
        print(out or err)
        return 0 if ok else 8

    if args.command == "info":
        print(json.dumps(config, indent=2, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
