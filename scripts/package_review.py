"""Build a source-only archive after scanning exactly the included files."""
from pathlib import Path
import zipfile
from check_secrets import ROOT, source_files, scan


def main():
    files = source_files()
    violations = scan(files)
    if violations:
        for item in violations:
            print(item['path'], item['rule'])
        raise SystemExit('Packaging stopped: secret scan failed')
    output = ROOT / 'dist' / 'codepilot-review.zip'
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(f'Packaged {len(files)} files: {output}')


if __name__ == '__main__':
    main()
