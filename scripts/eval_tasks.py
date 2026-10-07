"""Opt-in real-model fixture evaluation. No external repositories or publishing."""
from __future__ import annotations
import argparse
import json
import hashlib
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Explicitly permit billed model calls')
    parser.add_argument('--image', default='codepilot-sandbox:local')
    parser.add_argument('--baseline', type=Path, help='Compare only reports with exactly the same suite and cases')
    parser.add_argument('--context-mode', choices=['on-demand', 'full'], default='on-demand')
    parser.add_argument('--review-output', choices=['json', 'structured'], default='structured')
    parser.add_argument('--case-id', help='Run one case for diagnosis; not comparable to a complete suite')
    args = parser.parse_args()
    if not args.live:
        parser.error('Use --live to permit model calls. Offline engineering tests: scripts/evaluate.py')
    from agent.env_utils import load_environment
    load_environment()
    os.environ['MODEL_MAX_TOKENS'] = '2048'
    os.environ['CODEPILOT_SANDBOX_IMAGE'] = args.image
    from langchain.agents import create_agent
    from langchain.agents.middleware import ModelCallLimitMiddleware
    from langchain.agents.structured_output import ToolStrategy
    from pydantic import BaseModel, Field
    class ReviewFinding(BaseModel):
        file: str
        line: int = Field(ge=1)
        description: str = Field(min_length=1)
    class ReviewOutput(BaseModel):
        findings: list[ReviewFinding]
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from agent.backends.docker import DockerBackend
    from agent.core.model import make_main_model
    from agent.core.middleware import ToolErrorMiddleware
    from agent.core.middleware.invalid_tool_calls import InvalidToolCallMiddleware
    root = Path('test-results') / ('model-eval-' + str(int(time.time())))
    root.mkdir(parents=True)
    cases = json.loads(Path('evals/tasks.json').read_text(encoding='utf-8'))
    if args.case_id:
        cases = [case for case in cases if case['id'] == args.case_id]
        if not cases:
            parser.error('Unknown case id')
    suite_hash = hashlib.sha256(Path('evals/tasks.json').read_bytes()).hexdigest()
    from agent.evaluation import score_review, aggregate, compare
    records = []
    for case in cases:
        started = time.monotonic()
        backend = DockerBackend(root / case['id'])
        backend.write('/projects/task/solution.py', case['source'])
        backend.read_only = case['category'] == 'review'
        record = {'id': case['id'], 'category': case['category'], 'passed': False}
        if case['category'] == 'review':
            record.update(score_review([], case['expected_findings']))
        try:
            agent = create_agent(model=make_main_model(),
                response_format=ToolStrategy(ReviewOutput) if case['category'] == 'review' and args.review_output == 'structured' else None,
                system_prompt='完成用户要求。工具路径为 /projects/task。只使用提供的文件工具；不要运行 shell 或访问外部服务。',
                middleware=[FilesystemMiddleware(backend=backend),
                            InvalidToolCallMiddleware(),
                            ModelCallLimitMiddleware(run_limit=6, exit_behavior='error'),
                            ToolErrorMiddleware(backend=backend)])
            prompt = case['prompt']
            if args.context_mode == 'full':
                prompt += '\n以下为当前 solution.py 的完整内容，可直接依据它处理，避免重复读取：\n' + case['source']
            if case['category'] == 'review' and args.review_output == 'structured':
                prompt += '\n最终交付格式：使用 ReviewOutput 工具提交 findings。这取代前面所说的 JSON 文本数组格式；没有问题时提交空 findings。'
            result = agent.invoke({'messages': [{'role': 'user', 'content': prompt}]},
                                  config={'recursion_limit': 30})
            if case['category'] == 'review' and args.review_output == 'structured' and 'structured_response' not in result:
                from langchain_core.messages import HumanMessage
                record['format_retries'] = 1
                record['first_format_failure'] = [m.model_dump(include={'type', 'content', 'tool_calls', 'invalid_tool_calls'}) for m in result['messages'][-2:]]
                # One bounded retry is safe because this agent has only read-only
                # file tools. Preserve the complete conversation and token usage.
                result = agent.invoke({'messages': [*result['messages'], HumanMessage(content='尚未收到结构化交付。请调用 ReviewOutput 工具提交完整 findings；无需重新读取文件。')]}, config={'recursion_limit': 30})
            record['input_tokens'] = sum((getattr(m, 'usage_metadata', None) or {}).get('input_tokens', 0) for m in result['messages'])
            record['output_tokens'] = sum((getattr(m, 'usage_metadata', None) or {}).get('output_tokens', 0) for m in result['messages'])
            if case['category'] == 'review':
                if args.review_output == 'structured':
                    if 'structured_response' not in result:
                        # ToolStrategy permits a plain final message. Finalize once
                        # with a forced schema tool instead of accepting malformed text.
                        record['format_finalizations'] = 1
                        raw_answer = str(result['messages'][-1].content)
                        record['raw_review_answer'] = raw_answer
                        record['format_debug'] = [m.model_dump(include={'type', 'content', 'tool_calls', 'invalid_tool_calls'}) for m in result['messages'][-4:]]
                        if not raw_answer:
                            raise ValueError('模型未交付结构化结果，且没有可转换的文本结论')
                        finalized = make_main_model().with_structured_output(ReviewOutput, method='function_calling', include_raw=True).invoke([
                            ('system', '将已有审查结论转换为结构化 findings，不增删结论，无缺陷则返回空 findings。'),
                            ('user', raw_answer)])
                        usage = finalized['raw'].usage_metadata or {}
                        record['input_tokens'] += usage.get('input_tokens', 0)
                        record['output_tokens'] += usage.get('output_tokens', 0)
                        if finalized['parsed'] is None:
                            raise ValueError('结构化交付校验失败')
                        result['structured_response'] = finalized['parsed']
                    findings = [item.model_dump() for item in result['structured_response'].findings]
                else:
                    answer = str(result['messages'][-1].content).strip().removeprefix('```json').removesuffix('```').strip()
                    record['raw_review_answer'] = answer
                    findings = json.loads(answer)
                if not isinstance(findings, list):
                    raise ValueError('review output must be a JSON array')
                record.update(score_review(findings, case['expected_findings']))
                record['passed'] = record['false_positives'] == 0 and record['false_negatives'] == 0
                record['passed'] &= (backend.projects_dir / 'task' / 'solution.py').read_text(encoding='utf-8') == case['source']
                record['findings'] = findings
            else:
                # Hidden assertions are installed only after the model has finished.
                backend.write('/projects/task/acceptance.py', case['checks'])
                check = backend.execute('python /projects/task/acceptance.py', timeout=20)
                record['passed'], record['test_exit_code'] = check.exit_code == 0, check.exit_code
                record['test_output'] = check.output[:2000]
        except Exception as exc:
            from agent.security import redact
            record['error'] = redact(str(exc))[:2000]
        record['seconds'] = round(time.monotonic() - started, 2)
        records.append(record)
        print(json.dumps(record, ensure_ascii=True), flush=True)
        report = {'model': os.environ.get('MAIN_MODEL'), 'image': args.image, 'suite_sha256': suite_hash,
            'review_output': args.review_output,
            'context_mode': args.context_mode, 'scope': 'Fixed synthetic fixtures; review precision/recall measure locations, not semantic correctness.',
            'cases': records, 'summary': aggregate(records)}
        if args.baseline and len(records) == len(cases):
            report['baseline_delta'] = compare(report, json.loads(args.baseline.read_text(encoding='utf-8')))
        (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Report:', root / 'report.json')
    raise SystemExit(0 if all(r['passed'] for r in records) else 1)


if __name__ == '__main__':
    main()
