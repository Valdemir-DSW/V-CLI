"""
Script de Inicialização - V CLI
Verifica tudo antes de abrir a aplicação
"""

import os
import sys
import subprocess
from pathlib import Path

def verify_and_start():
    """Verifica ambiente e inicia V CLI"""
    
    base_dir = Path.cwd()
    cli_path = base_dir / "arduino-cli.exe"
    config_file = base_dir / "cli.yaml"
    main_py = base_dir / "main.py"
    
    print("=" * 60)
    print("V CLI - Inicialização")
    print("=" * 60)
    
    # Verificação 1: arduino-cli
    print("\n[1/4] Verificando arduino-cli...", end=" ")
    if not cli_path.exists():
        print("ERRO")
        print(f"\nArduino CLI não encontrado em: {cli_path}")
        print("Baixe em: https://github.com/arduino/arduino-cli/releases")
        print("e coloque na pasta do projeto.")
        return False
    print("OK")
    
    # Verificação 2: main.py
    print("[2/4] Verificando arquivos Python...", end=" ")
    if not main_py.exists():
        print("ERRO")
        print(f"main.py não encontrado")
        return False
    print("OK")
    
    # Verificação 3: Version check
    print("[3/4] Testando arduino-cli...", end=" ")
    try:
        result = subprocess.run(
            [str(cli_path), "version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            print("ERRO")
            print(f"Arduino CLI retornou erro: {result.stderr[:100]}")
            print("\nTentando recuperar com reset_cli.py...")
            return False
        print("OK")
    except Exception as e:
        print(f"ERRO: {e}")
        return False
    
    # Verificação 4: Configuração
    print("[4/4] Verificando configuração...", end=" ")
    if not config_file.exists():
        print("CRIAR")
        print("        (será criada automaticamente ao iniciar)")
    else:
        print("OK")
    
    print("\n" + "=" * 60)
    print("Iniciando V CLI...")
    print("=" * 60 + "\n")
    
    # Iniciar aplicação
    try:
        subprocess.run([sys.executable, str(main_py)])
        return True
    except Exception as e:
        print(f"Erro ao iniciar V CLI: {e}")
        return False


if __name__ == "__main__":
    success = verify_and_start()
    
    if not success:
        print("\nPara recuperar, execute:")
        print("  python reset_cli.py")
        print("\nPara mais informações, veja:")
        print("  RECUPERACAO.md")
        sys.exit(1)
