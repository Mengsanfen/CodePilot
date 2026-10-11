"""Apply explicit finding-by-finding judgments, preserving the original model report."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.evaluation import adjudicate

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    parser.add_argument('judgments', type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding='utf-8'))
    report['adjudication'] = adjudicate(report, json.loads(args.judgments.read_text(encoding='utf-8')))
    if report.get('mode') == 'live-model':
        report['summary']['review_valid_finding_rate'] = report['adjudication']['valid_defect_rate']
        report['summary']['review_valid_finding_note'] = 'Explicit evidence judgments; inspect adjudication and review_delivery_rate.'
    else:
        report['summary']['review_valid_defect_rate'] = report['adjudication']['valid_defect_rate']
        report['summary']['review_valid_defect_rate_note'] = 'Explicit evidence judgments; inspect adjudication and review_output_coverage.'
    target = args.report.with_name('adjudicated-report.json')
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(target)
