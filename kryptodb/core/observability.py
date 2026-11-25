"""OpenTelemetry tracer setup for KryptoDB."""

try:
    from opentelemetry import trace
except ImportError:
    class _NoOpSpan:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def set_attribute(self, *args, **kwargs): pass
        def record_exception(self, *args, **kwargs): pass

    class _NoOpTracer:
        def start_as_current_span(self, *args, **kwargs): return _NoOpSpan()

    class _NoOpTrace:
        @staticmethod
        def get_tracer(*args, **kwargs): return _NoOpTracer()

    trace = _NoOpTrace()  # type: ignore


tracer = trace.get_tracer("kryptodb")
