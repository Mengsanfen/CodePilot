"""Pinned review -> human confirmation -> repair -> executable checks -> independent re-review."""
import json
from agent.observability import stage
from agent.orchestration.review_evidence import review_scope, finalize, verify_once, read, matches, write
from agent.orchestration.store import RunConflict
from agent.tools.review_context import diff_snapshot


def validate_repair_snapshot(root, spec):
    snapshot = diff_snapshot(root)
    if snapshot['digest'] != spec['digest'] or snapshot['head'] != spec['head_sha']:
        raise RunConflict('工作区已偏离人工确认的审查快照，请重新审查后再修复')


def run_flow(store, run, spec, root):
    from agent.core import runtime
    from agent.server import ensure_backend_for_thread, readonly_subagent
    from agent.core.model import make_main_model
    from agent.backends.docker import DockerBackend
    backend = ensure_backend_for_thread(run['thread_id'])
    repair = spec['kind'] == 'repair'
    kind = 'coding' if repair else 'analysis'
    if repair and not isinstance(backend, DockerBackend):
        raise RuntimeError('确认修复流程要求 Docker 执行后端')
    if repair and not run['resume_checkpoint']:
        validate_repair_snapshot(root, spec)
    instruction = ('只修复以下用户确认的 findings；必要的测试改动可以一并实施。不要提交或发布。\n'
                   + json.dumps(spec['findings'], ensure_ascii=False) + '\n验收命令：' + spec['verification_command']
                   if repair else '审查已准备好的 PR head。先调用 review_local_diff，按需读取源代码；调用 add_review_finding 记录真实缺陷。若无缺陷明确说明，不编造。')
    store.add_thread_message(message_id='user:' + run['run_id'], thread_id=run['thread_id'],
        run_id=run['run_id'], author='user', content=run['prompt'], metadata={'task_kind': kind})
    agent = runtime._build_agent_for_runtime(thread_id=run['thread_id'], task_kind=kind, repo_url=run['repo_url'])
    with stage('repair.implement' if repair else 'review.analyze'), review_scope(None if repair else 'review.analyze'):
        content = f"仓库路径：/projects/{root.name}\n{instruction}"
        if run['resume_checkpoint']:
            state = agent.get_state({'configurable': {'thread_id': run['thread_id']}})
            if not state or not state.values:
                raise RunConflict('没有可恢复的审查/修复 Checkpoint')
            if state.next:
                result = runtime.run_agent_with_event_stream(agent=agent, thread_id=run['thread_id'], content=None, task_kind=kind)
            else:
                result = {'messages': [runtime._message_to_dict(m) for m in state.values.get('messages', [])]}
        else:
            result = runtime.run_agent_with_event_stream(agent=agent, thread_id=run['thread_id'], content=content, task_kind=kind)
        answer = runtime._extract_final_assistant_text(result['messages'])
        store.add_thread_message(message_id='answer:' + run['run_id'], thread_id=run['thread_id'],
            run_id=run['run_id'], author='agent', content=answer, metadata={'task_kind': kind})
    if repair:
        verification = verify_once(store, run, root, backend, spec['verification_command'])
        previous = read(store, run['run_id'], 'report')
        if matches(previous, diff_snapshot(root)) and previous.get('outcome') != 'incomplete' and previous.get('phase') == 'repair.re_review' and previous.get('verification') == verification:
            store.append_event(run['run_id'], run['lease_token'], 'review.reused', {'digest': previous['digest']})
        else:
            write(store, run, 'repair.re_review.read', {})
            with stage('repair.re_review'), review_scope('repair.re_review'):
                reviewer = readonly_subagent(backend, make_main_model(), reviewer=True)['runnable']
                reviewed = reviewer.invoke({'messages': [{'role': 'user', 'content':
                    f'先调用 review_local_diff，独立复审 /projects/{root.name} 当前 diff，调用 add_review_finding 记录剩余缺陷，并解释已确认问题是否解决：\n' + json.dumps(spec['findings'], ensure_ascii=False)}]},
                    config={'configurable': {'thread_id': run['thread_id'], 'repo_url': run['repo_url'], 'task_kind': 'analysis'}, 'recursion_limit': 40})
                text = runtime._message_content_to_text(reviewed['messages'][-1].content)
                store.add_thread_message(message_id='rereview:' + run['run_id'], thread_id=run['thread_id'],
                    run_id=run['run_id'], author='agent', content='独立复审：\n' + text, metadata={'task_kind': 'analysis'})
                finalize(store, run, diff_snapshot(root), 'repair.re_review', text, verification)
    else:
        finalize(store, run, diff_snapshot(root), 'review.analyze', answer)
    from agent.core.repo_memory import save_task_memory
    save_task_memory(store, run, root)
