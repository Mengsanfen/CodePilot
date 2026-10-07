"""Trusted diff reads and evidence validation, without an arbitrary shell."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from langchain_core.tools import tool
from agent.tools.runtime_context import get_runtime_configurable


def current_repo() -> Path:
    from agent.server import ensure_backend_for_thread
    from agent.tools.gitee_api import parse_gitee_repo_url
    config = get_runtime_configurable()
    backend = ensure_backend_for_thread(config['thread_id'])
    repo = parse_gitee_repo_url(config['repo_url'])
    return backend.projects_dir / repo.repo


def git_read(root: Path, *args: str) -> str:
    from agent.security import command_environment
    result = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull,
                             '-c', 'core.fsmonitor=false', '-C', str(root), *args],
                            env=command_environment(), capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=30)
    if result.returncode:
        raise ValueError('无法读取 Git 上下文')
    return result.stdout


def changed_lines(diff: str) -> dict[str, set[int]]:
    result, path = {}, None
    for line in diff.splitlines():
        if line.startswith('+++ b/'):
            path = line[6:]
            result.setdefault(path, set())
        elif line.startswith('@@') and path:
            match = re.search(r'\+(\d+)(?:,(\d+))? @@', line)
            if match:
                start, count = int(match[1]), int(match[2] or 1)
                result[path].update(range(start, start + count))
    return result


def diff_snapshot(root: Path) -> dict:
    # Includes staged and unstaged changes relative to the prepared baseline.
    baseline_file = root.parent.parent / 'policies' / 'baseline.txt'
    base = baseline_file.read_text().strip() if baseline_file.exists() else 'HEAD'
    if base != 'HEAD' and not re.fullmatch(r'[a-f0-9]{40,64}', base):
        raise ValueError('invalid baseline')
    head = git_read(root, 'rev-parse', 'HEAD').strip()
    tracked = set(filter(None, git_read(root, 'diff', '--no-renames', '--name-only', '-z', base, '--').split('\0')))
    untracked = set(filter(None, git_read(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')))
    digest = hashlib.sha256((base + ':' + head).encode())
    parts, skipped = [], []
    for name in sorted(tracked | untracked):
        path = root / name
        digest.update(name.encode() + b'\0')
        # Hash excluded content as well: approval identity must change even when
        # private/binary files cannot be sent to the reviewer.
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            digest.update(b'link:' + os.readlink(path).encode() if path.is_symlink() else b'outside')
            skipped.append({'file': name, 'reason': 'symlink'})
            continue
        data_hash = hashlib.sha256()
        if path.is_file():
            with path.open('rb') as source:
                for chunk in iter(lambda: source.read(65536), b''):
                    data_hash.update(chunk)
            digest.update(str(path.stat().st_mode).encode() + data_hash.digest())
        else:
            digest.update(b'deleted')
        if any(char in name for char in '\r\n\t"'):
            skipped.append({'file': name, 'reason': 'unsupported_filename'})
            continue
        if sensitive_path(name):
            skipped.append({'file': name, 'reason': 'sensitive'})
            continue
        if path.is_file() and path.stat().st_size > 200000:
            skipped.append({'file': name, 'reason': 'large_file'})
            continue
        if name in untracked:
            raw = path.read_bytes()
            if b'\0' in raw:
                skipped.append({'file': name, 'reason': 'binary'})
                continue
            lines = raw.decode('utf-8', errors='replace').splitlines()
            part = f'diff --git a/{name} b/{name}\n--- /dev/null\n+++ b/{name}\n@@ -0,0 +1,{len(lines)} @@\n'
            part += ''.join('+' + line + '\n' for line in lines)
        else:
            part = git_read(root, '-c', 'core.quotePath=false', 'diff', '--no-ext-diff',
                            '--no-textconv', '--no-renames', '--unified=0', base, '--', ':(literal)' + name)
            if 'Binary files ' in part or len(part) > 200000:
                skipped.append({'file': name, 'reason': 'binary_or_large_diff'})
                continue
        parts.append(part)
    diff = ''.join(parts)
    return {'base': base, 'head': head, 'digest': digest.hexdigest(), 'diff': diff, 'skipped': skipped,
            'changed_lines': {p: sorted(lines) for p, lines in changed_lines(diff).items()}}


def sensitive_path(name: str) -> bool:
    path = Path(name.lower())
    return (any(p in {'.env', '.git', '.secrets', '.git-cache'} or
                (p.startswith('.env.') and p != '.env.example') for p in path.parts)
            or path.suffix in {'.pem', '.key'})


def validate_finding(root: Path, file: str, line: int | None, severity: str) -> dict:
    if not file or Path(file).is_absolute() or '\\' in file or '..' in Path(file).parts:
        raise ValueError('finding 文件必须是仓库内相对路径')
    path = (root / file).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('finding 文件不存在或越界')
    if sensitive_path(file):
        raise ValueError('finding 不能读取敏感文件')
    if severity not in {'critical', 'high', 'medium', 'low'}:
        raise ValueError('severity 必须是 critical/high/medium/low')
    snapshot = diff_snapshot(root)
    if line is None or line not in snapshot['changed_lines'].get(file, []):
        raise ValueError('finding 必须定位到当前 diff 的新增或修改行；纯删除问题请写入总体审查结论')
    return snapshot


@tool
def review_local_diff() -> dict:
    """读取当前会话相对于初始提交的 diff、版本摘要和可定位的变更行。"""
    snapshot = diff_snapshot(current_repo())
    snapshot['truncated'] = len(snapshot['diff']) > 100000
    snapshot['diff'] = snapshot['diff'][:100000]
    from agent.orchestration.review_evidence import record_read
    record_read(snapshot)
    return snapshot
