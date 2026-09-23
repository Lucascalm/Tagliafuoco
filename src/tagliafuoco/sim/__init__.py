"""Rigiocata della stagione: finestre, stato del mondo e motore di replay.

Punto di ingresso unico del sottosistema. Chi valuta il sistema importa da qui
e non deve sapere in quale file vive ciascun pezzo. L'ordine concettuale e'
questo: `timeline` decide che cosa era visibile a che ora, `state` porta avanti
il mondo fra una finestra e l'altra, `replay` mette insieme le due cose e
invoca il pianificatore che gli viene passato.

Fra i nomi riesportati ci sono anche le tre etichette di `metrics_source`
(`METRICS_OFFICIAL`, `METRICS_FALLBACK`, `METRICS_NONE`). Non sono un dettaglio
di servizio: dicono se i numeri di una finestra vengono dalle metriche ufficiali
di `eval/` o dal riassunto minimo di ripiego, che conta la copertura in un altro
modo. Due rigiocate misurate con sorgenti diverse non sono confrontabili, e chi
legge un registro deve poter controllare quel campo senza sapere in quale file
vive la costante.
"""

from __future__ import annotations

from tagliafuoco.sim.replay import (
    AUTO,
    METRICS_FALLBACK,
    METRICS_NONE,
    METRICS_OFFICIAL,
    ReplayContext,
    ReplayError,
    ReplayResult,
    WindowOutcome,
    basic_metrics,
    build_context,
    build_scenario,
    replay_season,
    replay_window,
    save_result,
)
from tagliafuoco.sim.state import (
    VehicleState,
    WorldState,
    fires_still_burning,
    fresh_fleet_snapshot,
)
from tagliafuoco.sim.timeline import (
    ReplayWindow,
    TimelineError,
    as_utc,
    build_windows,
    is_night_at,
    span_of,
    visible_detections,
    visible_fires,
)

__all__ = [
    "AUTO",
    "METRICS_FALLBACK",
    "METRICS_NONE",
    "METRICS_OFFICIAL",
    "ReplayContext",
    "ReplayError",
    "ReplayResult",
    "ReplayWindow",
    "TimelineError",
    "VehicleState",
    "WindowOutcome",
    "WorldState",
    "as_utc",
    "basic_metrics",
    "build_context",
    "build_scenario",
    "build_windows",
    "fires_still_burning",
    "fresh_fleet_snapshot",
    "is_night_at",
    "replay_season",
    "replay_window",
    "save_result",
    "span_of",
    "visible_detections",
    "visible_fires",
]
