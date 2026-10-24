"""Calendar files (RFC 5545) and "Add to Google Calendar" links, without OAuth.

An event here is a dict: kind ("pass", "expiry", "follow_up"), title, start and end (a datetime for a timed
event, a date for an all-day one), description, uid. The .ics carries the description (a supplier letter
included: the file stays with whoever downloads it); the Google link carries only the title and the dates,
never the description, so no name or letter ever travels in a URL.

Written by hand, with no dependency: CRLF line ends, lines folded at 75 octets (never inside a UTF-8
character), TEXT escaped (backslash, semicolon, comma, newline), DTSTAMP on every event, times in UTC.
tests/test_geo_workflow.py parses every file with the icalendar package.
"""
from datetime import date, datetime, timezone
from typing import Any, Iterable
from urllib.parse import quote, urlencode

PRODID = "-//GreenTrace//Geo workflow//EN"
GOOGLE = "https://calendar.google.com/calendar/render"


def stamp(value: date | datetime) -> str:
    """The iCalendar and Google form of a date (20261231) or a UTC time (20261231T104112Z)."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return value.strftime("%Y%m%d")


def google_url(event: dict[str, Any]) -> str:
    """https://calendar.google.com/calendar/render?action=TEMPLATE&text=...&dates=start/end: title and dates only."""
    query = {"action": "TEMPLATE", "text": event["title"], "dates": f"{stamp(event['start'])}/{stamp(event['end'])}"}
    return f"{GOOGLE}?{urlencode(query, safe='/', quote_via=quote)}"


def escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """One content line folded at 75 octets: CRLF and a space before each continuation."""
    out, current, size = [], "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > (75 if not out else 74):  # a continuation starts with one space
            out.append(current)
            current, size = "", 0
        current += char
        size += width
    out.append(current)
    return "\r\n ".join(out)


def calendar(events: Iterable[dict[str, Any]], now: datetime, name: str) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{escape(name)}"]
    for event in events:
        timed = isinstance(event["start"], datetime)
        kind = "" if timed else ";VALUE=DATE"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{event['uid']}",
            f"DTSTAMP:{stamp(now)}",
            f"DTSTART{kind}:{stamp(event['start'])}",
            f"DTEND{kind}:{stamp(event['end'])}",
            f"SUMMARY:{escape(event['title'])}",
            f"DESCRIPTION:{escape(event.get('description') or '')}",
            f"CATEGORIES:{escape(event['kind'])}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)
