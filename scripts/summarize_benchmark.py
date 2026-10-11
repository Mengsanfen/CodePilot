"""Export an allowlisted public summary without prompts, code, keys or raw errors."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.benchmark import summarize


def render(report):
    if report['mode'] != 'live-model':
        raise ValueError('Fixture controls are not model success rates')
    suite = json.loads((ROOT / 'evals/repository-tasks.json').read_text(encoding='utf-8'))
    expected = {case['id']: case for case in suite['tasks']}
    records = report['cases']
    if len(records) != len(expected) or {row['id'] for row in records} != set(expected):
        raise ValueError('Public baseline summary requires the complete fixed task set')
    if any(row['base_sha'] != expected[row['id']]['base_sha'] for row in records):
        raise ValueError('Repository snapshots differ from the current manifest')
    metrics = summarize(records)
    if report.get('adjudication'):
        from agent.evaluation import adjudicate
        checked = adjudicate(report, report['adjudication']['judgments'])
        metrics['review_valid_finding_rate'] = checked['valid_defect_rate']
    lines = ['# 固定仓库评测结果', '',
             '本页由 `scripts/summarize_benchmark.py` 从完整模型报告生成。原始报告仅存 `test-results/`，不提交模型输出、仓库源码或环境配置。', '',
             '人工编写任务集，含注入回归；每例单次任务尝试，任务内允许多轮工具调用。成功率不代表企业或历史 Issue 的总体效果。方法见 [BENCHMARK](../BENCHMARK.md)。', '',
             '| 指标 | 实测 |', '| --- | --- |']
    for name, value in metrics.items():
        if isinstance(value, (int, float, bool)) or value is None:
            lines.append(f'| {name} | {round(value, 4) if type(value) is float else value} |')
    lines += ['', '`review_valid_finding_rate` 未裁定时为 None；定位匹配不等于缺陷语义正确。可靠性指标由独立故障实验报告。', '',
              '## 分类结果', '', '| 类型 | 通过 / 总数 |', '| --- | --- |']
    for category in ('bugfix', 'feature', 'testgen', 'review', 'repoqa'):
        group = [r for r in records if r['type'] == category]
        lines.append(f"| {category} | {sum(r['passed'] for r in group)} / {len(group)} |")
    lines += ['', '## 全部任务', '', '| 任务 | 结果 | 失败类型 | 秒 | 模型调用 | 输入 / 输出 Token |', '| --- | --- | --- | --- | --- | --- |']
    for row in records:
        lines.append(f"| {row['id']} | {'通过' if row['passed'] else '失败'} | {row.get('failure') or '—'} | {row['seconds']} | {row.get('model_calls', 0)} | {row.get('input_tokens', 0)} / {row.get('output_tokens', 0)} |")
    lines += ['', '## 可追溯版本', '']
    # Only hashes cross the public report boundary. Free-form errors and answers do not.
    for name in ('suite_sha256', 'verifier_sha256', 'image_id'):
        value = report[name].removeprefix('sha256:')
        if len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Invalid report fingerprint')
        lines.append(f'- {name}: `{value}`')
    lines += ['', '本轮在 Windows + Docker Desktop 上执行，使用本地配置的 DeepSeek 模型。原始逐例记录包含模型标识和 usage 完整性；缺失 usage 时已知 Token 只能作为下界。', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/benchmark/latest-summary.md')
    args = parser.parse_args()
    content = render(json.loads(args.report.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding='utf-8')
    print(args.output)
