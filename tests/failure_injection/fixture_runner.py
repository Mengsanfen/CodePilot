"""Deterministic graph inside a real supervised runner; never invokes an LLM."""
from pathlib import Path
import json
import os
import sys
import threading
import time
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main(mode, folder):
    from agent.orchestration.store import RunStore
    store = RunStore(os.environ['CODEPILOT_DATABASE_URL'])
    if mode == 'worker':
        from agent.orchestration.worker import Worker
        Worker(store, lease_seconds=3, timeout=90,
               command=[sys.executable, str(Path(__file__).resolve()), 'runner', str(folder)]).run_once()
        store.close()
        return
    from langgraph.graph import StateGraph, START, END
    from agent.core.persistence import make_checkpointer
    from agent.orchestration.approvals import propose
    from agent.orchestration.runner import watchdog
    run_id, token = os.environ['CODEPILOT_RUN_ID'], os.environ['CODEPILOT_LEASE_TOKEN']
    run = store.assert_lease(run_id, token)
    stop = threading.Event()
    watcher_store = RunStore(store.db_path)
    watcher = threading.Thread(target=watchdog, args=(stop, watcher_store, run_id, token), daemon=True)
    watcher.start()
    saver = make_checkpointer(folder / 'checkpoint.sqlite')
    class State(TypedDict):
        prepared: bool
        result: str
    def prepare(state):
        store.assert_lease(run_id, token)
        with (folder / 'preparations.txt').open('a', encoding='utf-8') as receipt:
            receipt.write('prepared\n')
        propose(store, run_id, token, 'comment_pr', {'body': 'fault fixture; never published'})
        return {'prepared': True}
    def work(state):
        receipt = folder / f"ready-{run['attempt']}.json"
        temporary = receipt.with_suffix('.tmp')
        temporary.write_text(json.dumps({'runner_pid': os.getpid(), 'owner': run['owner'],
                                        'lease_token': token}), encoding='utf-8')
        temporary.replace(receipt)
        while not (folder / 'finish').exists():
            store.assert_lease(run_id, token)
            time.sleep(.05)
        store.append_event(run_id, token, 'fixture.graph.completed', {'prepared': state['prepared']})
        return {'result': 'done'}
    graph = StateGraph(State)
    graph.add_node('prepare', prepare)
    graph.add_node('work', work)
    graph.add_edge(START, 'prepare')
    graph.add_edge('prepare', 'work')
    graph.add_edge('work', END)
    agent = graph.compile(checkpointer=saver)
    config = {'configurable': {'thread_id': run['thread_id']}}
    try:
        store.set_context(run_id, token, {'task_kind': 'failure_fixture', 'checkpoint': 'persistent'})
        if run['resume_checkpoint']:
            assert agent.get_state(config).next == ('work',), 'Resume must reuse the completed prepare node'
        result = agent.invoke(None if run['resume_checkpoint'] else {'prepared': False, 'result': ''}, config)
        assert result['result'] == 'done'
    finally:
        stop.set()
        watcher.join(2)
        watcher_store.close()
        saver.conn.close()
        store.close()


if __name__ == '__main__':
    main(sys.argv[1], Path(sys.argv[2]))
