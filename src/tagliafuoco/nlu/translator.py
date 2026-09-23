"""Traduzione dell'ordine del coordinatore con il modello linguistico.

Questo modulo e' il confine del sistema. Da una parte c'e' una frase dettata in
sala operativa; dall'altra c'e' un `ConstraintSet` tipizzato che il solver sa
applicare. Il modello linguistico sta esattamente su quel confine e fa una cosa
sola: riconoscere quale vincolo, fra quelli gia' implementati, l'ordine sta
chiedendo. Non decide, non stima, non calcola.

## Perche' il tool use batte "rispondi in JSON"

Il modo pigro di ottenere dati strutturati da un modello e' chiederglielo a
parole: "rispondi solo con un oggetto JSON fatto cosi'". Funziona spesso, e
proprio per questo e' pericoloso, perche' fallisce in modi che sembrano
successi. Il modello antepone una riga di cortesia, oppure avvolge il JSON in
un blocco di codice, oppure decide che un campo in piu' aiuta, oppure scrive
`"minutes": "venti"` invece di `20`. Ogni volta tocca scrivere codice di
ripulitura: taglia i backtick, cerca la prima graffa, prova a decodificare,
riprova. Quel codice e' fragile e nasconde il problema vero, che e' l'assenza
di un contratto.

Il tool use sposta il contratto dal testo alla richiesta. Si dichiara uno
strumento il cui `input_schema` e' lo schema JSON di `ConstraintSet` generato
da Pydantic, e si forza la chiamata con `tool_choice`. Il modello non puo' piu'
scegliere il formato: puo' solo riempire quello schema. La risposta arriva in
un blocco `tool_use` con un campo `input` che e' gia' un oggetto Python, non
una stringa da interpretare. Non c'e' nessun parsing di testo, quindi non c'e'
nessuna classe di errori di parsing.

C'e' anche un vantaggio che si vede solo a valle: lo schema e' generato dal
modello Pydantic, quindi non puo' divergere da quello che il codice accetta.
Un prompt che descrive il formato a parole invece diverge sempre, al primo
campo che qualcuno aggiunge a `schema.py`.

## Perche' Pydantic e' la barriera vera e non un dettaglio

Il tool use garantisce la **forma**, non il **contenuto**. Un modello puo'
restituire, dentro uno schema formalmente rispettato, `minutes: 5000` quando il
massimo dichiarato e' 600, oppure due vincoli con lo stesso `constraint_id`,
oppure un `zone_kind: "radius"` senza centro ne' raggio, oppure un valore di
`vehicle_kind` che nella flotta non esiste. Nessuna di queste cose e' un errore
di formato: sono errori di merito, ed e' esattamente li' che un traduttore
neurale sbaglia.

`ConstraintSet.model_validate` e' il punto in cui quegli errori si fermano.
Ogni intervallo numerico, ogni enumerazione chiusa, ogni invariante fra campi
(`from_minute < to_minute`, `min_used <= max_used`, gli identificativi
distinti) e' dichiarato una volta sola in `schema.py` e viene verificato qui,
prima che qualunque valore raggiunga il solver. Se la validazione passa, tutto
cio' che sta a valle lavora su dati di cui conosce gia' i limiti e puo'
smettere di difendersi. Se non passa, la traduzione viene rifiutata.

Questo e' il motivo per cui la validazione non e' un dettaglio implementativo:
e' la ragione per cui si puo' mettere un modello linguistico in un sistema di
supporto alle decisioni senza che diventi il sistema. Il modello propone dentro
un recinto, il recinto e' codice, e il codice non contratta.

## Cosa succede quando qualcosa va storto

L'ordine delle vie di uscita, dalla migliore alla peggiore, e' registrato ogni
volta nel campo `translator` del `ConstraintSet`, cosi' il benchmark puo'
misurare quanto spesso il sistema e' dovuto ripiegare:

1. `llm`: chiamata riuscita e output valido al primo colpo.
2. `llm_repaired`: il primo output non validava; l'errore di Pydantic e' stato
   rimandato al modello come risultato dello strumento e il secondo tentativo
   ha validato. Un solo tentativo, non un ciclo: se il modello non si corregge
   con l'errore in mano, non si corregge insistendo, e intanto il coordinatore
   aspetta.
3. `rules_fallback`: chiamata fallita o output ancora invalido: si usa il
   traduttore deterministico.
4. `rules`: la chiave non c'e' e la chiamata non viene nemmeno tentata.

Il caso 4 non e' un'eccezione: e' la configurazione normale di chi clona il
repository. Nessun test di questo progetto richiede una chiave API.
"""

from __future__ import annotations

import copy
import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from tagliafuoco import config
from tagliafuoco.domain.models import Scenario
from tagliafuoco.nlu import rule_translator
from tagliafuoco.nlu.schema import ConstraintSet

# Valori del campo `translator`. Sono costanti e non stringhe sparse perche' il
# benchmark le conta: un refuso qui falserebbe una metrica.
TRANSLATOR_LLM = "llm"
TRANSLATOR_LLM_REPAIRED = "llm_repaired"
TRANSLATOR_RULES = "rules"
TRANSLATOR_RULES_FALLBACK = "rules_fallback"

TOOL_NAME = "emit_constraint_set"

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "system_it.md"

# Quanti elementi dello scenario entrano nel contesto. Il contesto serve a
# risolvere i riferimenti ("l'elicottero di Cosenza"), non a far ragionare il
# modello sullo scenario: piu' e' ricco, piu' il modello e' tentato di
# decidere al posto del solver. Tarature, non misure.
MAX_CONTEXT_VEHICLES = 40
MAX_CONTEXT_FIRES = 30
MAX_CONTEXT_BASES = 20


class TranslationError(RuntimeError):
    """La traduzione con il modello non e' riuscita. Non esce da `translate`."""


# --------------------------------------------------------------------------
# Prompt e schema dello strumento
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_system_prompt() -> str:
    """Legge il prompt di sistema dal file versionato in `prompts/`."""
    if not SYSTEM_PROMPT_FILE.exists():  # pragma: no cover - installazione rotta
        raise TranslationError(f"manca il prompt di sistema: {SYSTEM_PROMPT_FILE}")
    return SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")


def _strip_discriminators(node: Any) -> Any:
    """Toglie le chiavi `discriminator` dallo schema generato da Pydantic.

    Pydantic emette per le unioni discriminate una chiave `discriminator` in
    stile OpenAPI accanto a `oneOf`. Non e' JSON Schema standard e i validatori
    delle API non sono tenuti ad accettarla. Toglierla non perde niente: il
    campo `kind` resta nello schema con il suo `const`, e la discriminazione
    vera la fa comunque Pydantic quando valida la risposta.
    """
    if isinstance(node, dict):
        return {
            k: _strip_discriminators(v)
            for k, v in node.items()
            if k != "discriminator"
        }
    if isinstance(node, list):
        return [_strip_discriminators(v) for v in node]
    return node


@lru_cache(maxsize=1)
def _tool_schema_cached() -> dict:
    """Schema ripulito, calcolato una volta sola. Non esce mai da qui."""
    return _strip_discriminators(ConstraintSet.model_json_schema())


def build_tool_schema() -> dict:
    """Schema JSON dello strumento: e' lo schema di `ConstraintSet`, non una copia.

    Generarlo da Pydantic invece di scriverlo a mano e' la scelta che tiene
    allineati il contratto del modello e il contratto del codice. Quando
    qualcuno aggiunge un campo a `schema.py`, lo strumento lo sa subito.

    Il risultato e' una copia profonda della versione in cache. Costruirlo e'
    costoso e si fa una volta sola, ma restituire l'oggetto della cache
    significherebbe consegnare a ogni chiamante lo stesso dizionario
    modificabile: basterebbe che uno solo di loro lo ritoccasse (per provare
    una variante, per togliere un campo) e tutte le chiamate successive
    partirebbero con lo schema alterato, senza che nessuno abbia modo di
    accorgersene. La cache resta privata proprio per questo.
    """
    return copy.deepcopy(_tool_schema_cached())


def build_tool_definition() -> dict:
    """Definizione completa dello strumento passata all'API."""
    return {
        "name": TOOL_NAME,
        "description": (
            "Registra l'insieme di vincoli tradotti dall'ordine del "
            "coordinatore. E' l'unico modo ammesso di rispondere."
        ),
        "input_schema": build_tool_schema(),
    }


# --------------------------------------------------------------------------
# Contesto dello scenario
# --------------------------------------------------------------------------


def build_scenario_context(scenario: Optional[Scenario]) -> str:
    """Riassunto compatto dello scenario, per risolvere i riferimenti.

    Senza questo blocco il modello non puo' tradurre "l'elicottero di Cosenza":
    il `base_id` e' un dato dello scenario, non una parola dell'ordine. Con un
    blocco troppo ricco, pero', il modello comincia a ragionare sui mezzi e a
    scegliere: per questo qui non compaiono velocita', autonomia ne' capacita'
    d'acqua, che servirebbero solo a decidere.
    """
    if scenario is None:
        return (
            "CONTESTO: nessuno scenario disponibile. Usa selettori per "
            "categoria (vehicle_kind, vehicle_domain, fire_comune) e non "
            "inventare identificativi."
        )

    lines: list[str] = ["CONTESTO DELLO SCENARIO"]
    lines.append(f"istante: {scenario.timestamp.isoformat()}")
    lines.append(f"orizzonte: {scenario.horizon_minutes} minuti")
    lines.append(f"notte: {'si' if scenario.is_night else 'no'}")
    lines.append(
        f"giornata operativa dei mezzi aerei: dal minuto "
        f"{config.DAYLIGHT_START_MINUTE} al minuto {config.DAYLIGHT_END_MINUTE}"
    )

    # Gli elenchi vuoti si dichiarano invece di essere omessi: un elenco che
    # non compare e' ambiguo (nessun mezzo, oppure elenco non fornito?), e
    # l'ambiguita' e' esattamente cio' che spinge un modello a inventare.
    bases = scenario.bases[:MAX_CONTEXT_BASES]
    if bases:
        lines.append("basi (base_id | nome | provincia):")
        for b in bases:
            lines.append(f"  {b.base_id} | {b.name} | {b.provincia}")
    else:
        lines.append("basi: nessuna")

    vehicles = scenario.vehicles[:MAX_CONTEXT_VEHICLES]
    if vehicles:
        lines.append("mezzi (vehicle_id | nome | classe | dominio | base | stato):")
        for v in vehicles:
            lines.append(
                f"  {v.vehicle_id} | {v.name} | {v.kind.value} | "
                f"{v.domain.value} | {v.home_base_id} | {v.status.value}"
            )
    else:
        lines.append("mezzi: nessuno")

    fires = scenario.fires[:MAX_CONTEXT_FIRES]
    if fires:
        lines.append(
            "focolai attivi (fire_id | comune | provincia | gravita' | "
            "interfaccia | area protetta):"
        )
        for f in fires:
            lines.append(
                f"  {f.fire_id} | {f.comune or '-'} | {f.provincia or '-'} | "
                f"{f.severity.value} | {'wui' if f.is_wui else 'no'} | "
                f"{f.protected_area_name or '-'}"
            )
    else:
        lines.append("focolai attivi: nessuno")

    # Il troncamento va dichiarato anche per le basi: senza questa riga il
    # modello vedrebbe un elenco che sembra completo e userebbe un base_id
    # sbagliato credendo che gli altri non esistano.
    if (
        len(scenario.vehicles) > MAX_CONTEXT_VEHICLES
        or len(scenario.fires) > MAX_CONTEXT_FIRES
        or len(scenario.bases) > MAX_CONTEXT_BASES
    ):
        lines.append(
            "(elenco troncato: usa selettori per categoria invece che per "
            "identificativo quando il mezzo o il focolaio non compare qui)"
        )
    return "\n".join(lines)


def _user_message(order_text: str, scenario: Optional[Scenario]) -> str:
    return (
        f"{build_scenario_context(scenario)}\n\n"
        f"ORDINE DEL COORDINATORE\n{order_text}"
    )


# --------------------------------------------------------------------------
# Chiamata al modello
# --------------------------------------------------------------------------


def _client(api_key: str):
    """Costruisce il client Anthropic. Import locale: l'SDK e' opzionale."""
    import anthropic  # import ritardato: senza chiave non serve nemmeno

    return anthropic.Anthropic(api_key=api_key, timeout=config.LLM_TIMEOUT_S)


def _create_message(client, **kwargs):
    """Chiama l'API forzando la chiamata allo strumento.

    `temperature=0` serve alla ripetibilita': due esecuzioni sullo stesso
    ordine devono dare lo stesso ConstraintSet, altrimenti il benchmark misura
    rumore. I modelli piu' recenti hanno pero' rimosso i parametri di
    campionamento e rifiutano la richiesta con un 400: in quel caso si ritenta
    una sola volta senza `temperature`, perche' un modello senza temperatura
    regolabile e' gia' deterministico quanto puo' esserlo.
    """
    import anthropic

    try:
        return client.messages.create(temperature=0.0, **kwargs)
    except anthropic.BadRequestError as exc:
        if "temperature" not in str(exc).lower():
            raise
        return client.messages.create(**kwargs)


def _extract_tool_input(message) -> dict:
    """Prende il payload del blocco `tool_use`. Solleva se non c'e'."""
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            # `input` e' gia' un oggetto: nessun parsing di testo, quindi
            # nessun errore di parsing. E' il vantaggio concreto del tool use.
            return dict(block.input)
    raise TranslationError(
        f"il modello non ha chiamato lo strumento (stop_reason="
        f"{getattr(message, 'stop_reason', '?')})"
    )


def _validation_feedback(exc: ValidationError) -> str:
    """Messaggio di riparazione: l'errore di Pydantic, in forma utilizzabile.

    Si rimandano al modello gli errori veri, campo per campo, non un generico
    "non ha funzionato": e' l'unica informazione che gli permette di correggersi
    invece di riprovare a caso.
    """
    problems = []
    for err in exc.errors()[:12]:
        where = ".".join(str(p) for p in err.get("loc", ()))
        problems.append(f"- {where}: {err.get('msg')}")
    return (
        "L'oggetto non rispetta lo schema. Errori di validazione:\n"
        + "\n".join(problems)
        + "\n\nRichiama lo strumento con un oggetto corretto. Non cambiare il "
        "significato dei vincoli: correggi solo cio' che lo schema rifiuta. Se "
        "un valore non sta negli intervalli ammessi, sostituisci quel vincolo "
        "con UNSUPPORTED invece di troncarlo."
    )


# --------------------------------------------------------------------------
# Funzione pubblica
# --------------------------------------------------------------------------


def translate(
    order_text: str,
    scenario: Optional[Scenario] = None,
    constraint_set_id: Optional[str] = None,
) -> ConstraintSet:
    """Traduce un ordine in italiano in un `ConstraintSet` validato.

    Restituisce sempre un `ConstraintSet`: quando il modello non e'
    disponibile, o sbaglia, il risultato arriva dal traduttore a regole. Il
    campo `translator` dice sempre quale via e' stata usata, e `translation_ms`
    misura il tempo totale, chiamata di rete compresa.
    """
    started = time.perf_counter()
    text = (order_text or "").strip()

    # Senza chiave non si tenta nemmeno la chiamata. Non e' un errore: e' la
    # configurazione normale del progetto.
    if not config.has_llm_key():
        return _from_rules(
            text,
            scenario,
            constraint_set_id,
            started,
            TRANSLATOR_RULES,
            ["nessuna ANTHROPIC_API_KEY: traduzione con il traduttore a regole"],
        )

    # Un ordine vuoto non ha niente da tradurre. Spendere una chiamata di rete
    # per farsi dire che non c'e' nessun vincolo costa tempo al coordinatore e
    # soldi a chi paga le chiamate, e l'esito e' gia' noto: UNSUPPORTED. La via
    # registrata resta `rules`, che e' la verita': il modello non e' stato
    # interpellato.
    if not text:
        return _from_rules(
            text,
            scenario,
            constraint_set_id,
            started,
            TRANSLATOR_RULES,
            ["ordine vuoto: nessuna chiamata al modello"],
        )

    try:
        payload, via = _translate_with_model(text, scenario)
    except Exception as exc:  # noqa: BLE001 - qualunque guasto deve ripiegare
        return _from_rules(
            text,
            scenario,
            constraint_set_id,
            started,
            TRANSLATOR_RULES_FALLBACK,
            [f"traduzione con modello fallita ({type(exc).__name__}: {exc})"],
        )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    # I campi di servizio li decide il chiamante, non il modello: se li avesse
    # inventati lui, il benchmark misurerebbe dati che il modello si e' scelto.
    # Si riscrivono per assegnazione, e Pydantic NON rivalida un'assegnazione
    # (il modello non dichiara `validate_assignment`): i limiti dello schema
    # vanno quindi rispettati qui a mano, altrimenti uscirebbe di qui un
    # oggetto che dichiara di aver validato e non e' vero. I limiti si leggono
    # da `rule_translator`, che a sua volta li ricava da `schema.py`: scriverli
    # a mano qui vorrebbe dire ricopiarli per la terza volta.
    payload.constraint_set_id = rule_translator.clean_set_id(
        constraint_set_id, _default_id(text)
    )
    payload.source_text = text[: rule_translator.MAX_SOURCE_TEXT_LENGTH]
    payload.translator = via
    payload.model_name = config.LLM_MODEL
    payload.translation_ms = round(elapsed_ms, 3)
    return payload


def _translate_with_model(
    text: str, scenario: Optional[Scenario]
) -> tuple[ConstraintSet, str]:
    """Chiamata, validazione e unico tentativo di riparazione."""
    client = _client(config.ANTHROPIC_API_KEY)
    tool = build_tool_definition()
    messages: list[dict] = [{"role": "user", "content": _user_message(text, scenario)}]

    request = {
        "model": config.LLM_MODEL,
        "max_tokens": config.LLM_MAX_TOKENS,
        "system": load_system_prompt(),
        "tools": [tool],
        # `tool_choice` forzato: il modello non puo' rispondere a parole. E'
        # questa riga, non il prompt, a garantire che arrivi una struttura.
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
    }

    first = _create_message(client, messages=messages, **request)
    raw = _extract_tool_input(first)
    try:
        return ConstraintSet.model_validate(raw), TRANSLATOR_LLM
    except ValidationError as exc:
        first_error = exc

    # Un solo giro di riparazione. Si rimanda l'errore di validazione come
    # risultato dello strumento, marcato come errore: e' la forma che l'API
    # prevede per dire "la tua chiamata non e' andata bene, ecco perche'".
    tool_use_id = _tool_use_id(first)
    messages.append({"role": "assistant", "content": first.content})
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": _validation_feedback(first_error),
                    "is_error": True,
                }
            ],
        }
    )
    second = _create_message(client, messages=messages, **request)
    repaired = _extract_tool_input(second)
    try:
        return ConstraintSet.model_validate(repaired), TRANSLATOR_LLM_REPAIRED
    except ValidationError as exc:
        raise TranslationError(
            f"output non valido anche dopo la riparazione: {exc.error_count()} "
            f"errori (primo giro: {first_error.error_count()})"
        ) from exc


def _tool_use_id(message) -> str:
    """Identificativo del blocco `tool_use` a cui il giro di riparazione risponde.

    Non e' un dettaglio di protocollo: un `tool_result` senza il
    `tool_use_id` giusto non viene collegato alla chiamata sbagliata, e il
    modello riceverebbe l'errore di validazione come un commento qualsiasi
    invece che come l'esito della sua chiamata.
    """
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            return block.id
    raise TranslationError("nessun blocco tool_use da riparare")


def _from_rules(
    text: str,
    scenario: Optional[Scenario],
    constraint_set_id: Optional[str],
    started: float,
    via: str,
    warnings: list[str],
) -> ConstraintSet:
    """Traduzione deterministica, marcata con la via che l'ha prodotta.

    Gli avvisi del ripiego vanno DAVANTI a quelli del traduttore a regole
    perche' sono la causa di tutto il resto: chi legge deve sapere prima di
    ogni altra cosa che il modello non e' stato usato, altrimenti attribuisce
    a lui una traduzione che non ha fatto. Quelli del traduttore a regole non
    si toccano, perche' sono cio' che il coordinatore deve leggere prima di
    lanciare il solver.

    `translation_ms` si misura dal `started` del chiamante e non da qui: e'
    il tempo che il coordinatore ha aspettato, chiamata fallita compresa, non
    quello impiegato dalle espressioni regolari.
    """
    result = rule_translator.translate(text, scenario, constraint_set_id)
    result.translator = via
    result.warnings = warnings + list(result.warnings)
    result.translation_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return result


def _default_id(text: str) -> str:
    """Identificativo derivato dal testo: due esecuzioni, lo stesso valore."""
    import hashlib

    return f"CS_LLM_{hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]}"


# --------------------------------------------------------------------------
# Utilita' per la diagnostica e per i test
# --------------------------------------------------------------------------


def describe_request(order_text: str, scenario: Optional[Scenario] = None) -> str:
    """Richiesta che verrebbe inviata, in chiaro. Non chiama niente.

    Serve a guardare con gli occhi che cosa vede il modello, senza spendere
    una chiamata e senza avere una chiave.
    """
    return json.dumps(
        {
            "model": config.LLM_MODEL,
            "tool": TOOL_NAME,
            "system_prompt_chars": len(load_system_prompt()),
            "user": _user_message(order_text, scenario),
        },
        ensure_ascii=False,
        indent=2,
    )


def clone_constraint_set(cs: ConstraintSet) -> ConstraintSet:
    """Copia profonda, per chi vuole annotare un risultato senza alterarlo."""
    return copy.deepcopy(cs)
