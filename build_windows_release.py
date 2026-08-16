import shutil
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
BUILD_DIR = BASE_DIR / "build_nuitka"
RELEASE_APP_DIR = BUILD_DIR / "release" / "V-CLI"
INSTALLER_DIR = BUILD_DIR / "installer"
ISS_PATH = BASE_DIR / "installer_inno.iss"


def run(cmd: list[str]) -> None:
    print("[RUN]", " ".join(str(part) for part in cmd))
    subprocess.run(cmd, cwd=BASE_DIR, check=True)


def resolve_iscc() -> Path | None:
    candidates = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path.home() / "AppData" / "Local" / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def ensure_release() -> None:
    if not RELEASE_APP_DIR.exists():
        run([sys.executable, "build_nuitka.py"])


def build_installer() -> Path:
    ensure_release()
    iscc = resolve_iscc()
    if not iscc:
        raise FileNotFoundError(
            "Inno Setup (ISCC.exe) não encontrado. Instale o Inno Setup 6 para gerar o instalador."
        )
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    run(
        [
            str(iscc),
            "/O" + str(INSTALLER_DIR),
            "/DAppSourceDir=" + str(RELEASE_APP_DIR),
            str(ISS_PATH),
        ]
    )
    installers = sorted(INSTALLER_DIR.glob("*.exe"))
    if not installers:
        raise FileNotFoundError("Nenhum instalador foi gerado pelo Inno Setup.")
    return installers[-1]


def main() -> int:
    installer_path = build_installer()
    print(f"[OK] Instalador gerado em: {installer_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
