"""Pianificatori banali usati come termine di paragone.

Esiste per dare un punto di ingresso unico: chi valuta il sistema importa da
qui e non deve sapere in quale file vive ciascuna regola. Le due baseline hanno
la stessa firma del solver, quindi il motore di rigiocata le accetta senza
sapere che cosa sono.
"""

from __future__ import annotations

from tagliafuoco.baseline.nearest import (
    BASELINE_PLANNERS,
    MAX_VEHICLES_PER_FIRE,
    PRODUCER_NEAREST,
    PRODUCER_PRIORITY,
    available_baselines,
    plan_nearest,
    plan_priority,
)

__all__ = [
    "BASELINE_PLANNERS",
    "MAX_VEHICLES_PER_FIRE",
    "PRODUCER_NEAREST",
    "PRODUCER_PRIORITY",
    "available_baselines",
    "plan_nearest",
    "plan_priority",
]
