"""
Script de inicialização/teste do V CLI
Verifique se tudo está configurado corretamente
"""

import os
import sys
from pathlib import Path

def check_environment():
    """Verifica se tudo está pronto para usar"""
    print("=" * 50)
    print("V CLI - Verificação de Ambiente")
    print("=" * 50)
    
    base_dir = Path.cwd()
    
    # Verificação 1: Arduino CLI
    cli_path = base_dir / "arduino-cli.exe"
    print(f"\n✓ Arduino CLI: ", end="")
    if cli_path.exists():
        print(f"✓ OK ({cli_path})")
    else:
        print(f"✗ NÃO ENCONTRADO")
        print(f"  Baixe em: https://github.com/arduino/arduino-cli/releases")
        print(f"  Coloque em: {cli_path}")
    
    # Verificação 2: Pasta de projetos
    projects_dir = base_dir / "projects"
    print(f"✓ Pasta de projetos: ", end="")
    if projects_dir.exists():
        count = len(list(projects_dir.iterdir()))
        print(f"✓ OK ({count} projetos)")
    else:
        projects_dir.mkdir(exist_ok=True)
        print(f"✓ Criada")
    
    # Verificação 3: cli.yaml
    cli_yaml = base_dir / "cli.yaml"
    print(f"✓ Arquivo cli.yaml: ", end="")
    if cli_yaml.exists():
        print(f"✓ OK")
    else:
        print(f"✗ NÃO ENCONTRADO")
        print(f"  Será criado automaticamente ao iniciar")
    
    # Verificação 4: Arquivos Python
    print(f"✓ Arquivos Python: ", end="")
    required_files = ["main.py", "cli_backend.py"]
    missing = [f for f in required_files if not (base_dir / f).exists()]
    
    if missing:
        print(f"✗ FALTANDO: {', '.join(missing)}")
    else:
        print(f"✓ OK")
    
    # Verificação 5: Python version
    print(f"✓ Versão Python: ", end="")
    if sys.version_info >= (3, 7):
        print(f"✓ OK ({sys.version.split()[0]})")
    else:
        print(f"✗ Mínimo Python 3.7 necessário")
    
    print("\n" + "=" * 50)
    print("Tudo pronto! Execute: python main.py")
    print("=" * 50)

if __name__ == "__main__":
    check_environment()
