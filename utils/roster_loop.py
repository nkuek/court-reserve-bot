"""Keeps the bot's CourtReserve reservations in step with the sign-up app's posted lineups."""

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
            if entry.get("failed") == fp and now - entry.get("failed_at", 0) < RETRY_AFTER_S:
                continue
            jobs.append({"key": key, "fingerprint": fp, "date": s["date"], "court": c["court"], "players": c["players"]})
    return jobs


async def _run(jobs: list[dict]) -> list[dict]:
    payload = json.dumps([{k: j[k] for k in ("date", "court", "players")} for j in jobs]).encode()
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


def _record(state: dict, job: dict, result: dict, now: float) -> None:
    entry = state.setdefault(job["key"], {})
    fp = job["fingerprint"]
    label = f"Court {job['court']} on {job['date']}"
    if result["status"] == "error":
        entry.update(failed=fp, failed_at=now)
        if entry.get("alerted") != fp:
            entry["alerted"] = fp
            send_discord_notification(
                f"**{label}:** could not update the CourtReserve players.\n{result.get('error', '')[:500]}\nThe bot retries every {RETRY_AFTER_S // 60} minutes.",
                title="Roster Sync Failed",
                success=False,
            )
        return
    entry.pop("failed", None)
    entry.pop("failed_at", None)
    entry["synced"] = fp
    problems = []
    if result["status"] == "not_found":
        problems.append("No reservation for this court under the bot's account.")
    problems += [f"{name}: {why}" for name, why in result.get("unmatched", {}).items()]
    if problems and entry.get("alerted") != fp:
        entry["alerted"] = fp
        send_discord_notification(
            f"**{label}:** added what it could. Fix these in CourtReserve or the app's Name list:\n"
            + "\n".join(f"- {p}" for p in problems),
            title="Roster Sync Needs a Hand",
            success=False,
        )


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
    state = _load_state()
    jobs = due_jobs(sessions, state, booker, time.time())
    if not jobs:
        return
    log.info(f"Copying {len(jobs)} lineup(s) to CourtReserve: {', '.join(j['key'] for j in jobs)}")
    try:
        results = await _run(jobs)
    except Exception as e:
        log.error(f"Roster sync run failed: {e}")
        results = [{"status": "error", "error": str(e)} for _ in jobs]
    now = time.time()
    for job, result in zip(jobs, results):
        log.info(f"Roster {job['key']}: {result['status']}")
        _record(state, job, result, now)
    _save_state(state)
