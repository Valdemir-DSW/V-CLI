from cli_backend import CLIBackend

b = CLIBackend('.')
libs = b.list_libraries_fixed()
print('Libs:', len(libs))
if libs:
    for i, lib in enumerate(libs[:3]):
        name = lib.get('name', '?')
        version = lib.get('version', 'N/A')
        print(f'  {i+1}. {name} v{version}')
