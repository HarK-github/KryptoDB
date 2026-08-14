FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .
CMD ["python", "-m", "synapse.daemon.mcp_server"]
