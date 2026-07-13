"""IST helpers — Streamlit Cloud runs the app server in UTC, but the
business (and everyone entering/reading dates and times) is in India.
Use these instead of date.today()/datetime.now() anywhere the result is
shown to a user or used as a default — plain today()/now() silently uses
server time, which runs ~5:30 behind IST and rolls over to a new
calendar day 5:30 hours before India does.
"""
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist():
    return datetime.now(timezone.utc).astimezone(IST)


def today_ist():
    return now_ist().date()
