"""Scan deliverable source, never print matched content or modify local secrets."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '.venv', '.cache', '.pytest_cache', 'logs', 'workspace', 'test-results',
            'data', 'node_modules', '.output', '__pycache__', 'dist', '.idea', '.vscode', '.tanstack'}
SAFE = {'', 'test-only', 'codepilot-local-only', 'password', 'token', 'example', 'sentinel',
        'your-key', 'your-token', 'your-password', '<token>', '<password>', '<key>', '你的密码'}
RULES = {
    'provider_key': re.compile(r'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,})'),
    'credential_assignment': re.compile(r'(?:DEEPSEEK_API_KEY|GITHUB_TOKEN)[ \t]*[=:][ \t]*[\"\x27]?([^\s\"\x27,}`]+)'),
    'bearer_token': re.compile(r'Bearer\s+([A-Za-z0-9_.-]{12,})', re.I),
    'database_password': re.compile(r'postgres(?:ql)?://[^\s:/]+:([^@\s]+)@'),
}


def excluded(path):
    return (any(p in EXCLUDED or p.startswith('.tmp') for p in path.parts)
            or path.suffix in {'.pyc', '.sqlite', '.zip'}
            or (path.name.startswith('.env') and path.name not in {'.env.example', '.env.production', '.env.development'}))


def source_files(root=ROOT):
    root = root.resolve()
    def safe_file(path):
        return path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root)

    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            capture_output=True)
    if result.returncode == 0:
        names = sorted(set(result.stdout.decode('utf-8').split('\0')) - {''})
        return [root / n for n in names if not excluded(Path(n)) and safe_file(root / n)]
    import os
    files = []
    for directory, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not excluded(Path(directory).relative_to(root) / d)
                   and not (Path(directory) / d).is_symlink()
                   and (Path(directory) / d).resolve().is_relative_to(root)]
        files.extend(Path(directory) / n for n in names if not excluded((Path(directory) / n).relative_to(root))
                     and safe_file(Path(directory) / n))
    return sorted(files)


def scan(paths, root=ROOT):
    violations = []
    for path in paths:
        raw = path.read_bytes()
        if b'\0' in raw:
            continue
        content = raw.decode('utf-8', errors='replace')
        for rule, pattern in RULES.items():
            for match in pattern.finditer(content):
                value = match.group(1) if match.lastindex else match.group()
                if value in SAFE or value.startswith(('$', '{', '<', 'os.', 'get_env(', 'require_env(')):
                    continue
                violations.append({'path': path.relative_to(root).as_posix(), 'rule': rule})
                break
    return violations


def main():
    import json
    errors = scan(source_files())
    tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True)
    if tracked.returncode == 0:
        for name in tracked.stdout.decode().split('\0'):
            if name and Path(name).name.startswith('.env') and Path(name).name not in {'.env.example', '.env.production', '.env.development'}:
                errors.append({'path': name, 'rule': 'tracked_secret_file'})
    for error in errors:
        print(json.dumps(error, ensure_ascii=True))
    print(f'Secret scan: {len(errors)} violation(s); only paths and rule names are reported.')
    return bool(errors)


if __name__ == '__main__':
    sys.exit(main())
