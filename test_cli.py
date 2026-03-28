#!/usr/bin/env python3
"""Teste rápido para diagnosticar problemas com arduino-cli"""

import subprocess
import json
from pathlib import Path

def test_cli():
    """Testa se arduino-cli está funcionando"""
    cli_path = Path.cwd() / "arduino-cli.exe"
    
    if not cli_path.exists():
        print(f"❌ arduino-cli não encontrado em: {cli_path}")
        return False
    
    print(f"✓ arduino-cli encontrado em: {cli_path}")
    
    # Teste 1: Versão
    print("\n=== TESTE 1: Versão ===")
    try:
        result = subprocess.run([str(cli_path), "version"], 
                              capture_output=True, text=True, timeout=10)
        print(f"Return code: {result.returncode}")
        print(f"Output: {result.stdout[:200]}")
        if result.stderr:
            print(f"Error: {result.stderr[:200]}")
    except Exception as e:
        print(f"Erro: {e}")
    
    # Teste 2: Board listall
    print("\n=== TESTE 2: Board Listall ===")
    try:
        result = subprocess.run([str(cli_path), "board", "listall", "--format", "json"], 
                              capture_output=True, text=True, timeout=30)
        print(f"Return code: {result.returncode}")
        print(f"Output length: {len(result.stdout)}")
        if result.stdout:
            try:
                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    print(f"JSON Keys: {list(data.keys())}")
                    if "boards" in data:
                        print(f"Boards count: {len(data.get('boards', []))}")
                        if data.get("boards"):
                            print(f"First board: {data['boards'][0]}")
                else:
                    print(f"JSON Type: {type(data)}")
            except Exception as e:
                print(f"JSON parse error: {e}")
                # Mostrar primeiros 500 chars
                print(f"Raw output: {result.stdout[:500]}")
        if result.stderr:
            print(f"Error: {result.stderr[:500]}")
    except Exception as e:
        print(f"Erro: {e}")
    
    # Teste 3: Lib list
    print("\n=== TESTE 3: Lib List ===")
    try:
        result = subprocess.run([str(cli_path), "lib", "list", "--format", "json"], 
                              capture_output=True, text=True, timeout=30)
        print(f"Return code: {result.returncode}")
        print(f"Output length: {len(result.stdout)}")
        if result.stdout:
            try:
                data = json.loads(result.stdout)
                if isinstance(data, dict):
                    print(f"JSON Keys: {list(data.keys())}")
                    if "installed_libraries" in data:
                        print(f"Libraries count: {len(data.get('installed_libraries', []))}")
                        if data.get("installed_libraries"):
                            print(f"First lib: {data['installed_libraries'][0]}")
                else:
                    print(f"JSON Type: {type(data)}")
            except Exception as e:
                print(f"JSON parse error: {e}")
                # Mostrar primeiros 500 chars
                print(f"Raw output: {result.stdout[:500]}")
        if result.stderr:
            print(f"Error: {result.stderr[:500]}")
    except Exception as e:
        print(f"Erro: {e}")

if __name__ == "__main__":
    test_cli()
