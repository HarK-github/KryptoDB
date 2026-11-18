"""OpenTelemetry tracer setup for KryptoDB."""

from opentelemetry import trace

tracer = trace.get_tracer("kryptodb")
