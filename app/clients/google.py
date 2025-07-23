"""
Google Client for interacting with multiple Google services.
"""

import os
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from utils.secrets import VaultSecretsLoader

# Load environment variables from .env file
load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    # Add other scopes as needed for different services
]


class GoogleClient:
    """
    A client for interacting with Google services using a service account.
    Attributes:
        credentials (Credentials): Google service account credentials.
    Methods:
        __init__: Initializes the GoogleClient with credentials.
        get_calendar_service: Returns the Google Calendar service instance.
    """

    def __init__(self):
        self.credentials = VaultSecretsLoader().load_secret(
            "google-credentials"
        ) or os.getenv("GOOGLE_CREDENTIALS", None)

        if self.credentials is None:
            raise ValueError(
                "Google credentials not found. Please set GOOGLE_CREDENTIALS environment variable or use Vault secrets."
            )

        self.credentials["private_key"] = self.credentials.get(
            "private_key", ""
        ).replace("\\n", "\n")

        if isinstance(self.credentials, str):
            self.credentials = service_account.Credentials.from_service_account_file(
                self.credentials, scopes=SCOPES
            )
        elif isinstance(self.credentials, dict):
            self.credentials = service_account.Credentials.from_service_account_info(
                self.credentials, scopes=SCOPES
            )
        else:
            raise ValueError(
                "Invalid Google credentials format. Must be a file path or a dictionary."
            )

    def get_calendar_service(self):
        """
        Returns the Google Calendar service instance.

        Returns:
            Resource: Google Calendar service instance.
        """
        return build("calendar", "v3", credentials=self.credentials)
