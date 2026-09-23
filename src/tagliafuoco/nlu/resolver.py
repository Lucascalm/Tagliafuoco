"""Risoluzione dei selettori contro uno scenario concreto.

Un `Selector` e' una descrizione per attributo ("gli elicotteri", "i focolai in
provincia di Cosenza"), non un elenco di matricole. Lo si conserva in quella
forma perche' resti valido da una finestra di pianificazione all'altra, ma a un
certo punto qualcuno deve trasformarlo nelle entita' vere dello scenario. Quel
qualcuno e' questo modulo.

Perche' vive da solo e non dentro `optimize/`: lo usano sia il solver, per
sapere su quali mezzi mettere i vincoli, sia il validatore, per verificare a
posteriori che il piano li rispetti. Se ognuno dei due risolvesse i selettori a
modo suo, una divergenza fra le due interpretazioni passerebbe inosservata e il
validatore direbbe "tutto a posto" su un piano che viola l'ordine ricevuto.

Due regole di progetto qui dentro:

1. Funzioni pure. Niente stato globale, niente cache, niente mutazioni dello
   scenario. Chiamare due volte la stessa funzione sugli stessi dati deve dare
   lo stesso risultato, nello stesso ordine.
2. Non si solleva mai un'eccezione perche' un selettore non trova nulla. Un
   ordine che nomina un mezzo assente dallo scenario e' un caso normale, non un
   errore di programmazione: si restituisce una lista vuota e lo si segnala
   nella `Resolution`, cosi' chi chiama puo' mostrarlo al coordinatore.

Il confronto dei nomi e' volutamente tollerante. In sala operativa nessuno
scrive "Reggio Calabria" per esteso ne' mette gli accenti: si dice "Reggio",
"reggio", "REGGIO". Un matching rigido qui non sarebbe rigore, sarebbe solo un
traduttore che non traduce.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional, Sequence

from tagliafuoco.domain.models import (
    Domain,
    FireCluster,
    Scenario,
    Severity,
    Vehicle,
    VehicleKind,
)
from tagliafuoco.nlu.schema import Selector, SelectorField


# --------------------------------------------------------------------------
# Normalizzazione dei nomi
# --------------------------------------------------------------------------

# Parole che in un toponimo italiano non portano informazione distintiva.
# "Spezzano della Sila" e "Spezzano Sila" sono lo stesso posto.
_FILLER_WORDS = frozenset(
    {
        "di", "del", "della", "dello", "dei", "degli", "delle",
        "da", "dal", "dalla", "d",
        "il", "lo", "la", "i", "gli", "le", "l",
        "in", "sul", "sulla", "su", "a", "al", "ai", "alla", "alle",
    }
)

# Parole generiche delle aree protette: "Parco Nazionale della Sila" e "Sila"
# devono corrispondere, altrimenti nessun ordine reale viene mai riconosciuto.
_PROTECTED_GENERIC_WORDS = frozenset(
    {
        "parco", "parchi", "nazionale", "regionale", "naturale", "naturalistica",
        "riserva", "riserve", "oasi", "area", "aree", "protetta", "protette",
    }
)


def normalize(text: str) -> str:
    """Forma canonica di un nome: niente accenti, niente maiuscole, niente punteggiatura.

    Non preserva le posizioni dei caratteri, quindi non va usata dove servono
    gli offset nel testo originale: il traduttore a regole ha per questo una
    sua versione che invece li preserva.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    only_words = re.sub(r"[^a-z0-9]+", " ", without_accents.lower())
    return " ".join(only_words.split())


def _tokens(text: str, extra_filler: frozenset = frozenset()) -> list[str]:
    """Token significativi di un nome, senza articoli e preposizioni.

    Si lavora a parole intere e non a sottostringhe perche' la sottostringa
    qui e' pericolosa: "Castro" e' dentro "Castrovillari" ma sono due comuni
    diversi, e un vincolo attribuito al comune sbagliato e' un vincolo
    inventato che passa lo schema senza che nessuno se ne accorga. Le parole
    di servizio si tolgono invece perche' chi detta le salta: "Spezzano
    Sila" e "Spezzano della Sila" sono lo stesso posto.
    """
    return [
        t
        for t in normalize(text).split()
        if t not in _FILLER_WORDS and t not in extra_filler
    ]


def name_matches(query: str, candidate: str) -> bool:
    """Confronto tollerante fra un nome detto a voce e un nome nei dati.

    Regola: si confrontano le sequenze di token significativi e si accetta
    anche il prefisso. Cosi' "Reggio" trova "Reggio Calabria" e "Reggio
    Calabria" trova "Reggio". Il confronto e' per token interi, non per
    sottostringa: "Castro" NON trova "Castrovillari", ed e' giusto cosi',
    sono due comuni diversi.
    """
    nq, nc = normalize(query), normalize(candidate)
    if not nq or not nc:
        return False
    if nq == nc:
        return True
    tq, tc = _tokens(query), _tokens(candidate)
    if not tq or not tc:
        return False
    if tq == tc:
        return True
    shorter, longer = (tq, tc) if len(tq) <= len(tc) else (tc, tq)
    return longer[: len(shorter)] == shorter


def place_name_matches(query: str, candidate: str) -> bool:
    """Come `name_matches` ma per le aree protette.

    Qui si tolgono anche le parole generiche ("parco", "nazionale", "riserva")
    e si accetta l'inclusione invece del solo prefisso, perche' il coordinatore
    dice "il Pollino" e il dato dice "Parco Nazionale del Pollino".

    Il caso delicato e' la query che, tolte le parole generiche, non lascia
    niente: "il parco", "la riserva". Li' non c'e' nessun nome distintivo da
    confrontare, e l'unico confronto onesto e' l'uguaglianza letterale. Il
    prefisso di `name_matches` qui non va usato, perche' quasi tutti i nomi
    veri cominciano proprio con la parola generica: "Parco" aggancerebbe il
    Pollino, la Sila e l'Aspromonte insieme. Un selettore che aggancia tutti i
    parchi e' un vincolo piu' largo di quello ricevuto, ed e' l'errore peggiore
    di un traduttore: passa lo schema, entra nel modello e cambia il piano
    senza che nessuno lo veda.
    """
    tq = _tokens(query, _PROTECTED_GENERIC_WORDS)
    tc = _tokens(candidate, _PROTECTED_GENERIC_WORDS)
    if not tq or not tc:
        return normalize(query) == normalize(candidate)
    return set(tq) <= set(tc) or set(tc) <= set(tq)


# --------------------------------------------------------------------------
# Sinonimi di tipo, dominio, gravita'
# --------------------------------------------------------------------------

_HELICOPTERS = (
    VehicleKind.ELICOTTERO_PESANTE,
    VehicleKind.ELICOTTERO_MEDIO,
    VehicleKind.ELICOTTERO_LEGGERO,
)

# Le chiavi sono gia' normalizzate. "elicottero" da solo vale per tutte e tre
# le classi: il coordinatore quasi mai specifica il peso del velivolo.
_VEHICLE_KIND_ALIASES: dict[str, tuple[VehicleKind, ...]] = {
    "canadair": (VehicleKind.CANADAIR,),
    "aereo": (VehicleKind.CANADAIR,),
    "aerei": (VehicleKind.CANADAIR,),
    "velivolo": (VehicleKind.CANADAIR,),
    "velivoli": (VehicleKind.CANADAIR,),
    "ala fissa": (VehicleKind.CANADAIR,),
    "elicottero": _HELICOPTERS,
    "elicotteri": _HELICOPTERS,
    "eli": _HELICOPTERS,
    "elicottero pesante": (VehicleKind.ELICOTTERO_PESANTE,),
    "elicotteri pesanti": (VehicleKind.ELICOTTERO_PESANTE,),
    "elicottero medio": (VehicleKind.ELICOTTERO_MEDIO,),
    "elicotteri medi": (VehicleKind.ELICOTTERO_MEDIO,),
    "elicottero leggero": (VehicleKind.ELICOTTERO_LEGGERO,),
    "elicotteri leggeri": (VehicleKind.ELICOTTERO_LEGGERO,),
    "autobotte": (VehicleKind.AUTOBOTTE,),
    "autobotti": (VehicleKind.AUTOBOTTE,),
    "modulo aib": (VehicleKind.MODULO_AIB,),
    "moduli aib": (VehicleKind.MODULO_AIB,),
    "modulo": (VehicleKind.MODULO_AIB,),
    "moduli": (VehicleKind.MODULO_AIB,),
    "squadra": (VehicleKind.SQUADRA_TERRA,),
    "squadre": (VehicleKind.SQUADRA_TERRA,),
    "squadra terra": (VehicleKind.SQUADRA_TERRA,),
    "squadre terra": (VehicleKind.SQUADRA_TERRA,),
    # Le forme con la preposizione sono quelle che si dicono davvero, e sono
    # anche quelle che il traduttore a regole riconosce nel testo. Senza queste
    # chiavi "mandate le squadre a terra su Cirella" restava senza selettore,
    # perche' `normalize` non toglie le preposizioni: toglierle qui
    # renderebbe "squadre a terra" indistinguibile da "squadre di volontari".
    "squadra a terra": (VehicleKind.SQUADRA_TERRA,),
    "squadre a terra": (VehicleKind.SQUADRA_TERRA,),
    "squadra di terra": (VehicleKind.SQUADRA_TERRA,),
    "squadre di terra": (VehicleKind.SQUADRA_TERRA,),
}

_DOMAIN_ALIASES: dict[str, Domain] = {
    "air": Domain.AIR,
    "aereo": Domain.AIR,
    "aerei": Domain.AIR,
    "aerea": Domain.AIR,
    "aeree": Domain.AIR,
    "mezzi aerei": Domain.AIR,
    "volo": Domain.AIR,
    "ground": Domain.GROUND,
    "terra": Domain.GROUND,
    "terrestre": Domain.GROUND,
    "terrestri": Domain.GROUND,
    "mezzi terrestri": Domain.GROUND,
    "suolo": Domain.GROUND,
}

_SEVERITY_ALIASES: dict[str, Severity] = {
    "low": Severity.LOW,
    "basso": Severity.LOW,
    "bassa": Severity.LOW,
    "moderate": Severity.MODERATE,
    "moderato": Severity.MODERATE,
    "moderata": Severity.MODERATE,
    "medio": Severity.MODERATE,
    "media": Severity.MODERATE,
    "high": Severity.HIGH,
    "alto": Severity.HIGH,
    "alta": Severity.HIGH,
    "grave": Severity.HIGH,
    "extreme": Severity.EXTREME,
    "estremo": Severity.EXTREME,
    "estrema": Severity.EXTREME,
    "gravissimo": Severity.EXTREME,
}

_TRUE_WORDS = frozenset({"true", "1", "si", "s", "yes", "vero", "wui", "y"})
_FALSE_WORDS = frozenset({"false", "0", "no", "n", "falso"})


def vehicle_kinds_for(value: str) -> tuple[VehicleKind, ...]:
    """Traduce un valore testuale in una o piu' classi di mezzo.

    Tupla vuota se il valore non e' riconducibile a nessuna classe: chi chiama
    decide se e' un avviso o un errore.
    """
    key = normalize(value)
    if key in _VEHICLE_KIND_ALIASES:
        return _VEHICLE_KIND_ALIASES[key]
    for kind in VehicleKind:
        if normalize(kind.value) == key:
            return (kind,)
    return ()


def domain_for(value: str) -> Optional[Domain]:
    """Traduce un valore testuale in un dominio di movimento."""
    key = normalize(value)
    if key in _DOMAIN_ALIASES:
        return _DOMAIN_ALIASES[key]
    for dom in Domain:
        if normalize(dom.value) == key:
            return dom
    return None


def severity_for(value: str) -> Optional[Severity]:
    """Traduce un valore testuale in una gravita'."""
    return _SEVERITY_ALIASES.get(normalize(value))


def boolean_for(value: str) -> Optional[bool]:
    """Traduce un valore testuale in un booleano, in italiano o in inglese."""
    key = normalize(value)
    if key in _TRUE_WORDS:
        return True
    if key in _FALSE_WORDS:
        return False
    return None


# --------------------------------------------------------------------------
# Esito della risoluzione
# --------------------------------------------------------------------------


@dataclass
class Resolution:
    """Esito di una risoluzione: cosa e' stato trovato e cosa no.

    Esiste perche' "nessun mezzo corrisponde" e "il selettore e' sbagliato"
    sono due cose diverse, e chi mostra il risultato al coordinatore deve
    poterle distinguere. `unmatched_values` elenca i valori del selettore che
    non hanno agganciato niente nello scenario.

    Attenzione a `items`: contiene le istanze vere dello scenario, non delle
    copie. E' voluto, perche' chi risolve un selettore vuole proprio quei
    mezzi e quei focolai, ma significa che modificarli modifica lo scenario.
    Chi ha bisogno di annotarli se ne fa una copia.
    """

    items: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unmatched_values: list[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return bool(self.items)

    @property
    def ids(self) -> list[str]:
        """Identificativi delle entita' risolte, mezzi o focolai che siano.

        I due attributi si provano in sequenza e non con un `or`: una matricola
        vuota e' un dato sbagliato, non un invito a leggere il campo del lato
        opposto.
        """
        out: list[str] = []
        for it in self.items:
            ident = getattr(it, "vehicle_id", None)
            if ident is None:
                ident = getattr(it, "fire_id", None)
            if ident:
                out.append(str(ident))
        return out

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)


# --------------------------------------------------------------------------
# Campi ammessi per lato
# --------------------------------------------------------------------------

VEHICLE_FIELDS = frozenset(
    {
        SelectorField.VEHICLE_ID,
        SelectorField.VEHICLE_KIND,
        SelectorField.VEHICLE_DOMAIN,
        SelectorField.BASE_ID,
        SelectorField.ALL,
    }
)

FIRE_FIELDS = frozenset(
    {
        SelectorField.FIRE_ID,
        SelectorField.FIRE_COMUNE,
        SelectorField.FIRE_PROVINCIA,
        SelectorField.FIRE_SEVERITY,
        SelectorField.FIRE_IS_WUI,
        SelectorField.FIRE_IN_PROTECTED_AREA,
        SelectorField.ALL,
    }
)


def _unique(values: Sequence[str]) -> list[str]:
    """Valori distinti, nell'ordine in cui compaiono.

    Un selettore puo' arrivare con lo stesso valore due volte (il modello lo
    ripete, oppure due regole diverse lo hanno aggiunto). Senza questo passo
    un valore che non aggancia nulla comparirebbe due volte in
    `unmatched_values` e l'avviso mostrato al coordinatore direbbe due volte
    la stessa cosa. Non si usa `set` perche' l'ordine deve restare quello
    dell'ordine ricevuto: il determinismo vale anche per la diagnostica.
    """
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _warn_if_values_ignored(selector: Selector, res: Resolution, side: str) -> None:
    """Avvisa quando un selettore `all` porta dei valori che nessuno leggera'.

    Lo schema ammette `field=all` con `values` valorizzato: la regola di
    `Selector` impone i valori solo quando il campo NON e' `all`, non li vieta
    quando lo e'. Un traduttore neurale puo' quindi emettere
    `{"field": "all", "values": ["canadair"]}` intendendo "tutti i canadair" e
    ottenere invece "tutti i mezzi", cioe' un vincolo piu' largo di quello
    ricevuto. Qui non si cambia la semantica, che e' quella dichiarata nello
    schema: si dice al coordinatore che quei valori sono caduti, perche' e'
    l'unico modo che ha di accorgersene prima che il piano sia gia' fatto.
    """
    if selector.values:
        res.warnings.append(
            f"il selettore 'all' porta anche dei valori {list(selector.values)}: "
            f"sono stati ignorati e il vincolo vale per tutti i {side}"
        )


def selector_targets_vehicles(selector: Selector) -> bool:
    """Vero se il selettore ha senso applicato ai mezzi."""
    return selector.field in VEHICLE_FIELDS


def selector_targets_fires(selector: Selector) -> bool:
    """Vero se il selettore ha senso applicato ai focolai."""
    return selector.field in FIRE_FIELDS


# --------------------------------------------------------------------------
# Risoluzione dei mezzi
# --------------------------------------------------------------------------


def resolve_vehicles_detailed(selector: Selector, scenario: Scenario) -> Resolution:
    """Risolve un selettore sui mezzi dello scenario, con diagnostica.

    L'ordine dei mezzi restituiti e' quello dello scenario, mai quello dei
    valori del selettore: il determinismo conta, perche' il confronto con la
    baseline deve essere ripetibile e non puo' dipendere da come il
    coordinatore ha scritto l'ordine.
    """
    res = Resolution()
    vehicles = list(scenario.vehicles)

    if not selector_targets_vehicles(selector):
        res.warnings.append(
            f"il selettore sul campo '{selector.field.value}' non seleziona "
            f"mezzi: nessun mezzo risolto"
        )
        return res

    if selector.field is SelectorField.ALL:
        res.items = vehicles
        _warn_if_values_ignored(selector, res, "mezzi")
        if not vehicles:
            res.warnings.append("lo scenario non contiene nessun mezzo")
        return res

    matched_ids: set[str] = set()
    for value in _unique(selector.values):
        found = _match_vehicles_for_value(selector.field, value, scenario, res)
        if not found:
            res.unmatched_values.append(value)
        matched_ids.update(v.vehicle_id for v in found)

    # Si riordina secondo lo scenario invece di seguire l'ordine dei valori
    # del selettore: il risultato non deve dipendere da come e' scritto l'ordine.
    res.items = [v for v in vehicles if v.vehicle_id in matched_ids]
    if res.unmatched_values:
        res.warnings.append(
            f"nessun mezzo corrisponde a {res.unmatched_values} sul campo "
            f"'{selector.field.value}'"
        )
        # Distinzione che conta per chi legge la diagnostica: "il canadair non
        # c'e'" e "non c'e' nessun mezzo" hanno cause diverse e rimedi diversi.
        if not vehicles:
            res.warnings.append("lo scenario non contiene nessun mezzo")
    return res


def _match_vehicles_for_value(
    field_: SelectorField,
    value: str,
    scenario: Scenario,
    res: Resolution,
) -> list[Vehicle]:
    """Mezzi che corrispondono a un singolo valore del selettore.

    Una funzione sola per tutti i campi, e non un metodo per campo, perche'
    l'insieme dei campi e' chiuso e dichiarato in `schema.py`: un ramo
    mancante qui e' un campo che nessuno risolve, e si vede subito leggendo
    l'elenco. I valori non riconosciuti non sollevano: aggiungono un avviso e
    restituiscono l'insieme vuoto, cosi' il vincolo semplicemente non si
    applica invece di far cadere la pianificazione.
    """
    vehicles = scenario.vehicles

    if field_ is SelectorField.VEHICLE_ID:
        # Due chiavi, e la differenza fra le due e' voluta. Sulla MATRICOLA si
        # accetta solo la differenza di maiuscole: e' un identificativo, e due
        # mezzi possono chiamarsi CAN_01 e CAN_02, quindi approssimare qui
        # vorrebbe dire prendere il mezzo sbagliato. Sul NOME si accetta invece
        # la tolleranza di `name_matches`, perche' il nome e' quello che il
        # coordinatore pronuncia ("il Canadair zero uno") e nessuno lo detta
        # come sta scritto nei dati.
        key = normalize(value)
        return [
            v
            for v in vehicles
            if normalize(v.vehicle_id) == key or name_matches(value, v.name)
        ]

    if field_ is SelectorField.VEHICLE_KIND:
        kinds = vehicle_kinds_for(value)
        if not kinds:
            res.warnings.append(f"classe di mezzo non riconosciuta: '{value}'")
            return []
        return [v for v in vehicles if v.kind in kinds]

    if field_ is SelectorField.VEHICLE_DOMAIN:
        dom = domain_for(value)
        if dom is None:
            res.warnings.append(f"dominio non riconosciuto: '{value}'")
            return []
        return [v for v in vehicles if v.domain is dom]

    if field_ is SelectorField.BASE_ID:
        key = normalize(value)
        exact = [v for v in vehicles if normalize(v.home_base_id) == key]
        if exact:
            return exact
        # Non e' un identificativo: allora e' il nome della base o la sua
        # provincia ("l'elicottero di Cosenza").
        base_ids = set(base_ids_for_place(value, scenario))
        return [v for v in vehicles if v.home_base_id in base_ids]

    return []


def resolve_vehicles(selector: Selector, scenario: Scenario) -> list[Vehicle]:
    """Mezzi dello scenario che il selettore descrive. Lista vuota se nessuno.

    Versione semplice, per chi vuole solo l'elenco: il solver, che con un
    insieme vuoto semplicemente non mette il vincolo. Chi invece deve spiegare
    al coordinatore *perche'* l'insieme e' vuoto usa
    `resolve_vehicles_detailed`, che riporta anche gli avvisi.

    Sono le istanze dello scenario, non copie: non vanno modificate.
    """
    return resolve_vehicles_detailed(selector, scenario).items


# --------------------------------------------------------------------------
# Risoluzione dei focolai
# --------------------------------------------------------------------------


def resolve_fires_detailed(selector: Selector, scenario: Scenario) -> Resolution:
    """Risolve un selettore sui focolai dello scenario, con diagnostica.

    Gemella di `resolve_vehicles_detailed` e con le stesse due garanzie, che
    qui contano anche di piu': l'ordine dei focolai e' quello dello scenario e
    non quello dei valori del selettore, perche' i focolai finiscono nelle
    metriche e un ordine diverso cambierebbe quale risulta "il primo servito";
    e un selettore che non aggancia niente da' lista vuota e avviso, mai
    un'eccezione, perche' a inizio giornata non avere focolai e' la normalita'.
    """
    res = Resolution()
    fires = list(scenario.fires)

    if not selector_targets_fires(selector):
        res.warnings.append(
            f"il selettore sul campo '{selector.field.value}' non seleziona "
            f"focolai: nessun focolaio risolto"
        )
        return res

    if selector.field is SelectorField.ALL:
        res.items = fires
        _warn_if_values_ignored(selector, res, "focolai")
        if not fires:
            res.warnings.append("lo scenario non contiene nessun focolaio")
        return res

    matched_ids: set[str] = set()
    for value in _unique(selector.values):
        found = _match_fires_for_value(selector.field, value, fires, res)
        if not found:
            res.unmatched_values.append(value)
        matched_ids.update(f.fire_id for f in found)

    res.items = [f for f in fires if f.fire_id in matched_ids]
    if res.unmatched_values:
        res.warnings.append(
            f"nessun focolaio corrisponde a {res.unmatched_values} sul campo "
            f"'{selector.field.value}'"
        )
        if not fires:
            res.warnings.append("lo scenario non contiene nessun focolaio")
    return res


def _match_fires_for_value(
    field_: SelectorField,
    value: str,
    fires: Sequence[FireCluster],
    res: Resolution,
) -> list[FireCluster]:
    """Focolai che corrispondono a un singolo valore del selettore."""
    if field_ is SelectorField.FIRE_ID:
        key = normalize(value)
        return [f for f in fires if normalize(f.fire_id) == key]

    if field_ is SelectorField.FIRE_COMUNE:
        # Si guarda anche la frazione piu' vicina: il coordinatore nomina
        # spesso una localita' ("Gallina") e non il comune ("Reggio Calabria").
        return [
            f
            for f in fires
            if (f.comune and name_matches(value, f.comune))
            or (f.nearest_settlement and name_matches(value, f.nearest_settlement))
        ]

    if field_ is SelectorField.FIRE_PROVINCIA:
        return [f for f in fires if f.provincia and name_matches(value, f.provincia)]

    if field_ is SelectorField.FIRE_SEVERITY:
        sev = severity_for(value)
        if sev is None:
            res.warnings.append(f"gravita' non riconosciuta: '{value}'")
            return []
        return [f for f in fires if f.severity is sev]

    if field_ is SelectorField.FIRE_IS_WUI:
        flag = boolean_for(value)
        if flag is None:
            res.warnings.append(f"valore booleano non riconosciuto: '{value}'")
            return []
        return [f for f in fires if f.is_wui is flag]

    if field_ is SelectorField.FIRE_IN_PROTECTED_AREA:
        flag = boolean_for(value)
        if flag is not None:
            return [f for f in fires if f.in_protected_area is flag]
        # Non e' un booleano: allora e' il nome di un parco.
        return [
            f
            for f in fires
            if f.in_protected_area
            and f.protected_area_name
            and place_name_matches(value, f.protected_area_name)
        ]

    return []


def resolve_fires(selector: Selector, scenario: Scenario) -> list[FireCluster]:
    """Focolai dello scenario che il selettore descrive. Lista vuota se nessuno.

    Come per i mezzi: sono le istanze dello scenario, non copie.
    """
    return resolve_fires_detailed(selector, scenario).items


# --------------------------------------------------------------------------
# Comodita' per chi costruisce i selettori e per la diagnostica
# --------------------------------------------------------------------------


def base_ids_for_place(place: str, scenario: Scenario) -> list[str]:
    """Basi che stanno nel posto indicato (nome della base o sua provincia).

    Serve a tradurre "l'elicottero di Cosenza": il coordinatore indica il mezzo
    attraverso la base dove sta di casa, non attraverso la matricola. Si prova
    prima il nome della base, che e' piu' specifico, e solo se non aggancia
    nulla si ripiega sulla provincia.
    """
    by_name = [b.base_id for b in scenario.bases if name_matches(place, b.name)]
    if by_name:
        return by_name
    return [b.base_id for b in scenario.bases if name_matches(place, b.provincia)]


def describe_resolution(res: Resolution) -> str:
    """Riga leggibile per il coordinatore: cosa e' stato risolto e cosa no."""
    if res.matched:
        return f"{len(res.items)} entita' risolte: {', '.join(res.ids)}"
    if res.warnings:
        return "nessuna entita' risolta: " + "; ".join(res.warnings)
    return "nessuna entita' risolta"
