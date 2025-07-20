# services/resume_reviewer.py
import os
from dotenv import load_dotenv
import cohere
from utils import VaultSecretsLoader

load_dotenv()


class ResumeReviewService:
    def __init__(self, model: str = "command-r-plus"):
        token = VaultSecretsLoader().load_secret("cohere-api-key") or os.getenv(
            "COHERE_API_KEY"
        )
        if not token:
            raise ValueError(
                "Cohere API key not found. Set COHERE_API_KEY environment variable or use Vault secrets."
            )

        self.client = cohere.Client(token)
        self.model = model

    def generate_feedback(self, prompt: str) -> str:
        response = self.client.generate(
            model=self.model, prompt=prompt, temperature=0.4
        )
        return response.generations[0].text.strip()
