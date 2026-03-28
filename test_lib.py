import subprocess
import json

# Testa se tem informação de versão em algum lugar
result = subprocess.run(['arduino-cli.exe', 'lib', 'list', '--json'], 
    capture_output=True, text=True, timeout=30)
data = json.loads(result.stdout)
if 'installed_libraries' in data and len(data['installed_libraries']) > 3:
    for i in range(3):
        lib = data['installed_libraries'][i]
        library = lib.get('library', {})
        name = library.get('name', '?')
        # Procura por qualquer coisa que pareça versão
        keys_with_version = [k for k in library.keys() if 'version' in k.lower()]
        all_keys = list(library.keys())
        print(f'{name}: version_keys={keys_with_version}, all_keys={len(all_keys)}')
