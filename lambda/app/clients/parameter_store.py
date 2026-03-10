"""
Parameter Store client for retrieving secrets.

This module provides a client for retrieving secrets from AWS Systems Manager Parameter Store.
It implements caching for Lambda execution context to minimize API calls and improve performance.
"""

import logging
from typing import Optional, Dict
import boto3
from botocore.exceptions import ClientError, BotoCoreError


logger = logging.getLogger(__name__)


class ParameterStoreClient:
    """
    Parameter Store client for retrieving and caching secrets.

    This client retrieves secrets from AWS Systems Manager Parameter Store
    and caches them for the duration of the Lambda execution context.
    """

    def __init__(self, prefix: str = "/the-herald/prod/", region_name: str = None):
        """
        Initialize the Parameter Store client.

        Args:
            prefix: Prefix for Parameter Store keys (e.g., "/the-herald/prod/")
            region_name: AWS region name (default: None, uses AWS_REGION env var or boto3 default)
        """
        self.prefix = prefix
        self.region_name = region_name
        self.ssm_client = boto3.client("ssm", region_name=region_name)
        self._cache: Dict[str, str] = {}
        logger.info(f"Initialized Parameter Store client with prefix: {prefix}")

    def get_parameter(self, parameter_name: str, decrypt: bool = True) -> Optional[str]:
        """
        Retrieve a parameter from Parameter Store with caching.

        Args:
            parameter_name: Name of the parameter (without prefix)
            decrypt: Whether to decrypt SecureString parameters (default: True)

        Returns:
            Parameter value if found, None otherwise

        Raises:
            ValueError: If parameter retrieval fails critically
        """
        # Check cache first
        cache_key = f"{self.prefix}{parameter_name}"
        if cache_key in self._cache:
            logger.debug(f"Retrieved parameter from cache: {cache_key}")
            return self._cache[cache_key]

        # Retrieve from Parameter Store
        full_parameter_name = f"{self.prefix}{parameter_name}"

        try:
            response = self.ssm_client.get_parameter(
                Name=full_parameter_name, WithDecryption=decrypt
            )

            value = response["Parameter"]["Value"]

            # Cache the value
            self._cache[cache_key] = value

            logger.info(f"Retrieved and cached parameter: {full_parameter_name}")
            return value

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")

            if error_code == "ParameterNotFound":
                logger.error(
                    f"Parameter not found: {full_parameter_name}. "
                    f"Ensure the parameter exists in Parameter Store."
                )
            else:
                logger.error(
                    f"ClientError retrieving parameter {full_parameter_name}: "
                    f"{error_code} - {e}",
                    exc_info=True,
                )

            raise ValueError(
                f"Failed to retrieve parameter {full_parameter_name}: {error_code}"
            ) from e

        except BotoCoreError as e:
            logger.error(
                f"BotoCoreError retrieving parameter {full_parameter_name}: {e}",
                exc_info=True,
            )
            raise ValueError(
                f"Failed to retrieve parameter {full_parameter_name}: BotoCoreError"
            ) from e

        except Exception as e:
            logger.error(
                f"Unexpected error retrieving parameter {full_parameter_name}: {e}",
                exc_info=True,
            )
            raise ValueError(
                f"Failed to retrieve parameter {full_parameter_name}: Unexpected error"
            ) from e

    def get_discord_token(self) -> str:
        """
        Retrieve the Discord bot token from Parameter Store.

        Returns:
            Discord bot token

        Raises:
            ValueError: If token retrieval fails
        """
        token = self.get_parameter("discord-token", decrypt=True)
        if not token:
            raise ValueError("Discord token is empty or None")
        return token

    def get_guild_id(self) -> str:
        """
        Retrieve the Discord guild ID from Parameter Store.

        Returns:
            Discord guild ID

        Raises:
            ValueError: If guild ID retrieval fails
        """
        guild_id = self.get_parameter("guild-id", decrypt=False)
        if not guild_id:
            raise ValueError("Discord guild ID is empty or None")
        return guild_id

    def clear_cache(self) -> None:
        """
        Clear the parameter cache.

        This is useful for testing or forcing a refresh of parameters.
        """
        self._cache.clear()
        logger.info("Cleared parameter cache")

    def get_cached_parameters(self) -> Dict[str, str]:
        """
        Get all cached parameters (for debugging/testing).

        Returns:
            Dictionary of cached parameters
        """
        return self._cache.copy()
