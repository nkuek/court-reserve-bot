"""Asks the booker after each booking whether they're playing, with buttons that survive restarts."""

import asyncio
import logging
from datetime import date

import discord

from utils import signup_app

log = logging.getLogger(__name__)


def _when(day: str) -> str:
    return f"{date.fromisoformat(day):%a %-m/%-d}"


# The custom id carries the answer and the session date, so a button still works after a restart.
class PlayingButton(discord.ui.DynamicItem[discord.ui.Button], template=r"booker:(?P<play>in|out):(?P<day>\d{4}-\d{2}-\d{2})"):
    def __init__(self, play: str, day: str):
        self.play, self.day = play, day
        super().__init__(
            discord.ui.Button(
                label="I'm playing" if play == "in" else "I can't play",
                style=discord.ButtonStyle.success if play == "in" else discord.ButtonStyle.secondary,
                custom_id=f"booker:{play}:{day}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["play"], match["day"])

    async def callback(self, interaction: discord.Interaction):
        playing = self.play == "in"
        try:
            await asyncio.to_thread(signup_app.set_booker_playing, self.day, playing)
        except Exception as e:
            log.warning(f"Could not update the booker for {self.day}: {e}")
            await interaction.response.send_message(f"Couldn't update the sign-up app: {e}")
            return
        log.info(f"Booker {'playing' if playing else 'out'} for {self.day}")
        text = (
            f"You're playing {_when(self.day)}. You're on the sign-up list."
            if playing
            else f"You're out for {_when(self.day)}. You're off the sign-up list, and the court stays booked."
        )
        # The buttons stay, so a change of plans is one more tap.
        await interaction.response.edit_message(content=text, view=prompt_view(self.day))


def prompt_view(day: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(PlayingButton("in", day))
    view.add_item(PlayingButton("out", day))
    return view


async def ask_booker(user: discord.abc.User, result: dict | None) -> None:
    """DMs the person who booked, after a booking that listed the court in the app."""
    if not signup_app.is_configured() or not result or not result.get("success") or not result.get("date"):
        return
    day = result["date"]
    try:
        await user.send(
            f"Are you playing {_when(day)} on {result.get('court_short', 'the court')}? "
            "You're signed up in the sign-up app. Tap \"I can't play\" to come off the list.",
            view=prompt_view(day),
        )
    except discord.HTTPException as e:
        log.warning(f"Could not ask the booker about {day}: {e}")
