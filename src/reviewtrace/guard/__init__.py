"""Revision guard: what changed that nobody asked for?

ReviewTrace's audit asks whether requested changes were made. The guard asks the opposite
question: did the revision also change things that should have stayed put? It checks
protected wording, numbers and hedging words, and reports each difference with evidence.
It reads text only; it cannot judge whether a change is wrong.
"""

from reviewtrace.guard.checks import GuardFinding, GuardReport, GuardStatus, guard_revision

__all__ = ["GuardFinding", "GuardReport", "GuardStatus", "guard_revision"]
