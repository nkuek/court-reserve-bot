"""Keeps the bot's CourtReserve reservations in step with the sign-up app.

Copies settled lineups onto the courts it booked and cancels courts the app dropped for too few players.
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from discord.ext import tasks

from constants import LOCAL_TZ
from utils import signup_app
from utils.discord import send_discord_notification

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "roster_sync.json"
SCRIPT = ROOT / "roster_sync.py"

# A lineup that failed to sync waits this long before the next attempt.
RETRY_AFTER_S = 30 * 60
# Scheduled bookings fire at 19:55. A second login then could slow the race for a court.
QUIET_START, QUIET_END = (19, 40), (20, 15)
RUN_TIMEOUT_S = 15 * 60


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1, sort_keys=True))


def _fingerprint(players: list[dict]) -> str:
    """Order-free, so a court only resyncs when who plays on it changes."""
    names = sorted(f"{p['name']}={p['crName']}" for p in players)
    return hashlib.sha256(json.dumps(names).encode()).hexdigest()[:16]


def _quiet_now() -> bool:
    now = datetime.now(LOCAL_TZ)
    return QUIET_START <= (now.hour, now.minute) < QUIET_END


def due_jobs(sessions: list[dict], state: dict, booker: str, now: float) -> list[dict]:
    """Courts the bot booked whose lineup differs from the last one it copied over."""
    jobs = []
    for s in sessions:
        for c in s["courts"]:
            # Courts other members booked sit on their accounts, which the bot can't edit.
            if c["bookedBy"].lower() != booker.lower():
                continue
            key = f"{s['date']} {c['court']}"
            fp = _fingerprint(c["players"])
            entry = state.get(key, {})
            if entry.get("synced") == fp:
                continue
            if entry.get("failed") == fp and now < entry.get("retry_at", entry.get("failed_at", 0) + RETRY_AFTER_S):
                continue
            jobs.append({"key": key, "fingerprint": fp, "date": s["date"], "court": c["court"], "players": c["players"]})
    return jobs


def due_cancellations(pending: list[dict], state: dict, booker: str, now: float) -> tuple[list[dict], list[dict]]:
    """Dropped courts the bot cancels itself, and ones another member booked."""
    own, others = [], []
    for c in pending:
        if c["bookedBy"].lower() != booker.lower():
            others.append(c)
            continue
        key = f"cancel {c['id']}"
        if now - state.get(key, {}).get("failed_at", 0) < RETRY_AFTER_S:
            continue
        own.append({"key": key, "id": c["id"], "action": "cancel", "date": c["date"], "court": c["court"]})
    return own, others


async def run_jobs(jobs: list[dict]) -> list[dict]:
    fields = ("action", "date", "court", "players", "to")
    payload = json.dumps([{k: j[k] for k in fields if k in j} for j in jobs]).encode()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(SCRIPT),
        cwd=ROOT, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(payload), RUN_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"roster_sync.py ran past {RUN_TIMEOUT_S // 60} minutes")
    text = out.decode(errors="replace")
    for line in text.splitlines():
        if "===RESULT_JSON===" not in line:
            log.info(f"  [roster] {line}")
    start, end = text.find("===RESULT_JSON==="), text.find("===END_RESULT_JSON===")
    if start < 0 or end < 0:
        raise RuntimeError(f"roster_sync.py exited {proc.returncode} without a result")
    return json.loads(text[start + len("===RESULT_JSON==="):end])


async def _record_cancel(state: dict, job: dict, result: dict, now: float) -> None:
    entry = state.setdefault(job["key"], {})
    label = f"Court {job['court']} on {job['date']}"
    if result["status"] == "error":
        entry["failed_at"] = now
        if not entry.get("alerted"):
            entry["alerted"] = True
            send_discord_notification(
                f"**{label}:** too few players signed up, but cancelling it in CourtReserve failed.\n"
                f"{result.get('error', '')[:500]}\nThe bot retries every {RETRY_AFTER_S // 60} minutes. Cancel it by hand if it's urgent.",
                title="Court Cancel Failed",
                success=False,
            )
        return
    try:
        await asyncio.to_thread(signup_app.mark_cancelled, job["id"])
    except Exception as e:
        # The reservation is already gone, so the next run finds nothing to cancel and marks it then.
        log.warning(f"Could not mark {label} cancelled in the sign-up app: {e}")
    state.pop(job["key"], None)
    if result["status"] == "cancelled":
        send_discord_notification(f"**{label}:** cancelled in CourtReserve. Fewer than 4 signed up.", title="Court Cancelled")
    else:
        log.info(f"{label} was already gone from CourtReserve")


def _record(state: dict, job: dict, result: dict, now: float) -> None:
    entry = state.setdefault(job["key"], {})
    fp = job["fingerprint"]
    label = f"Court {job['court']} on {job['date']}"
    if result["status"] == "error":
        entry.update(failed=fp, failed_at=now)
        entry.pop("retry_at", None)
        if entry.get("alerted") != fp:
            entry["alerted"] = fp
            send_discord_notification(
                f"**{label}:** could not update the CourtReserve players.\n{result.get('error', '')[:500]}\nThe bot retries every {RETRY_AFTER_S // 60} minutes.",
                title="Roster Sync Failed",
                success=False,
            )
        return
    if result.get("retry_at"):
        # Players outside their booking window become addable at a known time. That's no failure.
        entry.update(failed=fp, failed_at=now, retry_at=result["retry_at"] + 60)
        for name, at in result.get("waiting", {}).items():
            log.info(f"{label}: {name} can't be added until {datetime.fromtimestamp(at, LOCAL_TZ):%a %-m/%-d %-I:%M %p}")
    else:
        entry.pop("failed", None)
        entry.pop("failed_at", None)
        entry.pop("retry_at", None)
        entry["synced"] = fp
    problems = []
    if result["status"] == "not_found":
        problems.append("No reservation for this court under the bot's account.")
    problems += [f"{name}: {why}" for name, why in result.get("unmatched", {}).items()]
    kept = result.get("kept", [])
    if problems and entry.get("alerted") != fp:
        entry["alerted"] = fp
        held = f"\nLeft on the reservation until these are fixed: {', '.join(kept)}" if kept else ""
        send_discord_notification(
            f"**{label}:** added what it could. Fix these in CourtReserve or the app's Name list:\n"
            + "\n".join(f"- {p}" for p in problems) + held,
            title="Roster Sync Needs a Hand",
            success=False,
        )


def _report(job: dict, result: dict) -> None:
    """Shows the sync's outcome on the app's lineup, under each player's app name."""
    app_name = {p["crName"].lower(): p["name"] for p in job["players"] if p["crName"]}
    problems = [{**p, "name": app_name.get(p["name"].lower(), p["name"])} for p in result.get("problems", [])]
    try:
        signup_app.report_sync(job["date"], job["court"], result["status"], problems, result.get("error", "")[:300])
    except Exception as e:
        log.warning(f"Could not report Court {job['court']} on {job['date']} to the sign-up app: {e}")


@tasks.loop(minutes=5)
async def sync_rosters():
    if _quiet_now():
        return
    booker = os.environ.get("SIGNUP_BOOKER_NAME", "")
    try:
        sessions = await asyncio.to_thread(signup_app.rosters)
    except Exception as e:
        log.warning(f"Could not read lineups from the sign-up app: {e}")
        return
    try:
        pending = await asyncio.to_thread(signup_app.cancellations)
    except Exception as e:
        log.warning(f"Could not read court cancellations from the sign-up app: {e}")
        pending = []
    state = _load_state()
    cancels, others = due_cancellations(pending, state, booker, time.time())
    for c in others:
        # Another member's reservation sits on their account, so they cancel it.
        send_discord_notification(
            f"**Court {c['court']} on {c['date']}:** dropped for too few players. {c['bookedBy']} booked it, so they need to cancel it in CourtReserve.",
            title="Court Needs Cancelling",
            success=False,
        )
        try:
            await asyncio.to_thread(signup_app.mark_cancelled, c["id"])
        except Exception as e:
            log.warning(f"Could not mark Court {c['court']} on {c['date']} handled in the sign-up app: {e}")
    jobs = cancels + due_jobs(sessions, state, booker, time.time())
    if not jobs:
        return
    log.info(f"CourtReserve updates: {', '.join(j['key'] for j in jobs)}")
    try:
        results = await run_jobs(jobs)
    except Exception as e:
        log.error(f"Roster sync run failed: {e}")
        results = [{"status": "error", "error": str(e)} for _ in jobs]
    now = time.time()
    for job, result in zip(jobs, results):
        log.info(f"CourtReserve {job['key']}: {result['status']}")
        if job.get("action") == "cancel":
            await _record_cancel(state, job, result, now)
        else:
            _record(state, job, result, now)
            await asyncio.to_thread(_report, job, result)
    _save_state(state)
