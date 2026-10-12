"""Run explicit fault scenarios and export measured reliability assertions."""
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
output = ROOT / 'test-results' / ('faults-' + str(time.time_ns()))
output.mkdir(parents=True)
xml = output / 'results.xml'
started = time.monotonic()
result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'pytest', 'tests/failure_injection',
                         '-q', '-o', 'junit_family=xunit1', '--junitxml', str(xml)], cwd=ROOT)
if not xml.exists():
    raise SystemExit(result.returncode or 1)
records = []
for case in ET.parse(xml).findall('.//testcase'):
    records.append({'name': case.attrib['name'], 'seconds': float(case.attrib.get('time', 0)),
                    'status': 'skipped' if case.find('skipped') is not None else
                    'failed' if case.find('failure') is not None or case.find('error') is not None else 'passed',
                    'measurements': {p.attrib['name']: p.attrib['value'] for p in case.findall('properties/property')}})
crashes = [r for r in records if r['name'].startswith('test_worker_crash') and r['status'] != 'skipped']
passed_crashes = [r for r in crashes if r['status'] == 'passed']
measured = [r['measurements'] for r in records if r['status'] == 'passed']
report = {'scope': 'Real Worker/Checkpoint/DB/Docker; simulated Redis send failure and provider acceptance. Not a production SLO.',
          'elapsed_seconds': round(time.monotonic() - started, 3), 'cases': records,
          'passed': sum(r['status'] == 'passed' for r in records),
          'failed': sum(r['status'] == 'failed' for r in records),
          'skipped': sum(r['status'] == 'skipped' for r in records),
          'resume_success_rate': len(passed_crashes) / len(crashes) if crashes else None,
          'duplicate_external_effect_count': next((int(m['duplicate_external_effect_count']) for m in measured
                                                    if 'duplicate_external_effect_count' in m), None),
          'lease_fencing_violation_count': (sum(int(r['measurements']['lease_fencing_violation_count']) for r in passed_crashes)
                                           if len(passed_crashes) == len(crashes) and crashes else None),
          'event_replay_consistency': (all(r['measurements'].get('event_replay_consistency') == 'True' for r in crashes)
                                       if len(passed_crashes) == len(crashes) and crashes else None)}
(output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in report.items() if k != 'cases'}))
print('Report:', output / 'report.json')
raise SystemExit(result.returncode)
