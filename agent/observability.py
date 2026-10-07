"""Cross-process W3C trace context, local JSONL export and durable stage evidence."""
from __future__ import annotations
import os
import json
import time
from contextlib import contextmanager
from functools import lru_cache


@lru_cache(maxsize=1)
def tracer():
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
    class JsonlExporter(SpanExporter):
        def export(self, spans):
            from agent.core.settings import LOG_DIR
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with (LOG_DIR / f'traces-{os.getpid()}.jsonl').open('a', encoding='utf-8') as out:
                for span in spans:
                    # Only code-supplied attributes; never export prompts, results or credentials.
                    out.write(json.dumps({'name': span.name, 'trace_id': format(span.context.trace_id, '032x'),
                        'span_id': format(span.context.span_id, '016x'),
                        'parent_id': format(span.parent.span_id, '016x') if span.parent else None,
                        'start_ns': span.start_time, 'end_ns': span.end_time,
                        'status': span.status.status_code.name, 'attributes': dict(span.attributes)}, ensure_ascii=False) + '\n')
            return SpanExportResult.SUCCESS
    provider = TracerProvider(resource=Resource.create({'service.name': os.environ.get('OTEL_SERVICE_NAME', 'codepilot')}))
    provider.add_span_processor(SimpleSpanProcessor(JsonlExporter()))
    if os.environ.get('CODEPILOT_OTEL_CONSOLE', '').lower() == 'true':
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    return trace.get_tracer('codepilot')


def trace_carrier():
    from opentelemetry.propagate import inject
    carrier = {}
    inject(carrier)
    return carrier


@contextmanager
def run_span(run_id: str, attempt: int, carrier=None):
    from opentelemetry.propagate import extract
    with tracer().start_as_current_span('agent.run', context=extract(carrier or {}), attributes={
            'codepilot.run_id': run_id, 'codepilot.attempt': attempt}) as span:
        yield span


@contextmanager
def stage(name: str):
    from agent.orchestration.context import durable_event
    from opentelemetry.trace import Status, StatusCode
    started = time.monotonic()
    with tracer().start_as_current_span(name, record_exception=False, set_status_on_exception=False) as span:
        fields = {'stage': name, 'trace_id': format(span.get_span_context().trace_id, '032x'),
                  'span_id': format(span.get_span_context().span_id, '016x')}
        durable_event('stage.started', fields)
        try:
            yield span
        except BaseException as exc:
            span.set_status(Status(StatusCode.ERROR))
            durable_event('stage.failed', {**fields, 'error_type': type(exc).__name__,
                                          'seconds': round(time.monotonic() - started, 3)})
            raise
        else:
            durable_event('stage.completed', {**fields, 'seconds': round(time.monotonic() - started, 3)})
