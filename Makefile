.PHONY: init start stop logs

init:
	@echo "Setting up virtual environment and installing dependencies..."
	@if [ ! -d ".venv" ]; then python3 -m venv .venv; fi
	@.venv/bin/pip install -e .

start:
	@echo "Booting storage layer (Neo4j & Redis)..."
	@docker-compose up -d
	@echo "Starting KryptoDB Daemon in the background..."
	@.venv/bin/uvicorn kryptodb.daemon.server:app --port 8000 & echo $$! > .daemon.pid
	@echo "KryptoDB is ready! You can now use the CLI or connect your MCP client."

stop:
	@echo "Stopping KryptoDB Daemon..."
	@if [ -f .daemon.pid ]; then kill `cat .daemon.pid` || true; rm .daemon.pid; fi
	@echo "Stopping storage layer..."
	@docker-compose down

logs:
	@docker-compose logs -f
