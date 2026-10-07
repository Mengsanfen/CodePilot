"""Compile repository Python and catch accidental duplicate literal keys/definitions."""
import ast
from pathlib import Path

errors = []
for directory in ('agent', 'tests', 'scripts'):
    for path in Path(directory).rglob('*.py'):
        source = path.read_text(encoding='utf-8-sig')
        tree = ast.parse(source, filename=str(path))
        compile(tree, str(path), 'exec')
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                seen = set()
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        if key.value in seen:
                            errors.append(f'{path}:{key.lineno}: duplicate key {key.value}')
                        seen.add(key.value)
            if isinstance(node, (ast.Module, ast.ClassDef)):
                names = set()
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if child.name in names:
                            errors.append(f'{path}:{child.lineno}: duplicate definition {child.name}')
                        names.add(child.name)
if errors:
    raise SystemExit('\n'.join(errors))
print('Python compilation and duplicate-definition checks passed')
