"""
# app/services/calendar.py
This module provides a service for managing Google Calendar events.
It includes methods to add, remove, update, and list events in a Google Calendar.
It uses the Google Calendar API to interact with the calendar.
"""

import os
from datetime import datetime, timedelta
from clients.google import GoogleClient
from config.logger import LoggerConfig
from services.discord import DiscordService


class GoogleCalendarService:
    """
    Service for managing Google Calendar events.
    This service provides methods to add, remove, update, and list events in a Google Calendar.
    It uses the Google Calendar API to interact with the calendar.
    Attributes:
        discord_service (DiscordService): Service for interacting with Discord events.
        service (Resource): Google Calendar service instance.
        logger (LoggerConfig): Logger instance for logging events and errors.
        calendar_id (str): The ID of the calendar to interact with.
    Methods:
        add_event: Adds a 1-hour event to the calendar.
        remove_event: Removes an event from the calendar by its ID.
        update_event: Updates an existing event in the calendar.
        list_events: Lists events in the calendar.
        sync_discord_events: Syncs scheduled events from Discord and adds them to the Google Calendar.
    """

    def __init__(self):
        self.discord_service = (
            DiscordService()
        )  # Assuming DiscordService is defined elsewhere
        self.service = GoogleClient().get_calendar_service()
        self.logger = LoggerConfig(__name__).get_logger()
        self.calendar_id = os.getenv(
            "CALENDAR_ID", "primary"
        )  # Default to primary calendar if not set

    def add_event(self, summary, start_time, description=None, location=None) -> dict:
        """
        Adds a 1-hour event to the calendar.

        :param summary: Event title
        :param start_time: Datetime object (UTC)
        :param description: Optional event description
        :param location: Optional event location
        """
        end_time = start_time + timedelta(hours=1)
        event = {
            "summary": summary,
            "description": description,
            "location": location,
            "start": {
                "dateTime": start_time.isoformat(),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": end_time.isoformat(),
                "timeZone": "UTC",
            },
        }
        created_event = (
            self.service.events()
            .insert(calendarId=self.calendar_id, body=event)
            .execute()
        )

        self.logger.info(f"Event created: {created_event.get('htmlLink')}")
        return created_event

    def remove_event(self, event_id) -> None:
        """
        Removes an event from the calendar by its ID.

        :param event_id: The ID of the event to remove
        """
        self.logger.info(f"Removing event with ID: {event_id}")
        self.service.events().delete(
            calendarId=self.calendar_id, eventId=event_id
        ).execute()

    def update_event(
        self, event_id, start_time, summary=None, description=None, location=None
    ) -> None:
        """
        Updates an existing event in the calendar.

        :param event_id: The ID of the event to update
        :param summary: New event title
        :param start_time: New start time as a datetime object (UTC)
        :param description: New event description
        :param location: New event location
        """
        self.logger.info(f"Updating event with ID: {event_id}")
        if start_time is None:
            raise ValueError("Start time must be provided for updating an event.")

        event = (
            self.service.events()
            .get(calendarId=self.calendar_id, eventId=event_id)
            .execute()
        )

        if summary:
            event["summary"] = summary
        if start_time:
            end_time = start_time + timedelta(hours=1)
            event["start"] = {
                "dateTime": start_time.isoformat(),
                "timeZone": "UTC",
            }
            event["end"] = {
                "dateTime": end_time.isoformat(),
                "timeZone": "UTC",
            }
        if description:
            event["description"] = description
        if location:
            event["location"] = location

        updated_event = (
            self.service.events()
            .update(calendarId=self.calendar_id, eventId=event_id, body=event)
            .execute()
        )

        self.logger.info(f"Event updated: {updated_event.get('htmlLink')}")

        return updated_event

    def list_events(self, time_min=None, time_max=None, max_results=10) -> list:
        """
        Lists events in the calendar.

        :param time_min: Minimum time for events (datetime object, optional)
        :param time_max: Maximum time for events (datetime object, optional)
        :param max_results: Maximum number of results to return
        :return: List of events
        """
        if time_min is None:
            time_min = datetime.utcnow()
        if time_max is None:
            time_max = datetime.utcnow() + timedelta(days=90)

        events_result = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=time_min.strftime("%Y-%m-%dT%H:%M:%SZ"),
                timeMax=time_max.strftime("%Y-%m-%dT%H:%M:%SZ"),
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = events_result.get("items", [])
        if not events:
            self.logger.info("No upcoming events found.")

        return events

    def sync_discord_events(self) -> None:
        """
        Syncs scheduled events from Discord and adds them to the Google Calendar.
        This method should be called periodically to ensure the calendar is up-to-date.
        """
        discord_events = self.discord_service.list_scheduled_events()

        for event in discord_events:
            self.logger.info(f"Syncing Discord event: {event['name']}")

            start_time = datetime.fromisoformat(
                event["scheduled_start_time"].replace("Z", "+00:00")
            )

            existing_events = self.list_events(
                time_min=start_time - timedelta(minutes=5),  # small buffer
                time_max=start_time + timedelta(days=90)
            )
            matching_event = next(
                (e for e in existing_events if e.get("summary") == event["name"]),
                None
            )

            if not matching_event:
                # Event doesn't exist, create it
                self.add_event(
                    summary=event["name"],
                    start_time=start_time,
                    description=event.get("description"),
                    location=event.get("location"),
                )
                self.logger.info(f"Added new event: {event['name']} at {start_time.isoformat()}")
            else:
                # Check for any differences
                google_start = datetime.fromisoformat(
                    matching_event["start"]["dateTime"].replace("Z", "+00:00")
                )
                google_description = matching_event.get("description", "")
                google_location = matching_event.get("location", "")

                discord_description = event.get("description", "")
                discord_location = event.get("location", "")

                needs_update = (
                    google_start != start_time or
                    google_description != discord_description or
                    google_location != discord_location
                )

                if needs_update:
                    self.update_event(
                        event_id=matching_event["id"],
                        start_time=start_time,
                        summary=event["name"],
                        description=discord_description,
                        location=discord_location,
                    )
                    self.logger.info(f"Updated event: {event['name']} at {start_time.isoformat()}")
                else:
                    self.logger.info(f"No changes needed for event: {event['name']}")

        self.logger.info("Discord events synced with Google Calendar.")