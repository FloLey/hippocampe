"""Run guard and failure reports.

Every dream entry point runs through ``_guarded``: it takes the lock, checks the
API key, and turns a missing key or any exception into a written report rather
than a raw 500.
"""

from __future__ import annotations

import datetime
import logging
import os
import traceback

from wiki_server.store import write_files

from .config import DREAM_REPORTS_DIR, _dream_lock, _stamp

_log = logging.getLogger(__name__)


def _no_key_report(when: datetime.datetime, dry: bool) -> tuple[str, str]:
    day = _local_day(when)
    body = "ANTHROPIC_API_KEY is not set; cannot run the dream. Add it as a secret."
    suffix = "-dryrun" if dry else ""
    rel = f"{DREAM_REPORTS_DIR}/{day}{suffix}.md"
    report = f"# Dream {'dry-run' if dry else ''}, {_stamp(when)}\n\n{body}\n"
    write_files({rel: report}, f"dream: report {day}")
    return rel, report


def _error_report(when: datetime.datetime, dry: bool) -> tuple[str, str]:
    """Capture an unexpected failure into a report instead of crashing the
    request, so the user sees the cause and the dream never returns a raw 500."""
    day = _local_day(when)
    tb = traceback.format_exc()
    suffix = "-dryrun" if dry else ""
    rel = f"{DREAM_REPORTS_DIR}/{day}{suffix}.md"
    report = (
        f"# Dream {'dry-run' if dry else ''}, {_stamp(when)}\n\n"
        f"Le rêve a échoué. Trace technique :\n\n```\n{tb}\n```\n"
    )
    try:
        write_files({rel: report}, f"dream: error report {day}")
    except Exception:
        pass
    return rel, report


def _local_day(when: datetime.datetime) -> str:
    """The run's day in the scheduled zone. Reports are named by it, and the
    scheduler checks today's report by the same local day: naming them by the
    UTC day made a run just after local midnight invisible to that check, so the
    overflow trigger fired again every poll until UTC caught up."""
    try:
        from zoneinfo import ZoneInfo

        from .schedule import read_schedule

        return when.astimezone(ZoneInfo(read_schedule()["tz"])).date().isoformat()
    except Exception:
        return when.date().isoformat()


def _guarded(dry: bool, work) -> tuple[str, str]:
    """Run a dream entry point under the lock. Never raises: returns a report if
    the API key is missing or the work fails, so the UI never gets a raw 500."""
    with _dream_lock:
        when = datetime.datetime.now(datetime.timezone.utc)
        day = _local_day(when)
        try:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                return _no_key_report(when, dry)
            return work(when, day)
        except Exception:
            _log.exception("dream run failed")
            return _error_report(when, dry)
