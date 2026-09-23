"""Validatore indipendente dei piani di impiego.

PERCHE' QUESTO PACCHETTO ESISTE
-------------------------------
Il solver in `optimize/` produce un piano e CP-SAT lo dichiara ottimo. Quella
parola riguarda il modello, non il mondo: se il modello ha un errore, la
soluzione e' ottima per il problema sbagliato. Qui il piano viene riletto da
un'implementazione separata, che ricalcola tempi, cicli d'acqua, autonomia e
turni partendo da SPEC.md e non dal codice del solver.

Regola di confine, senza eccezioni: nessun modulo di questo pacchetto importa
alcunche' da `tagliafuoco.optimize`. Nemmeno la distanza geodetica, nemmeno le
utility. Le uniche dipendenze interne ammesse sono `domain/` (i dati),
`nlu/schema.py` (la forma dei vincoli) e `config.py` (le costanti di
taratura, che sono dati condivisi e non logica).

Uso tipico:

    from tagliafuoco.validate import validate_plan, render

    result = validate_plan(plan, scenario, constraint_set)
    render(result)
    if not result.is_valid:
        ...
"""

from __future__ import annotations

from tagliafuoco.validate.report import format_report, render, to_json
from tagliafuoco.validate.rules import (
    ALL_RULES,
    DEFAULT_TOLERANCE,
    Tolerance,
    ValidationSeverity,
    Violation,
)
from tagliafuoco.validate.validator import (
    VALIDATOR_VERSION,
    MetricComparison,
    RecomputedMetrics,
    ValidationResult,
    validate_plan,
)

__all__ = [
    "ALL_RULES",
    "DEFAULT_TOLERANCE",
    "MetricComparison",
    "RecomputedMetrics",
    "Tolerance",
    "VALIDATOR_VERSION",
    "ValidationResult",
    "ValidationSeverity",
    "Violation",
    "format_report",
    "render",
    "to_json",
    "validate_plan",
]
