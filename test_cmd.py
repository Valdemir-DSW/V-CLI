import subprocess
result = subprocess.run(['arduino-cli.exe', 'lib', 'list', '--json'], capture_output=True, text=True, timeout=30)
print('returncode:', result.returncode)
print('stdout length:', len(result.stdout))
print('First 500:', result.stdout[:500] if result.stdout else 'EMPTY')
