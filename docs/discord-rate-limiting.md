# Discord Rate Limiting Improvements

## Overview

The Discord service has been updated to handle rate limiting (HTTP 429 errors) more gracefully. This document explains the improvements made to prevent and handle rate limiting issues.

## Changes Made

### 1. Added Rate Limiting Handler

A new method `_make_request_with_retry()` has been added to the `DiscordService` class that:

- **Automatically retries requests** when rate limited (HTTP 429)
- **Respects Discord's Retry-After header** when provided
- **Uses exponential backoff** when no retry-after header is present
- **Adds small delays** between requests to prevent rapid-fire API calls
- **Logs detailed information** about rate limiting events

### 2. Updated All API Calls

All methods that make requests to Discord's API now use the new retry mechanism:

- `get_channel_id()`
- `check_messages_in_discord()`
- `send_message_to_channel()`
- `list_scheduled_events()`
- `list_scheduled_events_and_notify()`
- `_send_dm()`

### 3. Error Handling

The service now properly handles:

- **429 Too Many Requests**: Automatic retry with proper delays
- **Network timeouts**: Retry with exponential backoff
- **Connection errors**: Graceful error logging and retry
- **Invalid responses**: Proper error propagation

### 4. Dependencies

Added `ratelimit==2.2.1` to requirements.txt for additional rate limiting capabilities if needed in the future.

## Usage

The rate limiting improvements are transparent to existing code. No changes are needed to existing method calls:

```python
discord_service = DiscordService()

# This will automatically handle rate limiting
events = discord_service.list_scheduled_events()

# This will also handle rate limiting with retries
channel_id = discord_service.get_channel_id("general")
```

## Rate Limiting Behavior

- **Base delay**: 0.1 seconds before each request
- **Max retries**: 5 attempts per request
- **Exponential backoff**: 1, 2, 4, 8, 16 seconds
- **Retry-After respect**: When Discord provides a retry-after header, it will be used instead of exponential backoff

## Monitoring

All rate limiting events are logged with appropriate severity:

- **Warning**: When rate limited and retrying
- **Error**: When requests fail after all retries
- **Info**: Successful request completions

## Testing

Use the `test_rate_limiting.py` script to verify the rate limiting functionality works correctly in your environment.

## Discord API Limits

Discord has several rate limits:

- **Global rate limit**: 50 requests per second
- **Per-route rate limits**: Vary by endpoint
- **Per-guild rate limits**: Some endpoints have guild-specific limits

The improved service handles all these automatically with appropriate backoff strategies.
