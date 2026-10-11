import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from agent.benchmark import BenchmarkTask, Budget, summarize


def test_fixed_suite_has_versions_hidden_acceptance_and_bounded_budgets():
    suite = json.loads(Path('evals/repository-tasks.json').read_text(encoding='utf-8'))
    tasks = [BenchmarkTask.model_validate(row) for row in suite['tasks']]
    assert len(tasks) >= 20 and len({t.id for t in tasks}) == len(tasks)
    assert {t.type for t in tasks} == {'bugfix','feature','testgen','review','repoqa'}
    for task in tasks:
        assert task.base_sha == suite['repositories'][task.repo]['sha']
        for name in task.verification.hidden_files:
            assert Path(name).resolve().is_relative_to(Path('evals/hidden').resolve())
            assert Path(name).is_file()
        if task.type == 'testgen': assert task.mutants
    with pytest.raises(ValidationError):
        Budget(max_seconds=0, max_model_calls=10)


def test_review_delivery_failures_do_not_disappear_from_recall():
    records = [
        {'id':'a','type':'review','seconds':1,'passed':True,'delivered':True,'expected_defects':1,
         'true_positives':1, 'false_negatives':0, 'input_tokens':20, 'usage_complete':True},
        {'id':'b','type':'review','seconds':2,'passed':False,'delivered':False,'expected_defects':1,
         'false_negatives':1,'failure':'TIMEOUT','input_tokens':5,'usage_complete':False},
        {'id':'c','type':'review','seconds':3,'passed':False,'delivered':True,'expected_defects':0,
         'false_positives':1,'failure':'REVIEW_FALSE_POSITIVE','usage_complete':True},
    ]
    result = summarize(records)
    assert result['review_location_recall'] == .5
    assert result['review_location_precision'] == .5
    assert result['review_clean_case_false_positive_rate'] == 1
    assert result['review_delivery_rate'] == 2/3
    assert result['input_tokens'] == 25 and not result['usage_complete']
    assert result['review_valid_finding_rate'] is None
    assert result['p95_latency_seconds'] == 3 and result['median_latency_seconds'] == 2


def test_fixture_edits_fail_closed_on_version_drift(tmp_path):
    spec = importlib.util.spec_from_file_location('benchmark_runner', Path('scripts/benchmark.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    (tmp_path / 'source.py').write_text('old\nold\n')
    with pytest.raises(ValueError, match='uniquely'):
        module.edits(tmp_path, [{'path':'source.py','before':'old','after':'new'}])
    assert (tmp_path / 'source.py').read_text() == 'old\nold\n'
    with pytest.raises(ValueError, match='outside'):
        module.checked(tmp_path, '../escape.py')


@pytest.mark.parametrize('stop_at_budget', [True, False])
def test_graph_budget_allows_twenty_model_calls_but_not_twenty_one(tmp_path, stop_at_budget):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
    from agent.backends.docker import DockerBackend
    from scripts.benchmark import file_agent, agent_config
    class Model(FakeMessagesListChatModel):
        def bind_tools(self, *args, **kwargs):
            return self
    responses = [AIMessage(content='', tool_calls=[{'name': 'ls', 'args': {'path': '/projects'},
                  'id': str(i), 'type': 'tool_call'}]) for i in range(20)]
    if stop_at_budget:
        responses[-1] = AIMessage(content='done')
    model = Model(responses=responses)
    graph = file_agent(model, DockerBackend(tmp_path / 'sandbox'), 20)
    request = {'messages': [{'role': 'user', 'content': 'Fixture tool loop'}]}
    if stop_at_budget:
        result = graph.invoke(request, agent_config(20))
        assert result['messages'][-1].content == 'done'
        assert sum(isinstance(m, AIMessage) for m in result['messages']) == 20
    else:
        with pytest.raises(ModelCallLimitExceededError):
            graph.invoke(request, agent_config(20))
