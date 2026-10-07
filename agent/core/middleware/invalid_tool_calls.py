"""Keep the tool protocol balanced when a provider emits malformed JSON arguments."""
from langchain.agents.middleware.types import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage
from agent.orchestration.context import durable_event


class InvalidToolCallMiddleware(AgentMiddleware):
    @hook_config(can_jump_to=['model'])
    def after_model(self, state, runtime):
        last = state['messages'][-1]
        if not isinstance(last, AIMessage) or not last.invalid_tool_calls:
            return None
        replies = []
        for call in last.invalid_tool_calls:
            if not call.get('id'):
                raise ValueError('模型返回缺少 ID 的非法工具调用，无法安全恢复协议')
            replies.append(ToolMessage(tool_call_id=call['id'], name=call.get('name') or 'invalid',
                content='工具没有执行：参数不是合法 JSON。请修正 JSON 和必填字段后重新调用；不要重复已成功的工具。', status='error'))
        durable_event('model.invalid_tool_calls', {'count': len(replies)})
        # Valid calls, if any, still go through ToolNode. With only invalid calls,
        # return to the model under the existing ModelCallLimitMiddleware budget.
        return {'messages': replies, **({'jump_to': 'model'} if not last.tool_calls else {})}
