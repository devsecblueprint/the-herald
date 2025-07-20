"""
Vault Secrets Loader Module
This module provides a class to load secrets from Vault Agent-injected files.
"""

import os


class VaultSecretsLoader:
    """
    A class to load secrets from Vault Agent-injected files. It reads the content of a specified secret file and returns its content.
    The secrets are expected to be in a key-value format, where each line contains a key-value pair separated by an equals sign.
    Attributes:
        secret_path (str): The base path where Vault secrets are injected.
    Methods:
        load_secret(secret_file_name: str): Loads the secret from the Vault-injected file.
        _load_secret_file(filename: str): Reads the content of a secret file and returns it
    """

    def __init__(self, secret_path="/vault/secrets"):
        """
        Initializes the VaultSecretsLoader.

        Args:
            secret_path (str): The base path where Vault secrets are injected.
        """
        self.secret_path = secret_path

    def load_secret(self, secret_file_name: str):
        """
        Loads the secret from the Vault-injected file.

        Returns:
            str: The Redis password, or None if the file is not found.
        """
        return self._load_secret_file(secret_file_name)

    def _load_secret_file(self, filename):
        """
        Loads the content of a secret file.

        Args:
            filename (str): The name of the secret file to load.

        Returns:
            str: The content of the secret file, or None if the file is not found.
        """
        file_path = os.path.join(self.secret_path, filename)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if "=" in content:
                    # Read key value pairs from every line in the file
                    # and return as a dictionary
                    return dict(
                        line.split("=", 1) for line in content.splitlines() if line
                    )
                return content
        except FileNotFoundError:
            print(
                f"Secret file '{filename}' not found at path '{file_path}'. Is Vault Agent Injector configured?"
            )
            return None
