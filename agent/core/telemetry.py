"""Per-call model usage, including nested agents and failed calls, without prompt logging."""
from langchain_core.callbacks import BaseCallbackHandler
from agent.orchestration.context import durable_event


class ModelTelemetry(BaseCallbackHandler):
    def on_llm_end(self, response, *, run_id, **kwargs):
        usage = {}
        for batch in response.generations:
            for generation in batch:
                usage = getattr(getattr(generation, 'message', None), 'usage_metadata', None) or usage
        durable_event('model.usage', {'call_id': str(run_id),
            'input_tokens': usage.get('input_tokens', 0), 'output_tokens': usage.get('output_tokens', 0),
            'usage_available': bool(usage)})

    def on_llm_error(self, error, *, run_id, **kwargs):
        durable_event('model.failed', {'call_id': str(run_id), 'error_type': type(error).__name__})
