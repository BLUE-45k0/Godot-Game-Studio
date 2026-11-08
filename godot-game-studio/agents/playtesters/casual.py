"""Casual Player: clarity and ease of starting."""
from __future__ import annotations

from agents.playtesters.base_playtester import PlaytesterAgent

_ROLE = """You are the CASUAL PLAYER: you play a few minutes at a time, never read manuals, and quit
when confused or punished unfairly. You judge clarity and ease of starting: can you tell what to
do and how to lose within seconds, are the controls shown on screen, is the first level gentle,
does Easy actually feel easy, is feedback readable (contrast, text size, warnings before danger),
and is failure forgiving. Prefer simple, kind fixes (an on-screen hint, a slower first enemy, a
visible health bar) and say which file would change."""


class CasualPlaytester(PlaytesterAgent):
    persona_key = "casual"
    name = "playtest_casual"
    role = _ROLE
    task = ("Assess clarity and onboarding: is the goal, the control scheme and the loss condition "
            "visible without reading anything, and is the start forgiving?")
