import importlib.util
from pathlib import Path


def test_secret_scan_redacts_and_package_excludes_runtime(tmp_path):
    spec = importlib.util.spec_from_file_location('scan', Path('scripts/check_secrets.py'))
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    secret = 'sk-' + 'a' * 32
    path = tmp_path / 'leak.txt'
    path.write_text(secret)
    result = scan.scan([path], tmp_path)
    assert result == [{'path': 'leak.txt', 'rule': 'provider_key'}]
    assert secret not in str(result)
    for name in ['.env', 'workspace/repo/.env', 'ui/node_modules/a.js', 'test-results/run.json', '.git/config']:
        assert scan.excluded(Path(name))
    assert not scan.excluded(Path('.env.example'))
