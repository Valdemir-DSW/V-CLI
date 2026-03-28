from cli_backend import CLIBackend

b = CLIBackend('.')
all_boards = b.list_boards_all_versions()

# Agrupa por platform_id
platforms = {}
for entry in all_boards:
    plat_id = entry.get('platform_id', '')
    version = entry.get('platform_version', '')
    if plat_id not in platforms:
        platforms[plat_id] = []
    if version not in platforms[plat_id]:
        platforms[plat_id].append(version)

print(f'Plataformas encontradas: {len(platforms)}')
for plat_id in sorted(platforms.keys())[:5]:
    versions = sorted(platforms[plat_id], reverse=True)
    print(f'  {plat_id}: {versions[:5]} ... ({len(versions)} versões)')
