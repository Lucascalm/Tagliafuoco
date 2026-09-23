"""Sottosistema di ottimizzazione: il modello CP-SAT che assegna i mezzi.

Questo pacchetto e' l'unico posto del progetto in cui si decide chi va dove.
Il modello linguistico non entra qui: arriva al massimo un `ConstraintSet`
tipizzato, che `model.apply_constraints` traduce in vincoli gia' implementati.

La divisione interna e' questa:

- `geometry.py`  la fisica del solver (distanze, tempi, acqua, autonomia) e
                 la conversione dei minuti in interi. Non e' condivisa con
                 `validate/`, che ha la sua: il validatore deve poter
                 scoprire un errore di formula, non ereditarlo.
- `model.py`     variabili e vincoli CP-SAT, piu' la traduzione degli ordini.
- `objective.py` la funzione obiettivo pesata e le sue tarature.
- `solver.py`    esecuzione, gestione degli stati, rilassamento sugli ordini
                 contraddittori, scrittura del `Plan`.

Il punto di ingresso normale e' `solve`.
"""

from __future__ import annotations

from tagliafuoco.optimize.geometry import (
    MAX_SORTIES_HARD_CAP,
    MIN_SEASONAL_RELIABILITY,
    MINUTES_PER_DAY,
    MissionProfile,
    TIME_SCALE,
    WaterOption,
    best_water_source,
    build_mission_profile,
    daily_window_to_plan_minutes,
    from_units,
    haversine_km,
    minutes_from_midnight,
    mission_minutes,
    road_distance_km,
    to_units_down,
    to_units_up,
    travel_distance_km,
    travel_minutes,
    water_cycle_minutes,
)
from tagliafuoco.optimize.model import (
    DEFAULT_MAX_FIRES_PER_VEHICLE,
    LATE_THRESHOLD_UNITS,
    ConstraintApplication,
    ModelVars,
    PairVars,
    PenaltyTerm,
    apply_constraints,
    build_model,
    resolve_fire_ids,
    resolve_vehicle_ids,
    vehicle_is_employable,
)
from tagliafuoco.optimize.objective import (
    ACTIVATION_HOURS,
    OBJECTIVE_PRECISION,
    RELAXED_HARD_WEIGHT,
    SOFT_CONSTRAINT_WEIGHT,
    ObjectiveTerms,
    activation_cost_eur,
    assignment_cost_eur,
    build_terms,
    describe_weights,
    install_objective,
)
from tagliafuoco.optimize.solver import solve

# L'elenco e' scritto a mano e tenuto allineato alla documentazione del
# sottosistema: e' il contratto verso gli altri pacchetti (api, cli, eval,
# sim). Quello che non compare qui e' dettaglio interno e puo' cambiare senza
# preavviso; quello che compare no.
__all__ = [
    # geometry: fisica e conversione dei tempi
    "TIME_SCALE",
    "MAX_SORTIES_HARD_CAP",
    "MIN_SEASONAL_RELIABILITY",
    "MINUTES_PER_DAY",
    "MissionProfile",
    "WaterOption",
    "best_water_source",
    "build_mission_profile",
    # Conversione fra orari (minuti dalla mezzanotte, come TIME_WINDOW) e
    # minuti di piano. E' esposta perche' e' una CONVENZIONE condivisa con
    # validate/, baseline/ e il traduttore a regole, non un dettaglio interno:
    # chi deve verificare che i quattro sottosistemi la leggano allo stesso
    # modo deve poterla chiamare senza entrare nei moduli privati.
    "daily_window_to_plan_minutes",
    "minutes_from_midnight",
    "from_units",
    "haversine_km",
    "mission_minutes",
    "road_distance_km",
    "to_units_down",
    "to_units_up",
    "travel_distance_km",
    "travel_minutes",
    "water_cycle_minutes",
    # model: variabili, vincoli, traduzione degli ordini
    "DEFAULT_MAX_FIRES_PER_VEHICLE",
    "LATE_THRESHOLD_UNITS",
    "ConstraintApplication",
    "ModelVars",
    "PairVars",
    "PenaltyTerm",
    "apply_constraints",
    "build_model",
    "resolve_fire_ids",
    "resolve_vehicle_ids",
    "vehicle_is_employable",
    # objective: pesi e conversione in euro
    "ACTIVATION_HOURS",
    "OBJECTIVE_PRECISION",
    "RELAXED_HARD_WEIGHT",
    "SOFT_CONSTRAINT_WEIGHT",
    "ObjectiveTerms",
    "activation_cost_eur",
    "assignment_cost_eur",
    "build_terms",
    "describe_weights",
    "install_objective",
    # solver: il punto di ingresso normale
    "solve",
]
