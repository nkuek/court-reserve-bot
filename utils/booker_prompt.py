"""Asks the booker after each booking whether they're playing, and lets them hand off or cancel the court.

Every component's custom id carries what it needs, so the buttons and menus work after a restart.
"""

import asyncio
import logging
import os
from datetime import date

import discord

from utils import signup_app
from utils.roster_loop import run_jobs

log = logging.getLogger(__name__)

KEEP = "__keep__"
CANCEL = "__cancel__"
# Discord caps a select menu at 25 options. Keeping and cancelling the court take two.
MAX_PLAYERS = 23


def _when(day: str) -> str:
    return f"{date.fromisoformat(day):%a %-m/%-d}"


class PlayingButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"booker:(?P<play>in|out):(?P<day>\d{4}-\d{2}-\d{2}):(?P<court>[0-9A-Za-z]+)",
):
    def __init__(self, play: str, day: str, court: str):
        self.play, self.day, self.court = play, day, court
        super().__init__(
            discord.ui.Button(
                label="I'm playing" if play == "in" else "I can't play",
                style=discord.ButtonStyle.success if play == "in" else discord.ButtonStyle.secondary,
                custom_id=f"booker:{play}:{day}:{court}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["play"], match["day"], match["court"])

    async def callback(self, interaction: discord.Interaction):
        playing = self.play == "in"
        try:
            await asyncio.to_thread(signup_app.set_booker_playing, self.day, playing)
            others = [] if playing else await asyncio.to_thread(_others, self.day)
        except Exception as e:
            log.warning(f"Could not update the booker for {self.day}: {e}")
            await interaction.response.send_message(f"Couldn't update the sign-up app: {e}")
            return
        log.info(f"Booker {'playing' if playing else 'out'} for {self.day}")
        if playing:
            await interaction.response.edit_message(
                content=f"You're playing {_when(self.day)}. You're on the sign-up list.", view=prompt_view(self.day, self.court)
            )
        else:
            await interaction.response.edit_message(content=_out_text(self.day, self.court, others), view=prompt_view(self.day, self.court, others, menu=True))


def _out_text(day: str, court: str, others: list[dict]) -> str:
    choices = "hand court {c} to someone, cancel it, or keep it in your name" if others else "cancel court {c} or keep it in your name. Nobody else has signed up yet, so tap \"I can't play\" again later to hand it off"
    return f"You're out for {_when(day)} and off the sign-up list. Pick below: {choices.format(c=court)}."


class HandoffSelect(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r"handoff:(?P<day>\d{4}-\d{2}-\d{2}):(?P<court>[0-9A-Za-z]+)",
):
    def __init__(self, day: str, court: str, players: list[dict] | None = None):
        self.day, self.court = day, court
        options = [
            discord.SelectOption(label=f"Keep court {court} in my name", value=KEEP),
            discord.SelectOption(label=f"Cancel court {court} in CourtReserve", value=CANCEL),
        ]
        # The value is the CourtReserve name. The label is the name the group knows.
        seen = {KEEP, CANCEL}
        for p in players or []:
            # Discord rejects repeated values, so two names sharing a CourtReserve account show once.
            if p["crName"][:100] in seen or len(options) >= MAX_PLAYERS + 2:
                continue
            seen.add(p["crName"][:100])
            options.append(discord.SelectOption(label=p["name"][:100], value=p["crName"][:100]))
        super().__init__(
            discord.ui.Select(placeholder="Hand off, cancel, or keep the court…", options=options, custom_id=f"handoff:{day}:{court}")
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Select, match):
        return cls(match["day"], match["court"])

    async def callback(self, interaction: discord.Interaction):
        choice = interaction.data["values"][0]
        if choice == CANCEL:
            await interaction.response.edit_message(
                content=f"Cancel court {self.court} on {_when(self.day)} in CourtReserve? Anyone can book it after that, and it comes off the sign-up app.",
                view=confirm_view(self.day, self.court),
            )
            return
        if choice == KEEP:
            await interaction.response.edit_message(
                content=f"You're out for {_when(self.day)}. Court {self.court} stays in your name.",
                view=prompt_view(self.day, self.court),
            )
            return
        await interaction.response.edit_message(
            content=f"Handing court {self.court} on {_when(self.day)} to {choice} in CourtReserve. This takes a minute.",
            view=None,
        )
        try:
            [result] = await run_jobs([{"action": "transfer", "date": self.day, "court": self.court, "to": choice}])
        except Exception as e:
            result = {"status": "error", "error": str(e)}
        text = await _handoff_outcome(self.day, self.court, choice, result)
        log.info(f"Hand-off of {self.court} on {self.day} to {choice}: {result.get('status')}")
        await interaction.followup.send(text)


class CancelCourtButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"cancelcourt:(?P<answer>yes|no):(?P<day>\d{4}-\d{2}-\d{2}):(?P<court>[0-9A-Za-z]+)",
):
    def __init__(self, answer: str, day: str, court: str):
        self.answer, self.day, self.court = answer, day, court
        super().__init__(
            discord.ui.Button(
                label="Yes, cancel it" if answer == "yes" else "Back",
                style=discord.ButtonStyle.danger if answer == "yes" else discord.ButtonStyle.secondary,
                custom_id=f"cancelcourt:{answer}:{day}:{court}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["answer"], match["day"], match["court"])

    async def callback(self, interaction: discord.Interaction):
        if self.answer == "no":
            others = await asyncio.to_thread(_others, self.day)
            await interaction.response.edit_message(content=_out_text(self.day, self.court, others), view=prompt_view(self.day, self.court, others, menu=True))
            return
        when = f"court {self.court} on {_when(self.day)}"
        await interaction.response.edit_message(content=f"Cancelling {when} in CourtReserve. This takes a minute.", view=None)
        try:
            [result] = await run_jobs([{"action": "cancel", "date": self.day, "court": self.court}])
        except Exception as e:
            result = {"status": "error", "error": str(e)}
        log.info(f"Booker cancel of {self.court} on {self.day}: {result.get('status')}")
        if result.get("status") not in ("cancelled", "not_found"):
            await interaction.followup.send(f"Couldn't cancel {when}: {result.get('error', 'unknown error')[:300]}. Cancel it in CourtReserve instead.")
            return
        try:
            await asyncio.to_thread(signup_app.remove_court, self.day, self.court)
            note = "It's off the sign-up app too."
        except Exception as e:
            log.warning(f"Could not remove {self.court} on {self.day} from the sign-up app: {e}")
            note = "Remove it from the sign-up app by hand."
        done = "Cancelled" if result["status"] == "cancelled" else "Your reservation was already gone for"
        await interaction.followup.send(f"{done} {when}. {note}")


def confirm_view(day: str, court: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(CancelCourtButton("yes", day, court))
    view.add_item(CancelCourtButton("no", day, court))
    return view


async def _handoff_outcome(day: str, court: str, name: str, result: dict) -> str:
    when = f"court {court} on {_when(day)}"
    status = result.get("status")
    if status == "transferred":
        if result.get("still_mine"):
            return f"Handed {when} to {name}. The reservation stays under your account, so the bot keeps managing it."
        try:
            await asyncio.to_thread(signup_app.hand_off_court, day, court, name)
        except Exception as e:
            log.warning(f"Could not record {name} as the booker of {court} on {day}: {e}")
        return f"Handed {when} to {name}. The reservation moved to their account, so they manage it from now on."
    if status == "already_out":
        return f"You're already off the reservation for {when}."
    if status == "not_found":
        return f"Couldn't find your reservation for {when}."
    if status == "unmatched":
        return f"Couldn't hand {when} to {name}: {result.get('reason')}. Use Sub in CourtReserve instead."
    return f"Couldn't hand {when} to {name}: {result.get('error', 'unknown error')[:300]}. Use Sub in CourtReserve instead."


def _others(day: str) -> list[dict]:
    booker = os.environ.get("SIGNUP_BOOKER_NAME", "").lower()
    return [p for p in signup_app.players(day) if p["name"].lower() != booker]


def prompt_view(day: str, court: str, others: list[dict] | None = None, menu: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(PlayingButton("in", day, court))
    view.add_item(PlayingButton("out", day, court))
    if menu:
        view.add_item(HandoffSelect(day, court, others))
    return view


async def ask_booker(user: discord.abc.User, result: dict | None) -> None:
    """DMs the person who booked, after a booking that listed the court in the app."""
    if not signup_app.is_configured() or not result or not result.get("success") or not result.get("date"):
        return
    day, court = result["date"], result.get("court_short", "")
    if not court:
        return
    try:
        await user.send(
            f"Are you playing {_when(day)} on court {court}? You're signed up in the sign-up app. "
            "Tap \"I can't play\" to come off the list, then hand the court to someone or cancel it.",
            view=prompt_view(day, court),
        )
    except discord.HTTPException as e:
        log.warning(f"Could not ask the booker about {day}: {e}")
