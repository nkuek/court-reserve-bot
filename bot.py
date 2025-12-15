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
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

from utils.user_store import (
    save_user_credentials,
    get_user_credentials,
    delete_user_credentials,
    user_exists,
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

# Valid options
VALID_DURATIONS = [1.0, 1.5, 2.0, 2.5, 3.0]
VALID_EVENTS = [
    "Pickleball Open Play - Beginner",
    "Pickleball Open Play - Intermediate",
    "Pickleball Open Play - Advanced",
]

# Common booking times (evening hours)
COMMON_TIMES = [
    "8:00", "10:30", "18:00", "21:00",
]

# Common date options
COMMON_DATES = [
    ("latest", "5 days from now (earliest booking)"),
    ("tomorrow", "Tomorrow"),
    ("today", "Today"),
    ("+2d", "2 days from now"),
    ("+3d", "3 days from now"),
    ("+4d", "4 days from now"),
]

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
    """Autocomplete for date parameter."""
    if not current:
        return [
            app_commands.Choice(name=f"{value} — {desc}", value=value)
            for value, desc in COMMON_DATES
        ]

    # Filter by what user typed
    matches = [(v, d) for v, d in COMMON_DATES if current.lower() in v.lower() or current.lower() in d.lower()]

    # Allow custom date formats
    if not matches:
        return [app_commands.Choice(name=current, value=current)]

    return [
        app_commands.Choice(name=f"{value} — {desc}", value=value)
        for value, desc in matches
    ][:25]


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


@tree.command(name="ping", description="Check if the bot is online")
async def ping(interaction: discord.Interaction):
    """Simple ping command to check if bot is responsive."""
    await interaction.response.send_message(
        f"🏓 Pong! Bot is online. Latency: {round(client.latency * 1000)}ms",
        ephemeral=True
    )


@tree.command(name="help", description="Show all available commands")
async def help_command(interaction: discord.Interaction):
    """Show help information for all commands."""
    embed = discord.Embed(
        title="🏸 Court Booking Bot - Help",
        description="Book pickleball courts and register for open play via Discord.",
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="🔐 Getting Started",
        value=(
            "First, register your CourtReserve credentials:\n"
            "```/register```\n"
            "A secure popup form will appear. Your credentials are encrypted."
        ),
        inline=False,
    )

    embed.add_field(
        name="/book",
        value=(
            "Book a pickleball court.\n"
            "**Required:** `time`, `duration`\n"
            "**Optional:** `date`, `wait_until`\n"
            "```/book time:21:00 duration:2 date:tomorrow```"
        ),
        inline=False,
    )

    embed.add_field(
        name="/openplay",
        value=(
            "Register for an open play event.\n"
            "**Optional:** `event`, `date`, `wait_until`\n"
            "```/openplay event:Advanced date:12/15```"
        ),
        inline=False,
    )

    embed.add_field(
        name="/register",
        value="Save your CourtReserve credentials.",
        inline=True,
    )

    embed.add_field(
        name="/unregister",
        value="Delete your saved credentials.",
        inline=True,
    )

    embed.add_field(
        name="/cancel",
        value="Cancel a running booking task.",
        inline=True,
    )

    embed.add_field(
        name="📅 Date Formats",
        value=(
            "`latest` - 5 days ahead (default)\n"
            "`today` - Today\n"
            "`tomorrow` - Tomorrow\n"
            "`+3d` - 3 days from now\n"
            "`12/15` - December 15"
        ),
        inline=True,
    )

    embed.add_field(
        name="⏰ Wait Until",
        value=(
            "Delay execution until a specific time.\n"
            "`07:00` - Today at 7 AM\n"
            "`tomorrow 07:00` - Tomorrow at 7 AM"
        ),
        inline=True,
    )

    embed.set_footer(text="Tip: The bot waits until the exact reservation time before clicking save.")

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


@tree.command(name="book", description="Book a pickleball court")
@app_commands.describe(
    time="Reservation time in 24h format (e.g., 21:00 for 9 PM)",
    duration="Duration in hours (1, 1.5, 2, 2.5, or 3)",
    date="Date to book (today, tomorrow, +3d, 12/15, or latest)",
    wait_until="Wait until this time before starting (e.g., 07:00)",
)
@app_commands.choices(duration=[
    app_commands.Choice(name="1 hour", value=1.0),
    app_commands.Choice(name="1.5 hours", value=1.5),
    app_commands.Choice(name="2 hours", value=2.0),
    app_commands.Choice(name="2.5 hours", value=2.5),
    app_commands.Choice(name="3 hours", value=3.0),
])
@app_commands.autocomplete(time=time_autocomplete, date=date_autocomplete)
async def book(
    interaction: discord.Interaction,
    time: str,
    duration: float,
    date: str = "latest",
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
        "--email", email,
        "--password", password,
    ]

    if wait_until:
        cmd.extend(["--wait-until", wait_until])

    # Send initial response
    embed = discord.Embed(
        title="🎾 Court Booking Started",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    embed.add_field(name="Date", value=date, inline=True)
    embed.add_field(name="Time", value=time, inline=True)
    embed.add_field(name="Duration", value=f"{duration}h", inline=True)

    if wait_until:
        embed.add_field(name="Wait Until", value=wait_until, inline=True)

    embed.set_footer(text="Running in background... Results will be sent via DM.")

    await interaction.response.send_message(embed=embed, ephemeral=True)

    # Run the booking script in background
    task_desc = f"Court Booking: {date} @ {time}"
    asyncio.create_task(_run_script(interaction, cmd, "Court Booking", task_desc))


@tree.command(name="openplay", description="Register for an open play event")
@app_commands.describe(
    event="The open play event to register for",
    date="Date to register (today, tomorrow, +3d, 12/15, or latest)",
    wait_until="Wait until this time before starting (e.g., 07:00)",
)
@app_commands.choices(event=[
    app_commands.Choice(name="Beginner", value="Pickleball Open Play - Beginner"),
    app_commands.Choice(name="Intermediate", value="Pickleball Open Play - Intermediate"),
    app_commands.Choice(name="Advanced", value="Pickleball Open Play - Advanced"),
])
@app_commands.autocomplete(date=date_autocomplete)
async def openplay(
    interaction: discord.Interaction,
    event: str = "Pickleball Open Play - Intermediate",
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
        "--event", event,
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
    embed.add_field(name="Event", value=event.replace("Pickleball Open Play - ", ""), inline=True)
    embed.add_field(name="Date", value=date, inline=True)

    if wait_until:
        embed.add_field(name="Wait Until", value=wait_until, inline=True)

    embed.set_footer(text="Running in background... Results will be sent via DM.")

    await interaction.response.send_message(embed=embed, ephemeral=True)

    # Run the script in background
    event_short = event.replace("Pickleball Open Play - ", "")
    task_desc = f"Open Play: {event_short} on {date}"
    asyncio.create_task(_run_script(interaction, cmd, "Open Play Registration", task_desc))


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
        super().__init__(timeout=300)  # 5 minute timeout
        self.full_log = full_log
        self.task_name = task_name

    @discord.ui.button(label="View Full Log", style=discord.ButtonStyle.secondary, emoji="📜")
    async def view_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Send the full log as a file attachment."""
        # Create a text file with the log
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"log_{timestamp}.txt"

        file = discord.File(
            io.BytesIO(self.full_log.encode("utf-8")),
            filename=filename,
        )

        await interaction.response.send_message(
            f"📜 **Full log for {self.task_name}:**",
            file=file,
            ephemeral=True,
        )

        # Disable the button after use
        button.disabled = True
        button.label = "Log Sent"
        await interaction.message.edit(view=self)


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


async def _run_script(interaction: discord.Interaction, cmd: list[str], task_name: str, task_desc: str = ""):
    """Run a booking script and report results with live log updates."""
    user_id = interaction.user.id
    user_mention = interaction.user.mention
    task_id = str(uuid.uuid4())[:8]  # Short unique ID

    try:
        # Build environment with headless mode enabled
        env = os.environ.copy()
        env["HEADLESS"] = "true"

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

            # Send a log update every 10 seconds if there's new output
            if output_lines and (datetime.now() - last_update_time).seconds >= 10:
                recent_lines = output_lines[-10:]  # Last 10 lines
                log_text = "\n".join(recent_lines)

                if len(log_text) > 1900:
                    log_text = log_text[-1900:]

                try:
                    if status_message:
                        await status_message.edit(content=f"📋 **Live Log:**\n```\n{log_text}\n```")
                    else:
                        # Send live log via DM for privacy
                        try:
                            status_message = await interaction.user.send(
                                f"📋 **Live Log ({task_name}):**\n```\n{log_text}\n```"
                            )
                        except discord.Forbidden:
                            # Fall back to channel if DMs disabled
                            status_message = await interaction.followup.send(
                                f"📋 **Live Log:**\n```\n{log_text}\n```"
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

        # Delete the live log message if it exists
        if status_message:
            try:
                await status_message.delete()
            except:
                pass

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
