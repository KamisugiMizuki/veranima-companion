"""联网节假日日历：Nager.Date JSON + 本地周末降级。"""
from __future__ import annotations

import datetime as dt
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass


@dataclass(frozen=True)
class CalendarDay:
    date: dt.date
    day_type: str
    name: str = ""
    source: str = "local_weekday"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HolidayCalendar:
    def __init__(self, base_url: str = "https://date.nager.at/api/v3/PublicHolidays",
                 country_code: str = "CN", timeout: float = 8, cache_ttl: float = 86400):
        self.base_url = base_url.rstrip("/")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "https" or parsed.hostname not in {"date.nager.at"}:
            raise ValueError("calendar base_url must use https://date.nager.at")
        self.country_code = country_code.upper()
        self.timeout = max(1.0, float(timeout))
        self.cache_ttl = max(0.0, float(cache_ttl))
        self._cache: dict[int, tuple[float, dict[str, str]]] = {}

    def day(self, value: dt.date) -> CalendarDay:
        holidays = self._year(value.year)
        name = holidays.get(value.isoformat(), "")
        if name:
            return CalendarDay(value, "holiday_like", name, "online_calendar")
        if value.weekday() >= 5:
            return CalendarDay(value, "rest_like", "周末", "local_weekday")
        return CalendarDay(value, "baseline", "", "local_weekday")

    def prefetch(self, year: int) -> None:
        self._year(int(year))

    def _year(self, year: int) -> dict[str, str]:
        cached = self._cache.get(int(year))
        if cached and time.time() - cached[0] <= self.cache_ttl:
            return cached[1]
        url = f"{self.base_url}/{int(year)}/{self.country_code}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "veranima/1.0"})
            opener = urllib.request.build_opener(_NoRedirect)
            with opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    return {}
                payload = json.loads(response.read(512 * 1024).decode("utf-8"))
            result = {
                str(item["date"]): str(item.get("localName") or item.get("name") or "节假日")
                for item in payload if isinstance(item, dict) and item.get("date")
            }
            self._cache[int(year)] = (time.time(), result)
            return result
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.HTTPError):
            return {}
