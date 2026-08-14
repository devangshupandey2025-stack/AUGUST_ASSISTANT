from __future__ import annotations

import datetime
import unittest

from august.modules.calendar_module import IST, fetch_todays_events


class FakeCalendarList:
    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.kwargs = {}

    def list(self, **kwargs):
        self.kwargs = kwargs
        return self

    def execute(self) -> dict:
        return {"items": self.events}


class FakeCalendarService:
    def __init__(self, events: list[dict]) -> None:
        self.calendar_list = FakeCalendarList(events)

    def events(self) -> FakeCalendarList:
        return self.calendar_list


class CalendarModuleTests(unittest.TestCase):
    def test_fetch_todays_events_omits_timed_events_that_already_started(self) -> None:
        now = datetime.datetime(2026, 5, 3, 11, 30, tzinfo=IST)
        service = FakeCalendarService(
            [
                {"summary": "Old standup", "start": {"dateTime": "2026-05-03T09:00:00+05:30"}},
                {"summary": "Design review", "start": {"dateTime": "2026-05-03T14:00:00+05:30"}},
                {"summary": "Holiday", "start": {"date": "2026-05-03"}},
            ]
        )

        response = fetch_todays_events(now=now, service=service)

        self.assertNotIn("Old standup", response)
        self.assertIn("Design review at 02:00 PM", response)
        self.assertIn("Holiday today", response)
        self.assertIn("You have to do 2 events today", response)
        self.assertEqual(service.calendar_list.kwargs["timeMin"], now.isoformat())

    def test_fetch_todays_events_reports_no_more_events_when_only_past_timed_events_exist(self) -> None:
        now = datetime.datetime(2026, 5, 3, 11, 30, tzinfo=IST)
        service = FakeCalendarService(
            [
                {"summary": "Old standup", "start": {"dateTime": "2026-05-03T09:00:00+05:30"}},
            ]
        )

        response = fetch_todays_events(now=now, service=service)

        self.assertEqual(response, "You have no more events today.")


if __name__ == "__main__":
    unittest.main()
