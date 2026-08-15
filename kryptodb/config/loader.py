import tomllib
from pathlib import Path
from kryptodb.config.schema import AgentsSchema

class ConfigProvider:
    def __init__(self, agents_path: Path):
        self.agents_path = agents_path
        self._cache = None
        
    def load(self) -> AgentsSchema:
        if not self.agents_path.exists():
            return AgentsSchema(agents={})
            
        with open(self.agents_path, "rb") as f:
            try:
                data = tomllib.load(f)
                self._cache = AgentsSchema.model_validate(data)
                return self._cache
            except Exception:
                return AgentsSchema(agents={})
                
    def get_agent(self, agent_id: str):
        config = self._cache or self.load()
        return config.agents.get(agent_id)
