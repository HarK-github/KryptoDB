from sentence_transformers import SentenceTransformer
import os

class EmbeddingService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EmbeddingService, cls).__new__(cls)
            cls._instance.model = None
        return cls._instance

    def get_model(self):
        if self.model is None:
            # We use a very lightweight model for testing/speed
            self.model = SentenceTransformer('all-MiniLM-L6-v2')
        return self.model

    def encode(self, text: str) -> list[float]:
        model = self.get_model()
        # Returns a numpy array, convert to list of floats for Neo4j
        return model.encode(text).tolist()

embedding_service = EmbeddingService()
