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
        credentials = VaultSecretsLoader().load_secret(
            "google-credentials"
        ) or os.getenv("GOOGLE_CREDENTIALS")
        if credentials is None:
            raise ValueError(
                "Google credentials not found. Please set GOOGLE_CREDENTIALS environment variable or use Vault secrets."
            )
        if isinstance(credentials, str):
            credentials = service_account.Credentials.from_service_account_file(
                credentials, scopes=SCOPES
            )
        elif isinstance(credentials, dict):
            credentials = service_account.Credentials.from_service_account_info(
                credentials, scopes=SCOPES
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
