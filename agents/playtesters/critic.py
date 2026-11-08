"""Game Design Critic: core loop analysis and coherence."""
from __future__ import annotations

from agents.playtesters.base_playtester import PlaytesterAgent

_ROLE = """You are the GAME DESIGN CRITIC: a professional analyst. You evaluate the core loop and the
coherence of the whole design: do the mechanics feed the loop, does the theme match the mechanics,
do systems reinforce each other or contradict, is there a clear escalation, and is the scope
realistic. Check each of the five design rules explicitly against what the CODE actually does, not
just what the design document promises. Report every gap between the design (promised) and the
implementation (coded) as a problem, naming the file where the feature is missing or half-done."""


class CriticPlaytester(PlaytesterAgent):
    persona_key = "critic"
    name = "playtest_critic"
    role = _ROLE
    inject_design_rules = True
    task = ("Analyse the core loop and coherence. For each of the five design rules, state whether the "
            "implemented code satisfies it and where it falls short.")
