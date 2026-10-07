"""Reproducible offline engineering evaluation; reports measured tests, never invented model scores."""
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(__file__).resolve().parents[1]
output = root / 'test-results'
output.mkdir(exist_ok=True)
started = time.monotonic()
xml_path = output / ('evaluation-' + str(time.time_ns()) + '.xml')
result = subprocess.run([sys.executable, '-m', 'pytest', 'tests', '-q', '--junitxml',
                         str(xml_path)], cwd=root)
if not xml_path.exists():
    raise SystemExit(result.returncode or 1)
(output / 'evaluation.xml').write_bytes(xml_path.read_bytes())
tree = ET.parse(xml_path)
cases = tree.findall('.//testcase')
report = {'evaluation': 'offline-engineering-regression', 'elapsed_seconds': round(time.monotonic() - started, 2),
          'passed': sum(c.find('failure') is None and c.find('error') is None and c.find('skipped') is None for c in cases),
          'failed': sum(c.find('failure') is not None or c.find('error') is not None for c in cases),
          'skipped': sum(c.find('skipped') is not None for c in cases),
          'cases': [{'name': c.attrib['name'], 'seconds': float(c.attrib.get('time', 0)),
                     'status': 'skipped' if c.find('skipped') is not None else 'failed' if c.find('failure') is not None or c.find('error') is not None else 'passed'} for c in cases]}
(output / 'evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in report.items() if k != 'cases'}))
raise SystemExit(result.returncode)
