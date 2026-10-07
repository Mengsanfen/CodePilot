"""Verify actual LangGraph checkpoint continuation across graph reconstruction."""
import sqlite3
import pytest
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver


class State(TypedDict):
    prepared: bool
    result: str


def test_checkpoint_resume_does_not_repeat_completed_node(tmp_path):
    effects = []
    fail = True
    def prepare(state):
        effects.append('prepared')
        return {'prepared': True}
    def work(state):
        if fail:
            raise RuntimeError('simulated worker interruption')
        return {'result': 'done'}
    def build(saver):
        graph = StateGraph(State)
        graph.add_node('prepare', prepare)
        graph.add_node('work', work)
        graph.add_edge(START, 'prepare')
        graph.add_edge('prepare', 'work')
        graph.add_edge('work', END)
        return graph.compile(checkpointer=saver)
    path = tmp_path / 'checkpoint.sqlite'
    config = {'configurable': {'thread_id': 'resume-fixture'}}
    with sqlite3.connect(path, check_same_thread=False) as connection:
        agent = build(SqliteSaver(connection))
        with pytest.raises(RuntimeError):
            agent.invoke({'prepared': False, 'result': ''}, config)
        assert agent.get_state(config).next == ('work',)
    fail = False
    with sqlite3.connect(path, check_same_thread=False) as connection:
        restarted = build(SqliteSaver(connection))
        result = restarted.invoke(None, config)
        assert result['result'] == 'done'
    assert effects == ['prepared']
