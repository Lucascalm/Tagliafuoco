"""Traduttore deterministico: dall'ordine in italiano al ConstraintSet, senza modelli.

Questo modulo non e' un ripiego di serie B. Ha due mestieri, ed entrambi
contano quanto la traduzione con il modello linguistico:

1. E' il pavimento di sicurezza. Il progetto deve girare per intero senza
   `ANTHROPIC_API_KEY`: chi clona il repository lancia tutto, test compresi,
   senza chiavi. Quando la chiave manca, o quando la chiamata fallisce, o
   quando l'output del modello non valida, il sistema atterra qui.

2. E' il termine di paragone del benchmark. Misurare l'accuratezza di un
   traduttore neurale contro il nulla non dice niente. Misurarla contro un
   traduttore a espressioni regolari scritto con cura dice esattamente quanto
   valore aggiunge il modello: se la differenza e' piccola, il modello non
   serve, e saperlo e' un risultato.

Come funziona. Il testo viene "appiattito" (accenti tolti, minuscole) in modo
da conservare le posizioni dei caratteri, cosi' ogni vincolo puo' portarsi
dietro il frammento letterale dell'ordine da cui e' nato (`source_span`). Poi
si applicano delle regole in ordine di specificita': la prima che riconosce un
tratto di testo lo "consuma", e le regole piu' generiche che vengono dopo non
lo riusano. E' la stessa idea di un lexer a massima corrispondenza, e serve a
evitare che "entro le venti" produca sia una finestra temporale sia un tempo
massimo di risposta.

Quando nessuna regola riconosce niente, il risultato e' un `UNSUPPORTED` con
la motivazione. Non si inventa il vincolo plausibile: un traduttore che non sa
astenersi e' peggiore di uno che copre meno casi.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Callable, Mapping, Optional, TypeVar

from pydantic import BaseModel

from tagliafuoco import config
from tagliafuoco.domain.models import Scenario
from tagliafuoco.nlu.resolver import base_ids_for_place, vehicle_kinds_for
from tagliafuoco.nlu.schema import (
    AnyConstraint,
    BaseConstraint,
    ConstraintSet,
    Enforcement,
    ExcludeVehicle,
    ForbidAssignment,
    ForceAssignment,
    MaxResponseTime,
    MinVehiclesOnFire,
    ObjectiveTerm,
    ObjectiveWeight,
    PrioritizeZone,
    ProtectInfrastructure,
    ReserveVehicle,
    Selector,
    SelectorField,
    TimeWindow,
    Unsupported,
    VehicleTypeQuota,
    ZoneKind,
)

TRANSLATOR_NAME = "rules"

# Confidenza dichiarata dei vincoli prodotti. Sono tarature, non probabilita'
# misurate: servono a distinguere nel benchmark un riconoscimento lessicale
# netto ("i canadair non volano") da uno ricostruito per contesto (il mezzo non
# e' nominato accanto all'ordine e viene dedotto).
CONF_EXPLICIT = 0.9
CONF_INFERRED = 0.65

# Moltiplicatore di priorita' applicato a una zona nominata dall'ordine.
# Il campo ammette 1..10; 2.5 significa "questa zona pesa due volte e mezza le
# altre", abbastanza per cambiare l'ordine di servizio senza azzerare il resto.
ZONE_WEIGHT = 2.5
WUI_WEIGHT = 3.0


# --------------------------------------------------------------------------
# Appiattimento del testo che conserva gli offset
# --------------------------------------------------------------------------

# Sostituzioni uno-a-uno: la lunghezza del testo non cambia, quindi le
# posizioni trovate dalle regex valgono anche sul testo originale e il
# `source_span` mostra all'operatore le sue parole, accenti compresi.
#
# La tabella elenca solo le forme MINUSCOLE perche' `flatten` abbassa il testo
# prima di applicarla. Elencare anche le maiuscole sarebbe peggio che inutile:
# sembrerebbe di coprirle tutte e invece se ne coprirebbe solo il
# sottoinsieme scritto a mano, e un ordine dettato in maiuscolo si
# appiattirebbe in modo diverso dallo stesso ordine in minuscolo. Succedeva:
# "PLATI'" con l'accento maiuscolo si appiattiva bene solo perche' quella
# lettera era nell'elenco, mentre una "C" con cediglia restava com'era e il
# toponimo non agganciava piu' l'indice, che e' costruito da nomi in forma
# mista.
_FLATTEN_TABLE = str.maketrans(
    {
        "à": "a", "á": "a", "â": "a", "ä": "a", "å": "a",
        "è": "e", "é": "e", "ê": "e", "ë": "e",
        "ì": "i", "í": "i", "î": "i", "ï": "i",
        "ò": "o", "ó": "o", "ô": "o", "ö": "o",
        "ù": "u", "ú": "u", "û": "u", "ü": "u",
        "ç": "c", "ñ": "n",
        "’": "'", "‘": "'", "´": "'", "`": "'",
    }
)


def flatten(text: str) -> str:
    """Minuscole e accenti tolti, mantenendo la lunghezza del testo.

    La lunghezza e' l'invariante che tiene in piedi tutto il modulo: ogni
    posizione trovata dalle regex sul testo appiattito viene poi usata per
    ritagliare il `source_span` dal testo ORIGINALE. Se i due testi avessero
    lunghezze diverse, al coordinatore verrebbero mostrate parole spostate
    rispetto al vincolo, che e' peggio che non mostrargliene nessuna.

    L'ordine delle due operazioni non e' indifferente: prima le minuscole, poi
    la tabella. Al contrario la tabella vedrebbe il testo com'e' stato dettato
    e dovrebbe elencare ogni lettera due volte, una per forma; qualunque
    dimenticanza renderebbe "PLATI'" diverso da "plati'" e il toponimo in
    maiuscolo non verrebbe riconosciuto.

    `str.lower` da solo non garantisce l'invariante: esistono caratteri la cui
    forma minuscola occupa piu' di una posizione (la I con punto del turco,
    per dirne uno, che puo' arrivare da un nome proprio). In quel caso si
    ripassa carattere per carattere e si lasciano intatti quelli che si
    allungano: si perde il confronto su quella lettera, non l'allineamento di
    tutto il resto. La tabella invece e' uno-a-uno per costruzione e non puo'
    cambiare la lunghezza.
    """
    lowered = text.lower()
    if len(lowered) != len(text):
        lowered = "".join(
            ch.lower() if len(ch.lower()) == 1 else ch for ch in text
        )
    return lowered.translate(_FLATTEN_TABLE)


# --------------------------------------------------------------------------
# Numeri scritti in lettere
# --------------------------------------------------------------------------

# In sala operativa i numeri si dicono, non si scrivono: "entro venti minuti",
# "almeno due mezzi". Senza questa tabella meta' degli ordini reali non si
# tradurrebbe.
_NUMBER_WORDS: dict[str, int] = {
    "zero": 0, "un": 1, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4,
    "cinque": 5, "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10,
    "undici": 11, "dodici": 12, "tredici": 13, "quattordici": 14,
    "quindici": 15, "sedici": 16, "diciassette": 17, "diciotto": 18,
    "diciannove": 19, "venti": 20, "ventuno": 21, "ventidue": 22,
    "venticinque": 25, "trenta": 30, "trentacinque": 35, "quaranta": 40,
    "quarantacinque": 45, "cinquanta": 50, "sessanta": 60, "novanta": 90,
    "cento": 100,
}

# Alternanza ordinata dal piu' lungo al piu' corto: altrimenti "venti"
# aggancerebbe il prefisso di "venticinque".
_NUM_WORDS_ALT = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
NUM = rf"(?:\d{{1,4}}|{_NUM_WORDS_ALT})"


def parse_number(token: str) -> Optional[int]:
    """Numero da una cifra o da una parola italiana. None se non e' un numero."""
    t = token.strip().lower()
    if t.isdigit():
        return int(t)
    return _NUMBER_WORDS.get(t)


_Number = TypeVar("_Number", int, float)


def _clamp(value: _Number, low: _Number, high: _Number) -> _Number:
    """Riporta il valore dentro l'intervallo ammesso dallo schema."""
    return max(low, min(high, value))


# --------------------------------------------------------------------------
# Limiti presi da schema.py invece che riscritti a mano
# --------------------------------------------------------------------------
#
# Ogni valore che questo modulo produce deve stare negli intervalli dichiarati
# in `schema.py`, che e' un file di contratto e non si tocca. Ricopiarli qui a
# mano (1..600 per i minuti, 1..20 per il presidio, 64 caratteri per
# l'identificativo) funziona finche' nessuno li cambia di la'. Il giorno in cui
# qualcuno li cambia, il traduttore continuerebbe a tagliare sui vecchi limiti
# senza che niente fallisca: emetterebbe vincoli piu' stretti di quelli ammessi
# e lo direbbe pure, in un warning sbagliato. Leggerli da Pydantic costa una
# riflessione sola all'import e toglie di mezzo l'intera classe di errore.


def _field_constraint(model: type[BaseModel], field_name: str, attribute: str):
    """Valore di un vincolo dichiarato su un campo Pydantic (`ge`, `le`, ...).

    Pydantic v2 conserva i limiti di `Field(...)` in `FieldInfo.metadata`, come
    oggetti di `annotated_types`: uno per vincolo. Si scorre la lista e si
    prende il primo che espone l'attributo cercato.
    """
    for meta in model.model_fields[field_name].metadata:
        value = getattr(meta, attribute, None)
        if value is not None:
            return value
    return None


def _schema_range(model: type[BaseModel], field_name: str) -> tuple:
    """Estremi inclusivi dichiarati per un campo numerico dello schema.

    Solleva se il campo non li dichiara piu': un limite sparito e' una modifica
    del contratto, e va vista subito all'import invece di diventare un vincolo
    tagliato male sei mesi dopo.
    """
    low = _field_constraint(model, field_name, "ge")
    high = _field_constraint(model, field_name, "le")
    if low is None or high is None:
        raise RuntimeError(
            f"{model.__name__}.{field_name} non dichiara piu' ge/le in "
            f"schema.py: il traduttore a regole non sa piu' entro quali "
            f"limiti riportare i valori"
        )
    return (low, high)


def _schema_max_length(model: type[BaseModel], field_name: str) -> int:
    """Lunghezza massima dichiarata per un campo testuale dello schema."""
    value = _field_constraint(model, field_name, "max_length")
    if value is None:
        raise RuntimeError(
            f"{model.__name__}.{field_name} non dichiara piu' max_length in "
            f"schema.py"
        )
    return int(value)


#: Minuti dalla mezzanotte ammessi da TimeWindow: 0..1440.
DAY_MINUTE_RANGE = _schema_range(TimeWindow, "to_minute")
#: Tetto al tempo di arrivo ammesso da MaxResponseTime: 1..600 minuti.
RESPONSE_MINUTES_RANGE = _schema_range(MaxResponseTime, "minutes")
#: Presidio minimo ammesso da MinVehiclesOnFire: 1..20 mezzi.
MIN_VEHICLES_RANGE = _schema_range(MinVehiclesOnFire, "count")
#: Quota per categoria ammessa da VehicleTypeQuota: 0..100 mezzi.
QUOTA_RANGE = _schema_range(VehicleTypeQuota, "max_used")
#: Conteggio ammesso da ReserveVehicle: 1..50 mezzi.
RESERVE_COUNT_RANGE = _schema_range(ReserveVehicle, "count")
#: Peso ammesso da ObjectiveWeight: 0.0..100.0.
OBJECTIVE_WEIGHT_RANGE = _schema_range(ObjectiveWeight, "weight")

#: Lunghezza massima di `constraint_set_id` dichiarata in schema.py.
MAX_SET_ID_LENGTH = _schema_max_length(ConstraintSet, "constraint_set_id")
#: Lunghezza massima di `source_text` dichiarata in schema.py.
MAX_SOURCE_TEXT_LENGTH = _schema_max_length(ConstraintSet, "source_text")
#: Lunghezza massima di `source_span` dichiarata in schema.py.
MAX_SPAN_LENGTH = _schema_max_length(BaseConstraint, "source_span")
#: Lunghezza massima del nome di ProtectInfrastructure dichiarata in schema.py.
MAX_INFRASTRUCTURE_NAME_LENGTH = _schema_max_length(ProtectInfrastructure, "name")


# --------------------------------------------------------------------------
# Mezzi
# --------------------------------------------------------------------------

# Anche qui l'ordine conta: "elicotteri pesanti" prima di "elicotteri".
_VEHICLE_ALT = (
    r"(?:mezzi aerei|mezzi terrestri|elicotteri pesanti|elicottero pesante|"
    r"elicotteri medi|elicottero medio|elicotteri leggeri|elicottero leggero|"
    r"elicotteri|elicottero|canadair|velivoli|velivolo|aerei|aereo|"
    r"autobotti|autobotte|moduli aib|modulo aib|moduli|modulo|"
    r"squadre a terra|squadra a terra|squadre di terra|squadra di terra|"
    r"squadre|squadra|mezzi|mezzo)"
)
_VEHICLE_RE = re.compile(rf"\b{_VEHICLE_ALT}\b")

# Matricole tipo CAN_01, ELI_3, AIB_12: se il coordinatore le usa, sono
# l'indicazione piu' precisa possibile e vanno preferite a tutto il resto.
_VEHICLE_ID_RE = re.compile(r"\b([A-Z]{2,5}_\d{1,3})\b")

_DOMAIN_WORDS = {
    "mezzi aerei": "air",
    "mezzi terrestri": "ground",
}


def _selector_for_vehicle_word(word: str) -> Optional[Selector]:
    """Selettore corrispondente a una parola che nomina dei mezzi."""
    w = word.strip().lower()
    if w in _DOMAIN_WORDS:
        return Selector(
            field=SelectorField.VEHICLE_DOMAIN, values=[_DOMAIN_WORDS[w]]
        )
    if w in ("mezzi", "mezzo"):
        # "tenete un mezzo in riserva": nessuna classe indicata, vale per tutti.
        return Selector(field=SelectorField.ALL)
    kinds = vehicle_kinds_for(w)
    if not kinds:
        return None
    return Selector(
        field=SelectorField.VEHICLE_KIND, values=[k.value for k in kinds]
    )


# --------------------------------------------------------------------------
# Luoghi: comuni, province, aree protette
# --------------------------------------------------------------------------


class PlaceKind(str, Enum):
    COMUNE = "comune"
    PROVINCIA = "provincia"
    PROTECTED = "protected"


@dataclass(frozen=True)
class PlaceHit:
    """Un toponimo riconosciuto nel testo, con la sua posizione."""

    start: int
    end: int
    kind: PlaceKind
    canonical: str


# Elenco di appoggio, volutamente non esaustivo. I dati statici completi
# stanno in `data/static/comuni.json` e vengono caricati quando ci sono (vedi
# `_gazetteer`); questa lista serve a far funzionare il traduttore, e i suoi
# test, anche su un repository appena clonato. Sono esclusi di proposito i nomi
# che coincidono con parole comuni ("Rose", "Serra"), perche' produrrebbero
# falsi positivi che il traduttore non saprebbe smentire.
COMUNI_DI_APPOGGIO = [
    "Cosenza", "Catanzaro", "Crotone", "Vibo Valentia", "Reggio Calabria",
    "Lamezia Terme", "Rende", "Castrovillari", "Corigliano", "Rossano",
    "Cetraro", "Paola", "Amantea", "Scalea", "Praia a Mare", "Diamante",
    "Cirella", "Belvedere Marittimo", "San Giovanni in Fiore",
    "Spezzano della Sila", "Camigliatello", "Acri", "Bisignano", "Luzzi",
    "Montalto Uffugo", "San Marco Argentano", "Trebisacce", "Cariati",
    "Mormanno", "Morano Calabro", "Civita", "Cerchiara di Calabria",
    "Longobucco", "Cropalati", "Soveria Mannelli", "Decollatura",
    "Tiriolo", "Sersale", "Taverna", "Gimigliano", "Girifalco", "Chiaravalle",
    "Serra San Bruno", "Mongiana", "Fabrizia", "Soriano Calabro",
    "Nicotera", "Tropea", "Ricadi", "Pizzo", "Vibo Marina", "Mileto",
    "Gioia Tauro", "Palmi", "Bagnara Calabra", "Scilla", "Villa San Giovanni",
    "Gambarie", "Santo Stefano in Aspromonte", "Delianuova", "Sinopoli",
    "Melito di Porto Salvo", "Bova", "Roccaforte del Greco", "Africo",
    "San Luca", "Platì", "Bovalino", "Locri", "Siderno", "Gerace",
    "Caulonia", "Stilo", "Bivongi", "Isola Capo Rizzuto", "Cutro",
    "Santa Severina", "Cirò Marina", "Strongoli", "Petilia Policastro",
    "Mesoraca", "Botricello", "Sellia Marina", "Catanzaro Lido", "Gallina",
]

# I parchi si riconoscono dal nome distintivo, non dalla dicitura completa:
# nessuno dice "il Parco Nazionale della Sila", si dice "la Sila".
PARCHI = {
    "sila": "Parco Nazionale della Sila",
    "pollino": "Parco Nazionale del Pollino",
    "aspromonte": "Parco Nazionale dell'Aspromonte",
    "serre": "Parco Naturale Regionale delle Serre",
}

# Aggettivi provinciali: "il cosentino", "nel reggino".
AGGETTIVI_PROVINCIALI = {
    "cosentino": "Cosenza",
    "catanzarese": "Catanzaro",
    "reggino": "Reggio Calabria",
    "vibonese": "Vibo Valentia",
    "crotonese": "Crotone",
}


def _gazetteer() -> list[str]:
    """Elenco dei comuni: dai dati statici se ci sono, altrimenti di appoggio.

    Il caricamento e' protetto perche' `data/static/` puo' non essere ancora
    stato generato: il traduttore a regole deve funzionare lo stesso, e' il
    pavimento di sicurezza del sistema.
    """
    names = list(COMUNI_DI_APPOGGIO)
    try:  # pragma: no cover - dipende dalla presenza dei dati statici
        from tagliafuoco.domain.registry import load_comuni

        for row in load_comuni():
            nome = row.get("nome") if isinstance(row, dict) else None
            if nome and nome not in names:
                names.append(nome)
    except Exception:
        pass
    return names


_PROVINCIA_PREFIX_RE = re.compile(r"provincia\s+di\s*$")

# Lunghezza minima perche' la prima parola di un toponimo composto valga come
# abbreviazione. "Reggio" per "Reggio Calabria" si', "Bova" o "Villa" no:
# sotto questa soglia le parole italiane comuni cominciano a fare falsi
# positivi, e un falso positivo qui diventa un vincolo inventato.
MIN_ALIAS_LENGTH = 6


@dataclass(frozen=True)
class PlaceIndex:
    """Dizionario dei toponimi riconoscibili, con la regex che li trova.

    Esiste come oggetto e non come costante di modulo perche' l'insieme dei
    posti riconoscibili dipende dallo scenario: i comuni dove ci sono davvero
    dei focolai vanno riconosciuti anche se non stanno nell'elenco di appoggio.

    `entries` e' una vista di sola lettura, e non per pedanteria: l'indice di
    base e' un oggetto unico condiviso da tutte le traduzioni che non hanno
    scenario. Una voce aggiunta li' dentro da un chiamante resterebbe per
    tutta la vita del processo e cambierebbe silenziosamente le traduzioni
    successive, che e' il genere di bug che non si trova piu'. Chi vuole un
    indice piu' ricco ne costruisce uno con `build_place_index`.
    """

    pattern: re.Pattern
    entries: Mapping  # forma appiattita -> (PlaceKind, nome canonico)

    def lookup(self, token: str) -> tuple[PlaceKind, str]:
        return self.entries.get(token, (PlaceKind.COMUNE, token))


# Regex che non aggancia mai niente. Serve al caso limite dell'indice vuoto:
# un'alternanza vuota, `(?:)`, aggancia la stringa vuota a ogni posizione e
# produrrebbe un toponimo fantasma per ogni carattere dell'ordine.
_NEVER_RE = re.compile(r"(?!)")


def _compile_index(entries: dict) -> PlaceIndex:
    """Compila la regex dei toponimi, dal piu' lungo al piu' corto.

    L'ordine conta: in un'alternanza la regex prende la prima che aggancia, e
    "Reggio Calabria" deve vincere su "Reggio".
    """
    frozen = MappingProxyType(dict(entries))
    tokens = sorted((t for t in frozen if t and t.strip()), key=len, reverse=True)
    if not tokens:
        return PlaceIndex(_NEVER_RE, frozen)
    alt = "|".join(re.escape(t) for t in tokens)
    return PlaceIndex(re.compile(rf"\b(?:{alt})\b"), frozen)


def _base_entries() -> dict:
    entries: dict = {}
    for name in _gazetteer():
        entries.setdefault(flatten(name), (PlaceKind.COMUNE, name))
    for token, nome in PARCHI.items():
        entries[token] = (PlaceKind.PROTECTED, nome)
    for token, nome in AGGETTIVI_PROVINCIALI.items():
        entries[token] = (PlaceKind.PROVINCIA, nome)
    return entries


_BASE_INDEX = _compile_index(_base_entries())


def build_place_index(scenario: Optional[Scenario] = None) -> PlaceIndex:
    """Indice dei toponimi, arricchito con i posti presenti nello scenario.

    Senza scenario si usa l'elenco di appoggio e basta. Con lo scenario si
    aggiungono i comuni, le province, le frazioni e i parchi dei focolai
    attivi, piu' l'abbreviazione sulla prima parola dei nomi composti: in sala
    operativa si dice "Reggio", non "Reggio Calabria".
    """
    if scenario is None or not scenario.fires:
        return _BASE_INDEX

    entries = dict(_BASE_INDEX.entries)

    def add(name: Optional[str], kind: PlaceKind, alias: bool) -> None:
        if not name:
            return
        flat = flatten(name)
        entries.setdefault(flat, (kind, name))
        if not alias:
            return
        head = flat.split(" ")[0]
        if len(head) >= MIN_ALIAS_LENGTH and head not in entries:
            entries[head] = (kind, name)

    for fire in scenario.fires:
        add(fire.comune, PlaceKind.COMUNE, alias=True)
        add(fire.nearest_settlement, PlaceKind.COMUNE, alias=False)
        add(fire.provincia, PlaceKind.PROVINCIA, alias=False)
        add(fire.protected_area_name, PlaceKind.PROTECTED, alias=False)
    return _compile_index(entries)


def find_places(
    hay: str,
    start: int = 0,
    end: Optional[int] = None,
    index: Optional[PlaceIndex] = None,
) -> list[PlaceHit]:
    """Toponimi riconosciuti nella porzione di testo indicata."""
    idx = index or _BASE_INDEX
    end = len(hay) if end is None else end
    hits: list[PlaceHit] = []
    for m in idx.pattern.finditer(hay, start, end):
        kind, canonical = idx.lookup(m.group(0))
        if kind is PlaceKind.COMUNE:
            # "provincia di Cosenza" e' la provincia; "Cosenza" da sola e' il
            # comune capoluogo. Si guarda che cosa precede il nome.
            prefix = hay[max(0, m.start() - 20) : m.start()]
            if _PROVINCIA_PREFIX_RE.search(prefix) or (
                canonical in config.PROVINCE and "provincia" in prefix
            ):
                kind = PlaceKind.PROVINCIA
        hits.append(PlaceHit(m.start(), m.end(), kind, canonical))
    return hits


def _fire_selector_for(place: PlaceHit) -> Selector:
    """Selettore di focolai corrispondente a un toponimo."""
    if place.kind is PlaceKind.PROVINCIA:
        return Selector(field=SelectorField.FIRE_PROVINCIA, values=[place.canonical])
    if place.kind is PlaceKind.PROTECTED:
        return Selector(
            field=SelectorField.FIRE_IN_PROTECTED_AREA, values=[place.canonical]
        )
    return Selector(field=SelectorField.FIRE_COMUNE, values=[place.canonical])


def _zone_kind_for(place: PlaceHit) -> ZoneKind:
    if place.kind is PlaceKind.PROVINCIA:
        return ZoneKind.PROVINCIA
    if place.kind is PlaceKind.PROTECTED:
        return ZoneKind.PROTECTED_AREA
    return ZoneKind.COMUNE


# --------------------------------------------------------------------------
# Contesto di traduzione
# --------------------------------------------------------------------------


@dataclass
class _Ctx:
    """Stato di una singola traduzione.

    `consumed` e' il meccanismo che evita i vincoli doppi: quando una regola
    riconosce un tratto di testo se lo prende, e le regole successive, piu'
    generiche, lo saltano.
    """

    raw: str
    hay: str
    scenario: Optional[Scenario] = None
    consumed: list[tuple[int, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counter: int = 0
    index: Optional[PlaceIndex] = None

    def __post_init__(self) -> None:
        # L'indice dei toponimi si costruisce una volta per traduzione: con lo
        # scenario riconosce anche i posti dove i focolai ci sono davvero.
        if self.index is None:
            self.index = build_place_index(self.scenario)

    def places(self, start: int = 0, end: Optional[int] = None) -> list[PlaceHit]:
        """Toponimi nella porzione indicata, secondo l'indice di questa traduzione."""
        return find_places(self.hay, start, end, self.index)

    def new_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}_{self.counter:02d}"

    def consume(self, start: int, end: int) -> None:
        self.consumed.append((start, end))

    def is_consumed(self, start: int, end: int) -> bool:
        return any(start < ce and cs < end for cs, ce in self.consumed)

    def span(self, start: int, end: int) -> str:
        """Frammento originale dell'ordine, per mostrarlo al coordinatore."""
        return self.raw[start:end].strip()[:MAX_SPAN_LENGTH]


# --------------------------------------------------------------------------
# Confini di proposizione
# --------------------------------------------------------------------------
#
# Quasi tutte le regole guardano "accanto" al frammento che hanno riconosciuto
# per completarlo: il mezzo, il focolaio, il numero. Quel "accanto" va limitato,
# altrimenti un ordine prende pezzi dall'ordine successivo e produce un vincolo
# che nessuno ha dato.
#
# I livelli di confine sono due, e la differenza non e' un dettaglio di stile.
#
# - Punteggiatura FORTE (punto, punto e virgola, due punti, esclamativo,
#   interrogativo, a capo): separa due ordini distinti. "Almeno due mezzi.
#   Mandate tutto su Cirella" sono due ordini, e il minimo vale per tutti i
#   focolai, non per quelli di Cirella.
# - Punteggiatura DEBOLE: la virgola. Dentro un ordine dettato a voce la
#   virgola spesso non separa niente ("tutti sul posto entro venti minuti,
#   l'elicottero resta di riserva" e' un ordine solo), quindi le ricerche di
#   contesto la attraversano. La attraversa chi cerca il complemento di un
#   vincolo gia' riconosciuto; NON la attraversa chi parte da un verbo per
#   andare a cercare un toponimo (`_trigger_then_place`), perche' li' la
#   virgola separa davvero: in "mandate i canadair, su Paola andate voi" il
#   luogo appartiene alla proposizione dopo.
#
# I numeri sono l'eccezione che obbliga a scrivere le due espressioni con i
# lookaround invece che con una classe di caratteri secca: in italiano gli
# orari si scrivono "19:30" o "19.30" e i decimali "1,5". Un punto, un due
# punti o una virgola fra due cifre non chiudono nessuna proposizione, e
# trattarli come confini taglierebbe un orario a meta' proprio nelle regole
# che quell'orario devono leggere.

#: Separatori che chiudono una proposizione anche solo debolmente (virgola inclusa).
_CLAUSE_END_RE = re.compile(r"[;!?\n]|(?<!\d)[.,:](?!\d)")

#: Separatori che chiudono davvero un ordine e ne aprono un altro.
_STRONG_PUNCT_RE = re.compile(r"[;!?\n]|(?<!\d)[.:](?!\d)")


def _clause_start(hay: str, pos: int) -> int:
    """Inizio dell'ordine che contiene `pos`, cioe' dopo l'ultimo punto fermo.

    Serve alle ricerche all'indietro: il complemento di un vincolo puo' stare
    prima del vincolo ("su Cirella almeno due mezzi"), ma non prima del punto
    che chiude l'ordine precedente.
    """
    last = 0
    for m in _STRONG_PUNCT_RE.finditer(hay, 0, pos):
        last = m.end()
    return last


def _clause_limit(hay: str, pos: int) -> int:
    """Fine dell'ordine che contiene `pos`, o il testo intero se non si chiude.

    Gemella di `_clause_start` per le ricerche in avanti. Restituisce la
    posizione del separatore, non quella successiva, cosi' il separatore stesso
    resta fuori dalla finestra di ricerca.
    """
    m = _STRONG_PUNCT_RE.search(hay, pos)
    return m.start() if m else len(hay)


def _clause_end(hay: str, start: int) -> int:
    """Fine della proposizione che comincia in `start`, virgola compresa.

    Confine debole: lo usa chi deve prendersi una proposizione intera e non
    l'ordine intero. L'astensione (`_rule_out_of_repertoire`) consuma fin qui
    perche' "Avvisate il sindaco di Paola, poi concentratevi su Scilla" e'
    fuori repertorio solo nella prima meta'.
    """
    m = _CLAUSE_END_RE.search(hay, start)
    return m.start() if m else len(hay)


# --------------------------------------------------------------------------
# Riconoscimento del mezzo vicino a un ordine
# --------------------------------------------------------------------------

# Solo le forme di genitivo. "l'elicottero DI Cosenza" indica la base del
# mezzo; "i canadair SU Cirella" indica invece il focolaio, ed e' tutta un'altra
# cosa: confonderle significava marchiare come "base" ogni luogo nominato.
_OF_PLACE_RE = re.compile(r"^\s*(?:di|del|della|dello|dei|delle|dell')\s*")


def _vehicle_selector_near(
    ctx: _Ctx, start: int, end: int, before: int = 80, after: int = 50
) -> tuple[Optional[Selector], Optional[tuple[int, int]]]:
    """Selettore dei mezzi nominati intorno a un ordine, e la sua posizione.

    Si cerca in una finestra attorno al frammento riconosciuto e si prende la
    menzione piu' vicina: in italiano il complemento oggetto puo' stare prima
    ("l'elicottero resta di riserva") o dopo ("tenete di riserva l'elicottero").
    """
    win_start = max(0, start - before)
    win_end = min(len(ctx.hay), end + after)

    def distance(m: re.Match) -> int:
        """Quanti caratteri separano la menzione dal frammento riconosciuto."""
        if m.end() <= start:
            return start - m.end()
        if m.start() >= end:
            return m.start() - end
        return 0

    # Una matricola esplicita batte qualunque nome di categoria: e' il modo
    # piu' preciso in cui il coordinatore puo' indicare un mezzo. Fra due
    # matricole vince la piu' vicina, non la prima da sinistra: la vicinanza
    # e' il criterio usato anche per le categorie e mescolarli darebbe
    # risultati che dipendono dall'ordine delle parole invece che dal senso.
    ids = list(_VEHICLE_ID_RE.finditer(ctx.raw, win_start, win_end))
    if ids:
        m = min(ids, key=distance)
        return (
            Selector(field=SelectorField.VEHICLE_ID, values=[m.group(1)]),
            (m.start(), m.end()),
        )

    candidates = sorted(
        _VEHICLE_RE.finditer(ctx.hay, win_start, win_end),
        key=lambda m: (distance(m), m.start()),
    )
    # Si scorre in ordine di vicinanza e ci si ferma alla prima menzione che
    # si sa tradurre. Fermarsi alla piu' vicina e basta faceva perdere l'intero
    # vincolo quando quella menzione non era traducibile, anche se poche
    # parole piu' in la' ce n'era una buona.
    for m in candidates:
        selector = _selector_for_vehicle_word(m.group(0))
        if selector is None:
            continue

        # "l'elicottero di Cosenza": il mezzo e' identificato dalla base.
        tail = ctx.hay[m.end() : min(len(ctx.hay), m.end() + 30)]
        of_match = _OF_PLACE_RE.match(tail)
        if of_match:
            place_start = m.end() + of_match.end()
            places = ctx.places(place_start, place_start + 30)
            near = [p for p in places if p.start == place_start]
            if near:
                refined = _refine_by_base(ctx, selector, near[0])
                if refined is not None:
                    return refined, (m.start(), near[0].end)
        return selector, (m.start(), m.end())
    return None, None


def _refine_by_base(
    ctx: _Ctx, selector: Selector, place: PlaceHit
) -> Optional[Selector]:
    """Restringe un selettore di mezzi alla base indicata dal toponimo.

    Senza scenario non si puo' fare: le basi e le matricole sono dati dello
    scenario, non del testo. In quel caso si lascia il selettore per categoria
    e si annota l'imprecisione, che e' meglio di un vincolo inventato.

    Il punto delicato e' che il toponimo **restringe**, non sostituisce. "I
    mezzi aerei di Cosenza" sono i mezzi che stanno a Cosenza *e* volano: un
    selettore sulla sola base li allargherebbe alle autobotti della stessa
    base, cioe' produrrebbe un vincolo piu' largo di quello ricevuto. Per
    questo i selettori per categoria e per dominio si risolvono in matricole,
    che e' l'unico modo di esprimere l'intersezione con lo schema attuale.
    """
    if ctx.scenario is None:
        ctx.warnings.append(
            f"'{place.canonical}' qualifica un mezzo ma manca lo scenario: "
            f"il vincolo resta sulla categoria e non sul singolo mezzo"
        )
        return None
    base_ids = base_ids_for_place(place.canonical, ctx.scenario)
    if not base_ids:
        ctx.warnings.append(
            f"nessuna base corrisponde a '{place.canonical}': il vincolo resta "
            f"sulla categoria del mezzo"
        )
        return None

    if selector.field is SelectorField.ALL:
        # "i mezzi di Cosenza": nessun attributo da conservare, e il selettore
        # per base e' la forma piu' fedele e piu' stabile fra una finestra e
        # l'altra. Si ordina per rendere ripetibile il risultato.
        return Selector(field=SelectorField.BASE_ID, values=sorted(base_ids))

    if selector.field is SelectorField.VEHICLE_KIND:
        attribute, what = "kind", "categoria"
    elif selector.field is SelectorField.VEHICLE_DOMAIN:
        attribute, what = "domain", "dominio"
    else:
        return Selector(field=SelectorField.BASE_ID, values=sorted(base_ids))

    wanted = set(selector.values)
    ids = [
        v.vehicle_id
        for v in ctx.scenario.vehicles
        if v.home_base_id in base_ids and getattr(v, attribute).value in wanted
    ]
    if ids:
        return Selector(field=SelectorField.VEHICLE_ID, values=ids)
    ctx.warnings.append(
        f"nessun mezzo di quel {what} e' di stanza a '{place.canonical}'"
    )
    return None


def _fire_selector_near(
    ctx: _Ctx, start: int, end: int, after: int = 70, before: int = 45
) -> tuple[Selector, Optional[tuple[int, int]]]:
    """Focolai indicati accanto a un ordine. ALL se non ne sono indicati.

    Si guarda prima a destra ("almeno due mezzi su Cirella") e poi a sinistra
    ("su Cirella almeno due mezzi"), che in italiano parlato e' altrettanto
    frequente. Nessuna delle due ricerche scavalca la punteggiatura forte: su
    "Almeno due mezzi. Mandate tutto su Cirella" il minimo riguarda tutti i
    focolai, non quello di Cirella, che appartiene alla frase dopo. Prima di
    questo limite la ricerca in avanti attraversava il punto e attaccava al
    vincolo un focolaio preso da un altro ordine.
    """
    ceiling = min(_clause_limit(ctx.hay, end), end + after)
    places = ctx.places(end, ceiling) if ceiling > end else []
    if places:
        p = places[0]
        return _fire_selector_for(p), (p.start, p.end)

    floor = max(_clause_start(ctx.hay, start), start - before)
    behind = ctx.places(floor, start)
    if behind:
        p = behind[-1]  # il piu' vicino all'ordine
        return _fire_selector_for(p), (p.start, p.end)
    return Selector(field=SelectorField.ALL), None


# --------------------------------------------------------------------------
# Regole
# --------------------------------------------------------------------------

Rule = Callable[[_Ctx], list[AnyConstraint]]

# 1. Finestra temporale ------------------------------------------------------

_TIME_RANGE_RE = re.compile(
    rf"\bdalle\s+({NUM})(?:[:.](\d{{2}}))?\s+(?:alle|fino alle)\s+({NUM})(?:[:.](\d{{2}}))?"
)
_TIME_UNTIL_RE = re.compile(
    rf"\b(?:fino alle|sino alle|non oltre le|solo fino alle|entro le)\s+"
    rf"({NUM})(?:[:.](\d{{2}}))?"
)
_MORNING_HINT_RE = re.compile(r"\b(?:del mattino|di mattina|antimeridiane)\b")


def _hour_to_minute(hour: int, minute: int, evening_bias: bool) -> Optional[int]:
    """Ora del giorno in minuti dalla mezzanotte. None se non e' un'ora.

    Taratura dichiarata: un'ora fra 1 e 11 detta come termine di un'attivita'
    antincendio si legge come ora serale. "Gli aerei fino alle otto" vuol dire
    le venti, non le otto del mattino, perche' alle otto del mattino
    l'attivita' comincia. La lettura si ribalta se l'ordine dice "di mattina".

    Le ore oltre le 24 e i minuti oltre i 59 non si riportano dentro
    l'intervallo: si rifiutano. Riportarli dentro trasformava "fino alle 99"
    in una finestra fino a mezzanotte, cioe' in un vincolo che il coordinatore
    non ha chiesto e che nessuno avrebbe mai smentito guardando il
    `source_span`. Meglio un vincolo in meno e un avviso in piu'.

    Le 24 valgono come fine giornata (il minuto 1440, che e' l'estremo ammesso
    da `TimeWindow`), ma solo a minuti zero: "le 24:30" non sono un orario, e
    tagliarle a mezzanotte era esattamente il vincolo inventato che il
    paragrafo qui sopra dice di non produrre. Si rifiutano come le 25.
    """
    if not 0 <= hour <= 24 or not 0 <= minute <= 59:
        return None
    if hour == 24 and minute:
        return None
    if evening_bias and 1 <= hour <= 11:
        hour += 12
    total = hour * 60 + minute
    # Controllo di coerenza con lo schema, non un taglio: dopo i filtri qui
    # sopra `total` ci sta per costruzione, e se un giorno non ci stesse piu'
    # la cosa giusta sarebbe astenersi, non consegnare un altro orario.
    low, high = DAY_MINUTE_RANGE
    return total if low <= total <= high else None


def _rule_time_window(ctx: _Ctx) -> list[AnyConstraint]:
    """Finestra oraria di impiego: "gli aerei fino alle venti"."""
    out: list[AnyConstraint] = []
    morning = bool(_MORNING_HINT_RE.search(ctx.hay))

    for m in _TIME_RANGE_RE.finditer(ctx.hay):
        if ctx.is_consumed(m.start(), m.end()):
            continue
        h_from, h_to = parse_number(m.group(1)), parse_number(m.group(3))
        if h_from is None or h_to is None:
            continue
        from_minute = _hour_to_minute(h_from, int(m.group(2) or 0), False)
        # In un intervallo esplicito la lettura serale si applica solo se
        # serve a tenere la finestra in ordine: "dalle otto alle undici" e'
        # una mattinata, non un turno che finisce alle ventitre'. L'indizio
        # e' nella forma stessa dell'ordine, e vale piu' della taratura.
        to_minute = _hour_to_minute(h_to, int(m.group(4) or 0), False)
        if (
            not morning
            and from_minute is not None
            and to_minute is not None
            and to_minute <= from_minute
        ):
            to_minute = _hour_to_minute(h_to, int(m.group(4) or 0), True)
        c = _make_time_window(ctx, m.start(), m.end(), from_minute, to_minute)
        if c is not None:
            out.append(c)

    for m in _TIME_UNTIL_RE.finditer(ctx.hay):
        if ctx.is_consumed(m.start(), m.end()):
            continue
        h_to = parse_number(m.group(1))
        if h_to is None:
            continue
        to_minute = _hour_to_minute(h_to, int(m.group(2) or 0), not morning)
        # L'inizio non e' detto: si prende l'alba operativa da config, che e'
        # il momento in cui i mezzi aerei possono cominciare a volare.
        c = _make_time_window(
            ctx, m.start(), m.end(), config.DAYLIGHT_START_MINUTE, to_minute
        )
        if c is not None:
            out.append(c)
    return out


def _make_time_window(
    ctx: _Ctx,
    start: int,
    end: int,
    from_minute: Optional[int],
    to_minute: Optional[int],
) -> Optional[TimeWindow]:
    """Costruisce la finestra, oppure si astiene e lo dice.

    Due motivi per astenersi, e vanno distinti nell'avviso: l'ora non esiste
    ("fino alle 99") oppure la finestra e' rovesciata. Nel primo caso il testo
    non e' un orario, nel secondo lo e' ma non si puo' applicare: al
    coordinatore serve sapere quale dei due.
    """
    if from_minute is None or to_minute is None:
        ctx.warnings.append(
            f"orario non valido in '{ctx.span(start, end)}': nessuna finestra "
            f"temporale emessa"
        )
        return None
    if to_minute <= from_minute:
        ctx.warnings.append(
            f"finestra oraria incoerente in '{ctx.span(start, end)}': ignorata"
        )
        return None
    selector, veh_span = _vehicle_selector_near(ctx, start, end)
    if selector is None:
        # Le finestre orarie riguardano quasi sempre il volo: e' il vincolo
        # fisico che il coordinatore ha in testa quando dice "fino alle otto".
        selector = Selector(field=SelectorField.VEHICLE_DOMAIN, values=["air"])
        ctx.warnings.append(
            f"nessun mezzo nominato in '{ctx.span(start, end)}': la finestra "
            f"e' stata applicata ai mezzi aerei"
        )
        confidence = CONF_INFERRED
    else:
        confidence = CONF_EXPLICIT
    span_start = min(start, veh_span[0]) if veh_span else start
    span_end = max(end, veh_span[1]) if veh_span else end
    # Si consuma il tratto CONTINUO dall'orario alla menzione del mezzo, non i
    # due estremi separatamente. Quello che sta in mezzo e' quasi sempre il
    # verbo ("gli elicotteri operano dalle sei alle venti") e lasciarlo libero
    # non servirebbe a nessuno; se invece in mezzo c'e' un toponimo, tenerlo
    # consumato e' proprio cio' che impedisce alla regola dei toponimi di
    # ricavarne una priorita' di zona che l'ordine non chiede.
    #
    # Il prezzo di questa scelta e' che la finestra oraria si prende anche il
    # testo di un vincolo piu' specifico che stesse nel mezzo: e' il motivo per
    # cui `_rule_min_vehicles` gira PRIMA di questa regola (vedi `RULES`).
    ctx.consume(span_start, span_end)
    return TimeWindow(
        constraint_id=ctx.new_id("TW"),
        selector=selector,
        from_minute=from_minute,
        to_minute=to_minute,
        enforcement=Enforcement.HARD,
        confidence=confidence,
        source_span=ctx.span(span_start, span_end),
        # L'unita' va scritta per esteso: `from_minute` e `to_minute` sono
        # minuti dalla MEZZANOTTE (come DAYLIGHT_START_MINUTE in config),
        # mentre i tempi di un Assignment sono minuti dall'inizio
        # dell'orizzonte. Sono due origini diverse e chi applica il vincolo in
        # optimize/ deve convertire, non confrontare direttamente.
        rationale=(
            f"impiego consentito dal minuto {from_minute} al minuto "
            f"{to_minute} contati dalla mezzanotte"
        ),
    )


# 2. Tempo massimo di risposta ----------------------------------------------

# Le durate, in sala operativa, si dicono in tre modi e si usano tutti e tre:
# in cifre ("20 minuti"), con il numero scritto in lettere ("venti minuti") e
# a parole ("un quarto d'ora", "mezz'ora", "un'ora e mezza"). L'apostrofo
# arriva dritto o tipografico, e a volte non arriva affatto ("mezzora"). Il
# testo e' gia' passato da `flatten`, che riporta l'apostrofo tipografico a
# quello dritto conservando gli offset; qui l'apostrofo resta comunque
# facoltativo, insieme agli spazi, perche' chi detta non e' coerente.
_D_ONE_HOUR = r"(?:un\s*'?\s*ora|una\s+ora)"
_D_HOUR_AND_HALF = rf"(?:{_D_ONE_HOUR}|{NUM}\s*ore?)\s+e\s+mezz[ao]"
_D_THREE_QUARTERS = r"tre\s+quarti\s+d\s*'?\s*ora"
_D_QUARTER = r"(?:un\s+)?quarto\s+d\s*'?\s*ora"
_D_HALF_HOUR = r"(?:una\s+)?(?:mezz\s*'?\s*ora|mezza\s+ora)"
_D_NUM_UNIT = rf"{NUM}\s*(?:minuti|minuto|min|ore|ora|h)"

# L'ordine e' quello della corrispondenza massima, come nel resto del modulo:
# "un'ora e mezza" prima di "un'ora", altrimenti novanta minuti diventerebbero
# sessanta, cioe' un vincolo piu' stretto di quello ricevuto.
_DURATION_ALT = "|".join(
    (
        _D_HOUR_AND_HALF,
        _D_THREE_QUARTERS,
        _D_QUARTER,
        _D_HALF_HOUR,
        _D_ONE_HOUR,
        _D_NUM_UNIT,
    )
)

# Le stesse forme, una per una, per rileggere il frammento gia' riconosciuto e
# ricavarne i minuti. Stanno separate da `_DURATION_ALT` perche' li' i gruppi
# devono restare non catturanti: il frammento si estrae per nome e si riparsa.
_RE_HOUR_AND_HALF = re.compile(rf"(?:{_D_ONE_HOUR}|({NUM})\s*ore?)\s+e\s+mezz[ao]")
_RE_THREE_QUARTERS = re.compile(_D_THREE_QUARTERS)
_RE_QUARTER = re.compile(_D_QUARTER)
_RE_HALF_HOUR = re.compile(_D_HALF_HOUR)
_RE_ONE_HOUR = re.compile(_D_ONE_HOUR)
_RE_NUM_UNIT = re.compile(rf"({NUM})\s*(minuti|minuto|min|ore|ora|h)")


def parse_duration_minutes(text: str) -> Optional[int]:
    """Minuti di una durata detta in italiano. None se non e' una durata.

    Copre le cifre, i numeri scritti in lettere e le frazioni d'ora dette a
    parole. Le equivalenze non sono tarature del modello ma della lingua: un
    quarto d'ora e' quindici minuti e nessuno intende altro.
    """
    t = " ".join(text.split()).lower()
    if not t:
        return None
    m = _RE_HOUR_AND_HALF.fullmatch(t)
    if m:
        hours = parse_number(m.group(1)) if m.group(1) else 1
        return None if hours is None else hours * 60 + 30
    if _RE_THREE_QUARTERS.fullmatch(t):
        return 45
    if _RE_QUARTER.fullmatch(t):
        return 15
    if _RE_HALF_HOUR.fullmatch(t):
        return 30
    if _RE_ONE_HOUR.fullmatch(t):
        return 60
    m = _RE_NUM_UNIT.fullmatch(t)
    if m:
        n = parse_number(m.group(1))
        if n is None:
            return None
        return n * 60 if m.group(2).startswith(("or", "h")) else n
    return None


# Marcatori che pongono un tetto da soli: "entro venti minuti" e "al massimo
# venti minuti" chiedono la stessa cosa.
_CEILING_DIRECT = (
    r"entro e non oltre|entro|non oltre|non piu' di|non piu di|"
    r"non superiore a|non superiori a|al massimo|massimo|al piu'|"
    r"in meno di|meno di|sotto"
)

# Comparativi che pongono un tetto soltanto sotto negazione. "Oltre la
# mezz'ora" da solo descrive un tempo lungo, non lo vieta; "nessuno oltre la
# mezz'ora" lo vieta, ed e' la negazione a rovesciare il verso. Tenerli
# separati dai marcatori diretti e' il modo di non leggere "ci vogliono oltre
# venti minuti", che e' una constatazione, come se fosse un ordine.
_CEILING_COMPARATIVE = (
    r"superiore a|superiori a|al di sopra di|oltre|sopra|piu' di|piu di|"
    r"super(?:ate|iate|ino|ano|are|i|a)"
)
_NEGATION_WORDS = r"nessuno|nessun|nessuna|niente|nemmeno|neanche|neppure|mai|non"

# Articolo fra il marcatore e la durata: "oltre I cinque minuti", "sopra IL
# quarto d'ora", "entro L'ora e mezza".
_ARTICLE = r"(?:(?:i|il|lo|la|le|gli|un|uno|una)\s+|l'\s*)"

_MAX_TIME_DIRECT_RE = re.compile(
    rf"\b(?P<marker>{_CEILING_DIRECT})\s+{_ARTICLE}?(?P<dur>{_DURATION_ALT})\b"
)
# Il comparativo si riconosce da solo e la negazione si cerca dopo, in
# `_governing_negation`. La separazione non e' stilistica: e' il solo modo di
# usare il confine di proposizione che il modulo gia' definisce
# (`_CLAUSE_END_RE`) invece di riscriverlo a mano dentro l'espressione
# regolare, dove i lookaround che tengono insieme "19:30" e "1,5" andrebbero
# ripetuti, e ripetuti si sbagliano.
_MAX_TIME_COMPARATIVE_RE = re.compile(
    rf"\b(?P<marker>{_CEILING_COMPARATIVE})\s+{_ARTICLE}?(?P<dur>{_DURATION_ALT})\b"
)

_NEGATION_RE = re.compile(rf"\b(?:{_NEGATION_WORDS})\b")

# Quanti caratteri prima del marcatore si accetta la negazione che lo governa.
# Non e' una misura della lingua, e' una taratura: oltre una trentina di
# caratteri la negazione appartiene quasi sempre a un altro pezzo della frase.
_NEGATION_WINDOW = 30


def _weak_clause_start(hay: str, pos: int) -> int:
    """Inizio della proposizione che contiene `pos`, virgola compresa.

    Gemella all'indietro di `_clause_end`, e usa lo stesso confine debole:
    quello che serve a chi deve sapere se due parole stanno nella stessa
    proposizione, non soltanto nello stesso ordine.
    """
    last = 0
    for m in _CLAUSE_END_RE.finditer(hay, 0, pos):
        last = m.end()
    return last


def _governing_negation(hay: str, marker_start: int) -> Optional[int]:
    """Posizione della negazione che governa il marcatore, None se non c'e'.

    E' la negazione a rovesciare il verso di un marcatore di tempo, e lo
    rovescia in tutti e due i sensi: fa di "oltre venti minuti" un tetto e di
    "meno di venti minuti" un pavimento. Per questo la ricerca sta in un posto
    solo. Due copie della stessa idea divergono, e qui divergere significa
    leggere una frase come un ordine e la sua gemella come una constatazione.

    Il confine e' quello della PROPOSIZIONE, virgola compresa, non una finestra
    di caratteri qualsiasi: in "non mandate i canadair, oltre venti minuti ci
    mettono tutti" il "non" non governa l'"oltre", e non lo governa nemmeno in
    "non so quanto ci vuole: oltre venti minuti secondo loro" o dopo un punto
    esclamativo. Scritto come classe di caratteri, il confine dimenticava i due
    punti, il punto esclamativo e quello interrogativo, che questo stesso
    modulo tratta da separatori dappertutto: una constatazione della frase
    dopo diventava un vincolo, e un vincolo inventato non si vede, perche' e'
    ben formato, passa lo schema ed entra nel modello.

    Fra due negazioni vince la piu' vicina al marcatore: e' quella che lo
    governa, ed e' anche quella che fa un `source_span` contenente la
    negazione senza trascinarsi mezza frase.
    """
    lo = max(_weak_clause_start(hay, marker_start), marker_start - _NEGATION_WINDOW)
    found = None
    for m in _NEGATION_RE.finditer(hay, lo, marker_start):
        found = m.start()
    return found


def _is_floor_form(hay: str, marker_start: int, marker: str) -> bool:
    """True se una negazione rovescia il marcatore in un minimo.

    "Non meno di venti minuti" e' un pavimento, non un tetto: il marcatore e'
    lo stesso di "meno di venti minuti", a rovesciarlo e' la negazione davanti.
    Emetterlo come MAX_RESPONSE_TIME imporrebbe l'esatto contrario dell'ordine
    ricevuto.
    """
    if "meno di" not in marker:
        return False
    return _governing_negation(hay, marker_start) is not None


# "Sui focolai vicino alle case non voglio tempi sopra il quarto d'ora": qui
# l'interfaccia urbano-foresta non e' una priorita', e' il BERSAGLIO del tetto,
# cioe' il `fire_selector` del vincolo. Emettere anche un PRIORITIZE_ZONE
# significherebbe aggiungere un ordine che il coordinatore non ha dato, ed e'
# la lettura che il prompt del traduttore con modello linguistico gia'
# prescrive (`prompts/system_it.md`, esempio 5): i due traduttori devono
# rispondere la stessa cosa sulla stessa frase, altrimenti il benchmark
# confronta due repertori invece di due traduttori.
#
# La preposizione davanti non e' un ornamento, e' il guardiano della regola.
# "Prima le zone vicino alle case, ... nessuno sopra i venticinque minuti" dice
# due cose distinte, una priorita' e un tetto, e li' "vicino alle case" NON
# qualifica il tempo: senza la preposizione il tetto si mangerebbe la priorita'.
_WUI_TARGET_RE = re.compile(
    r"\b(?:sull'|sulle|sulla|sui|sul|su|nelle|nei|nella|nel|per)\s+"
    r"(?:(?:i|gli|le|quei|quelli|quelle|ogni|tutti|tutte)\s+)?"
    r"(?:focolai|focolaio|incendi|incendio|fuochi|fuoco|fronti|fronte|"
    r"zone|zona|aree|area)?\s*"
    r"(?:vicin[oaie]\s+(?:all[ea]\s+)?|a ridosso (?:delle|dei|del)\s+)?"
    r"(?:centri abitati|centro abitato|zone abitate|case|abitazioni|"
    r"abitato|abitati|frazioni|interfaccia(?:\s+urbano[ -]foresta)?)\b"
)

# Quanto lontano dal tetto si accetta la designazione del bersaglio. Sono gli
# stessi ordini di grandezza usati da `_fire_selector_near` per i toponimi: il
# bersaglio di un ordine sta accanto all'ordine, non tre proposizioni piu' in
# la'.
_WUI_TARGET_BEFORE = 60
_WUI_TARGET_AFTER = 60


def _wui_fire_selector_near(
    ctx: _Ctx, start: int, end: int
) -> Optional[tuple[Selector, tuple[int, int]]]:
    """Focolai in interfaccia urbano-foresta indicati come bersaglio dell'ordine.

    None se accanto all'ordine non ce n'e' nessuno, che e' il caso normale. Si
    guarda prima dietro, perche' in italiano il complemento anticipato e' la
    forma piu' frequente ("sui focolai vicino alle case non voglio..."), e poi
    avanti. Nessuna delle due ricerche scavalca la punteggiatura forte: il
    bersaglio della frase successiva non e' il bersaglio di questa.
    """
    floor = max(_clause_start(ctx.hay, start), start - _WUI_TARGET_BEFORE)
    behind = [m for m in _WUI_TARGET_RE.finditer(ctx.hay, floor, start)]
    if behind:
        m = behind[-1]  # il piu' vicino all'ordine
        if not ctx.is_consumed(m.start(), m.end()):
            return Selector(field=SelectorField.FIRE_IS_WUI, values=["true"]), (
                m.start(),
                m.end(),
            )

    ceiling = min(_clause_limit(ctx.hay, end), end + _WUI_TARGET_AFTER)
    if ceiling > end:
        ahead = _WUI_TARGET_RE.search(ctx.hay, end, ceiling)
        if ahead is not None and not ctx.is_consumed(ahead.start(), ahead.end()):
            return Selector(field=SelectorField.FIRE_IS_WUI, values=["true"]), (
                ahead.start(),
                ahead.end(),
            )
    return None


def _rule_max_response_time(ctx: _Ctx) -> list[AnyConstraint]:
    """Tetto al tempo di arrivo: "entro venti minuti", "nessuno oltre la mezz'ora".

    Il tetto si chiede in due modi, e la differenza fra i due e' il verso. Ci
    sono i marcatori che il tetto lo pongono da soli ("entro", "al massimo",
    "non piu' di", "in meno di") e ci sono i comparativi, che da soli dicono
    l'opposto: "oltre venti minuti" e' un tempo lungo, non un divieto.
    Diventano un tetto solo quando una negazione li governa, ed e' la forma
    piu' frequente in sala operativa: "nessuno oltre i quaranta minuti", "non
    voglio tempi sopra il quarto d'ora". Per questo sono due espressioni
    regolari e non una: il comparativo senza negazione non produce niente.

    Il verso va guardato in tutte e due le direzioni. "Almeno venti minuti" non
    e' un tetto e qui non deve entrare, e nemmeno "non meno di venti minuti",
    che e' il marcatore "meno di" rovesciato dalla negazione che lo precede.

    Perche' la regola pretende sempre una durata esplicita, cioe' un'unita'
    ("minuti", "ore") o una frazione d'ora detta a parole: una regola avida che
    trasformasse ogni numero seguito da "minuti" in un vincolo sarebbe peggiore
    del buco che chiude. Produrrebbe vincoli inventati, e un vincolo inventato
    non si vede: e' ben formato, passa lo schema, entra nel modello e cambia il
    piano. "Vi aggiorno fra un'ora" contiene una durata e non e' un ordine di
    impiego; e' il marcatore, non il numero, a dire che c'e' un tetto.

    Le ore del giorno restano affare della regola della finestra temporale, che
    gira prima e consuma il testo: "fino alle venti" e' un orario, "venti
    minuti" e' una durata, e la differenza sta nell'unita' che segue il numero.

    Su quali focolai valga il tetto si legge accanto all'ordine: un toponimo se
    c'e', altrimenti l'interfaccia urbano-foresta quando e' nominata come
    bersaglio ("sui focolai vicino alle case"). In quel caso il testo viene
    consumato, cosi' la regola della priorita' urbano-foresta non ne ricava
    anche un PRIORITIZE_ZONE: il coordinatore ha detto dove vale il tetto, non
    ha chiesto in piu' di dare precedenza a quelle zone.
    """
    out: list[AnyConstraint] = []
    found: list[tuple[int, int, int]] = []

    # I marcatori diretti valgono da soli, e cadono soltanto se una negazione
    # li rovescia in un pavimento ("non meno di venti minuti").
    for m in _MAX_TIME_DIRECT_RE.finditer(ctx.hay):
        if _is_floor_form(ctx.hay, m.start("marker"), m.group("marker")):
            continue
        minutes = parse_duration_minutes(m.group("dur"))
        if minutes is None:
            continue
        found.append((m.start(), m.end(), minutes))

    # I comparativi valgono solo al contrario: senza una negazione che li
    # governi nella stessa proposizione non pongono nessun tetto. Il vincolo
    # parte dalla negazione, cosi' il `source_span` mostra al coordinatore la
    # parola da cui il verso dipende.
    for m in _MAX_TIME_COMPARATIVE_RE.finditer(ctx.hay):
        negation = _governing_negation(ctx.hay, m.start("marker"))
        if negation is None:
            continue
        minutes = parse_duration_minutes(m.group("dur"))
        if minutes is None:
            continue
        found.append((negation, m.end(), minutes))

    for start, end, minutes in sorted(found):
        if ctx.is_consumed(start, end) or minutes <= 0:
            continue
        fire_selector, fire_span = _fire_selector_near(ctx, start, end)
        if fire_selector.field is SelectorField.ALL:
            # Un toponimo e' piu' specifico dell'interfaccia urbano-foresta e
            # vince: "almeno due mezzi su Cirella, entro venti minuti" parla
            # dei focolai di Cirella. Solo quando non ce n'e' nessuno si
            # guarda se il bersaglio e' detto in un altro modo.
            wui = _wui_fire_selector_near(ctx, start, end)
            if wui is not None:
                fire_selector, fire_span = wui
        lo = min(start, fire_span[0]) if fire_span else start
        hi = max(end, fire_span[1]) if fire_span else end
        ctx.consume(lo, hi)
        # Il valore che finisce nel vincolo e' quello riportato dentro
        # l'intervallo dello schema, e la motivazione deve citare quello, non
        # il numero detto: se l'ordine dice "entro venti ore" e il vincolo
        # applicato e' 600 minuti, un `rationale` che dice 1200 farebbe
        # credere al coordinatore di aver ottenuto cio' che ha chiesto.
        applied = _clamp(minutes, *RESPONSE_MINUTES_RANGE)
        if applied != minutes:
            ctx.warnings.append(
                f"'{ctx.span(start, end)}' chiede {minutes} minuti: fuori "
                f"dall'intervallo ammesso, ridotto a {applied}"
            )
        out.append(
            MaxResponseTime(
                constraint_id=ctx.new_id("MRT"),
                minutes=applied,
                fire_selector=fire_selector,
                enforcement=Enforcement.HARD,
                confidence=CONF_EXPLICIT,
                source_span=ctx.span(lo, hi),
                rationale=(
                    f"arrivo entro {applied} minuti sui focolai in "
                    f"interfaccia urbano-foresta"
                    if fire_selector.field is SelectorField.FIRE_IS_WUI
                    else f"arrivo sul focolaio entro {applied} minuti"
                ),
            )
        )
    return out


# 3. Numero minimo di mezzi su un focolaio -----------------------------------

_MIN_VEHICLES_RE = re.compile(
    rf"\b(?:almeno|minimo|non meno di)\s+({NUM})\s+({_VEHICLE_ALT})\b"
)


def _rule_min_vehicles(ctx: _Ctx) -> list[AnyConstraint]:
    """Presidio minimo: "almeno due mezzi su Cirella"."""
    out: list[AnyConstraint] = []
    for m in _MIN_VEHICLES_RE.finditer(ctx.hay):
        if ctx.is_consumed(m.start(), m.end()):
            continue
        count = parse_number(m.group(1))
        if count is None or count < 1:
            continue
        word = m.group(2)
        vehicle_selector = _selector_for_vehicle_word(word)
        if vehicle_selector is not None and vehicle_selector.field is SelectorField.ALL:
            vehicle_selector = None  # "almeno due mezzi": nessuna categoria

        tail = ctx.hay[m.end() : min(len(ctx.hay), m.end() + 70)]
        has_su = bool(re.match(r"\s*(?:su|sul|sulla|sull'|a|ad|per)\b", tail))
        fire_selector, fire_span = _fire_selector_near(ctx, m.start(), m.end())
        if not has_su and fire_selector.field is SelectorField.ALL:
            ctx.warnings.append(
                f"'{ctx.span(m.start(), m.end())}' non indica il focolaio: il "
                f"minimo e' stato applicato a tutti"
            )
        lo = min(m.start(), fire_span[0]) if fire_span else m.start()
        hi = max(m.end(), fire_span[1]) if fire_span else m.end()
        ctx.consume(lo, hi)
        applied = _clamp(count, *MIN_VEHICLES_RANGE)
        if applied != count:
            ctx.warnings.append(
                f"'{ctx.span(m.start(), m.end())}' chiede {count} mezzi: fuori "
                f"dall'intervallo ammesso, ridotto a {applied}"
            )
        out.append(
            MinVehiclesOnFire(
                constraint_id=ctx.new_id("MIN"),
                fire_selector=fire_selector,
                count=applied,
                vehicle_selector=vehicle_selector,
                enforcement=Enforcement.HARD,
                confidence=CONF_EXPLICIT if fire_span else CONF_INFERRED,
                source_span=ctx.span(lo, hi),
                rationale=f"almeno {applied} mezzi sul focolaio indicato",
            )
        )
    return out


# 4. Quote per tipo di mezzo -------------------------------------------------

_QUOTA_MAX_RE = re.compile(
    rf"\b(?:al massimo|non piu' di|non piu di|massimo|al piu')\s+({NUM})\s+"
    rf"({_VEHICLE_ALT})\b"
)
_QUOTA_MIN_RE = re.compile(
    rf"\b(?:impiegate|usate|tenete impegnati|servono)\s+almeno\s+({NUM})\s+"
    rf"({_VEHICLE_ALT})\b"
)


def _rule_quota(ctx: _Ctx) -> list[AnyConstraint]:
    """Tetto o minimo all'impiego di una categoria: "al massimo due canadair"."""
    out: list[AnyConstraint] = []
    for regex, is_max in ((_QUOTA_MAX_RE, True), (_QUOTA_MIN_RE, False)):
        for m in regex.finditer(ctx.hay):
            if ctx.is_consumed(m.start(), m.end()):
                continue
            n = parse_number(m.group(1))
            selector = _selector_for_vehicle_word(m.group(2))
            if n is None or selector is None:
                continue
            ctx.consume(m.start(), m.end())
            applied = _clamp(n, *QUOTA_RANGE)
            if applied != n:
                ctx.warnings.append(
                    f"'{ctx.span(m.start(), m.end())}' chiede {n} mezzi: fuori "
                    f"dall'intervallo ammesso, riportato a {applied}"
                )
            out.append(
                VehicleTypeQuota(
                    constraint_id=ctx.new_id("QUO"),
                    selector=selector,
                    max_used=applied if is_max else None,
                    min_used=None if is_max else applied,
                    enforcement=Enforcement.HARD,
                    confidence=CONF_EXPLICIT,
                    source_span=ctx.span(m.start(), m.end()),
                    rationale=(
                        f"al massimo {applied} mezzi di questa categoria"
                        if is_max
                        else f"almeno {applied} mezzi di questa categoria impiegati"
                    ),
                )
            )
    return out


# Quanto lontano dal verbo si accetta la preposizione che introduce il luogo.
# Quaranta caratteri sono circa sei parole: "mandate subito tutto quello che
# avete su Cirella" ci sta, una frase successiva no.
_TRIGGER_REACH = 40


def _trigger_then_place(
    ctx: _Ctx, trigger_re: re.Pattern, prep_re: re.Pattern, reach: int = _TRIGGER_REACH
) -> list[tuple[int, int, PlaceHit]]:
    """Coppie (inizio, fine, luogo) per "verbo ... preposizione + toponimo".

    Perche' due regex invece di una sola con il verbo e la preposizione
    attaccati: con una regex unica il motore si ferma alla PRIMA preposizione
    che incontra, e se li' non c'e' nessun toponimo l'occorrenza e' persa per
    sempre, perche' `finditer` riparte da dopo quella preposizione e il verbo
    resta indietro. Succedeva davvero: in "mandate le squadre a terra su
    Cirella" la preposizione trovata era la "a" di "a terra", dopo la quale
    non c'e' nessun luogo, e l'ordine di impiego spariva. Qui invece il verbo
    si tiene e si provano tutte le preposizioni della sua proposizione, fino a
    trovarne una seguita da un toponimo.

    La finestra non scavalca la punteggiatura (nemmeno la virgola, che qui
    separa davvero: "mandate i canadair, su Paola andate voi").
    """
    out: list[tuple[int, int, PlaceHit]] = []
    for trigger in trigger_re.finditer(ctx.hay):
        # `reach` misura la distanza dal verbo all'INIZIO della preposizione,
        # come faceva il quantificatore pigro della regex unica; l'estremo
        # passato a finditer e' piu' largo perche' limita la fine del match.
        limit = min(len(ctx.hay), trigger.end() + reach + 12)
        stop = _CLAUSE_END_RE.search(ctx.hay, trigger.end(), limit)
        if stop:
            limit = stop.start()
        for prep in prep_re.finditer(ctx.hay, trigger.end(), limit):
            if prep.start() - trigger.end() > reach:
                break
            after = prep.end()
            hits = [
                p
                for p in ctx.places(after, min(len(ctx.hay), after + 40))
                if p.start <= after + 2
            ]
            if hits:
                out.append((trigger.start(), after, hits[0]))
                break
    return out


# 5. Divieto di accoppiamento mezzo-focolaio ---------------------------------

_FORBID_TRIGGER_RE = re.compile(
    r"\b(?:niente|nessun|nessuna|non mandate|non mandare|non portate|"
    r"non portare|non usate|non impiegate|evitate)\b"
)
_FORBID_PREP_RE = re.compile(r"\b(?:su|sul|sulla|sull'|a|ad)\s+")


def _rule_forbid(ctx: _Ctx) -> list[AnyConstraint]:
    """Divieto mirato: "niente canadair su Cirella"."""
    out: list[AnyConstraint] = []
    for lo, hi, place in _trigger_then_place(
        ctx, _FORBID_TRIGGER_RE, _FORBID_PREP_RE
    ):
        if ctx.is_consumed(lo, hi):
            continue
        vehicle_selector, veh_span = _vehicle_selector_near(
            ctx, lo, hi, before=20, after=0
        )
        if vehicle_selector is None:
            continue
        start = min(lo, veh_span[0]) if veh_span else lo
        ctx.consume(start, place.end)
        out.append(
            ForbidAssignment(
                constraint_id=ctx.new_id("FBD"),
                vehicle_selector=vehicle_selector,
                fire_selector=_fire_selector_for(place),
                enforcement=Enforcement.HARD,
                confidence=CONF_EXPLICIT,
                source_span=ctx.span(start, place.end),
                rationale=f"quei mezzi non vanno impiegati su {place.canonical}",
            )
        )
    return out


# 6. Riserva -----------------------------------------------------------------

_RESERVE_RE = re.compile(
    r"\b(?:rest[ai]\s+(?:di|in)\s+riserva|riman(?:e|gono)\s+(?:di|in)\s+riserva|"
    r"tenet[ei](?:ne|lo|li|la)?\s+(?:[^.;,]{0,25}?\s)?(?:di|in)\s+riserva|"
    r"tenere\s+(?:[^.;,]{0,25}?\s)?(?:di|in)\s+riserva|"
    r"(?:di|in)\s+riserva|di scorta|di rincalzo|"
    r"tenet[ei](?:ne)?\s+(?:[^.;,]{0,20}?\s)?liber[oi])\b"
)
_COUNT_NEAR_RE = re.compile(rf"\b({NUM})\b")


def _rule_reserve(ctx: _Ctx) -> list[AnyConstraint]:
    """Mezzo tenuto fuori dal piano: "l'elicottero resta di riserva"."""
    out: list[AnyConstraint] = []
    for m in _RESERVE_RE.finditer(ctx.hay):
        if ctx.is_consumed(m.start(), m.end()):
            continue
        selector, veh_span = _vehicle_selector_near(ctx, m.start(), m.end())
        if selector is None:
            selector = Selector(field=SelectorField.ALL)
            confidence = CONF_INFERRED
            ctx.warnings.append(
                f"'{ctx.span(m.start(), m.end())}' non nomina il mezzo: la "
                f"riserva e' stata applicata a un mezzo qualsiasi"
            )
        else:
            confidence = CONF_EXPLICIT
        count = _count_near(ctx, m.start(), m.end())
        start = min(m.start(), veh_span[0]) if veh_span else m.start()
        end = max(m.end(), veh_span[1]) if veh_span else m.end()
        ctx.consume(start, end)
        out.append(
            ReserveVehicle(
                constraint_id=ctx.new_id("RES"),
                selector=selector,
                count=count,
                enforcement=Enforcement.HARD,
                confidence=confidence,
                source_span=ctx.span(start, end),
                rationale="mezzo tenuto fuori dal piano corrente",
            )
        )
    return out


def _count_near(ctx: _Ctx, start: int, end: int) -> Optional[int]:
    """Numero di mezzi citato accanto all'ordine, se c'e' ed e' plausibile.

    Due filtri, entrambi nati da falsi positivi veri:

    1. Non si esce dalla frase. Un numero che sta prima del punto appartiene a
       un altro ordine e non ha niente a che fare con questa riserva.
    2. Si saltano i numeri gia' consumati da una regola precedente. E' il caso
       di "tutti sul posto entro venti minuti, l'elicottero resta di riserva":
       la virgola non separa, ma il venti e' gia' stato preso dal tempo di
       risposta, e senza questo controllo diventava "venti elicotteri di
       riserva". Funziona perche' la regola della riserva gira dopo quella del
       tempo di risposta, e l'ordine delle regole e' dichiarato in `RULES`.
    """
    lo = max(_clause_start(ctx.hay, start), start - 45)
    hi = min(_clause_limit(ctx.hay, end), end + 20, len(ctx.hay))
    if hi <= lo:
        return None
    for m in _COUNT_NEAR_RE.finditer(ctx.hay, lo, hi):
        if ctx.is_consumed(m.start(), m.end()):
            continue
        n = parse_number(m.group(1))
        # Il tetto dello schema per ReserveVehicle.count e' 50: un numero piu'
        # grande non e' un conteggio di mezzi, e' un altro numero finito li'.
        if n is None or not RESERVE_COUNT_RANGE[0] <= n <= RESERVE_COUNT_RANGE[1]:
            continue
        return n
    return None


# 7. Esclusione --------------------------------------------------------------

# Nota sul terzo ramo, quello "nudo". "a terra" da solo segnala un mezzo
# fermo, ma "squadra a terra" e "squadre a terra" sono il NOME di una
# categoria di mezzi: senza i due lookbehind, "mandate le squadre a terra su
# Cirella" veniva letto come "le squadre sono ferme" e l'ordine si ribaltava.
# I lookbehind sono a larghezza fissa perche' il modulo `re` lo richiede.
_EXCLUDE_RE = re.compile(
    r"\b(?:non\s+(?:volano|vola|decollano|decolla|si alzano|escono|esce|"
    r"partono|parte|sono impiegabili|vanno impiegati|si usano)|"
    r"(?:sono|restano|rimangono|stanno|sono rimasti)\s+(?:a terra|fermi|ferme|"
    r"fermo|ferma|bloccati|bloccate)|"
    r"(?<!squadra )(?<!squadre )(?:a terra|fermi|ferme|fermo|ferma)|"
    r"in manutenzione|guast[oiae]|indisponibil[ei]|non disponibil[ei]|"
    r"esclud(?:ete|ere|i)|lasciate a terra|tenete a terra|"
    r"equipaggi(?:o)? a terra)\b"
)


def _rule_exclude(ctx: _Ctx) -> list[AnyConstraint]:
    """Mezzo non impiegabile: "i Canadair non volano", "gli elicotteri sono fermi"."""
    out: list[AnyConstraint] = []
    for m in _EXCLUDE_RE.finditer(ctx.hay):
        if ctx.is_consumed(m.start(), m.end()):
            continue
        selector, veh_span = _vehicle_selector_near(ctx, m.start(), m.end())
        if selector is None:
            # Senza un mezzo nominato non si sa che cosa escludere: meglio non
            # produrre nulla e lasciare che l'ordine risulti non coperto.
            continue
        start = min(m.start(), veh_span[0]) if veh_span else m.start()
        end = max(m.end(), veh_span[1]) if veh_span else m.end()
        ctx.consume(start, end)
        out.append(
            ExcludeVehicle(
                constraint_id=ctx.new_id("EXC"),
                selector=selector,
                enforcement=Enforcement.HARD,
                confidence=CONF_EXPLICIT,
                source_span=ctx.span(start, end),
                rationale="mezzo non impiegabile in questa finestra",
            )
        )
    return out


# 8. Interfaccia urbano-foresta ----------------------------------------------

_WUI_RE = re.compile(
    r"(?:"
    r"\bvicin[oaie]\s+(?:all[ea]\s+)?(?:case|abitazioni|abitato|paesi|"
    r"centri abitati)\b"
    r"|\bzone abitate\b|\bcentri? abitati?\b|\bcentro abitato\b"
    r"|\binterfaccia(?:\s+urbano[ -]foresta)?\b"
    r"|\ba ridosso (?:del|delle|dei)\s+(?:case|abitazioni|paese|paesi)\b"
    r"|\bdove ci sono le case\b"
    r"|\b(?:prima|priorita'?|precedenza|proteggete|proteggere|salvaguardate|"
    r"difendete|salvate|coprite)\b[^.;]{0,35}?"
    r"\b(?:case|abitazioni|abitati|abitato|frazioni)\b"
    r")"
)


def _rule_wui(ctx: _Ctx) -> list[AnyConstraint]:
    """Priorita' alle zone in interfaccia urbano-foresta.

    E' l'ordine che il coordinatore ripete piu' spesso, in una decina di forme
    diverse. Si emette una sola volta anche se compare piu' volte: due vincoli
    identici sullo stesso insieme non aggiungono informazione.
    """
    for m in _WUI_RE.finditer(ctx.hay):
        if ctx.is_consumed(m.start(), m.end()):
            continue
        ctx.consume(m.start(), m.end())
        return [
            PrioritizeZone(
                constraint_id=ctx.new_id("PZ"),
                zone_kind=ZoneKind.WUI,
                weight_multiplier=WUI_WEIGHT,
                # PRIORITIZE_ZONE cambia i pesi dell'obiettivo, non rende
                # infattibile niente: e' SOFT per costruzione.
                enforcement=Enforcement.SOFT,
                confidence=CONF_EXPLICIT,
                source_span=ctx.span(m.start(), m.end()),
                rationale="priorita' ai focolai in interfaccia urbano-foresta",
            )
        ]
    return []


# 9. Infrastrutture da proteggere -------------------------------------------

_INFRASTRUCTURE_WORDS = (
    r"ospedale|depuratore|centrale(?: elettrica| eolica)?|elettrodotto|"
    r"traliccio|tralicci|metanodotto|gasdotto|impianto|impianti|scuola|"
    r"campeggio|distributore|serbatoio|acquedotto|cabina|ripetitore|"
    r"casa di riposo|agriturismo|deposito|discarica|stazione"
)
_INFRASTRUCTURE_RE = re.compile(
    rf"\b(?:proteggete|proteggere|salvate|difendete|mettete in sicurezza|"
    rf"tutelate|coprite)\s+(?:il|lo|la|l'|i|gli|le)?\s*"
    rf"({_INFRASTRUCTURE_WORDS})([a-z' ]{{0,30}})?"
)


def _rule_protect_infrastructure(ctx: _Ctx) -> list[AnyConstraint]:
    """Difesa di un punto sensibile: "proteggete il depuratore di Cirella"."""
    out: list[AnyConstraint] = []
    for m in _INFRASTRUCTURE_RE.finditer(ctx.hay):
        if ctx.is_consumed(m.start(), m.end()):
            continue
        name = ctx.span(m.start(1), m.end())[
            :MAX_INFRASTRUCTURE_NAME_LENGTH
        ].strip(" .,;'")
        if not name:
            continue
        ctx.consume(m.start(), m.end())
        out.append(
            ProtectInfrastructure(
                constraint_id=ctx.new_id("INF"),
                name=name,
                # Senza coordinate il vincolo resta dichiarativo: chi lo applica
                # deve geolocalizzare il punto. Il raggio e' quello di default
                # dello schema, 5 km, taratura non misurata.
                buffer_km=5.0,
                weight_multiplier=3.0,
                enforcement=Enforcement.SOFT,
                confidence=CONF_INFERRED,
                source_span=ctx.span(m.start(), m.end()),
                rationale=f"difesa prioritaria di {name}",
            )
        )
        ctx.warnings.append(
            f"'{name}' non ha coordinate: il vincolo va geolocalizzato prima "
            f"di poter essere applicato"
        )
    return out


# 10. Concentrazione delle forze: ordine forte o preferenza ------------------

_FORCE_TRIGGER_RE = re.compile(
    r"\b(?:tutto|tutti|tutte le forze|ogni mezzo|mandate tutto|mandate tutti|"
    r"dirottate|spostate|portate|mandate|schierate)\b"
)
_FORCE_PREP_RE = re.compile(r"\b(?:su|sul|sulla|sull'|a|ad|verso)\s+")

_FOCUS_TRIGGER_RE = re.compile(
    r"\b(?:concentratevi|concentrate(?: gli sforzi| le forze)?|"
    r"date precedenza|priorita'?|precedenza|guardate|occhio|attenzione|"
    r"tenete d'occhio|non perdete di vista)\b"
)
_FOCUS_PREP_RE = re.compile(
    r"\b(?:su|sul|sulla|sull'|a|ad|al|alla|ai|alle|verso)\s+"
)
# La preferenza ammette meno distanza fra il verbo e il luogo rispetto
# all'ordine forte: "priorita'" e "occhio" sono parole frequenti e una
# finestra larga le farebbe agganciare a toponimi che non c'entrano.
_FOCUS_REACH = 30


def _rule_force(ctx: _Ctx) -> list[AnyConstraint]:
    """Ordine forte: "mandate i canadair su Cirella" diventa un vincolo duro."""
    out: list[AnyConstraint] = []
    for lo, hi, place in _trigger_then_place(ctx, _FORCE_TRIGGER_RE, _FORCE_PREP_RE):
        if ctx.is_consumed(lo, hi):
            continue
        vehicle_selector, veh_span = _vehicle_selector_near(
            ctx, lo, hi, before=25, after=0
        )
        if vehicle_selector is None:
            vehicle_selector = Selector(field=SelectorField.ALL)
        start = min(lo, veh_span[0]) if veh_span else lo
        ctx.consume(start, place.end)
        out.append(
            ForceAssignment(
                constraint_id=ctx.new_id("FRC"),
                vehicle_selector=vehicle_selector,
                fire_selector=_fire_selector_for(place),
                min_count=1,
                enforcement=Enforcement.HARD,
                confidence=CONF_EXPLICIT,
                source_span=ctx.span(start, place.end),
                rationale=f"impiego imposto su {place.canonical}",
            )
        )
    return out


def _rule_focus(ctx: _Ctx) -> list[AnyConstraint]:
    """Preferenza: "concentratevi su Cirella" alza il peso, non impone nulla.

    La distinzione fra questa regola e la precedente e' la forza dell'ordine,
    ed e' presente nella lingua: "mandate i canadair su X" e' un ordine,
    "concentratevi su X" e' un'indicazione di priorita'. Tradurre la seconda
    come vincolo duro renderebbe infattibili piani perfettamente ragionevoli.
    """
    out: list[AnyConstraint] = []
    for lo, hi, place in _trigger_then_place(
        ctx, _FOCUS_TRIGGER_RE, _FOCUS_PREP_RE, reach=_FOCUS_REACH
    ):
        if ctx.is_consumed(lo, hi):
            continue
        ctx.consume(lo, place.end)
        out.append(
            PrioritizeZone(
                constraint_id=ctx.new_id("PZ"),
                zone_kind=_zone_kind_for(place),
                zone_values=[place.canonical],
                weight_multiplier=ZONE_WEIGHT,
                enforcement=Enforcement.SOFT,
                confidence=CONF_EXPLICIT,
                source_span=ctx.span(lo, place.end),
                rationale=f"priorita' ai focolai su {place.canonical}",
            )
        )
    return out


# 11. Pesi dell'obiettivo ----------------------------------------------------

_OBJECTIVE_RULES: list[tuple[re.Pattern, ObjectiveTerm, float, str]] = [
    (
        re.compile(
            r"\b(?:conta piu'? arrivare presto|l'importante e'? arrivare presto|"
            r"arrivare (?:presto|subito) (?:e'? )?la priorita'?|"
            r"velocita'? prima di tutto|conta la rapidita'?)\b"
        ),
        ObjectiveTerm.RESPONSE_TIME,
        2.0,
        "arrivare presto conta piu' del resto",
    ),
    (
        re.compile(
            r"\b(?:risparmiat[ei](?: ore di volo| carburante)?|"
            r"risparmiare(?: ore di volo| carburante)?|contenete i costi|"
            r"contenete le ore di volo|occhio ai costi|costa troppo|"
            r"riducete le ore di volo)\b"
        ),
        ObjectiveTerm.COST,
        3.0,
        "contenimento del costo di impiego",
    ),
    (
        re.compile(
            r"\b(?:copriamo tutto|copertura prima di tutto|"
            r"nessun focolaio scoperto|non lasciate niente scoperto)\b"
        ),
        ObjectiveTerm.COVERAGE,
        2.0,
        "massimizzare il numero di focolai presidiati",
    ),
]


def _rule_objective_weight(ctx: _Ctx) -> list[AnyConstraint]:
    """Ripesatura di un termine dell'obiettivo.

    Il peso emesso e' il default di `config.OBJECTIVE_WEIGHTS` moltiplicato per
    un fattore: cosi' la scala resta quella del solver e l'ordine sposta il
    baricentro invece di riscriverlo.
    """
    out: list[AnyConstraint] = []
    for regex, term, factor, why in _OBJECTIVE_RULES:
        m = regex.search(ctx.hay)
        if not m or ctx.is_consumed(m.start(), m.end()):
            continue
        base = config.OBJECTIVE_WEIGHTS.get(term.value, 1.0)
        ctx.consume(m.start(), m.end())
        out.append(
            ObjectiveWeight(
                constraint_id=ctx.new_id("OBJ"),
                term=term,
                # I pesi di partenza vengono da config, che e' di un altro
                # proprietario: si riportano dentro l'intervallo 0..100 dello
                # schema invece di fidarsi. Una taratura cambiata la' non deve
                # poter far fallire la traduzione di un ordine qui.
                weight=_clamp(round(base * factor, 3), *OBJECTIVE_WEIGHT_RANGE),
                enforcement=Enforcement.SOFT,
                confidence=CONF_INFERRED,
                source_span=ctx.span(m.start(), m.end()),
                rationale=why,
            )
        )
    return out


# 12. Toponimi residui -------------------------------------------------------


def _word_window(text: str, start: int, end: int, left: int = 22, right: int = 12) -> tuple[int, int]:
    """Allarga uno span a parole intere, per un `source_span` leggibile.

    Mostrare "Nel reggino ci sono t" al coordinatore non aiuta nessuno: lo
    span si allarga e poi si riallinea al confine di parola piu' vicino.
    """
    a = max(0, start - left)
    if a > 0:
        space = text.find(" ", a)
        a = space + 1 if 0 <= space < start else a
    b = min(len(text), end + right)
    if b < len(text):
        space = text.rfind(" ", end, b)
        b = space if space > end else b
    return a, b


def _rule_places(ctx: _Ctx) -> list[AnyConstraint]:
    """Un toponimo nominato e non ancora usato alza la priorita' di quella zona.

    E' la regola piu' debole e sta per ultima di proposito: se il posto e' gia'
    finito dentro un vincolo piu' specifico (un divieto, un impiego imposto, un
    tempo di risposta) la sua posizione e' stata consumata e qui non si vede.
    """
    out: list[AnyConstraint] = []
    seen: set[tuple[str, str]] = set()
    for place in ctx.places():
        if ctx.is_consumed(place.start, place.end):
            continue
        key = (place.kind.value, place.canonical)
        if key in seen:
            continue
        seen.add(key)
        ctx.consume(place.start, place.end)
        out.append(
            PrioritizeZone(
                constraint_id=ctx.new_id("PZ"),
                zone_kind=_zone_kind_for(place),
                zone_values=[place.canonical],
                weight_multiplier=ZONE_WEIGHT,
                enforcement=Enforcement.SOFT,
                confidence=CONF_INFERRED,
                source_span=ctx.span(*_word_window(ctx.raw, place.start, place.end)),
                rationale=f"zona nominata nell'ordine: {place.canonical}",
            )
        )
    return out


# 13. Richieste fuori repertorio --------------------------------------------

# Cose che il coordinatore chiede davvero e che il solver non sa rappresentare.
# Riconoscerle esplicitamente vale piu' che ignorarle: l'operatore vede che
# quella parte dell'ordine non e' stata applicata, e il benchmark puo' misurare
# quanto spesso il traduttore si astiene quando deve.
_OUT_OF_REPERTOIRE: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"\bevacu\w+"),
        "l'evacuazione della popolazione non e' un vincolo di impiego dei mezzi",
    ),
    (
        # Le due persone dell'imperativo, non solo quella di cortesia. Il
        # coordinatore che detta da' del tu tanto quanto del voi ("avvisa i
        # sindaci", "chiama la prefettura"): riconoscere solo "avvisate" faceva
        # cadere la meta' degli ordini di comunicazione nell'astensione
        # generica, che dice all'operatore "non ho capito" invece di "questo
        # non e' un vincolo di impiego dei mezzi". Sono due messaggi diversi e
        # il secondo e' quello vero.
        re.compile(
            r"\b(?:avvis(?:ate|are|ami|a|i)|inform(?:ate|are|a)|"
            r"chiam(?:ate|are|a)|contatt(?:ate|are|a)|"
            r"allert(?:ate|are|a)|avvert(?:ite|ire|i))\b"
        ),
        "avvisi e comunicazioni verso altri enti non sono rappresentabili come vincolo",
    ),
    (
        re.compile(r"\bchiud\w+\s+(?:la\s+)?(?:strada|statale|provinciale)|viabilita'?"),
        "la chiusura della viabilita' non e' un vincolo di impiego dei mezzi",
    ),
    (
        re.compile(r"\b(?:previsioni|meteo|se il vento|quando cala il vento)\b"),
        "le condizioni meteo sono dati dello scenario, non vincoli imponibili",
    ),
    (
        re.compile(r"\b(?:decidete voi|fate voi|vedete voi|come vi pare)\b"),
        "l'ordine delega la decisione al sistema: non contiene nessun vincolo",
    ),
    (
        re.compile(r"\b(?:quanti mezzi servono|stimate|calcolate quanto)\b"),
        "la stima del fabbisogno e' compito del modello, non un vincolo da imporre",
    ),
    (
        re.compile(r"\b(?:rinforzi|flotta nazionale|mezzi da fuori regione)\b"),
        "i mezzi fuori dalla flotta dello scenario non sono rappresentabili",
    ),
]


def _rule_out_of_repertoire(ctx: _Ctx) -> list[AnyConstraint]:
    """Emette UNSUPPORTED per le parti dell'ordine fuori dal repertorio.

    Consuma l'intera proposizione, non solo la parola che l'ha fatta scattare.
    "Avvisate il sindaco di Paola" nomina Paola, ma non sta chiedendo di dare
    priorita' a Paola: senza questa consumazione la regola dei toponimi, che
    viene dopo, inventerebbe un vincolo che nell'ordine non c'e'.

    Si scorrono TUTTE le occorrenze di ogni motivo, non solo la prima. Sul
    vincolo emesso non cambierebbe niente (due astensioni con lo stesso motivo
    si fondono nella deduplicazione finale), ma cambia moltissimo su cio' che
    resta da consumare: con la sola prima occorrenza, "Avvisate il sindaco di
    Paola. Avvisate anche il prefetto di Scilla" lasciava scoperta la seconda
    proposizione e la regola dei toponimi ne ricavava una priorita' su Scilla.
    Un ordine di sole comunicazioni produceva cosi' un vincolo operativo, che
    e' precisamente l'allucinazione che questa regola esiste per impedire.
    """
    out: list[AnyConstraint] = []
    for regex, reason in _OUT_OF_REPERTOIRE:
        for m in regex.finditer(ctx.hay):
            if ctx.is_consumed(m.start(), m.end()):
                continue
            end = max(m.end(), _clause_end(ctx.hay, m.end()))
            ctx.consume(m.start(), end)
            out.append(
                Unsupported(
                    constraint_id=ctx.new_id("UNS"),
                    reason=reason,
                    confidence=CONF_EXPLICIT,
                    source_span=ctx.span(m.start(), end),
                    # Il motivo va scritto anche qui, e non e' una ripetizione
                    # inutile: `ConstraintSet.summary_lines` e tutto cio' che
                    # gli sta dietro (tabella della CLI, risposta della API)
                    # mostrano `rationale`, che e' il campo comune a ogni
                    # vincolo, e non `reason`, che esiste solo su UNSUPPORTED.
                    # Con una frase generica li' dentro l'astensione era il
                    # solo vincolo che non sapeva dire perche' esisteva,
                    # proprio quello per cui la spiegazione conta di piu'.
                    rationale=reason,
                )
            )
    return out


# L'ordine e' significativo: dalle regole piu' specifiche alle piu' generiche.
#
# Il presidio minimo apre l'elenco perche' e' la regola che legge piu' cose
# insieme (un numero, una categoria di mezzi e un focolaio) ed e' quindi la
# piu' specifica di tutte. Farla girare dopo la finestra oraria costava un
# vincolo intero: in "almeno due mezzi su Cirella fino alle venti" la finestra
# oraria cerca il mezzo fino a ottanta caratteri indietro, trovava il "mezzi"
# del presidio e si portava via l'intera frase, presidio compreso.
RULES: list[Rule] = [
    _rule_min_vehicles,
    _rule_time_window,
    _rule_max_response_time,
    _rule_quota,
    _rule_forbid,
    _rule_reserve,
    _rule_exclude,
    _rule_wui,
    _rule_protect_infrastructure,
    _rule_force,
    _rule_focus,
    _rule_objective_weight,
    # L'astensione precede la regola dei toponimi: una proposizione fuori
    # repertorio si prende il suo pezzo di testo prima che qualcuno ne ricavi
    # un vincolo che il coordinatore non ha chiesto.
    _rule_out_of_repertoire,
    _rule_places,
]


# --------------------------------------------------------------------------
# Funzione pubblica
# --------------------------------------------------------------------------


def translate(
    order_text: str,
    scenario: Optional[Scenario] = None,
    constraint_set_id: Optional[str] = None,
) -> ConstraintSet:
    """Traduce un ordine in italiano in un ConstraintSet, senza modelli.

    Lo `scenario` e' opzionale e serve solo a risolvere le indicazioni che
    dipendono dai dati ("l'elicottero di Cosenza"). Senza di esso la traduzione
    resta corretta, solo meno specifica, e l'imprecisione viene annotata nei
    warning invece di essere nascosta.
    """
    started = time.perf_counter()
    raw = (order_text or "").strip()
    ctx = _Ctx(raw=raw, hay=flatten(raw), scenario=scenario)

    constraints: list[AnyConstraint] = []
    if raw:
        for rule in RULES:
            constraints.extend(rule(ctx))
        constraints = _deduplicate(constraints)

    if not constraints:
        constraints.append(
            Unsupported(
                constraint_id=ctx.new_id("UNS"),
                reason=(
                    "nessun vincolo del repertorio riconosciuto nell'ordine"
                    if raw
                    else "ordine vuoto"
                ),
                confidence=CONF_EXPLICIT,
                source_span=raw[:MAX_SPAN_LENGTH],
                rationale="astensione: meglio nessun vincolo di uno inventato",
            )
        )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return ConstraintSet(
        constraint_set_id=clean_set_id(constraint_set_id, _default_id(raw)),
        source_text=raw[:MAX_SOURCE_TEXT_LENGTH],
        constraints=constraints,
        translator=TRANSLATOR_NAME,
        model_name="",
        # `perf_counter` e' monotono: non torna indietro se l'orologio di
        # sistema viene corretto durante la traduzione, quindi translation_ms
        # non puo' risultare negativo, cosa che lo schema vieta.
        translation_ms=round(elapsed_ms, 3),
        warnings=_unique_warnings(ctx.warnings),
    )


def clean_set_id(value: Optional[str], fallback: str) -> str:
    """Identificativo utilizzabile a partire da quello chiesto dal chiamante.

    Lo schema impone un identificativo fra 1 e 64 caratteri. Il chiamante puo'
    passarne uno vuoto o troppo lungo (arriva da una riga di comando, da una
    richiesta HTTP, da un file del benchmark), e in quel caso `translate`
    sollevava una ValidationError: il contratto pero' dice che `translate`
    restituisce sempre un ConstraintSet, perche' a valle c'e' una sala
    operativa, non un test. Qui si riporta il valore dentro i limiti invece di
    far fallire l'intera traduzione.
    """
    if value is None:
        return fallback
    cleaned = value.strip()[:MAX_SET_ID_LENGTH]
    return cleaned or fallback


def _unique_warnings(warnings: list[str]) -> list[str]:
    """Avvisi distinti, nell'ordine in cui sono stati prodotti.

    Lo stesso avviso puo' nascere piu' volte (un ordine che nomina due volte
    un mezzo senza scenario). Ripeterlo non aggiunge informazione e allunga
    quello che il coordinatore deve leggere prima di lanciare il solver.
    """
    out: list[str] = []
    seen: set[str] = set()
    for w in warnings:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


# Campi che descrivono la provenienza del vincolo, non il suo contenuto: due
# vincoli che differiscono solo per questi sono lo stesso vincolo.
_PROVENANCE_FIELDS = {"constraint_id", "source_span", "rationale", "confidence"}


def _deduplicate(constraints: list[AnyConstraint]) -> list[AnyConstraint]:
    """Toglie i vincoli semanticamente identici, tenendo il primo.

    Un ordine reale ripete lo stesso concetto in due modi ("gli elicotteri sono
    fermi, equipaggio a terra") e piu' regole lo riconoscono. Due vincoli
    identici non aggiungono informazione al solver e gonfiano le metriche del
    benchmark, dove ogni vincolo in piu' conta come una previsione in piu'.
    """
    out: list[AnyConstraint] = []
    seen: set[str] = set()
    for c in constraints:
        payload = c.model_dump(mode="json")
        for field_name in _PROVENANCE_FIELDS:
            payload.pop(field_name, None)
        key = repr(sorted(payload.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _default_id(text: str) -> str:
    """Identificativo stabile e ripetibile, derivato dal testo dell'ordine.

    Deterministico di proposito: due esecuzioni sullo stesso ordine devono
    produrre lo stesso identificativo, altrimenti i confronti del benchmark
    diventano rumorosi.
    """
    import hashlib

    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"CS_RULES_{digest}"
