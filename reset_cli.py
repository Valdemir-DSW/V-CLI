"""
Script de Recuperação - V CLI
Reseta a configuração do arduino-cli se estiver corrompida
"""

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

def reset_cli_config():
    """Remove e recria configuração do arduino-cli"""
    
    base_dir = Path.cwd()
    cli_path = base_dir / "arduino-cli.exe"
    config_file = base_dir / "cli.yaml"
    
    print("=" * 60)
    print("V CLI - Script de Recuperação")
    print("=" * 60)
    
    # Verificar arduino-cli
    if not cli_path.exists():
        print(f"\nERRO: arduino-cli.exe não encontrado!")
        print(f"Baixe em: https://github.com/arduino/arduino-cli/releases")
        print(f"Coloque em: {cli_path}")
        return False
    
    print(f"\n✓ arduino-cli encontrado: {cli_path}")
    
    # Remover configuração corrompida
    if config_file.exists():
        print(f"\nRemovendo configuração antiga...")
        try:
            config_file.unlink()
            print(f"✓ Removido: {config_file}")
        except Exception as e:
            print(f"Erro ao remover: {e}")
            return False
    
    # Remover diretórios de dados (opcional)
    appdata_cli = Path.home() / "AppData" / "Local" / "Arduino15"
    if appdata_cli.exists():
        print(f"\nEncontrado: {appdata_cli}")
        response = input("Remover também dados locais do Arduino (sim/não)? ").lower()
        if response in ['s', 'sim', 'y', 'yes']:
            try:
                shutil.rmtree(appdata_cli)
                print(f"✓ Removido diretório de dados")
            except Exception as e:
                print(f"Aviso: Não consegui remover todos os dados: {e}")
    
    # Reinicializar CLI
    print(f"\nInicializando arduino-cli...")
    try:
        result = subprocess.run(
            [str(cli_path), "config", "init", "--config-file", str(config_file)],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            print(f"✓ Configuração criada com sucesso")
        else:
            print(f"Aviso: {result.stderr[:200]}")
            if not config_file.exists():
                print("Criando cli.yaml manualmente...")
                create_default_config(config_file)
    
    except Exception as e:
        print(f"Erro ao inicializar: {e}")
        print("Criando cli.yaml manualmente...")
        create_default_config(config_file)
    
    # Testar configuração
    print(f"\nTestando configuração...")
    try:
        result = subprocess.run(
            [str(cli_path), "--config-file", str(config_file), "version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            version = result.stdout.strip().split('\n')[0]
            print(f"✓ CLI funcionando: {version}")
            print("\n" + "=" * 60)
            print("Recuperação bem-sucedida!")
            print("Execute: python main.py")
            print("=" * 60)
            return True
        else:
            print(f"Erro ao testar: {result.stderr[:200]}")
            return False
    
    except Exception as e:
        print(f"Erro no teste: {e}")
        return False


def create_default_config(config_file: Path):
    """Cria arquivo cli.yaml padrão"""
    
    config_content = """# V CLI - Configuração Arduino CLI
# Gerado automaticamente

board_manager:
    additional_urls:
        - https://github.com/stm32duino/BoardManagerFiles/raw/main/package_stmicroelectronics_index.json
        - https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json

cli:
    network:
        user_agent_timeout: 10000
    updater:
        enable_notification: false

directories:
    data: ""
    downloads: ""
    builtin:
        tools: ""
"""
    
    try:
        with open(config_file, 'w') as f:
            f.write(config_content)
        print(f"✓ Arquivo cli.yaml criado manualmente")
    except Exception as e:
        print(f"Erro ao criar cli.yaml: {e}")


if __name__ == "__main__":
    success = reset_cli_config()
    sys.exit(0 if success else 1)
