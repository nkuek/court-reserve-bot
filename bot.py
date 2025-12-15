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

    embed.set_footer(text="Running in background... Results will be sent via DM.")

    await interaction.response.send_message(embed=embed, ephemeral=True)

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

    embed.set_footer(text="Running in background... Results will be sent via DM.")

    await interaction.response.send_message(embed=embed, ephemeral=True)

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
    """Run a booking script and report results with live log updates."""
    user_id = interaction.user.id
    user_mention = interaction.user.mention

    try:
        # Start the process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=get_script_dir(),
        )

        running_tasks[user_id] = process

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

        # Remove from running tasks
        if user_id in running_tasks:
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

        # Add condensed log as a field if not too long
        if len(output) <= 800:
            embed.add_field(name="📜 Full Log", value=f"```\n{output}\n```", inline=False)
        elif len(output) <= 1500:
            # Show truncated log
            embed.add_field(name="📜 Log (truncated)", value=f"```\n...{output[-700:]}\n```", inline=False)

        # Send result via DM for privacy
        try:
            await interaction.user.send(embed=embed)
        except discord.Forbidden:
            # User has DMs disabled, fall back to followup
            await interaction.followup.send(
                content=f"{user_mention} (couldn't DM you - enable DMs for private results)",
                embed=embed
            )

        # Delete the live log message if it exists
        if status_message:
            try:
                await status_message.delete()
            except:
                pass

    except Exception as e:
        log.error(f"Error running script: {e}")

        if user_id in running_tasks:
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
