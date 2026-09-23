"""Sottosistema NLU: dall'ordine del coordinatore al ConstraintSet.

Questo pacchetto esiste per tenere il modello linguistico dentro un recinto. Ne
escono soltanto istanze di `ConstraintSet` (definito in `schema.py`), mai
assegnazioni, mai numeri liberi, mai codice.

Tre pezzi, con tre mestieri distinti:

- `translator.translate` e' la porta d'ingresso. Usa il modello linguistico con
  tool use quando la chiave c'e', ricade sul traduttore a regole quando non
  c'e' o quando la traduzione non valida.
- `rule_translator.translate` e' la traduzione deterministica a espressioni
  regolari: pavimento di sicurezza e termine di paragone del benchmark.
- `resolver` trasforma i selettori ("gli elicotteri") nelle entita' vere di uno
  scenario. Lo usano sia `optimize/` sia `validate/`.

Le funzioni pubbliche sono riesportate qui in modo che gli altri sottosistemi
importino da `tagliafuoco.nlu` senza conoscere la disposizione interna dei
moduli.
"""

from __future__ import annotations

from tagliafuoco.nlu import rule_translator, resolver, translator
from tagliafuoco.nlu.resolver import (
    Resolution,
    base_ids_for_place,
    describe_resolution,
    name_matches,
    normalize,
    place_name_matches,
    resolve_fires,
    resolve_fires_detailed,
    resolve_vehicles,
    resolve_vehicles_detailed,
    selector_targets_fires,
    selector_targets_vehicles,
)
from tagliafuoco.nlu.schema import (
    AnyConstraint,
    ConstraintKind,
    ConstraintSet,
    Enforcement,
    Selector,
    SelectorField,
    ZoneKind,
)
from tagliafuoco.nlu.translator import (
    TRANSLATOR_LLM,
    TRANSLATOR_LLM_REPAIRED,
    TRANSLATOR_RULES,
    TRANSLATOR_RULES_FALLBACK,
    build_scenario_context,
    build_tool_definition,
    build_tool_schema,
    load_system_prompt,
    translate,
)

#: Traduzione deterministica, senza rete e senza chiave. Nome esplicito perche'
#: chi la vuole la vuole apposta: `translate` sceglie da solo la via migliore.
translate_with_rules = rule_translator.translate

__all__ = [
    "AnyConstraint",
    "ConstraintKind",
    "ConstraintSet",
    "Enforcement",
    "Resolution",
    "Selector",
    "SelectorField",
    "TRANSLATOR_LLM",
    "TRANSLATOR_LLM_REPAIRED",
    "TRANSLATOR_RULES",
    "TRANSLATOR_RULES_FALLBACK",
    "ZoneKind",
    "base_ids_for_place",
    "build_scenario_context",
    "build_tool_definition",
    "build_tool_schema",
    "describe_resolution",
    "load_system_prompt",
    "name_matches",
    "normalize",
    "place_name_matches",
    "resolve_fires",
    "resolve_fires_detailed",
    "resolve_vehicles",
    "resolve_vehicles_detailed",
    "resolver",
    "rule_translator",
    "selector_targets_fires",
    "selector_targets_vehicles",
    "translate",
    "translate_with_rules",
    "translator",
]
