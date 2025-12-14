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
import logging
import os
import sys
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

# Track running tasks
running_tasks: dict[int, asyncio.subprocess.Process] = {}


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
            "```/register email:you@example.com password:yourpass```\n"
            "Your credentials are encrypted and stored securely."
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


@tree.command(name="register", description="Save your CourtReserve credentials")
@app_commands.describe(
    email="Your CourtReserve email address",
    password="Your CourtReserve password",
)
async def register(
    interaction: discord.Interaction,
    email: str,
    password: str,
):
    """Register user credentials for booking."""
    try:
        save_user_credentials(interaction.user.id, email, password)

        embed = discord.Embed(
            title="✅ Registration Successful",
            description=(
                f"Your credentials have been saved securely.\n\n"
                f"**Email:** {email}\n"
                f"**Password:** {'•' * len(password)}\n\n"
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

    embed.set_footer(text="Running in background...")

    await interaction.response.send_message(embed=embed)

    # Run the booking script in background
    asyncio.create_task(_run_script(interaction, cmd, "Court Booking"))


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

    embed.set_footer(text="Running in background...")

    await interaction.response.send_message(embed=embed)

    # Run the script in background
    asyncio.create_task(_run_script(interaction, cmd, "Open Play Registration"))


@tree.command(name="cancel", description="Cancel a running booking task")
async def cancel(interaction: discord.Interaction):
    """Cancel any running booking task for this user."""
    user_id = interaction.user.id

    if user_id in running_tasks:
        process = running_tasks[user_id]
        process.terminate()
        del running_tasks[user_id]
        await interaction.response.send_message(
            "✅ Cancelled your running booking task.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "❌ You don't have any running booking tasks.",
            ephemeral=True
        )


async def _run_script(interaction: discord.Interaction, cmd: list[str], task_name: str):
    """Run a booking script and report results."""
    user_id = interaction.user.id

    try:
        # Start the process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=get_script_dir(),
        )

        running_tasks[user_id] = process

        # Wait for completion
        stdout, _ = await process.communicate()
        output = stdout.decode() if stdout else ""

        # Remove from running tasks
        if user_id in running_tasks:
            del running_tasks[user_id]

        # Determine success/failure
        if process.returncode == 0:
            embed = discord.Embed(
                title=f"✅ {task_name} Complete",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            # Try to extract success details from output
            if "SUCCESS" in output:
                embed.description = "Reservation confirmed!"
        else:
            embed = discord.Embed(
                title=f"❌ {task_name} Failed",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            # Show last few lines of output for debugging
            last_lines = "\n".join(output.strip().split("\n")[-5:])
            if last_lines:
                embed.description = f"```\n{last_lines[:1000]}\n```"

        await interaction.followup.send(embed=embed)

    except Exception as e:
        log.error(f"Error running script: {e}")

        if user_id in running_tasks:
            del running_tasks[user_id]

        embed = discord.Embed(
            title=f"❌ {task_name} Error",
            description=str(e)[:1000],
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        await interaction.followup.send(embed=embed)


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
