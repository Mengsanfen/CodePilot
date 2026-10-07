"""Deterministic fixture scoring; location precision is not semantic review precision."""
from statistics import mean


def score_review(predicted, expected):
    expected = {(f['file'], f['line']) for f in expected}
    matched = set()
    false_positives = 0
    for item in predicted:
        location = (item.get('file'), item.get('line')) if isinstance(item, dict) else (None, None)
        if location in expected and location not in matched:
            matched.add(location)
        else:
            false_positives += 1
    return {'true_positives': len(matched), 'false_positives': false_positives,
            'false_negatives': len(expected - matched)}


def aggregate(records):
    review = [r for r in records if r['category'] == 'review']
    tp = sum(r.get('true_positives', 0) for r in review)
    fp = sum(r.get('false_positives', 0) for r in review)
    fn = sum(r.get('false_negatives', 0) for r in review)
    return {'cases': len(records), 'passed': sum(bool(r['passed']) for r in records),
        'review_valid_defect_rate': None,
        'review_valid_defect_rate_note': 'Requires evidence-bound adjudication; location matches alone are insufficient.',
        'review_output_coverage': sum('findings' in r for r in review) / len(review) if review else None,
        'end_to_end_success_rate': sum(bool(r['passed']) for r in records) / len(records) if records else None,
        'review_location_precision': tp / (tp + fp) if tp + fp else None,
        'review_location_recall': tp / (tp + fn) if tp + fn else None,
        'review_true_positives': tp, 'review_false_positives': fp, 'review_false_negatives': fn,
        'mean_seconds': round(mean(r['seconds'] for r in records), 3) if records else None,
        'input_tokens': sum(r.get('input_tokens', 0) for r in records),
        'output_tokens': sum(r.get('output_tokens', 0) for r in records)}


def compare(report, baseline):
    if report['suite_sha256'] != baseline.get('suite_sha256'):
        raise ValueError('评测集版本不同，不能计算收益')
    if [r['id'] for r in report['cases']] != [r['id'] for r in baseline['cases']]:
        raise ValueError('评测样例不完整，不能比较')
    return {key: report['summary'][key] - value for key, value in baseline['summary'].items()
            if isinstance(value, (float, int)) and isinstance(report['summary'].get(key), (float, int))}


def adjudicate(report, judgments):
    """Judgments must bind to the exact finding text, not merely its mutable index."""
    import hashlib
    import json
    expected = {}
    for case in report['cases']:
        for i, finding in enumerate(case.get('findings', [])):
            digest = hashlib.sha256(json.dumps(finding, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            expected[(case['id'], i)] = digest
    seen, valid = set(), 0
    for item in judgments:
        key = (item['case_id'], item['index'])
        if key in seen or expected.get(key) != item['finding_sha256'] or not isinstance(item['valid'], bool) or not item.get('evidence'):
            raise ValueError('裁定重复、证据缺失或 finding 摘要不匹配')
        seen.add(key)
        valid += item['valid']
    if seen != set(expected):
        raise ValueError('必须逐条裁定全部输出，避免只挑选正确 finding')
    return {'valid': valid, 'total': len(expected), 'valid_defect_rate': valid/len(expected) if expected else None,
            'judgments': judgments, 'scope': 'Explicit adjudication of emitted findings; report coverage separately.'}
