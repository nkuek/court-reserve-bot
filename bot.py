#!/usr/bin/env python3
"""
Discord bot for court booking and open play registration.

Usage:
    python bot.py

Commands:
    /register - Save your CourtReserve credentials
    /unregister - Delete your saved credentials
    /book time:21:00 duration:2 [date:tomorrow]
    /openplay [event:Advanced] [date:tomorrow]
    /ping - Check if bot is online
"""

import asyncio
import io
import aiohttp
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

from discord.ext import tasks
from constants import VALID_DURATIONS
from utils.user_store import (
    save_user_credentials,
    get_user_credentials,
    delete_user_credentials,
    user_exists,
    save_schedule,
    get_user_schedules,
    get_all_schedules,
    update_schedule_last_run,
    delete_schedule,
    set_schedule_enabled,
    set_schedule_skip_next,
    clear_schedule_skip,
    # Admin functions
    admin_get_all_schedules,
    admin_delete_schedule,
    admin_set_schedule_enabled,
    admin_get_all_users,
)

# Load .env from the same directory as this script
load_dotenv(Path(__file__).parent / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# Bot setup
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Admin users (comma-separated Discord user IDs in env var)
ADMIN_USER_IDS: set[int] = set()
_admin_ids_str = os.environ.get("ADMIN_USER_IDS", "")
if _admin_ids_str:
    for uid in _admin_ids_str.split(","):
        try:
            ADMIN_USER_IDS.add(int(uid.strip()))
        except ValueError:
            pass


def is_admin(user_id: int) -> bool:
    """Check if a user is an admin."""
    return user_id in ADMIN_USER_IDS


# Available courts with short names for UI (matching court_booking.py)
COURTS = [
    ("5A", "Pickleball Court 5A (Bubble B)"),
    ("5B", "Pickleball Court 5B (Bubble B)"),
    ("5C", "Pickleball Court 5C (Bubble B)"),
    ("6A", "Pickleball Court 6A (Bubble B)"),
    ("6B", "Pickleball Court 6B (Bubble B)"),
    ("6C", "Pickleball Court 6C (Bubble B)"),
    ("7A", "Pickleball Court #7A (Bubble B)"),
    ("7B", "Pickleball Court #7B (Bubble B)"),
    ("8A", "Pickleball Court #8A (Bubble B)"),
    ("8B", "Pickleball Court #8B (Bubble B)"),
]

# Court sort order for display
COURT_SORT_ORDER = {full: idx for idx, (_, full) in enumerate(COURTS)}


def sort_courts(court_name: str) -> int:
    """Return sort key for a court name (5A first, 8B last)."""
    return COURT_SORT_ORDER.get(court_name, 999)

# Common booking times (evening hours)
COMMON_TIMES = [
    "8:00", "10:30", "18:00", "21:00",
]

def get_date_options() -> list[tuple[str, str]]:
    """Generate date options with actual dates."""
    today = datetime.now()

    options = []

    # Today
    today_str = today.strftime("%a %m/%d")
    options.append(("today", f"Today ({today_str})"))

    # Tomorrow
    tomorrow = today + timedelta(days=1)
    tomorrow_str = tomorrow.strftime("%a %m/%d")
    options.append(("tomorrow", f"Tomorrow ({tomorrow_str})"))

    # +2d through +4d
    for days in range(2, 5):
        future = today + timedelta(days=days)
        future_str = future.strftime("%a %m/%d")
        options.append((f"+{days}d", future_str))

    # Latest (5 days ahead)
    latest = today + timedelta(days=5)
    latest_str = latest.strftime("%a %m/%d")
    options.append(("latest", f"{latest_str} (earliest booking)"))

    return options

# Track running tasks: user_id -> list of (task_id, process, description)
running_tasks: dict[int, list[tuple[str, asyncio.subprocess.Process, str]]] = {}


async def time_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for time parameter."""
    if not current:
        # Show common evening times
        return [
            app_commands.Choice(name=f"{t} ({_format_12h(t)})", value=t)
            for t in COMMON_TIMES
        ][:25]

    # Filter times that match what user typed
    matches = [t for t in COMMON_TIMES if t.startswith(current)]

    # Also allow custom times if they typed a valid format
    if ":" in current and current not in matches:
        matches.insert(0, current)

    return [
        app_commands.Choice(name=f"{t} ({_format_12h(t)})", value=t)
        for t in matches
    ][:25]


async def date_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for date parameter with actual dates."""
    date_options = get_date_options()

    if not current:
        return [
            app_commands.Choice(name=f"{value} — {desc}", value=value)
            for value, desc in date_options
        ]

    # Filter by what user typed
    current_lower = current.lower()
    matches = [
        (v, d) for v, d in date_options
        if current_lower in v.lower() or current_lower in d.lower()
    ]

    # Allow custom date formats
    if not matches:
        return [app_commands.Choice(name=current, value=current)]

    return [
        app_commands.Choice(name=f"{value} — {desc}", value=value)
        for value, desc in matches
    ][:25]


def get_openplay_date_options() -> list[tuple[str, str]]:
    """Generate date options for open play (Tuesdays and Thursdays only)."""
    today = datetime.now()
    options = []

    # Look ahead up to 7 days to find the next Tuesdays/Thursdays
    for days_ahead in range(8):
        future = today + timedelta(days=days_ahead)
        day_of_week = future.weekday()

        # Only include Tuesday (1) and Thursday (3)
        if day_of_week in (1, 3):
            date_str = future.strftime("%a %m/%d")
            day_name = "Tuesday" if day_of_week == 1 else "Thursday"

            if days_ahead == 0:
                label = f"Today ({date_str})"
            elif days_ahead == 1:
                label = f"Tomorrow ({date_str})"
            else:
                label = f"{day_name} ({date_str})"

            # Use +Nd format for the value
            if days_ahead == 0:
                value = "today"
            elif days_ahead == 1:
                value = "tomorrow"
            else:
                value = f"+{days_ahead}d"

            options.append((value, label))

    return options


async def openplay_date_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for open play dates (Tuesdays and Thursdays only)."""
    date_options = get_openplay_date_options()

    if not current:
        return [
            app_commands.Choice(name=f"{value} — {desc}", value=value)
            for value, desc in date_options
        ]

    # Filter by what user typed
    current_lower = current.lower()
    matches = [
        (v, d) for v, d in date_options
        if current_lower in v.lower() or current_lower in d.lower()
    ]

    # Allow custom date formats (user might know what they're doing)
    if not matches:
        return [app_commands.Choice(name=current, value=current)]

    return [
        app_commands.Choice(name=f"{value} — {desc}", value=value)
        for value, desc in matches
    ][:25]


async def court_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for court parameter."""
    if not current:
        # Show all courts with "Any" option first
        choices = [app_commands.Choice(name="Any (auto-select)", value="any")]
        choices.extend([
            app_commands.Choice(name=f"Court {short}", value=full)
            for short, full in COURTS
        ])
        return choices[:25]

    # Filter by what user typed
    current_lower = current.lower()
    matches = [
        (short, full) for short, full in COURTS
        if current_lower in short.lower() or current_lower in full.lower()
    ]

    if not matches:
        return [app_commands.Choice(name="Any (auto-select)", value="any")]

    choices = [
        app_commands.Choice(name=f"Court {short}", value=full)
        for short, full in matches
    ]
    return choices[:25]


def _format_12h(time_str: str) -> str:
    """Convert 24h time to 12h format for display."""
    try:
        hour, minute = map(int, time_str.split(":"))
        period = "AM" if hour < 12 else "PM"
        display_hour = hour % 12 or 12
        return f"{display_hour}:{minute:02d} {period}"
    except:
        return time_str


def get_python_path() -> str:
    """Get the path to the Python interpreter."""
    # Use the same Python that's running this script
    return sys.executable


def get_script_dir() -> Path:
    """Get the directory containing the scripts."""
    return Path(__file__).parent


@client.event
async def on_ready():
    """Called when bot is ready."""
    log.info(f"Bot is online as {client.user}")

    # Sync commands with Discord
    try:
        synced = await tree.sync()
        log.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        log.error(f"Failed to sync commands: {e}")

    # Start the background scheduler
    if not check_schedules.is_running():
        check_schedules.start()
        log.info("Background scheduler started")


@tree.command(name="ping", description="Check if the bot is online")
async def ping(interaction: discord.Interaction):
    """Simple ping command to check if bot is responsive."""
    await interaction.response.send_message(
        f"🏓 Pong! Bot is online. Latency: {round(client.latency * 1000)}ms\n"
        f"Your User ID: `{interaction.user.id}`",
        ephemeral=True
    )


@tree.command(name="help", description="Show all available commands")
async def help_command(interaction: discord.Interaction):
    """Show help information for all commands."""
    embed = discord.Embed(
        title="🏸 Court Booking Bot",
        description="Book pickleball courts and register for open play via Discord.",
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="🔐 Getting Started",
        value=(
            "Register your CourtReserve credentials:\n"
            "```/register```\n"
            "Your credentials are encrypted and stored securely."
        ),
        inline=False,
    )

    embed.add_field(
        name="📋 /check-availability",
        value=(
            "See available time slots for each court.\n"
            "Includes **quick-book dropdown** to book directly!\n"
            "```/check-availability date:latest```"
        ),
        inline=False,
    )

    embed.add_field(
        name="🎾 /book",
        value=(
            "Book a specific court and time.\n"
            "**Required:** `time`, `duration`\n"
            "**Optional:** `date`, `court`, `wait_until`\n"
            "```/book time:21:00 duration:2```"
        ),
        inline=False,
    )

    embed.add_field(
        name="🏓 /openplay",
        value=(
            "Register for open play (Tue/Thu only).\n"
            "Auto-detects Intermediate or Thursday event.\n"
            "```/openplay date:+3d```"
        ),
        inline=False,
    )

    embed.add_field(
        name="/account",
        value="View your registered email.",
        inline=True,
    )

    embed.add_field(
        name="/cancel",
        value="Cancel a running task.",
        inline=True,
    )

    embed.add_field(
        name="/ping",
        value="Check if bot is online.",
        inline=True,
    )

    embed.add_field(
        name="📅 Date Options",
        value=(
            "Autocomplete shows actual dates!\n"
            "`latest` — 5 days ahead\n"
            "`today` / `tomorrow`\n"
            "`+3d` — 3 days from now"
        ),
        inline=True,
    )

    embed.add_field(
        name="⏰ Timing",
        value=(
            "`/book` waits until 1 min before\n"
            "reservation time by default.\n"
            "Override with `wait_until`:\n"
            "`07:00` — Today at 7 AM\n"
            "`tomorrow 07:00`"
        ),
        inline=True,
    )

    embed.add_field(
        name="🔁 Recurring Schedules",
        value=(
            "`/schedule openplay` — Auto-register\n"
            "`/schedule book` — Auto-book\n"
            "`/schedule list` — View all\n"
            "`/schedule skip` — Skip next run\n"
            "`/schedule cancel` — Stop running"
        ),
        inline=True,
    )

    embed.set_footer(text="💡 Tip: Use /check-availability to browse and quick-book available slots!")

    await interaction.response.send_message(embed=embed, ephemeral=True)


class RegisterModal(discord.ui.Modal, title="CourtReserve Login"):
    """Modal for registering credentials securely."""

    email = discord.ui.TextInput(
        label="Email",
        placeholder="your.email@example.com",
        required=True,
        max_length=100,
    )

    password = discord.ui.TextInput(
        label="Password (only you can see this form)",
        placeholder="Your CourtReserve password",
        required=True,
        max_length=100,
        style=discord.TextStyle.short,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            save_user_credentials(
                interaction.user.id,
                str(self.email),
                str(self.password),
            )

            embed = discord.Embed(
                title="✅ Registration Successful",
                description=(
                    f"Your credentials have been saved securely.\n\n"
                    f"**Email:** {self.email}\n"
                    f"**Password:** {'•' * len(str(self.password))}\n\n"
                    f"You can now use `/book` and `/openplay` commands!"
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text="Use /unregister to delete your credentials.")

            await interaction.response.send_message(embed=embed, ephemeral=True)
            log.info(f"User {interaction.user.id} registered")

        except Exception as e:
            log.error(f"Failed to register user {interaction.user.id}: {e}")
            await interaction.response.send_message(
                f"❌ Failed to save credentials: {e}",
                ephemeral=True
            )


@tree.command(name="register", description="Save your CourtReserve credentials")
async def register(interaction: discord.Interaction):
    """Open a secure form to register credentials."""
    await interaction.response.send_modal(RegisterModal())


@tree.command(name="unregister", description="Delete your saved credentials")
async def unregister(interaction: discord.Interaction):
    """Delete user credentials."""
    if delete_user_credentials(interaction.user.id):
        await interaction.response.send_message(
            "✅ Your credentials have been deleted.",
            ephemeral=True
        )
        log.info(f"User {interaction.user.id} unregistered")
    else:
        await interaction.response.send_message(
            "❌ You don't have any saved credentials.",
            ephemeral=True
        )


@tree.command(name="account", description="View your registered email address")
async def account(interaction: discord.Interaction):
    """Show the user's registered email."""
    credentials = get_user_credentials(interaction.user.id)

    if credentials:
        email, _ = credentials
        embed = discord.Embed(
            title="🔐 Your Account",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Email", value=email, inline=False)
        embed.set_footer(text="Use /register to update or /unregister to delete.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(
            "❌ You haven't registered yet.\nUse `/register` to save your CourtReserve credentials.",
            ephemeral=True,
        )


@tree.command(name="openplay", description="Register for an open play event")
@app_commands.describe(
    date="Date to register (Tuesdays and Thursdays only)",
    wait_until="Wait until this time before starting (e.g., 07:00)",
)
@app_commands.autocomplete(date=openplay_date_autocomplete)
async def openplay(
    interaction: discord.Interaction,
    date: str = "latest",
    wait_until: str | None = None,
):
    """Register for an open play event."""
    # Check for user credentials
    credentials = get_user_credentials(interaction.user.id)
    if not credentials:
        await interaction.response.send_message(
            "❌ You haven't registered your credentials yet.\n"
            "Use `/register` to save your CourtReserve login first.",
            ephemeral=True
        )
        return

    email, password = credentials

    # Build command
    cmd = [
        get_python_path(),
        str(get_script_dir() / "open_play.py"),
        "--date", date,
        "--email", email,
        "--password", password,
    ]

    if wait_until:
        cmd.extend(["--wait-until", wait_until])

    # Send initial response
    embed = discord.Embed(
        title="🏸 Open Play Registration Started",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    embed.add_field(name="Date", value=date, inline=True)

    if wait_until:
        embed.add_field(name="Wait Until", value=wait_until, inline=True)

    embed.set_footer(text="Running in background... Results will be sent via DM.")

    await interaction.response.send_message(embed=embed, ephemeral=True)

    # Run the script in background
    task_desc = f"Open Play: {date}"
    asyncio.create_task(_run_script(interaction, cmd, "Open Play Registration", task_desc))


def convert_to_24h(time_str: str) -> str:
    """Convert '9:00 AM' to '9:00' or '9:00 PM' to '21:00'."""
    time_str = time_str.strip().upper()

    # Parse the time
    if "AM" in time_str:
        time_part = time_str.replace("AM", "").strip()
        hour, minute = time_part.split(":")
        hour = int(hour)
        if hour == 12:
            hour = 0
    else:  # PM
        time_part = time_str.replace("PM", "").strip()
        hour, minute = time_part.split(":")
        hour = int(hour)
        if hour != 12:
            hour += 12

    return f"{hour}:{minute}"


class BookSlotView(discord.ui.View):
    """View with time range filter, slot and duration selection for quick booking."""

    # Time ranges for filtering
    TIME_RANGES = {
        "morning": ("Morning (6am-12pm)", 6, 12),
        "afternoon": ("Afternoon (12pm-5pm)", 12, 17),
        "evening": ("Evening (5pm-11pm)", 17, 23),
    }

    def __init__(self, slots_data: dict[str, list[str]], date: str, user_id: int):
        super().__init__(timeout=300)  # 5 minute timeout
        self.date = date
        self.user_id = user_id
        self.slots_data = slots_data  # Store for duration validation
        self.selected_court: str | None = None
        self.selected_time: str | None = None
        self.selected_duration: float = 2.0  # Default
        self.slot_map: dict[str, tuple[str, str]] = {}  # value -> (court, time)
        self.time_courts_map: dict[str, list[tuple[str, str]]] = {}  # time_str -> [(court, short_name)]
        self.all_slots: list[tuple[str, str, str, int]] = []  # (time_str, court, short_name, hour)

        # Collect all slots with parsed hour for filtering
        for court in slots_data.keys():
            times = slots_data[court]
            short_name = court.replace("Pickleball Court ", "").replace(" (Bubble B)", "").replace("#", "")

            for time_str in times:
                hour = self._parse_hour(time_str)
                self.all_slots.append((time_str, court, short_name, hour))

        # Sort by time
        self.all_slots.sort(key=lambda x: x[3] * 60 + int(x[0].split(":")[1].split()[0]))

        # Group all slots by time
        from collections import OrderedDict
        time_to_courts: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()

        for time_str, court, short_name, hour in self.all_slots:
            if time_str not in time_to_courts:
                time_to_courts[time_str] = []
            time_to_courts[time_str].append((court, short_name))

        # Build slot options - one per unique time (limit to 25)
        slot_options = self._build_slot_options(time_to_courts)

        # Count slots per time range (for filter)
        range_counts = {r: 0 for r in self.TIME_RANGES}
        for time_str, courts in time_to_courts.items():
            hour = self._parse_hour(time_str)
            for range_key, (_, start, end) in self.TIME_RANGES.items():
                if start <= hour < end:
                    range_counts[range_key] += 1
                    break

        # Only show time range filter if there are more than 25 unique times
        if len(time_to_courts) > 25:
            range_options = [
                discord.SelectOption(label="All times", value="all", description="Show first 25 times")
            ]
            for range_key, (label, _, _) in self.TIME_RANGES.items():
                count = range_counts[range_key]
                if count > 0:
                    range_options.append(
                        discord.SelectOption(
                            label=f"{label}",
                            value=range_key,
                            description=f"{count} times available",
                        )
                    )

            self.range_select = discord.ui.Select(
                placeholder="🔍 Filter by time range (optional)...",
                options=range_options,
                row=0,
            )
            self.range_select.callback = self.range_callback
            self.add_item(self.range_select)
            slot_row = 1
        else:
            slot_row = 0

        # Add slot select
        truncated_note = f" (showing 25 of {len(time_to_courts)})" if len(time_to_courts) > 25 else ""
        self.slot_select = discord.ui.Select(
            placeholder=f"1️⃣ Select time{truncated_note}...",
            options=slot_options if slot_options else [discord.SelectOption(label="No slots", value="none")],
            disabled=not slot_options,
            row=slot_row,
        )
        self.slot_select.callback = self.slot_callback
        self.add_item(self.slot_select)

        # Add duration select
        self.duration_select = discord.ui.Select(
            placeholder="2️⃣ Select duration...",
            options=[
                discord.SelectOption(label="1 hour", value="1.0"),
                discord.SelectOption(label="1.5 hours", value="1.5"),
                discord.SelectOption(label="2 hours", value="2.0", default=True),
                discord.SelectOption(label="2.5 hours", value="2.5"),
                discord.SelectOption(label="3 hours", value="3.0"),
            ],
            row=slot_row + 1,
        )
        self.duration_select.callback = self.duration_callback
        self.add_item(self.duration_select)

        # Add book button
        self.book_button = discord.ui.Button(
            label="Book Now",
            style=discord.ButtonStyle.green,
            emoji="🎾",
            row=slot_row + 2,
        )
        self.book_button.callback = self.book_callback
        self.add_item(self.book_button)

    def _build_slot_options(self, time_to_courts: dict[str, list[tuple[str, str]]]) -> list[discord.SelectOption]:
        """Build slot options from time->courts mapping."""
        self.slot_map.clear()
        self.time_courts_map.clear()
        slot_options = []

        for time_str, courts in list(time_to_courts.items())[:25]:
            self.time_courts_map[time_str] = courts

            value = time_str.replace(" ", "_").replace(":", "")
            self.slot_map[value] = (courts[0][0], time_str)

            court_names = [short for _, short in courts]
            if len(court_names) <= 3:
                courts_str = ", ".join(court_names)
            else:
                courts_str = f"{court_names[0]}, {court_names[1]}... (+{len(court_names) - 2})"

            slot_options.append(
                discord.SelectOption(
                    label=f"{time_str}",
                    value=value,
                    description=f"Courts: {courts_str}",
                )
            )

        return slot_options

    def _parse_hour(self, time_str: str) -> int:
        """Parse hour from time string like '9:00 AM' -> 9 or '2:00 PM' -> 14."""
        time_str = time_str.strip().upper()
        try:
            if "AM" in time_str:
                hour = int(time_str.split(":")[0])
                if hour == 12:
                    hour = 0
            else:  # PM
                hour = int(time_str.split(":")[0])
                if hour != 12:
                    hour += 12
            return hour
        except:
            return 0

    def _get_required_slots(self, start_time: str, duration_hours: float) -> list[str]:
        """Get all time slots required for a booking with given start time and duration.

        For a 2-hour booking starting at 9:00 PM, returns:
        ['9:00 PM', '9:30 PM', '10:00 PM', '10:30 PM']
        """
        from datetime import datetime, timedelta

        # Parse the start time
        start_time = start_time.strip().upper()
        time_part = start_time.replace("AM", "").replace("PM", "").strip()
        hour, minute = map(int, time_part.split(":"))

        if "PM" in start_time and hour != 12:
            hour += 12
        elif "AM" in start_time and hour == 12:
            hour = 0

        # Generate all 30-minute slots needed
        slots = []
        num_slots = int(duration_hours * 2)  # 2 slots per hour

        for i in range(num_slots):
            slot_hour = hour + (minute + i * 30) // 60
            slot_minute = (minute + i * 30) % 60

            # Convert back to 12-hour format
            if slot_hour == 0:
                formatted = f"12:{slot_minute:02d} AM"
            elif slot_hour < 12:
                formatted = f"{slot_hour}:{slot_minute:02d} AM"
            elif slot_hour == 12:
                formatted = f"12:{slot_minute:02d} PM"
            else:
                formatted = f"{slot_hour - 12}:{slot_minute:02d} PM"

            slots.append(formatted)

        return slots

    def _check_duration_available(self, court: str, start_time: str, duration_hours: float) -> tuple[bool, list[str]]:
        """Check if all time slots for the requested duration are available.

        Returns (is_available, missing_slots).
        """
        required_slots = self._get_required_slots(start_time, duration_hours)
        court_slots = set(self.slots_data.get(court, []))

        missing = [slot for slot in required_slots if slot not in court_slots]

        return len(missing) == 0, missing

    def _get_valid_durations(self, court: str, start_time: str) -> list[float]:
        """Get list of valid durations for a court/time combination."""
        all_durations = [1.0, 1.5, 2.0, 2.5, 3.0]
        valid = []

        for duration in all_durations:
            is_available, _ = self._check_duration_available(court, start_time, duration)
            if is_available:
                valid.append(duration)

        return valid

    async def range_callback(self, interaction: discord.Interaction):
        """Handle time range selection - update slot dropdown."""
        range_key = self.range_select.values[0]

        if range_key == "all":
            # Show all slots (first 25)
            from collections import OrderedDict
            time_to_courts: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()

            for time_str, court, short_name, hour in self.all_slots:
                if time_str not in time_to_courts:
                    time_to_courts[time_str] = []
                time_to_courts[time_str].append((court, short_name))

            filter_label = "All times"
        else:
            _, start_hour, end_hour = self.TIME_RANGES[range_key]

            # Filter slots by time range
            from collections import OrderedDict
            time_to_courts: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()

            for time_str, court, short_name, hour in self.all_slots:
                if start_hour <= hour < end_hour:
                    if time_str not in time_to_courts:
                        time_to_courts[time_str] = []
                    time_to_courts[time_str].append((court, short_name))

            filter_label = self.TIME_RANGES[range_key][0]

        # Build slot options
        slot_options = self._build_slot_options(time_to_courts)

        # Update slot select
        self.slot_select.options = slot_options if slot_options else [discord.SelectOption(label="No slots", value="none")]
        self.slot_select.disabled = not slot_options

        truncated_msg = ""
        if len(time_to_courts) > 25:
            truncated_msg = f" (showing 25 of {len(time_to_courts)})"
        self.slot_select.placeholder = f"1️⃣ Select time{truncated_msg}..."

        await interaction.response.edit_message(view=self)

    async def slot_callback(self, interaction: discord.Interaction):
        """Handle slot selection."""
        if self.slot_select.values[0] == "none":
            await interaction.response.defer()
            return

        selected_value = self.slot_select.values[0]
        _, self.selected_time = self.slot_map[selected_value]

        # Check if multiple courts are available at this time
        courts = self.time_courts_map.get(self.selected_time, [])

        # Auto-select first court
        self.selected_court = courts[0][0]
        short_name = courts[0][1]

        # Get valid durations for this court/time
        valid_durations = self._get_valid_durations(self.selected_court, self.selected_time)

        if not valid_durations:
            # Shouldn't happen, but handle gracefully
            valid_durations = [1.0]

        # Update duration dropdown with only valid options
        duration_labels = {
            1.0: "1 hour",
            1.5: "1.5 hours",
            2.0: "2 hours",
            2.5: "2.5 hours",
            3.0: "3 hours",
        }

        # Set default to 2h if available, otherwise longest available
        if 2.0 in valid_durations:
            self.selected_duration = 2.0
        else:
            self.selected_duration = max(valid_durations)

        self.duration_select.options = [
            discord.SelectOption(
                label=duration_labels[d],
                value=str(d),
                default=(d == self.selected_duration),
            )
            for d in valid_durations
        ]

        # Update slot select to show the selected time as default
        new_slot_options = []
        for option in self.slot_select.options:
            new_slot_options.append(
                discord.SelectOption(
                    label=option.label,
                    value=option.value,
                    description=option.description,
                    default=(option.value == selected_value),
                )
            )
        self.slot_select.options = new_slot_options

        await interaction.response.edit_message(view=self)

    async def duration_callback(self, interaction: discord.Interaction):
        """Handle duration selection."""
        self.selected_duration = float(self.duration_select.values[0])
        await interaction.response.defer()

    async def book_callback(self, interaction: discord.Interaction):
        """Book the selected slot."""
        if not self.selected_court or not self.selected_time:
            await interaction.response.send_message(
                "❌ Please select a time slot first!",
                ephemeral=True,
            )
            return

        # Verify duration is available before proceeding
        is_available, missing_slots = self._check_duration_available(
            self.selected_court, self.selected_time, self.selected_duration
        )

        if not is_available:
            short_name = self.selected_court.replace("Pickleball Court ", "").replace(" (Bubble B)", "").replace("#", "")
            missing_str = ", ".join(missing_slots[:3])
            if len(missing_slots) > 3:
                missing_str += f" (+{len(missing_slots) - 3} more)"

            await interaction.response.send_message(
                f"❌ **Cannot book {self.selected_duration}h starting at {self.selected_time}**\n\n"
                f"Court **{short_name}** is not available for the full duration.\n"
                f"Missing slots: {missing_str}\n\n"
                f"💡 Try a shorter duration or different time.",
                ephemeral=True,
            )
            return

        # Get credentials
        credentials = get_user_credentials(interaction.user.id)
        if not credentials:
            await interaction.response.send_message(
                "❌ You need to `/register` first.",
                ephemeral=True,
            )
            return

        email, password = credentials
        short_name = self.selected_court.replace("Pickleball Court ", "").replace(" (Bubble B)", "").replace("#", "")
        time_24h = convert_to_24h(self.selected_time)

        # Build booking command
        # Uses default behavior: wait until 1 minute before reservation time
        cmd = [
            get_python_path(),
            str(get_script_dir() / "court_booking.py"),
            "--time", time_24h,
            "--duration", str(self.selected_duration),
            "--date", self.date,
            "--parallel",
            "--attempts", "3",
            "--court", self.selected_court,
            "--email", email,
            "--password", password,
        ]

        # Send confirmation
        embed = discord.Embed(
            title="🎾 Court Booking Started",
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        embed.add_field(name="Date", value=self.date, inline=True)
        embed.add_field(name="Time", value=self.selected_time, inline=True)
        embed.add_field(name="Duration", value=f"{self.selected_duration}h", inline=True)
        embed.add_field(name="Court", value=short_name, inline=True)
        embed.set_footer(text="Running in background... Results will be sent via DM.")

        # Disable the view after booking
        self.book_button.disabled = True
        self.book_button.label = "Booking..."
        self.slot_select.disabled = True
        self.duration_select.disabled = True
        if hasattr(self, 'range_select'):
            self.range_select.disabled = True

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed, ephemeral=True)

        # Run booking in background
        task_desc = f"Court Booking: {self.date} @ {self.selected_time} (Court {short_name})"
        asyncio.create_task(_run_script(interaction, cmd, "Court Booking", task_desc))


class CancelSelectMenu(discord.ui.Select):
    """Dropdown menu for selecting which task to cancel."""

    def __init__(self, tasks: list[tuple[str, asyncio.subprocess.Process, str]]):
        self.tasks = {t[0]: t for t in tasks}  # Map task_id -> task tuple
        options = [
            discord.SelectOption(
                label=desc[:100],
                value=task_id,
                description=f"Task ID: {task_id}",
            )
            for task_id, _, desc in tasks
        ]
        # Add "Cancel All" option
        options.append(
            discord.SelectOption(
                label="Cancel All Tasks",
                value="__all__",
                description=f"Cancel all {len(tasks)} running task(s)",
                emoji="🗑️",
            )
        )
        super().__init__(placeholder="Select a task to cancel...", options=options)

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        selected = self.values[0]

        if selected == "__all__":
            # Cancel all tasks
            tasks_to_cancel = list(self.tasks.values())
        else:
            # Cancel specific task
            tasks_to_cancel = [self.tasks[selected]] if selected in self.tasks else []

        cancelled = []
        for task_id, process, desc in tasks_to_cancel:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            cancelled.append(desc)

            # Remove from running_tasks
            if user_id in running_tasks:
                running_tasks[user_id] = [
                    t for t in running_tasks[user_id] if t[0] != task_id
                ]
                if not running_tasks[user_id]:
                    del running_tasks[user_id]

        if cancelled:
            cancelled_list = "\n".join(f"• {d}" for d in cancelled)
            await interaction.response.edit_message(
                content=f"✅ Cancelled {len(cancelled)} task(s):\n{cancelled_list}",
                view=None,
            )
        else:
            await interaction.response.edit_message(
                content="❌ Task not found (may have already finished).",
                view=None,
            )


class CancelView(discord.ui.View):
    """View containing the cancel select menu."""

    def __init__(self, tasks: list[tuple[str, asyncio.subprocess.Process, str]]):
        super().__init__(timeout=60)
        self.add_item(CancelSelectMenu(tasks))


class FullLogView(discord.ui.View):
    """View with a button to show the full log."""

    def __init__(self, full_log: str, task_name: str):
        # Keep the button active indefinitely so users can download later
        super().__init__(timeout=None)
        self.full_log = full_log
        self.task_name = task_name

    @discord.ui.button(label="View Full Log", style=discord.ButtonStyle.secondary, emoji="📜")
    async def view_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Send the full log as a file attachment."""
        # Create a text file with the log
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"log_{timestamp}.txt"

        # Upload to 0x0.st for a simple public link (do not use for secrets)
        upload_url = None
        try:
            form = aiohttp.FormData()
            form.add_field(
                "file",
                self.full_log.encode("utf-8"),
                filename=filename,
                content_type="text/plain",
            )
            async with aiohttp.ClientSession() as session:
                async with session.post("https://0x0.st", data=form, timeout=30) as resp:
                    if resp.status == 200:
                        upload_url = (await resp.text()).strip()
        except Exception as e:
            # Fall back to direct attachment
            upload_url = None

        content = f"📜 **Full log for {self.task_name}:**"
        if upload_url:
            content += f"\n{upload_url}"

        if upload_url:
            await interaction.response.send_message(content, ephemeral=True)
        else:
            file = discord.File(
                io.BytesIO(self.full_log.encode("utf-8")),
                filename=filename,
            )
            await interaction.response.send_message(
                content,
                file=file,
                ephemeral=True,
            )

        # Disable the button after use
        button.disabled = True
        button.label = "Log Sent"
        await interaction.message.edit(view=self)


class LiveLogCancelView(discord.ui.View):
    """View with a cancel button for live log messages."""

    def __init__(
        self,
        user_id: int,
        task_id: str | None = None,
        schedule_id: int | None = None,
        process: asyncio.subprocess.Process | None = None,
    ):
        super().__init__(timeout=None)  # No timeout - stays active while task runs
        self.user_id = user_id
        self.task_id = task_id
        self.schedule_id = schedule_id
        self.process = process
        self.cancelled = False
        self.status_message: discord.Message | None = None  # Track the live log message

    @discord.ui.button(label="Cancel Task", style=discord.ButtonStyle.danger, emoji="🛑")
    async def cancel_task(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Cancel the running task."""
        # Only allow the task owner to cancel
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ You can only cancel your own tasks.",
                ephemeral=True,
            )
            return

        if self.cancelled:
            await interaction.response.send_message(
                "ℹ️ This task was already cancelled.",
                ephemeral=True,
            )
            return

        self.cancelled = True

        # Terminate the process
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

        # Clean up tracking
        if self.task_id and self.user_id in running_tasks:
            running_tasks[self.user_id] = [
                t for t in running_tasks[self.user_id] if t[0] != self.task_id
            ]
            if not running_tasks[self.user_id]:
                del running_tasks[self.user_id]

        if self.schedule_id and self.schedule_id in _running_scheduled:
            del _running_scheduled[self.schedule_id]

        # Delete the live log message instead of editing it
        try:
            await interaction.message.delete()
        except:
            pass

        # Send a clean cancellation confirmation
        await interaction.response.send_message(
            "🛑 **Task Cancelled**\nThe task has been stopped.",
        )
        log.info(f"User {self.user_id} cancelled task via button (task_id={self.task_id}, schedule_id={self.schedule_id})")


@tree.command(name="cancel", description="Cancel a running booking task")
async def cancel(interaction: discord.Interaction):
    """Cancel any running booking task for this user."""
    user_id = interaction.user.id

    if user_id in running_tasks and running_tasks[user_id]:
        tasks = running_tasks[user_id]

        if len(tasks) == 1:
            # Only one task, cancel it directly
            task_id, process, desc = tasks[0]
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            del running_tasks[user_id]
            await interaction.response.send_message(
                f"✅ Cancelled: {desc}",
                ephemeral=True,
            )
        else:
            # Multiple tasks, show selection menu
            view = CancelView(tasks)
            task_list = "\n".join(f"• `{t[0]}` — {t[2]}" for t in tasks)
            await interaction.response.send_message(
                f"You have **{len(tasks)}** running tasks:\n{task_list}\n\nSelect which to cancel:",
                view=view,
                ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            "❌ You don't have any running booking tasks.",
            ephemeral=True,
        )


@tree.command(name="book", description="Book a pickleball court")
@app_commands.describe(
    time="Reservation time in 24h format (e.g., 21:00 for 9 PM)",
    duration="Duration in hours (1, 1.5, 2, 2.5, or 3)",
    date="Date to book (today, tomorrow, +3d, 12/15, or latest)",
    court="Specific court to book (or 'any' for auto-select)",
    wait_until="Wait until this time before starting (e.g., 07:00)",
)
@app_commands.choices(duration=[
    app_commands.Choice(name="1 hour", value=1.0),
    app_commands.Choice(name="1.5 hours", value=1.5),
    app_commands.Choice(name="2 hours", value=2.0),
    app_commands.Choice(name="2.5 hours", value=2.5),
    app_commands.Choice(name="3 hours", value=3.0),
])
@app_commands.autocomplete(time=time_autocomplete, date=date_autocomplete, court=court_autocomplete)
async def book(
    interaction: discord.Interaction,
    time: str,
    duration: float,
    date: str = "latest",
    court: str = "any",
    wait_until: str | None = None,
):
    """Book a pickleball court."""
    # Check for user credentials
    credentials = get_user_credentials(interaction.user.id)
    if not credentials:
        await interaction.response.send_message(
            "❌ You haven't registered your credentials yet.\n"
            "Use `/register` to save your CourtReserve login first.",
            ephemeral=True
        )
        return

    email, password = credentials

    # Validate time format
    if not _validate_time(time):
        await interaction.response.send_message(
            "❌ Invalid time format. Use HH:MM (e.g., 21:00 for 9 PM)",
            ephemeral=True
        )
        return

    # Build command
    cmd = [
        get_python_path(),
        str(get_script_dir() / "court_booking.py"),
        "--time", time,
        "--duration", str(duration),
        "--date", date,
        "--parallel",
        "--attempts", "3",
        "--email", email,
        "--password", password,
    ]

    # Add court if specified (not "any")
    if court and court.lower() != "any":
        cmd.extend(["--court", court])

    if wait_until:
        cmd.extend(["--wait-until", wait_until])
    # Otherwise, use default behavior: wait until 1 minute before reservation time

    # Format court for display
    court_display = "Any" if court.lower() == "any" else court.replace("Pickleball Court ", "").replace(" (Bubble B)", "")

    # Send initial response
    embed = discord.Embed(
        title="🎾 Court Booking Started",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    embed.add_field(name="Date", value=date, inline=True)
    embed.add_field(name="Time", value=time, inline=True)
    embed.add_field(name="Duration", value=f"{duration}h", inline=True)
    embed.add_field(name="Court", value=court_display, inline=True)

    if wait_until:
        embed.add_field(name="Wait Until", value=wait_until, inline=True)

    embed.set_footer(text="Running in background... Results will be sent via DM.")

    await interaction.response.send_message(embed=embed, ephemeral=True)

    # Run the booking script in background
    task_desc = f"Court Booking: {date} @ {time} (Court: {court_display})"
    asyncio.create_task(_run_script(interaction, cmd, "Court Booking", task_desc))


@tree.command(name="check-availability", description="Check available court slots for a date")
@app_commands.describe(
    date="Date to check (today, tomorrow, +3d, 12/15, or latest)",
)
@app_commands.autocomplete(date=date_autocomplete)
async def check_availability(
    interaction: discord.Interaction,
    date: str = "latest",
):
    """Check available court slots and book directly from the results."""
    # Check for user credentials
    credentials = get_user_credentials(interaction.user.id)
    if not credentials:
        await interaction.response.send_message(
            "❌ You haven't registered your credentials yet.\n"
            "Use `/register` to save your CourtReserve login first.",
            ephemeral=True
        )
        return

    email, password = credentials

    # Build command
    cmd = [
        get_python_path(),
        str(get_script_dir() / "check_slots.py"),
        "--date", date,
        "--email", email,
        "--password", password,
        "--format", "json",  # Use JSON for easy parsing
    ]

    # Send initial response
    await interaction.response.defer(ephemeral=True, thinking=True)

    # Run the script and capture output (streaming to console)
    task_name = "Check Availability"
    task_desc = f"Checking slots: {date}"
    task_id = f"check-{datetime.now().strftime('%H%M%S')}-{interaction.user.id}"
    user_id = interaction.user.id
    log.info(f"[Check Availability] Starting: {task_desc}")

    try:
        env = os.environ.copy()
        env["HEADLESS"] = "true"

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=get_script_dir(),
            env=env,
        )

        # Track this task
        if user_id not in running_tasks:
            running_tasks[user_id] = []
        running_tasks[user_id].append((task_id, process, task_desc))

        # Set up cancel view for live logs
        cancel_view = LiveLogCancelView(
            user_id=user_id,
            task_id=task_id,
            process=process,
        )

        # Stream output line by line (shows progress in console)
        output_lines = []
        last_update_time = datetime.now()
        status_message = None

        async def read_output():
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                decoded = line.decode().strip()
                if decoded:
                    output_lines.append(decoded)
                    log.info(f"[Check Availability] {decoded}")

        # Start reading output
        read_task = asyncio.create_task(read_output())

        # Send periodic log updates while waiting
        while not read_task.done() or process.returncode is None:
            await asyncio.sleep(3)

            # Check if cancelled via button
            if cancel_view.cancelled:
                break

            # Send a log update every 10 seconds if there's new output
            if output_lines and (datetime.now() - last_update_time).seconds >= 10:
                recent_lines = output_lines[-10:]
                log_text = "\n".join(recent_lines)
                if len(log_text) > 1900:
                    log_text = log_text[-1900:]

                try:
                    if status_message:
                        await status_message.edit(content=f"📋 **Live Log:**\n```\n{log_text}\n```", view=cancel_view)
                    else:
                        try:
                            status_message = await interaction.user.send(
                                f"📋 **Live Log ({task_name}):**\n```\n{log_text}\n```",
                                view=cancel_view,
                            )
                        except discord.Forbidden:
                            pass  # DMs disabled
                    last_update_time = datetime.now()
                except Exception as e:
                    log.warning(f"Failed to send log update: {e}")

            if process.returncode is not None:
                break

        # Ensure we've read all output
        await read_task
        await process.wait()
        output = "\n".join(output_lines)
        log.info(f"[Check Availability] Finished with exit code {process.returncode}")

        # Remove from running tasks
        if user_id in running_tasks:
            running_tasks[user_id] = [t for t in running_tasks[user_id] if t[0] != task_id]
            if not running_tasks[user_id]:
                del running_tasks[user_id]

        # Delete the live log message
        if status_message:
            try:
                await status_message.delete()
            except:
                pass

        # Check if cancelled
        if cancel_view.cancelled:
            # Already handled by cancel button callback
            return

        if process.returncode != 0:
            await interaction.followup.send(
                f"❌ Failed to check slots. Check logs:\n```\n{output[-1500:]}\n```",
                ephemeral=True,
            )
            return

        # Parse JSON from output
        json_match = re.search(r"===JSON_START===\n(.+?)\n===JSON_END===", output, re.DOTALL)

        if not json_match:
            await interaction.followup.send(
                f"❌ Could not parse slot data. Raw output:\n```\n{output[-1000:]}\n```",
                ephemeral=True,
            )
            return

        data = json.loads(json_match.group(1))
        slots_data = data.get("slots", {})
        ranges_data = data.get("ranges", {})  # Pre-grouped time ranges
        date_str = data.get("date", date)
        total_slots = data.get("total_slots", 0)

        # Build embed with inline fields (grid on desktop, stacks on mobile)
        embed = discord.Embed(
            title=f"🎾 Available Slots — {date_str}",
            color=discord.Color.green() if total_slots > 0 else discord.Color.orange(),
            timestamp=datetime.now(),
        )

        if not slots_data:
            embed.description = "😔 No slots available for this date."
        else:
            # Add inline fields for each court (creates a grid layout)
            for court in sorted(ranges_data.keys(), key=sort_courts):
                ranges = ranges_data[court]
                slot_count = len(slots_data.get(court, []))
                # Shorten court name
                short_name = court.replace("Pickleball Court ", "").replace(" (Bubble B)", "").replace("#", "")

                if ranges:
                    # Join ranges with newlines for vertical display
                    # Add zero-width space at end for padding between fields
                    ranges_str = "\n".join(ranges) + "\n\u200b"
                    embed.add_field(
                        name=f"Court {short_name}",
                        value=ranges_str,
                        inline=True,  # Creates grid layout
                    )
                else:
                    embed.add_field(
                        name=f"Court {short_name}",
                        value="No slots\n\u200b",
                        inline=True,
                    )

            embed.set_footer(text=f"Total: {total_slots} slot(s) across {len(slots_data)} court(s)")

        # Add quick-book dropdown if there are slots
        view = None
        if slots_data:
            view = BookSlotView(slots_data, date, interaction.user.id)

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    except asyncio.TimeoutError:
        # Clean up task tracking
        if user_id in running_tasks:
            running_tasks[user_id] = [t for t in running_tasks[user_id] if t[0] != task_id]
            if not running_tasks[user_id]:
                del running_tasks[user_id]
        await interaction.followup.send(
            "❌ Request timed out. The server may be slow. Try again.",
            ephemeral=True,
        )
    except Exception as e:
        # Clean up task tracking
        if user_id in running_tasks:
            running_tasks[user_id] = [t for t in running_tasks[user_id] if t[0] != task_id]
            if not running_tasks[user_id]:
                del running_tasks[user_id]
        log.error(f"Error checking slots: {e}")
        await interaction.followup.send(
            f"❌ Error checking slots: {e}",
            ephemeral=True,
        )


# ============== Scheduling Commands ==============

DAYS_OF_WEEK = [
    ("monday", "Monday", 0),
    ("tuesday", "Tuesday", 1),
    ("wednesday", "Wednesday", 2),
    ("thursday", "Thursday", 3),
    ("friday", "Friday", 4),
    ("saturday", "Saturday", 5),
    ("sunday", "Sunday", 6),
]

DAY_NAMES = {num: name for _, name, num in DAYS_OF_WEEK}

# Helpers
def _format_schedule_when(sched: dict) -> tuple[str, datetime | None]:
    """Return human display string and parsed datetime (if one-time)."""
    if sched.get("one_time") and sched.get("run_at"):
        try:
            dt = datetime.fromisoformat(sched["run_at"])
            return dt.strftime("%a %m/%d @ %I:%M %p"), dt
        except Exception:
            return "Invalid date/time", None

    day = sched.get("day_of_week")
    hour = sched.get("hour", 0)
    minute = sched.get("minute", 0)
    day_name = DAY_NAMES.get(day, "?")
    time_12h = f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"
    return f"{day_name} @ {time_12h}", None


def _next_n_dates(n: int = 5) -> list[datetime]:
    """Return next n dates including today."""
    today = datetime.now().date()
    return [today + timedelta(days=i) for i in range(n)]

# Command group for schedule commands
schedule_group = app_commands.Group(name="schedule", description="Manage recurring scheduled tasks")
tree.add_command(schedule_group)


@schedule_group.command(name="openplay", description="Schedule recurring open play registration")
@app_commands.describe(
    day="Day of week to run (Choose Thursday to book for open play on Tuesday, Saturday for Thursday)",
    time="Time to run (e.g., 19:00 for 7 PM)",
)
@app_commands.choices(day=[
    app_commands.Choice(name="Thursday", value=3),
    app_commands.Choice(name="Saturday", value=5),
])
async def schedule_openplay(
    interaction: discord.Interaction,
    day: int,
    time: str,
):
    """Schedule a recurring open play registration."""
    # Check credentials
    if not user_exists(interaction.user.id):
        await interaction.response.send_message(
            "❌ You need to `/register` first.",
            ephemeral=True,
        )
        return

    # Parse time
    try:
        parts = time.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError()
    except:
        await interaction.response.send_message(
            "❌ Invalid time format. Use HH:MM (e.g., 19:00 for 7 PM).",
            ephemeral=True,
        )
        return

    # Save schedule
    schedule_id = save_schedule(
        discord_id=interaction.user.id,
        task_type="openplay",
        day_of_week=day,
        hour=hour,
        minute=minute,
    )

    day_name = DAY_NAMES[day]
    time_12h = f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"

    embed = discord.Embed(
        title="✅ Schedule Created",
        description=f"Open play registration will run automatically!",
        color=discord.Color.green(),
    )
    embed.add_field(name="Day", value=day_name, inline=True)
    embed.add_field(name="Time", value=time_12h, inline=True)
    embed.add_field(name="Schedule ID", value=f"#{schedule_id}", inline=True)
    embed.set_footer(text="Use /schedule list to view, /schedule remove to delete.")

    await interaction.response.send_message(embed=embed, ephemeral=True)
    log.info(f"User {interaction.user.id} created schedule #{schedule_id}: openplay on {day_name} at {time}")

    # Immediately schedule the task (don't wait for 5-minute check)
    sched = {
        "id": schedule_id,
        "discord_id": interaction.user.id,
        "task_type": "openplay",
        "day_of_week": day,
        "hour": hour,
        "minute": minute,
        "last_run": None,
        "params": None,
    }
    if schedule_id not in _scheduled_tasks:
        task = asyncio.create_task(schedule_next_run(sched))
        _scheduled_tasks[schedule_id] = task


@schedule_group.command(name="book", description="Schedule recurring court booking")
@app_commands.describe(
    day="Day of week to run the booking script",
    run_time="Time to run the script (e.g., 07:00 for 7 AM)",
    booking_time="Court time to book (e.g., 21:00 for 9 PM)",
    duration="Duration in hours (1, 1.5, 2, 2.5, or 3)",
    court="Specific court to book (or 'any' for auto-select)",
)
@app_commands.choices(day=[
    app_commands.Choice(name="Monday", value=0),
    app_commands.Choice(name="Tuesday", value=1),
    app_commands.Choice(name="Wednesday", value=2),
    app_commands.Choice(name="Thursday", value=3),
    app_commands.Choice(name="Friday", value=4),
    app_commands.Choice(name="Saturday", value=5),
    app_commands.Choice(name="Sunday", value=6),
])
@app_commands.choices(duration=[
    app_commands.Choice(name="1 hour", value=1.0),
    app_commands.Choice(name="1.5 hours", value=1.5),
    app_commands.Choice(name="2 hours", value=2.0),
    app_commands.Choice(name="2.5 hours", value=2.5),
    app_commands.Choice(name="3 hours", value=3.0),
])
@app_commands.autocomplete(court=court_autocomplete, booking_time=time_autocomplete)
async def schedule_book(
    interaction: discord.Interaction,
    day: int,
    run_time: str,
    booking_time: str,
    duration: float,
    court: str = "any",
):
    """Schedule a recurring court booking."""
    # Check credentials
    if not user_exists(interaction.user.id):
        await interaction.response.send_message(
            "❌ You need to `/register` first.",
            ephemeral=True,
        )
        return

    # Parse run time
    try:
        parts = run_time.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError()
    except:
        await interaction.response.send_message(
            "❌ Invalid run time format. Use HH:MM (e.g., 07:00 for 7 AM).",
            ephemeral=True,
        )
        return

    # Validate booking time
    if not _validate_time(booking_time):
        await interaction.response.send_message(
            "❌ Invalid booking time format. Use HH:MM (e.g., 21:00 for 9 PM).",
            ephemeral=True,
        )
        return

    # Build params
    params = {
        "booking_time": booking_time,
        "duration": duration,
        "court": court,
    }

    # Save schedule
    schedule_id = save_schedule(
        discord_id=interaction.user.id,
        task_type="book",
        day_of_week=day,
        hour=hour,
        minute=minute,
        params=params,
    )

    day_name = DAY_NAMES[day]
    run_time_12h = f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"
    booking_time_12h = _format_12h(booking_time)
    court_display = "Any" if court.lower() == "any" else court.replace("Pickleball Court ", "").replace(" (Bubble B)", "")

    embed = discord.Embed(
        title="✅ Booking Schedule Created",
        description=f"Court booking will run automatically every {day_name}!",
        color=discord.Color.green(),
    )
    embed.add_field(name="Runs At", value=f"{day_name} @ {run_time_12h}", inline=True)
    embed.add_field(name="Books For", value=f"latest @ {booking_time_12h}", inline=True)
    embed.add_field(name="Duration", value=f"{duration}h", inline=True)
    embed.add_field(name="Court", value=court_display, inline=True)
    embed.add_field(name="Schedule ID", value=f"#{schedule_id}", inline=True)
    embed.set_footer(text="Use /schedule list to view, /schedule remove to delete.")

    await interaction.response.send_message(embed=embed, ephemeral=True)
    log.info(f"User {interaction.user.id} created schedule #{schedule_id}: book on {day_name} at {run_time} for {booking_time}")

    # Immediately schedule the task (don't wait for 5-minute check)
    sched = {
        "id": schedule_id,
        "discord_id": interaction.user.id,
        "task_type": "book",
        "day_of_week": day,
        "hour": hour,
        "minute": minute,
        "last_run": None,
        "params": params,
    }
    if schedule_id not in _scheduled_tasks:
        task = asyncio.create_task(schedule_next_run(sched))
        _scheduled_tasks[schedule_id] = task


@schedule_group.command(name="once", description="Schedule a one-time task")
@app_commands.describe(
    task="Choose what to run (open play registration or court booking)",
    when="Date/time to run (local). Format: YYYY-MM-DD HH:MM (24h).",
    booking_time="Court time to book (for booking tasks, e.g., 21:00)",
    duration="Duration in hours (for booking tasks)",
    court="Specific court to book (or 'any' for auto-select)",
)
@app_commands.choices(task=[
    app_commands.Choice(name="Open Play", value="openplay"),
    app_commands.Choice(name="Book Court", value="book"),
])
@app_commands.choices(duration=[
    app_commands.Choice(name="1 hour", value=1.0),
    app_commands.Choice(name="1.5 hours", value=1.5),
    app_commands.Choice(name="2 hours", value=2.0),
    app_commands.Choice(name="2.5 hours", value=2.5),
    app_commands.Choice(name="3 hours", value=3.0),
])
@app_commands.autocomplete(court=court_autocomplete, booking_time=time_autocomplete)
async def schedule_once(
    interaction: discord.Interaction,
    task: app_commands.Choice[str],
    when: str,
    booking_time: str | None = None,
    duration: float | None = None,
    court: str = "any",
):
    """Schedule a one-time run for open play or booking."""
    # Check credentials
    if not user_exists(interaction.user.id):
        await interaction.response.send_message(
            "❌ You need to `/register` first.",
            ephemeral=True,
        )
        return

    # Accept either a formatted datetime or a date key like "today"/"YYYY-MM-DD"
    run_dt = None
    # Option 1: key from predefined list (today + next 4 days)
    predefined = {d.strftime("%Y-%m-%d"): d for d in _next_n_dates(5)}
    if when.lower() in ("today", "tomorrow"):
        base_date = datetime.now().date() + timedelta(days=0 if when.lower() == "today" else 1)
        # Default to 19:00 if only date provided
        run_dt = datetime.combine(base_date, datetime.strptime("19:00", "%H:%M").time())
    elif when in predefined:
        run_dt = datetime.combine(predefined[when], datetime.strptime("19:00", "%H:%M").time())
    else:
        # Fallback: full datetime
        try:
            run_dt = datetime.strptime(when, "%Y-%m-%d %H:%M")
        except Exception:
            await interaction.response.send_message(
                "❌ Invalid datetime. Use `YYYY-MM-DD HH:MM` (24h), or pick from the dropdown.",
                ephemeral=True,
            )
            return

    if run_dt <= datetime.now():
        await interaction.response.send_message(
            "❌ The time you entered is in the past. Please choose a future time.",
            ephemeral=True,
        )
        return

    params = None
    if task.value == "book":
        if not booking_time or duration is None:
            await interaction.response.send_message(
                "❌ For booking, please provide `booking_time` and `duration`.",
                ephemeral=True,
            )
            return
        if not _validate_time(booking_time):
            await interaction.response.send_message(
                "❌ Invalid booking time format. Use HH:MM (e.g., 21:00).",
                ephemeral=True,
            )
            return
        params = {
            "booking_time": booking_time,
            "duration": duration,
            "court": court,
        }

    schedule_id = save_schedule(
        discord_id=interaction.user.id,
        task_type=task.value,
        day_of_week=run_dt.weekday(),
        hour=run_dt.hour,
        minute=run_dt.minute,
        params=params,
        run_at=run_dt.isoformat(),
        one_time=True,
    )

    when_display = run_dt.strftime("%a %m/%d @ %I:%M %p")
    embed = discord.Embed(
        title="✅ One-Time Schedule Created",
        description=f"{task.name} will run once at the specified time.",
        color=discord.Color.green(),
    )
    embed.add_field(name="When", value=when_display, inline=True)
    embed.add_field(name="Schedule ID", value=f"#{schedule_id}", inline=True)
    if task.value == "book" and params:
        embed.add_field(name="Books For", value=_format_12h(params['booking_time']), inline=True)
        embed.add_field(name="Duration", value=f"{params['duration']}h", inline=True)
        court_display = "Any" if court.lower() == "any" else court.replace("Pickleball Court ", "").replace(" (Bubble B)", "")
        embed.add_field(name="Court", value=court_display, inline=True)
    embed.set_footer(text="Use /schedule list to view, /schedule remove to delete.")

    await interaction.response.send_message(embed=embed, ephemeral=True)
    log.info(f"User {interaction.user.id} created one-time schedule #{schedule_id}: {task.value} at {when}")

    # Immediately schedule the task
    sched = {
        "id": schedule_id,
        "discord_id": interaction.user.id,
        "task_type": task.value,
        "day_of_week": run_dt.weekday(),
        "hour": run_dt.hour,
        "minute": run_dt.minute,
        "last_run": None,
        "params": params,
        "run_at": run_dt.isoformat(),
        "one_time": True,
    }
    if schedule_id not in _scheduled_tasks:
        task_handle = asyncio.create_task(schedule_next_run(sched))
        _scheduled_tasks[schedule_id] = task_handle


async def schedule_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[int]]:
    """Autocomplete for schedule_id - shows user's schedules."""
    schedules = get_user_schedules(interaction.user.id)

    choices = []
    for sched in schedules:
        day_name = DAY_NAMES.get(sched["day_of_week"], "?")[:3]
        hour = sched["hour"]
        minute = sched["minute"]
        time_12h = f"{hour % 12 or 12}:{minute:02d}{'AM' if hour < 12 else 'PM'}"

        # Build label based on task type
        if sched["task_type"] == "book" and sched.get("params"):
            params = sched["params"]
            booking_time = params.get("booking_time", "?")
            label = f"#{sched['id']} Book {day_name} → {_format_12h(booking_time)}"
        else:
            label = f"#{sched['id']} {sched['task_type'].title()} {day_name} @ {time_12h}"

        # Add status indicator
        if not sched["enabled"]:
            label += " (paused)"

        # Filter by current input
        if not current or current in str(sched["id"]) or current.lower() in label.lower():
            choices.append(app_commands.Choice(name=label[:100], value=sched["id"]))

    return choices[:25]


class ScheduleManageView(discord.ui.View):
    """View with dropdown and buttons for managing schedules."""

    def __init__(self, schedules: list[dict], user_id: int):
        super().__init__(timeout=300)  # 5 minute timeout
        self.schedules = {s["id"]: s for s in schedules}
        self.user_id = user_id
        self.selected_id: int | None = None

        # Create schedule selector dropdown
        options = []
        for sched in schedules:
            when_display, _ = _format_schedule_when(sched)
            status_emoji = "⏸️" if not sched["enabled"] else "⏭️" if sched.get("skip_next") else "✅"
            label = f"#{sched['id']} {sched['task_type'].title()} — {when_display}"

            options.append(discord.SelectOption(
                label=label[:100],
                value=str(sched["id"]),
                emoji=status_emoji,
            ))

        self.schedule_select = discord.ui.Select(
            placeholder="Select a schedule to manage...",
            options=options,
            row=0,
        )
        self.schedule_select.callback = self.on_select
        self.add_item(self.schedule_select)

        # Action buttons (initially disabled)
        self.pause_btn = discord.ui.Button(label="Pause", style=discord.ButtonStyle.secondary, emoji="⏸️", row=1, disabled=True)
        self.pause_btn.callback = self.on_pause
        self.add_item(self.pause_btn)

        self.skip_btn = discord.ui.Button(label="Skip Next", style=discord.ButtonStyle.secondary, emoji="⏭️", row=1, disabled=True)
        self.skip_btn.callback = self.on_skip
        self.add_item(self.skip_btn)

        self.remove_btn = discord.ui.Button(label="Remove", style=discord.ButtonStyle.danger, emoji="🗑️", row=1, disabled=True)
        self.remove_btn.callback = self.on_remove
        self.add_item(self.remove_btn)

    async def on_select(self, interaction: discord.Interaction):
        """Handle schedule selection."""
        self.selected_id = int(self.schedule_select.values[0])
        sched = self.schedules.get(self.selected_id)

        if sched:
            # Enable buttons
            self.pause_btn.disabled = False
            self.skip_btn.disabled = False
            self.remove_btn.disabled = False

            # Update pause button based on current state
            if sched["enabled"]:
                self.pause_btn.label = "Pause"
                self.pause_btn.emoji = "⏸️"
            else:
                self.pause_btn.label = "Resume"
                self.pause_btn.emoji = "▶️"

            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()

    async def on_pause(self, interaction: discord.Interaction):
        """Toggle pause/resume."""
        if not self.selected_id:
            await interaction.response.defer()
            return

        sched = self.schedules.get(self.selected_id)
        if not sched:
            await interaction.response.send_message("❌ Schedule not found.", ephemeral=True)
            return

        new_state = not sched["enabled"]
        if set_schedule_enabled(self.user_id, self.selected_id, new_state):
            sched["enabled"] = new_state

            if new_state:
                self.pause_btn.label = "Pause"
                self.pause_btn.emoji = "⏸️"
                msg = f"▶️ Schedule **#{self.selected_id}** resumed."
            else:
                self.pause_btn.label = "Resume"
                self.pause_btn.emoji = "▶️"
                msg = f"⏸️ Schedule **#{self.selected_id}** paused."

            await interaction.response.edit_message(view=self)
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message("❌ Failed to update schedule.", ephemeral=True)

    async def on_skip(self, interaction: discord.Interaction):
        """Skip next run."""
        if not self.selected_id:
            await interaction.response.defer()
            return

        if set_schedule_skip_next(self.user_id, self.selected_id, True):
            sched = self.schedules.get(self.selected_id)
            day_name = DAY_NAMES.get(sched["day_of_week"], "Unknown") if sched else "Unknown"
            await interaction.response.send_message(
                f"⏭️ Schedule **#{self.selected_id}** will skip its next run on {day_name}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("❌ Failed to update schedule.", ephemeral=True)

    async def on_remove(self, interaction: discord.Interaction):
        """Remove schedule."""
        if not self.selected_id:
            await interaction.response.defer()
            return

        schedule_id = self.selected_id

        # Cancel running process if any
        cancelled_process = False
        if schedule_id in _running_scheduled:
            process, task_name = _running_scheduled[schedule_id]
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            del _running_scheduled[schedule_id]
            cancelled_process = True

        # Cancel pending scheduled task
        if schedule_id in _scheduled_tasks:
            _scheduled_tasks[schedule_id].cancel()
            del _scheduled_tasks[schedule_id]

        # Delete from database
        if delete_schedule(self.user_id, schedule_id):
            # Remove from local list and update dropdown
            del self.schedules[schedule_id]

            if self.schedules:
                # Update dropdown options
                options = []
                for sched in self.schedules.values():
                    when_display, _ = _format_schedule_when(sched)
                    status_emoji = "⏸️" if not sched["enabled"] else "⏭️" if sched.get("skip_next") else "✅"
                    label = f"#{sched['id']} {sched['task_type'].title()} — {when_display}"
                    options.append(discord.SelectOption(label=label[:100], value=str(sched["id"]), emoji=status_emoji))

                self.schedule_select.options = options
                self.selected_id = None
                self.pause_btn.disabled = True
                self.skip_btn.disabled = True
                self.remove_btn.disabled = True

                await interaction.response.edit_message(view=self)
            else:
                # No more schedules
                await interaction.response.edit_message(
                    content="📅 All schedules removed!",
                    embed=None,
                    view=None,
                )

            if cancelled_process:
                await interaction.followup.send(
                    f"✅ Schedule **#{schedule_id}** removed.\n🛑 Running task was also cancelled.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(f"✅ Schedule **#{schedule_id}** removed.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Failed to remove schedule.", ephemeral=True)


@schedule_group.command(name="list", description="View your scheduled tasks")
async def schedule_list(interaction: discord.Interaction):
    """List all scheduled tasks for the user."""
    schedules = get_user_schedules(interaction.user.id)

    if not schedules:
        await interaction.response.send_message(
            "📅 You don't have any scheduled tasks.\n"
            "Use `/schedule openplay` or `/schedule book` to create one!",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="📅 Your Scheduled Tasks",
        color=discord.Color.blue(),
    )

    for sched in schedules:
        when_display, run_dt = _format_schedule_when(sched)

        # Build status string
        if not sched["enabled"]:
            status = "⏸️ Paused"
        elif sched.get("skip_next"):
            status = "⏭️ Skipping next"
        elif sched["id"] in _running_scheduled:
            status = "🔄 Running"
        else:
            status = "✅ Active"

        last_run = sched["last_run"] or "Never"

        # Build description based on task type
        if sched["task_type"] == "book" and sched.get("params"):
            params = sched["params"]
            booking_time = params.get("booking_time", "?")
            duration = params.get("duration", "?")
            court = params.get("court", "any")
            court_display = "Any" if court == "any" else court.replace("Pickleball Court ", "").replace(" (Bubble B)", "")

            desc = (
                f"**Runs:** {when_display}\n"
                f"**Books:** latest @ {_format_12h(booking_time)} ({duration}h)\n"
                f"**Court:** {court_display}\n"
                f"**Status:** {status} • Last run: {last_run}"
            )
        else:
            # openplay or other
            desc = f"**When:** {when_display}\n**Status:** {status}\n**Last run:** {last_run}"

        # Make ID very prominent with emoji
        embed.add_field(
            name=f"🆔 {sched['id']}  •  {sched['task_type'].title()}",
            value=desc,
            inline=False,
        )

    embed.set_footer(text="Select a schedule below to manage it.")

    # Add interactive view
    view = ScheduleManageView(schedules, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@schedule_group.command(name="remove", description="Remove a scheduled task")
@app_commands.describe(
    schedule_id="Select the schedule to remove",
)
@app_commands.autocomplete(schedule_id=schedule_autocomplete)
async def schedule_remove(
    interaction: discord.Interaction,
    schedule_id: int,
):
    """Remove a scheduled task."""
    user_id = interaction.user.id

    # Cancel running process if any
    cancelled_process = False
    if schedule_id in _running_scheduled:
        process, task_name = _running_scheduled[schedule_id]
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        del _running_scheduled[schedule_id]
        cancelled_process = True
        log.info(f"Cancelled running process for schedule #{schedule_id}")

    # Cancel pending scheduled task
    if schedule_id in _scheduled_tasks:
        _scheduled_tasks[schedule_id].cancel()
        del _scheduled_tasks[schedule_id]

    # Delete from database
    if delete_schedule(user_id, schedule_id):
        if cancelled_process:
            msg = f"✅ Schedule **#{schedule_id}** has been removed.\n🛑 The running task was also cancelled."
        else:
            msg = f"✅ Schedule **#{schedule_id}** has been removed."
        await interaction.response.send_message(msg, ephemeral=True)
        log.info(f"User {user_id} removed schedule #{schedule_id}")
    else:
        await interaction.response.send_message(
            f"❌ Schedule #{schedule_id} not found or doesn't belong to you.",
            ephemeral=True,
        )


@schedule_group.command(name="pause", description="Pause a scheduled task")
@app_commands.describe(
    schedule_id="Select the schedule to pause",
)
@app_commands.autocomplete(schedule_id=schedule_autocomplete)
async def schedule_pause(
    interaction: discord.Interaction,
    schedule_id: int,
):
    """Pause a scheduled task."""
    if set_schedule_enabled(interaction.user.id, schedule_id, False):
        await interaction.response.send_message(
            f"⏸️ Schedule **#{schedule_id}** has been paused.\n"
            f"Use `/schedule resume` to re-enable it.",
            ephemeral=True,
        )
        log.info(f"User {interaction.user.id} paused schedule #{schedule_id}")
    else:
        await interaction.response.send_message(
            f"❌ Schedule #{schedule_id} not found or doesn't belong to you.",
            ephemeral=True,
        )


@schedule_group.command(name="resume", description="Resume a paused scheduled task")
@app_commands.describe(
    schedule_id="Select the schedule to resume",
)
@app_commands.autocomplete(schedule_id=schedule_autocomplete)
async def schedule_resume(
    interaction: discord.Interaction,
    schedule_id: int,
):
    """Resume a paused scheduled task."""
    if set_schedule_enabled(interaction.user.id, schedule_id, True):
        await interaction.response.send_message(
            f"▶️ Schedule **#{schedule_id}** has been resumed and will run at its next scheduled time.",
            ephemeral=True,
        )
        log.info(f"User {interaction.user.id} resumed schedule #{schedule_id}")
    else:
        await interaction.response.send_message(
            f"❌ Schedule #{schedule_id} not found or doesn't belong to you.",
            ephemeral=True,
        )


@schedule_group.command(name="skip", description="Skip the next scheduled run")
@app_commands.describe(
    schedule_id="Select the schedule to skip",
)
@app_commands.autocomplete(schedule_id=schedule_autocomplete)
async def schedule_skip(
    interaction: discord.Interaction,
    schedule_id: int,
):
    """Skip the next scheduled run without cancelling the schedule."""
    user_id = interaction.user.id

    # Verify the schedule belongs to the user
    schedules = get_user_schedules(user_id)
    sched = next((s for s in schedules if s["id"] == schedule_id), None)

    if not sched:
        await interaction.response.send_message(
            f"❌ Schedule #{schedule_id} not found or doesn't belong to you.",
            ephemeral=True,
        )
        return

    # Set skip_next flag
    if set_schedule_skip_next(user_id, schedule_id, True):
        day_name = DAY_NAMES.get(sched["day_of_week"], "Unknown")
        await interaction.response.send_message(
            f"⏭️ Schedule **#{schedule_id}** will skip its next run.\n"
            f"The {sched['task_type']} on {day_name} will be skipped once, then resume normally.",
            ephemeral=True,
        )
        log.info(f"User {user_id} set skip_next for schedule #{schedule_id}")
    else:
        await interaction.response.send_message(
            f"❌ Failed to update schedule #{schedule_id}.",
            ephemeral=True,
        )


@schedule_group.command(name="cancel", description="Cancel a running scheduled task and skip this instance")
@app_commands.describe(
    schedule_id="Select the schedule to cancel (if currently running)",
)
@app_commands.autocomplete(schedule_id=schedule_autocomplete)
async def schedule_cancel(
    interaction: discord.Interaction,
    schedule_id: int,
):
    """Cancel a running scheduled task and skip this week's run."""
    user_id = interaction.user.id

    # Verify the schedule belongs to the user
    schedules = get_user_schedules(user_id)
    sched = next((s for s in schedules if s["id"] == schedule_id), None)

    if not sched:
        await interaction.response.send_message(
            f"❌ Schedule #{schedule_id} not found or doesn't belong to you.",
            ephemeral=True,
        )
        return

    cancelled_process = False
    cancelled_pending = False

    # Check if this schedule has a running process
    if schedule_id in _running_scheduled:
        process, task_name = _running_scheduled[schedule_id]
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        del _running_scheduled[schedule_id]
        cancelled_process = True
        log.info(f"User {user_id} cancelled running process for schedule #{schedule_id}")

    # Also cancel the pending scheduled task (so it doesn't run again soon)
    if schedule_id in _scheduled_tasks:
        _scheduled_tasks[schedule_id].cancel()
        del _scheduled_tasks[schedule_id]
        cancelled_pending = True

    # Mark as "ran today" to skip this instance
    today_str = datetime.now().strftime("%Y-%m-%d")
    update_schedule_last_run(schedule_id, today_str)

    # Reschedule for next week
    sched_data = {
        "id": schedule_id,
        "discord_id": user_id,
        "task_type": sched["task_type"],
        "day_of_week": sched["day_of_week"],
        "hour": sched["hour"],
        "minute": sched["minute"],
        "last_run": today_str,
        "params": sched.get("params"),
    }
    task = asyncio.create_task(schedule_next_run(sched_data))
    _scheduled_tasks[schedule_id] = task

    day_name = DAY_NAMES.get(sched["day_of_week"], "Unknown")

    if cancelled_process:
        msg = (
            f"🛑 **Cancelled running task** for schedule **#{schedule_id}**\n"
            f"This week's {sched['task_type']} has been stopped and skipped.\n"
            f"Next run: {day_name} (next week)"
        )
    else:
        msg = (
            f"⏭️ **Skipped this week's run** for schedule **#{schedule_id}**\n"
            f"The {sched['task_type']} will not run this week.\n"
            f"Next run: {day_name} (next week)"
        )

    await interaction.response.send_message(msg, ephemeral=True)
    log.info(f"User {user_id} skipped schedule #{schedule_id} for this week")


# ============== Background Scheduler ==============

# Track scheduled asyncio tasks to avoid duplicates
_scheduled_tasks: dict[int, asyncio.Task] = {}

# Track currently running scheduled processes: schedule_id -> (process, task_name)
_running_scheduled: dict[int, tuple[asyncio.subprocess.Process, str]] = {}


async def schedule_next_run(sched: dict):
    """Schedule a task to run at its exact scheduled time."""
    schedule_id = sched["id"]
    is_one_time = sched.get("one_time") or False
    run_at = sched.get("run_at")

    # Calculate seconds until next run
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    if is_one_time and run_at:
        try:
            next_run = datetime.fromisoformat(run_at)
        except Exception:
            log.error(f"Schedule #{schedule_id}: Invalid run_at format '{run_at}', deleting schedule")
            admin_delete_schedule(schedule_id)
            return
        wait_seconds = (next_run - now).total_seconds()
        if wait_seconds < 0:
            log.info(f"Schedule #{schedule_id} (one-time) is in the past, deleting")
            admin_delete_schedule(schedule_id)
            return
        day_name = next_run.strftime("%A")
        target_hour = next_run.hour
        target_minute = next_run.minute
    else:
        target_day = sched["day_of_week"]
        target_hour = sched["hour"]
        target_minute = sched["minute"]

        # Build target datetime for this week
        target_time_today = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

        # Calculate days until target day
        days_ahead = target_day - now.weekday()

        if days_ahead < 0:
            # Target day already passed this week
            days_ahead += 7
        elif days_ahead == 0:
            # Same day - check if time already passed OR already ran today
            if now >= target_time_today or sched.get("last_run") == today_str:
                days_ahead = 7  # Schedule for next week
        # else: days_ahead > 0, target day is later this week (keep as-is)

        next_run = target_time_today + timedelta(days=days_ahead)
        wait_seconds = (next_run - now).total_seconds()

        # Safety: ensure we're not waiting negative time
        if wait_seconds < 0:
            days_ahead += 7
            next_run += timedelta(days=7)
            wait_seconds = (next_run - now).total_seconds()

        day_name = DAY_NAMES.get(target_day, "Unknown")

    log.info(f"Schedule #{schedule_id}: Next run at {next_run.strftime('%Y-%m-%d %H:%M:%S')} ({wait_seconds:.0f}s from now)")

    # Send reminder 1 hour before (if more than 1 hour away)
    reminder_seconds = wait_seconds - 3600  # 1 hour = 3600 seconds
    if reminder_seconds > 60:  # Only if more than 1 minute until reminder
        await asyncio.sleep(reminder_seconds)

        # Send reminder DM
        try:
            user = await client.fetch_user(sched["discord_id"])
            task_type = sched["task_type"]
            time_12h = f"{target_hour % 12 or 12}:{target_minute:02d} {'AM' if target_hour < 12 else 'PM'}"

            if task_type == "book":
                params = sched.get("params") or {}
                booking_time = params.get("booking_time", "?")
                reminder_msg = (
                    f"⏰ **Reminder** (Schedule #{schedule_id})\n"
                    f"Your scheduled court booking runs in **1 hour**!\n"
                    f"📅 {day_name} at {time_12h}\n"
                    f"🎾 Booking for: {_format_12h(booking_time)}\n\n"
                    f"Use `/schedule cancel` or `/schedule skip` to skip this run."
                )
            else:
                reminder_msg = (
                    f"⏰ **Reminder** (Schedule #{schedule_id})\n"
                    f"Your scheduled {task_type} runs in **1 hour**!\n"
                    f"📅 {day_name} at {time_12h}\n\n"
                    f"Use `/schedule cancel` or `/schedule skip` to skip this run."
                )

            await user.send(reminder_msg)
            log.info(f"Schedule #{schedule_id}: Sent 1-hour reminder to user {sched['discord_id']}")
        except Exception as e:
            log.warning(f"Schedule #{schedule_id}: Failed to send reminder: {e}")

        # Wait the remaining hour
        await asyncio.sleep(3600)
    else:
        # Less than 1 hour away, just wait
        await asyncio.sleep(wait_seconds)

    # Double-check we haven't already run today (in case of restart)
    today_str = datetime.now().strftime("%Y-%m-%d")
    schedules = get_all_schedules()
    current_sched = next((s for s in schedules if s["id"] == schedule_id), None)

    if not current_sched:
        log.info(f"Schedule #{schedule_id} was deleted, skipping")
        return

    if current_sched["last_run"] == today_str:
        log.info(f"Schedule #{schedule_id} already ran today, skipping")
    elif current_sched.get("skip_next"):
        log.info(f"Schedule #{schedule_id} marked to skip, skipping this run")
        # Clear the skip flag and notify user
        clear_schedule_skip(schedule_id)
        try:
            user = await client.fetch_user(current_sched["discord_id"])
            msg = (
                f"⏭️ **Skipped scheduled task** #{schedule_id}\n"
                f"This run was skipped as requested."
            )
            if current_sched.get("one_time"):
                msg += "\nThis was a one-time schedule and will now be deleted."
            else:
                msg += "\nThe schedule will resume next week."
            await user.send(msg)
        except:
            pass
        update_schedule_last_run(schedule_id, today_str)
        if current_sched.get("one_time"):
            admin_delete_schedule(schedule_id)
            if schedule_id in _scheduled_tasks:
                del _scheduled_tasks[schedule_id]
            return
    else:
        log.info(f"⏰ Running scheduled task #{schedule_id} (precise trigger)")
        try:
            await run_scheduled_task(current_sched)
            update_schedule_last_run(schedule_id, today_str)
            log.info(f"Schedule #{schedule_id} completed successfully")
        except Exception as e:
            log.error(f"Schedule #{schedule_id} failed: {e}")

    # Remove from tracking
    if schedule_id in _scheduled_tasks:
        del _scheduled_tasks[schedule_id]

    # One-time schedules: delete after run/skip
    if current_sched and current_sched.get("one_time"):
        log.info(f"Schedule #{schedule_id} is one-time; deleting after completion/skip")
        admin_delete_schedule(schedule_id)
        return

    # Reschedule (reload to get updated last_run)
    schedules = get_all_schedules()
    updated_sched = next((s for s in schedules if s["id"] == schedule_id), None)
    if updated_sched:
        task = asyncio.create_task(schedule_next_run(updated_sched))
        _scheduled_tasks[schedule_id] = task


@tasks.loop(minutes=5)
async def check_schedules():
    """Periodically refresh scheduled tasks (handles new/deleted schedules)."""
    schedules = get_all_schedules()
    current_ids = {s["id"] for s in schedules}

    # Cancel tasks for deleted schedules
    for sched_id in list(_scheduled_tasks.keys()):
        if sched_id not in current_ids:
            log.info(f"Schedule #{sched_id} removed, cancelling task")
            _scheduled_tasks[sched_id].cancel()
            del _scheduled_tasks[sched_id]

    # Add tasks for new schedules
    for sched in schedules:
        if sched["id"] not in _scheduled_tasks:
            task = asyncio.create_task(schedule_next_run(sched))
            _scheduled_tasks[sched["id"]] = task


@check_schedules.before_loop
async def before_check_schedules():
    """Wait until the bot is ready before starting the scheduler."""
    await client.wait_until_ready()

    # Initial scheduling of all tasks
    schedules = get_all_schedules()
    for sched in schedules:
        if sched["id"] not in _scheduled_tasks:
            task = asyncio.create_task(schedule_next_run(sched))
            _scheduled_tasks[sched["id"]] = task

    log.info(f"Scheduler started - {len(schedules)} task(s) scheduled with second-precision timing")


async def run_scheduled_task(sched: dict):
    """Run a scheduled task."""
    discord_id = sched["discord_id"]
    task_type = sched["task_type"]
    params = sched.get("params") or {}

    # Get user credentials
    credentials = get_user_credentials(discord_id)
    if not credentials:
        log.warning(f"Schedule #{sched['id']}: User {discord_id} has no credentials")
        return

    email, password = credentials

    # Get the Discord user to send notifications
    try:
        user = await client.fetch_user(discord_id)
    except:
        log.warning(f"Schedule #{sched['id']}: Could not fetch user {discord_id}")
        user = None

    if task_type == "openplay":
        # Build open play command
        cmd = [
            get_python_path(),
            str(get_script_dir() / "open_play.py"),
            "--date", "latest",
            "--email", email,
            "--password", password,
        ]

        # Notify user that scheduled task is starting
        if user:
            try:
                await user.send(
                    f"🕐 **Scheduled Task Starting** (Schedule #{sched['id']})\n"
                    f"Running your scheduled open play registration..."
                )
            except:
                pass

        # Run the script
        await run_scheduled_script(cmd, "Open Play (Scheduled)", discord_id, user, sched["id"])

    elif task_type == "book":
        # Get params
        booking_time = params.get("booking_time", "21:00")
        duration = params.get("duration", 2.0)
        court = params.get("court", "any")

        # Build court booking command
        cmd = [
            get_python_path(),
            str(get_script_dir() / "court_booking.py"),
            "--time", booking_time,
            "--duration", str(duration),
            "--date", "latest",  # Always book the latest available date
            "--parallel",
            "--attempts", "3",
            "--email", email,
            "--password", password,
            "--no-wait",  # Execute immediately - scheduler already handles timing
        ]

        # Add court if specified
        if court and court.lower() != "any":
            cmd.extend(["--court", court])

        court_display = "Any" if court.lower() == "any" else court.replace("Pickleball Court ", "").replace(" (Bubble B)", "")
        booking_time_12h = _format_12h(booking_time)

        # Notify user that scheduled task is starting
        if user:
            try:
                await user.send(
                    f"🕐 **Scheduled Booking Starting** (Schedule #{sched['id']})\n"
                    f"Booking court for **{booking_time_12h}** ({duration}h) — Court: {court_display}"
                )
            except:
                pass

        # Run the script
        await run_scheduled_script(cmd, "Court Booking (Scheduled)", discord_id, user, sched["id"])


async def run_scheduled_script(cmd: list[str], task_name: str, discord_id: int, user, schedule_id: int | None = None):
    """Run a script for a scheduled task with live log updates."""
    try:
        env = os.environ.copy()
        env["HEADLESS"] = "true"

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=get_script_dir(),
            env=env,
        )

        # Track this running process so it can be cancelled
        if schedule_id is not None:
            _running_scheduled[schedule_id] = (process, task_name)

        output_lines = []
        last_update_time = datetime.now()
        status_message = None
        cancel_view = LiveLogCancelView(
            user_id=discord_id,
            schedule_id=schedule_id,
            process=process,
        ) if user else None

        async def read_output():
            """Read output line by line."""
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                decoded = line.decode().strip()
                if decoded:
                    output_lines.append(decoded)
                    log.info(f"[{task_name}] {decoded}")

        # Start reading output
        read_task = asyncio.create_task(read_output())

        # Send periodic log updates while waiting
        while not read_task.done() or process.returncode is None:
            await asyncio.sleep(3)  # Check every 3 seconds

            # Check if cancelled via button
            if cancel_view and cancel_view.cancelled:
                break

            # Send a log update every 10 seconds if there's new output
            if user and output_lines and (datetime.now() - last_update_time).seconds >= 10:
                recent_lines = output_lines[-10:]  # Last 10 lines
                log_text = "\n".join(recent_lines)

                if len(log_text) > 1900:
                    log_text = log_text[-1900:]

                try:
                    if status_message:
                        await status_message.edit(content=f"📋 **Live Log:**\n```\n{log_text}\n```", view=cancel_view)
                    else:
                        status_message = await user.send(
                            f"📋 **Live Log ({task_name}):**\n```\n{log_text}\n```",
                            view=cancel_view,
                        )
                    last_update_time = datetime.now()
                except Exception as e:
                    log.warning(f"Failed to send log update: {e}")

            # Check if process finished
            if process.returncode is not None:
                break

        # Ensure we've read all output
        await read_task
        await process.wait()
        output = "\n".join(output_lines)

        # Delete the live log message - we'll send a final result
        if status_message:
            try:
                await status_message.delete()
            except:
                pass
            status_message = None

        # Check if cancelled (don't send failure message)
        if cancel_view and cancel_view.cancelled:
            # Already handled by the cancel button callback
            return output, process.returncode

        # Send result to user
        if user:
            if process.returncode == 0:
                embed = discord.Embed(
                    title=f"🎉 {task_name} - SUCCESS!",
                    color=discord.Color.green(),
                    timestamp=datetime.now(),
                )

                # Extract useful details from output
                success_details = []
                for line in output_lines:
                    line_lower = line.lower()
                    if any(kw in line_lower for kw in ["success", "reservation saved", "registered", "booked", "confirmed"]):
                        clean_line = line.split("]")[-1].strip() if "]" in line else line
                        success_details.append(clean_line)

                if success_details:
                    embed.description = "✅ " + "\n✅ ".join(success_details[:3])
                else:
                    embed.description = "✅ Your scheduled task completed successfully!"

                embed.set_footer(text="You're all set! See you on the court 🏸")

            else:
                embed = discord.Embed(
                    title=f"❌ {task_name} - FAILED",
                    color=discord.Color.red(),
                    timestamp=datetime.now(),
                )

                # Try to find the actual error message
                error_lines = []
                for line in output_lines:
                    line_lower = line.lower()
                    if any(kw in line_lower for kw in ["error", "failed", "unavailable", "exception", "could not"]):
                        clean_line = line.split("]")[-1].strip() if "]" in line else line
                        error_lines.append(clean_line)

                if error_lines:
                    embed.description = "**What went wrong:**\n" + "\n".join(error_lines[-3:])
                else:
                    last_lines = "\n".join(output_lines[-5:])
                    embed.description = f"**Last output:**\n```\n{last_lines[:1000]}\n```"

                embed.add_field(
                    name="💡 What to do",
                    value="Try running the command again, or check if the court/event is still available.",
                    inline=False,
                )

            # Add log preview and full log button
            view = FullLogView(output, task_name) if output else None

            if len(output) <= 800:
                embed.add_field(name="📜 Full Log", value=f"```\n{output}\n```", inline=False)
                view = None
            elif len(output) <= 1500:
                embed.add_field(name="📜 Log (truncated)", value=f"```\n...{output[-700:]}\n```", inline=False)
            else:
                embed.add_field(name="📜 Log (truncated)", value=f"```\n...{output[-500:]}\n```", inline=False)

            try:
                await user.send(embed=embed, view=view)
            except:
                log.warning(f"Could not DM user {discord_id} with scheduled task result")

    except Exception as e:
        log.error(f"Error running scheduled script: {e}")
        if user:
            try:
                embed = discord.Embed(
                    title=f"💥 {task_name} - ERROR",
                    description=f"**An unexpected error occurred:**\n```\n{str(e)[:1000]}\n```",
                    color=discord.Color.red(),
                    timestamp=datetime.now(),
                )
                await user.send(embed=embed)
            except:
                pass
    finally:
        # Remove from running scheduled tasks tracking
        if schedule_id is not None and schedule_id in _running_scheduled:
            del _running_scheduled[schedule_id]


async def _run_script(interaction: discord.Interaction, cmd: list[str], task_name: str, task_desc: str = ""):
    """Run a booking script and report results with live log updates."""
    user_id = interaction.user.id
    user_mention = interaction.user.mention
    task_id = str(uuid.uuid4())[:8]  # Short unique ID

    try:
        # Build environment with headless mode enabled and tracing on
        env = os.environ.copy()
        env["HEADLESS"] = "true"
        env["ENABLE_TRACING"] = "true"

        # Start the process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=get_script_dir(),
            env=env,
        )

        # Track this task
        if user_id not in running_tasks:
            running_tasks[user_id] = []
        description = task_desc or task_name
        running_tasks[user_id].append((task_id, process, description))

        # Collect output and send periodic updates
        output_lines = []
        last_update_time = datetime.now()
        status_message = None
        cancel_view = LiveLogCancelView(
            user_id=user_id,
            task_id=task_id,
            process=process,
        )

        async def read_output():
            """Read output line by line."""
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                decoded = line.decode().strip()
                if decoded:
                    output_lines.append(decoded)
                    log.info(f"[{task_name}] {decoded}")

        # Start reading output
        read_task = asyncio.create_task(read_output())

        # Send periodic log updates while waiting
        while not read_task.done() or process.returncode is None:
            await asyncio.sleep(3)  # Check every 3 seconds

            # Check if cancelled via button
            if cancel_view.cancelled:
                break

            # Send a log update every 10 seconds if there's new output
            if output_lines and (datetime.now() - last_update_time).seconds >= 10:
                recent_lines = output_lines[-10:]  # Last 10 lines
                log_text = "\n".join(recent_lines)

                if len(log_text) > 1900:
                    log_text = log_text[-1900:]

                try:
                    if status_message:
                        await status_message.edit(content=f"📋 **Live Log:**\n```\n{log_text}\n```", view=cancel_view)
                    else:
                        # Send live log via DM for privacy
                        try:
                            status_message = await interaction.user.send(
                                f"📋 **Live Log ({task_name}):**\n```\n{log_text}\n```",
                                view=cancel_view,
                            )
                        except discord.Forbidden:
                            # Fall back to channel if DMs disabled
                            status_message = await interaction.followup.send(
                                f"📋 **Live Log:**\n```\n{log_text}\n```",
                                view=cancel_view,
                            )
                    last_update_time = datetime.now()
                except Exception as e:
                    log.warning(f"Failed to send log update: {e}")

            # Check if process finished
            if process.returncode is not None:
                break

        # Ensure we've read all output
        await read_task
        await process.wait()

        output = "\n".join(output_lines)

        # Remove this specific task from running tasks
        if user_id in running_tasks:
            running_tasks[user_id] = [
                t for t in running_tasks[user_id] if t[0] != task_id
            ]
            if not running_tasks[user_id]:
                del running_tasks[user_id]

        # Delete the live log message - we'll send a final result
        if status_message:
            try:
                await status_message.delete()
            except:
                pass
            status_message = None  # Prevent double deletion below

        # Check if cancelled (don't send failure message)
        if cancel_view.cancelled:
            # Already handled by the cancel button callback
            return

        # Determine success/failure and notify user clearly
        if process.returncode == 0:
            # SUCCESS - Green embed with celebration
            embed = discord.Embed(
                title=f"🎉 {task_name} - SUCCESS!",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )

            # Extract useful details from output
            success_details = []
            for line in output_lines:
                line_lower = line.lower()
                if any(keyword in line_lower for keyword in ["success", "reservation saved", "registered", "booked", "confirmed"]):
                    # Clean up the line (remove timestamps and log prefixes)
                    clean_line = line.split("]")[-1].strip() if "]" in line else line
                    success_details.append(clean_line)
                elif "court" in line_lower and (":" in line or "selected" in line_lower):
                    clean_line = line.split("]")[-1].strip() if "]" in line else line
                    success_details.append(clean_line)

            if success_details:
                embed.description = "✅ " + "\n✅ ".join(success_details[:3])  # Top 3 relevant lines
            else:
                embed.description = "✅ Your reservation was completed successfully!"

            embed.set_footer(text="You're all set! See you on the court 🏸")

        else:
            # FAILURE - Red embed with clear error info
            embed = discord.Embed(
                title=f"❌ {task_name} - FAILED",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )

            # Try to find the actual error message
            error_lines = []
            for line in output_lines:
                line_lower = line.lower()
                if any(keyword in line_lower for keyword in ["error", "failed", "unavailable", "exception", "could not", "unable"]):
                    clean_line = line.split("]")[-1].strip() if "]" in line else line
                    error_lines.append(clean_line)

            if error_lines:
                embed.description = "**What went wrong:**\n" + "\n".join(error_lines[-3:])  # Last 3 error lines
            else:
                # Fall back to last few lines
                last_lines = "\n".join(output_lines[-5:])
                embed.description = f"**Last output:**\n```\n{last_lines[:1000]}\n```"

            embed.add_field(
                name="💡 What to do",
                value="Try running the command again, or check if the court/event is still available.",
                inline=False
            )
            embed.set_footer(text="Need help? Check the logs above for more details.")

        # Add log preview and full log button
        view = FullLogView(output, task_name) if output else None

        if len(output) <= 800:
            embed.add_field(name="📜 Full Log", value=f"```\n{output}\n```", inline=False)
            view = None  # No need for button if log fits
        elif len(output) <= 1500:
            # Show truncated log with button for full
            embed.add_field(name="📜 Log (truncated)", value=f"```\n...{output[-700:]}\n```", inline=False)
        else:
            # Show last few lines with button for full
            embed.add_field(name="📜 Log (truncated)", value=f"```\n...{output[-500:]}\n```", inline=False)

        # Send result via DM for privacy
        try:
            await interaction.user.send(embed=embed, view=view)
        except discord.Forbidden:
            # User has DMs disabled, fall back to followup
            await interaction.followup.send(
                content=f"{user_mention} (couldn't DM you - enable DMs for private results)",
                embed=embed,
                view=view,
            )

    except Exception as e:
        log.error(f"Error running script: {e}")

        # Remove this specific task from running tasks
        if user_id in running_tasks:
            running_tasks[user_id] = [
                t for t in running_tasks[user_id] if t[0] != task_id
            ]
            if not running_tasks[user_id]:
                del running_tasks[user_id]

        embed = discord.Embed(
            title=f"💥 {task_name} - ERROR",
            description=f"**An unexpected error occurred:**\n```\n{str(e)[:1000]}\n```",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.add_field(
            name="💡 What to do",
            value="This is usually a temporary issue. Try again in a few minutes.",
            inline=False
        )
        embed.set_footer(text="If the problem persists, check the Fly.io logs.")

        # Send error via DM for privacy
        try:
            await interaction.user.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send(content=user_mention, embed=embed)


def _validate_time(time_str: str) -> bool:
    """Validate time string format."""
    try:
        parts = time_str.split(":")
        if len(parts) != 2:
            return False
        hour, minute = int(parts[0]), int(parts[1])
        return 0 <= hour <= 23 and 0 <= minute <= 59
    except ValueError:
        return False


# ============== Admin Commands ==============

class AdminScheduleView(discord.ui.View):
    """Admin view for managing all schedules."""

    def __init__(self, schedules: list[dict]):
        super().__init__(timeout=300)
        self.schedules = {s["id"]: s for s in schedules}
        self.selected_id: int | None = None

        if not schedules:
            return

        # Create schedule selector dropdown
        options = []
        for sched in schedules[:25]:  # Discord limit
            when_display, _ = _format_schedule_when(sched)
            status_emoji = "⏸️" if not sched["enabled"] else "✅"
            # Include user ID in label
            label = f"#{sched['id']} U:{sched['discord_id']} {sched['task_type'][:4]} {when_display}"

            options.append(discord.SelectOption(
                label=label[:100],
                value=str(sched["id"]),
                emoji=status_emoji,
            ))

        self.schedule_select = discord.ui.Select(
            placeholder="Select a schedule...",
            options=options,
            row=0,
        )
        self.schedule_select.callback = self.on_select
        self.add_item(self.schedule_select)

        # Action buttons
        self.pause_btn = discord.ui.Button(label="Pause/Resume", style=discord.ButtonStyle.secondary, emoji="⏸️", row=1, disabled=True)
        self.pause_btn.callback = self.on_pause
        self.add_item(self.pause_btn)

        self.cancel_btn = discord.ui.Button(label="Cancel Running", style=discord.ButtonStyle.primary, emoji="🛑", row=1, disabled=True)
        self.cancel_btn.callback = self.on_cancel
        self.add_item(self.cancel_btn)

        self.remove_btn = discord.ui.Button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", row=1, disabled=True)
        self.remove_btn.callback = self.on_remove
        self.add_item(self.remove_btn)

    async def on_select(self, interaction: discord.Interaction):
        self.selected_id = int(self.schedule_select.values[0])
        sched = self.schedules.get(self.selected_id)

        if sched:
            self.pause_btn.disabled = False
            self.cancel_btn.disabled = False
            self.remove_btn.disabled = False

            # Show schedule details
            when_display, _ = _format_schedule_when(sched)

            # Update the view to enable buttons
            await interaction.response.edit_message(view=self)

            # Send details via follow-up so we don't lose the original interaction
            await interaction.followup.send(
                f"**Selected Schedule #{sched['id']}**\n"
                f"User: <@{sched['discord_id']}> (`{sched['discord_id']}`)\n"
                f"Type: {sched['task_type']}\n"
                f"When: {when_display}\n"
                f"Enabled: {'Yes' if sched['enabled'] else 'No'}\n"
                f"Running: {'Yes' if sched['id'] in _running_scheduled else 'No'}",
                ephemeral=True,
            )
        else:
            await interaction.response.defer()

    async def on_pause(self, interaction: discord.Interaction):
        if not self.selected_id:
            await interaction.response.defer()
            return

        sched = self.schedules.get(self.selected_id)
        if not sched:
            await interaction.response.send_message("❌ Schedule not found.", ephemeral=True)
            return

        new_state = not sched["enabled"]
        if admin_set_schedule_enabled(self.selected_id, new_state):
            sched["enabled"] = new_state
            status = "enabled" if new_state else "disabled"
            await interaction.response.send_message(
                f"✅ Schedule **#{self.selected_id}** has been **{status}**.",
                ephemeral=True,
            )
            log.info(f"Admin {interaction.user.id} {status} schedule #{self.selected_id}")
        else:
            await interaction.response.send_message("❌ Failed to update schedule.", ephemeral=True)

    async def on_cancel(self, interaction: discord.Interaction):
        if not self.selected_id:
            await interaction.response.defer()
            return

        schedule_id = self.selected_id
        cancelled = False

        # Cancel running process
        if schedule_id in _running_scheduled:
            process, task_name = _running_scheduled[schedule_id]
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            del _running_scheduled[schedule_id]
            cancelled = True

        # Cancel pending task
        if schedule_id in _scheduled_tasks:
            _scheduled_tasks[schedule_id].cancel()
            del _scheduled_tasks[schedule_id]

        if cancelled:
            await interaction.response.send_message(
                f"🛑 Cancelled running task for schedule **#{schedule_id}**.",
                ephemeral=True,
            )
            log.info(f"Admin {interaction.user.id} cancelled running task for schedule #{schedule_id}")
        else:
            await interaction.response.send_message(
                f"ℹ️ Schedule **#{schedule_id}** was not running.",
                ephemeral=True,
            )

    async def on_remove(self, interaction: discord.Interaction):
        if not self.selected_id:
            await interaction.response.defer()
            return

        schedule_id = self.selected_id

        # Cancel running process
        if schedule_id in _running_scheduled:
            process, task_name = _running_scheduled[schedule_id]
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            del _running_scheduled[schedule_id]

        # Cancel pending task
        if schedule_id in _scheduled_tasks:
            _scheduled_tasks[schedule_id].cancel()
            del _scheduled_tasks[schedule_id]

        # Delete from database
        if admin_delete_schedule(schedule_id):
            del self.schedules[schedule_id]
            await interaction.response.send_message(
                f"✅ Schedule **#{schedule_id}** has been deleted.",
                ephemeral=True,
            )
            log.info(f"Admin {interaction.user.id} deleted schedule #{schedule_id}")
        else:
            await interaction.response.send_message("❌ Failed to delete schedule.", ephemeral=True)


class AdminTasksView(discord.ui.View):
    """Admin view for managing running tasks."""

    def __init__(self, tasks: list[dict]):
        super().__init__(timeout=300)
        self.tasks = {self._get_task_key(t): t for t in tasks}
        self.selected_key: str | None = None

        if not tasks:
            return

        # Create task selector dropdown
        options = []
        for task in tasks[:25]:  # Discord limit
            if task["type"] == "manual":
                label = f"M:{task['task_id'][:8]} U:{task['user_id']}"
                desc = task["desc"][:50] if task.get("desc") else "Manual task"
                key = f"manual:{task['user_id']}:{task['task_id']}"
            else:
                label = f"S:#{task['schedule_id']} U:{task['user_id']}"
                desc = task.get("task_name", "Scheduled task")[:50]
                key = f"scheduled:{task['schedule_id']}"

            options.append(discord.SelectOption(
                label=label[:100],
                description=desc,
                value=key,
                emoji="🔄" if task["type"] == "manual" else "📅",
            ))

        self.task_select = discord.ui.Select(
            placeholder="Select a task to cancel...",
            options=options,
            row=0,
        )
        self.task_select.callback = self.on_select
        self.add_item(self.task_select)

        # Cancel button
        self.cancel_btn = discord.ui.Button(
            label="Cancel Selected Task",
            style=discord.ButtonStyle.danger,
            emoji="🛑",
            row=1,
            disabled=True,
        )
        self.cancel_btn.callback = self.on_cancel
        self.add_item(self.cancel_btn)

        # Cancel all button
        self.cancel_all_btn = discord.ui.Button(
            label="Cancel All Tasks",
            style=discord.ButtonStyle.danger,
            emoji="⚠️",
            row=1,
        )
        self.cancel_all_btn.callback = self.on_cancel_all
        self.add_item(self.cancel_all_btn)

    def _get_task_key(self, task: dict) -> str:
        if task["type"] == "manual":
            return f"manual:{task['user_id']}:{task['task_id']}"
        else:
            return f"scheduled:{task['schedule_id']}"

    async def on_select(self, interaction: discord.Interaction):
        self.selected_key = self.task_select.values[0]
        task = self.tasks.get(self.selected_key)

        if task:
            self.cancel_btn.disabled = False

            # Update the view first to enable the button
            await interaction.response.edit_message(view=self)

            # Then send task details as a follow-up
            if task["type"] == "manual":
                await interaction.followup.send(
                    f"**Selected Manual Task**\n"
                    f"Task ID: `{task['task_id']}`\n"
                    f"User: <@{task['user_id']}> (`{task['user_id']}`)\n"
                    f"Description: {task.get('desc', 'N/A')}",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"**Selected Scheduled Task**\n"
                    f"Schedule ID: #{task['schedule_id']}\n"
                    f"User: <@{task['user_id']}> (`{task['user_id']}`)\n"
                    f"Task: {task.get('task_name', 'N/A')}",
                    ephemeral=True,
                )
        else:
            await interaction.response.defer()

    async def on_cancel(self, interaction: discord.Interaction):
        if not self.selected_key:
            await interaction.response.defer()
            return

        task = self.tasks.get(self.selected_key)
        if not task:
            await interaction.response.send_message("❌ Task not found.", ephemeral=True)
            return

        await self._cancel_task(interaction, task)

    async def on_cancel_all(self, interaction: discord.Interaction):
        if not self.tasks:
            await interaction.response.send_message("ℹ️ No tasks to cancel.", ephemeral=True)
            return

        cancelled_count = 0
        for task in list(self.tasks.values()):
            if await self._cancel_task_silent(task):
                cancelled_count += 1

        # Clear all tasks
        self.tasks.clear()

        log.info(f"Admin {interaction.user.id} cancelled all {cancelled_count} running tasks")

        # Rebuild dropdown (will disable since no tasks left)
        self._rebuild_dropdown()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"🛑 Cancelled **{cancelled_count}** task(s).",
            ephemeral=True,
        )

    def _rebuild_dropdown(self):
        """Rebuild the dropdown options after a task is cancelled."""
        if not self.tasks:
            # No tasks left, disable everything
            self.task_select.disabled = True
            self.task_select.placeholder = "No tasks running"
            self.task_select.options = [discord.SelectOption(label="No tasks", value="none")]
            self.cancel_btn.disabled = True
            self.cancel_all_btn.disabled = True
            return

        # Rebuild options
        options = []
        for key, task in list(self.tasks.items())[:25]:
            if task["type"] == "manual":
                label = f"M:{task['task_id'][:8]} U:{task['user_id']}"
                desc = task["desc"][:50] if task.get("desc") else "Manual task"
            else:
                label = f"S:#{task['schedule_id']} U:{task['user_id']}"
                desc = task.get("task_name", "Scheduled task")[:50]

            options.append(discord.SelectOption(
                label=label[:100],
                description=desc,
                value=key,
                emoji="🔄" if task["type"] == "manual" else "📅",
            ))

        self.task_select.options = options
        self.cancel_btn.disabled = True  # Reset until new selection
        self.selected_key = None

    async def _cancel_task(self, interaction: discord.Interaction, task: dict):
        """Cancel a task and send confirmation."""
        cancelled_msg = ""

        if task["type"] == "manual":
            user_id = task["user_id"]
            task_id = task["task_id"]
            process = task["process"]

            # Terminate process
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

            # Clean up tracking
            if user_id in running_tasks:
                running_tasks[user_id] = [t for t in running_tasks[user_id] if t[0] != task_id]
                if not running_tasks[user_id]:
                    del running_tasks[user_id]

            del self.tasks[self.selected_key]
            cancelled_msg = f"🛑 Cancelled manual task `{task_id}` for user <@{user_id}>."
            log.info(f"Admin {interaction.user.id} cancelled manual task {task_id} for user {user_id}")

        else:  # Scheduled task
            schedule_id = task["schedule_id"]
            process = task["process"]

            # Terminate process
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

            # Clean up tracking
            if schedule_id in _running_scheduled:
                del _running_scheduled[schedule_id]

            del self.tasks[self.selected_key]
            cancelled_msg = f"🛑 Cancelled scheduled task for schedule #{schedule_id}."
            log.info(f"Admin {interaction.user.id} cancelled scheduled task #{schedule_id}")

        # Rebuild dropdown and update the message
        self._rebuild_dropdown()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(cancelled_msg, ephemeral=True)

    async def _cancel_task_silent(self, task: dict) -> bool:
        """Cancel a task without sending a message. Returns True if cancelled."""
        try:
            if task["type"] == "manual":
                user_id = task["user_id"]
                task_id = task["task_id"]
                process = task["process"]

                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()

                if user_id in running_tasks:
                    running_tasks[user_id] = [t for t in running_tasks[user_id] if t[0] != task_id]
                    if not running_tasks[user_id]:
                        del running_tasks[user_id]

                return True

            else:  # Scheduled task
                schedule_id = task["schedule_id"]
                process = task["process"]

                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()

                if schedule_id in _running_scheduled:
                    del _running_scheduled[schedule_id]

                return True
        except Exception as e:
            log.error(f"Error cancelling task: {e}")
            return False


# Command group for admin commands
admin_group = app_commands.Group(name="admin", description="Admin commands for managing users and schedules")
tree.add_command(admin_group)


async def admin_user_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for user IDs in admin commands."""
    if not is_admin(interaction.user.id):
        return []

    choices = []

    # Add users with running tasks first
    for user_id in running_tasks.keys():
        task_count = len(running_tasks[user_id])
        label = f"{user_id} ({task_count} running)"
        if not current or current in str(user_id):
            choices.append(app_commands.Choice(name=label, value=str(user_id)))

    # Add registered users
    users = admin_get_all_users()
    for user in users:
        uid = user["discord_id"]
        if uid not in running_tasks:  # Don't duplicate
            email = user["email"][:20] + "..." if len(user["email"]) > 20 else user["email"]
            label = f"{uid} ({email})"
            if not current or current in str(uid) or current.lower() in email.lower():
                choices.append(app_commands.Choice(name=label[:100], value=str(uid)))

    return choices[:25]


async def admin_task_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for task IDs in admin commands."""
    if not is_admin(interaction.user.id):
        return []

    choices = []

    # Get all running manual tasks
    for user_id, tasks in running_tasks.items():
        for task_id, process, desc in tasks:
            label = f"{task_id} (User:{user_id}) {desc[:30]}"
            if not current or current in task_id or current in str(user_id):
                choices.append(app_commands.Choice(name=label[:100], value=task_id))

    # Get all running scheduled tasks
    for schedule_id, (process, task_name) in _running_scheduled.items():
        label = f"sched#{schedule_id} {task_name[:40]}"
        if not current or current in str(schedule_id) or current.lower() in task_name.lower():
            choices.append(app_commands.Choice(name=label[:100], value=f"schedule:{schedule_id}"))

    if not choices:
        choices.append(app_commands.Choice(name="No running tasks", value="none"))

    return choices[:25]


async def admin_schedule_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[int]]:
    """Autocomplete for schedule IDs in admin commands."""
    if not is_admin(interaction.user.id):
        return []

    schedules = admin_get_all_schedules()
    choices = []

    for sched in schedules:
        day_name = DAY_NAMES.get(sched["day_of_week"], "?")[:3]
        hour = sched["hour"]
        minute = sched["minute"]
        time_12h = f"{hour % 12 or 12}:{minute:02d}{'AM' if hour < 12 else 'PM'}"

        status = "🔄" if sched["id"] in _running_scheduled else "✅" if sched["enabled"] else "⏸️"
        label = f"{status} #{sched['id']} U:{sched['discord_id']} {sched['task_type'][:4]} {day_name}@{time_12h}"

        if not current or current in str(sched["id"]) or current in str(sched["discord_id"]):
            choices.append(app_commands.Choice(name=label[:100], value=sched["id"]))

    return choices[:25]


@admin_group.command(name="schedules", description="View and manage all schedules")
async def admin_schedules(interaction: discord.Interaction):
    """Admin command to view all schedules across all users."""
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True,
        )
        return

    schedules = admin_get_all_schedules()

    if not schedules:
        await interaction.response.send_message(
            "📅 No schedules found across any users.",
            ephemeral=True,
        )
        return

    # Group by user
    by_user: dict[int, list[dict]] = {}
    for sched in schedules:
        uid = sched["discord_id"]
        if uid not in by_user:
            by_user[uid] = []
        by_user[uid].append(sched)

    embed = discord.Embed(
        title="🔧 Admin: All Schedules",
        description=f"**{len(schedules)}** schedule(s) across **{len(by_user)}** user(s)",
        color=discord.Color.orange(),
    )

    for user_id, user_scheds in list(by_user.items())[:10]:  # Limit to 10 users
        sched_lines = []
        for s in user_scheds[:5]:  # Limit to 5 per user
            when_display, _ = _format_schedule_when(s)
            status = "⏸️" if not s["enabled"] else "🔄" if s["id"] in _running_scheduled else "✅"
            sched_lines.append(f"{status} #{s['id']} {s['task_type']} {when_display}")

        if len(user_scheds) > 5:
            sched_lines.append(f"... +{len(user_scheds) - 5} more")

        embed.add_field(
            name=f"User {user_id}",
            value="\n".join(sched_lines) or "None",
            inline=True,
        )

    if len(by_user) > 10:
        embed.set_footer(text=f"Showing 10 of {len(by_user)} users. Use dropdown to manage individual schedules.")

    view = AdminScheduleView(schedules)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@admin_group.command(name="tasks", description="View and cancel running tasks")
async def admin_tasks(interaction: discord.Interaction):
    """Admin command to view and cancel running tasks."""
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True,
        )
        return

    # Collect all running tasks
    all_tasks = []

    # Manual tasks (from running_tasks)
    for user_id, tasks in running_tasks.items():
        for task_id, process, desc in tasks:
            all_tasks.append({
                "type": "manual",
                "user_id": user_id,
                "task_id": task_id,
                "desc": desc,
                "process": process,
            })

    # Scheduled tasks (from _running_scheduled)
    for schedule_id, (process, task_name) in _running_scheduled.items():
        # Get schedule info
        schedules = admin_get_all_schedules()
        sched = next((s for s in schedules if s["id"] == schedule_id), None)
        user_id = sched["discord_id"] if sched else "?"

        all_tasks.append({
            "type": "scheduled",
            "user_id": user_id,
            "schedule_id": schedule_id,
            "task_name": task_name,
            "process": process,
        })

    if not all_tasks:
        await interaction.response.send_message(
            "✅ No tasks are currently running.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="🔧 Admin: Running Tasks",
        description=f"**{len(all_tasks)}** task(s) currently running",
        color=discord.Color.orange(),
    )

    for task in all_tasks[:25]:
        if task["type"] == "manual":
            embed.add_field(
                name=f"🔄 Manual: {task['task_id'][:8]}...",
                value=f"User: <@{task['user_id']}>\nDesc: {task['desc'][:50]}",
                inline=True,
            )
        else:
            embed.add_field(
                name=f"📅 Scheduled: #{task['schedule_id']}",
                value=f"User: <@{task['user_id']}>\nTask: {task['task_name']}",
                inline=True,
            )

    view = AdminTasksView(all_tasks)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@admin_group.command(name="users", description="View registered users")
async def admin_users(interaction: discord.Interaction):
    """Admin command to view all registered users."""
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True,
        )
        return

    users = admin_get_all_users()

    if not users:
        await interaction.response.send_message(
            "👥 No registered users found.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="🔧 Admin: Registered Users",
        description=f"**{len(users)}** user(s) registered",
        color=discord.Color.orange(),
    )

    for user in users[:25]:
        embed.add_field(
            name=f"User {user['discord_id']}",
            value=f"Email: `{user['email']}`",
            inline=True,
        )

    if len(users) > 25:
        embed.set_footer(text=f"Showing 25 of {len(users)} users.")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="cancel-task", description="Cancel a specific user's running task")
@app_commands.describe(
    user_id="Select a user with running tasks",
    task_id="Select the task to cancel",
)
@app_commands.autocomplete(user_id=admin_user_autocomplete, task_id=admin_task_autocomplete)
async def admin_cancel_task(
    interaction: discord.Interaction,
    user_id: str,
    task_id: str,
):
    """Admin command to cancel a specific user's task."""
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True,
        )
        return

    try:
        uid = int(user_id)
    except ValueError:
        await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True)
        return

    if uid not in running_tasks:
        await interaction.response.send_message(
            f"❌ No running tasks found for user {uid}.",
            ephemeral=True,
        )
        return

    # Find and cancel the task
    tasks = running_tasks[uid]
    target_task = next((t for t in tasks if t[0] == task_id), None)

    if not target_task:
        await interaction.response.send_message(
            f"❌ Task `{task_id}` not found for user {uid}.",
            ephemeral=True,
        )
        return

    _, process, desc = target_task
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()

    # Remove from running tasks
    running_tasks[uid] = [t for t in tasks if t[0] != task_id]
    if not running_tasks[uid]:
        del running_tasks[uid]

    await interaction.response.send_message(
        f"✅ Cancelled task `{task_id}` for user {uid}.\nDescription: {desc}",
        ephemeral=True,
    )
    log.info(f"Admin {interaction.user.id} cancelled task {task_id} for user {uid}")


@admin_group.command(name="schedule", description="Manage a specific schedule")
@app_commands.describe(
    schedule_id="Select the schedule to manage",
    action="Action to perform",
)
@app_commands.choices(action=[
    app_commands.Choice(name="View details", value="view"),
    app_commands.Choice(name="Pause", value="pause"),
    app_commands.Choice(name="Resume", value="resume"),
    app_commands.Choice(name="Cancel running", value="cancel"),
    app_commands.Choice(name="Delete", value="delete"),
])
@app_commands.autocomplete(schedule_id=admin_schedule_autocomplete)
async def admin_schedule(
    interaction: discord.Interaction,
    schedule_id: int,
    action: str,
):
    """Admin command to manage a specific schedule."""
    if not is_admin(interaction.user.id):
        await interaction.response.send_message(
            "❌ You don't have permission to use this command.",
            ephemeral=True,
        )
        return

    # Get schedule info
    schedules = admin_get_all_schedules()
    sched = next((s for s in schedules if s["id"] == schedule_id), None)

    if not sched:
        await interaction.response.send_message(
            f"❌ Schedule #{schedule_id} not found.",
            ephemeral=True,
        )
        return

    day_name = DAY_NAMES.get(sched["day_of_week"], "Unknown")
    hour = sched["hour"]
    minute = sched["minute"]
    time_12h = f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"

    if action == "view":
        status = "Running" if schedule_id in _running_scheduled else "Enabled" if sched["enabled"] else "Paused"
        embed = discord.Embed(
            title=f"Schedule #{schedule_id}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="User", value=f"<@{sched['discord_id']}> (`{sched['discord_id']}`)", inline=False)
        embed.add_field(name="Type", value=sched["task_type"], inline=True)
        embed.add_field(name="When", value=f"{day_name} at {time_12h}", inline=True)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Last Run", value=sched["last_run"] or "Never", inline=True)
        if sched.get("params"):
            embed.add_field(name="Params", value=f"```{sched['params']}```", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    elif action == "pause":
        if admin_set_schedule_enabled(schedule_id, False):
            await interaction.response.send_message(
                f"⏸️ Schedule **#{schedule_id}** has been paused.",
                ephemeral=True,
            )
            log.info(f"Admin {interaction.user.id} paused schedule #{schedule_id}")
        else:
            await interaction.response.send_message("❌ Failed to pause schedule.", ephemeral=True)

    elif action == "resume":
        if admin_set_schedule_enabled(schedule_id, True):
            await interaction.response.send_message(
                f"▶️ Schedule **#{schedule_id}** has been resumed.",
                ephemeral=True,
            )
            log.info(f"Admin {interaction.user.id} resumed schedule #{schedule_id}")
        else:
            await interaction.response.send_message("❌ Failed to resume schedule.", ephemeral=True)

    elif action == "cancel":
        cancelled = False
        if schedule_id in _running_scheduled:
            process, task_name = _running_scheduled[schedule_id]
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            del _running_scheduled[schedule_id]
            cancelled = True

        if schedule_id in _scheduled_tasks:
            _scheduled_tasks[schedule_id].cancel()
            del _scheduled_tasks[schedule_id]

        if cancelled:
            await interaction.response.send_message(
                f"🛑 Cancelled running task for schedule **#{schedule_id}**.",
                ephemeral=True,
            )
            log.info(f"Admin {interaction.user.id} cancelled running task for schedule #{schedule_id}")
        else:
            await interaction.response.send_message(
                f"ℹ️ Schedule **#{schedule_id}** was not running.",
                ephemeral=True,
            )

    elif action == "delete":
        # Cancel any running processes
        if schedule_id in _running_scheduled:
            process, task_name = _running_scheduled[schedule_id]
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            del _running_scheduled[schedule_id]

        if schedule_id in _scheduled_tasks:
            _scheduled_tasks[schedule_id].cancel()
            del _scheduled_tasks[schedule_id]

        if admin_delete_schedule(schedule_id):
            await interaction.response.send_message(
                f"🗑️ Schedule **#{schedule_id}** has been deleted.",
                ephemeral=True,
            )
            log.info(f"Admin {interaction.user.id} deleted schedule #{schedule_id}")
        else:
            await interaction.response.send_message("❌ Failed to delete schedule.", ephemeral=True)


def main():
    """Main entry point."""
    token = os.environ.get("DISCORD_BOT_TOKEN")

    if not token:
        log.error(
            "DISCORD_BOT_TOKEN not set.\n"
            "  1. Create a bot at https://discord.com/developers/applications\n"
            "  2. Copy the bot token\n"
            "  3. Add to .env: DISCORD_BOT_TOKEN=your_token_here"
        )
        sys.exit(1)

    log.info("Starting Discord bot...")
    client.run(token)


if __name__ == "__main__":
    main()
