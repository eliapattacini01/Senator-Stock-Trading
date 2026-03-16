"""
Background scheduler that runs the Senate and House scrapers on a daily schedule.
Integrated into the FastAPI app via lifespan events.
"""

import datetime as _dt
import logging
import sys
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

LOGGER = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="America/New_York")


# ── scraper jobs ───────────────────────────────────────────────────────────────

def _ensure_path():
    root = os.path.dirname(os.path.dirname(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)


def _latest_file_date(chamber: str) -> _dt.date:
    """Return the latest file_date in the DB for the given chamber, or 30 days ago as fallback."""
    try:
        from backend.db import get_connection
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "SELECT MAX(file_date) FROM transactions WHERE chamber = %s", (chamber,)
        )
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            # Subtract 7 days as a safety overlap to avoid missing late-filed disclosures
            return row[0] - _dt.timedelta(days=7)
    except Exception as exc:
        LOGGER.warning("Could not query latest file_date: %s", exc)
    return _dt.date.today() - _dt.timedelta(days=30)


def _run_senate_scrape():
    LOGGER.info("[Scheduler] Starting Senate EFDS scrape (incremental)")
    try:
        _ensure_path()
        from Scraping.main   import main as senate_main
        from Scraping.ingest import ingest_to_db
        since = _latest_file_date("Senate")
        since_str = since.strftime("%m/%d/%Y 00:00:00")
        LOGGER.info("[Scheduler] Senate: fetching filings since %s", since_str)
        df = senate_main(since_date=since_str)
        n  = ingest_to_db(df, chamber="Senate")
        LOGGER.info("[Scheduler] Senate EFDS complete: %d rows upserted", n)
    except Exception as exc:
        LOGGER.exception("[Scheduler] Senate EFDS failed: %s", exc)


def _run_house_scrape():
    LOGGER.info("[Scheduler] Starting House EFTS scrape (incremental)")
    try:
        _ensure_path()
        from Scraping.house_scraper import main as house_main
        from Scraping.ingest        import ingest_to_db
        since      = _latest_file_date("House")
        from_year  = since.year
        LOGGER.info("[Scheduler] House: fetching filings from year %d", from_year)
        df = house_main(from_year=from_year)
        n  = ingest_to_db(df, chamber="House")
        LOGGER.info("[Scheduler] House EFTS complete: %d rows upserted", n)
    except Exception as exc:
        LOGGER.exception("[Scheduler] House EFTS failed: %s", exc)


def _run_watcher_scrape():
    """Senate Stock Watcher (GitHub, no API key, historical gap-fill)."""
    LOGGER.info("[Scheduler] Starting Senate Stock Watcher scrape")
    try:
        _ensure_path()
        from Scraping.watcher_scraper import main as watcher_main
        from Scraping.ingest          import ingest_to_db
        df = watcher_main()
        n  = ingest_to_db(df, chamber="Senate")
        LOGGER.info("[Scheduler] Watcher scrape complete: %d rows upserted", n)
    except Exception as exc:
        LOGGER.exception("[Scheduler] Watcher scrape failed: %s", exc)


def _run_fmp_scrape():
    """FMP API — only runs if FMP_API_KEY is present in environment."""
    if not os.environ.get("FMP_API_KEY"):
        LOGGER.info("[Scheduler] FMP skipped (no FMP_API_KEY)")
        return
    LOGGER.info("[Scheduler] Starting FMP scrape")
    try:
        _ensure_path()
        from Scraping.fmp_scraper import main as fmp_main
        from Scraping.ingest      import ingest_to_db
        df = fmp_main()
        n  = ingest_to_db(df)
        LOGGER.info("[Scheduler] FMP scrape complete: %d rows upserted", n)
    except Exception as exc:
        LOGGER.exception("[Scheduler] FMP scrape failed: %s", exc)


def _run_finnhub_scrape():
    """Finnhub API — only runs if FINNHUB_API_KEY is present in environment."""
    if not os.environ.get("FINNHUB_API_KEY"):
        LOGGER.info("[Scheduler] Finnhub skipped (no FINNHUB_API_KEY)")
        return
    LOGGER.info("[Scheduler] Starting Finnhub scrape")
    try:
        _ensure_path()
        from Scraping.finnhub_scraper import main as finnhub_main
        from Scraping.ingest          import ingest_to_db
        df = finnhub_main()
        n  = ingest_to_db(df)
        LOGGER.info("[Scheduler] Finnhub scrape complete: %d rows upserted", n)
    except Exception as exc:
        LOGGER.exception("[Scheduler] Finnhub scrape failed: %s", exc)


def _run_quiverquant_scrape():
    """QuiverQuant — no API key needed, returns 1000 most recent trades (both chambers)."""
    LOGGER.info("[Scheduler] Starting QuiverQuant scrape")
    try:
        _ensure_path()
        from Scraping.quiverquant_scraper import main as qq_main
        from Scraping.ingest              import ingest_to_db
        df = qq_main()
        n  = ingest_to_db(df)
        LOGGER.info("[Scheduler] QuiverQuant scrape complete: %d rows upserted", n)
    except Exception as exc:
        LOGGER.exception("[Scheduler] QuiverQuant scrape failed: %s", exc)


# ── lifecycle ──────────────────────────────────────────────────────────────────

def start():
    """Register jobs and start the scheduler (call from FastAPI startup)."""
    jobs = [
        # Official scrapers — daily
        ("senate_daily",      _run_senate_scrape,      2, 0),
        ("house_daily",       _run_house_scrape,        3, 0),
        # QuiverQuant — daily (no key, both chambers, recent 1000 trades)
        ("quiverquant_daily", _run_quiverquant_scrape, 3, 30),
        # Supplementary sources — weekly (Sunday early morning) to avoid API limits
        ("watcher_weekly",    _run_watcher_scrape,     4, 0),   # Sun 04:00
        ("fmp_weekly",        _run_fmp_scrape,         4, 30),  # Sun 04:30 (skipped if no key)
        ("finnhub_weekly",    _run_finnhub_scrape,     5, 0),   # Sun 05:00 (skipped if no key)
    ]
    for job_id, fn, hour, minute in jobs:
        trigger = CronTrigger(
            day_of_week="sun" if "weekly" in job_id else "*",
            hour=hour, minute=minute,
        )
        scheduler.add_job(fn, trigger, id=job_id, replace_existing=True, misfire_grace_time=3600)

    scheduler.start()
    LOGGER.info("[Scheduler] Started. Daily: senate@02:00 house@03:00. "
                "Weekly (Sun): watcher@04:00 fmp@04:30 finnhub@05:00 ET.")


def stop():
    """Gracefully shut down the scheduler (call from FastAPI shutdown)."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        LOGGER.info("[Scheduler] Stopped.")


VALID_JOBS = {"senate_daily", "house_daily", "quiverquant_daily", "watcher_weekly", "fmp_weekly", "finnhub_weekly"}


def trigger_now(job_id: str) -> bool:
    """
    Manually trigger a job immediately (non-blocking).
    job_id: 'senate_daily' or 'house_daily'
    Returns True if the job was found and triggered.
    """
    job = scheduler.get_job(job_id)
    if job is None:
        return False
    job.modify(next_run_time=__import__("datetime").datetime.now())
    return True
