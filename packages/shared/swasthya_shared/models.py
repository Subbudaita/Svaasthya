"""Claude model identifiers, tiered per docs/PLAN.md §6.

These are exact IDs — no date suffixes, no substitutions. Tests pin them.
"""

from enum import StrEnum


class ModelTier(StrEnum):
    # Weekly causal hypothesis run (F8).
    OPUS = "claude-opus-5"
    # Chat, vision, rituals (F3, F4, F14).
    SONNET = "claude-sonnet-5"
    # Inbound red-flag classifier and routing (F13).
    HAIKU = "claude-haiku-4-5"
