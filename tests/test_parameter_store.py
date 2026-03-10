"""
Simple test to validate Parameter Store client implementation.
This is a basic validation script, not a full unit test suite.
"""

import logging
from app.clients.parameter_store import ParameterStoreClient

# Configure logging
logging.basicConfig(level=logging.INFO)


def test_parameter_store_client():
    """Test basic Parameter Store client functionality."""

    print("Testing Parameter Store Client...")

    # Test 1: Initialization
    print("\n1. Testing initialization...")
    client = ParameterStoreClient(prefix="/the-herald/test/", region_name="us-east-1")
    assert client.prefix == "/the-herald/test/"
    assert client.region_name == "us-east-1"
    assert len(client._cache) == 0
    print("✓ Initialization successful")

    # Test 2: Cache functionality
    print("\n2. Testing cache functionality...")
    assert len(client.get_cached_parameters()) == 0
    print("✓ Cache is initially empty")

    # Test 3: Clear cache
    print("\n3. Testing cache clearing...")
    client.clear_cache()
    assert len(client._cache) == 0
    print("✓ Cache cleared successfully")

    # Test 4: Method signatures
    print("\n4. Testing method signatures...")
    assert hasattr(client, "get_parameter")
    assert hasattr(client, "get_discord_token")
    assert hasattr(client, "get_guild_id")
    assert hasattr(client, "clear_cache")
    assert hasattr(client, "get_cached_parameters")
    print("✓ All required methods exist")

    print("\n✅ All basic tests passed!")
    print("\nNote: AWS API calls are not tested here.")
    print("To test AWS integration, ensure:")
    print("  1. AWS credentials are configured")
    print("  2. Parameters exist in Parameter Store:")
    print("     - /the-herald/test/discord-token (SecureString)")
    print("     - /the-herald/test/guild-id (String)")


if __name__ == "__main__":
    test_parameter_store_client()
