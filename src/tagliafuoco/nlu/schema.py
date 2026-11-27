"""Schema dei vincoli: il confine fra il linguaggio naturale e il solver.

Questo file e' il punto di controllo dell'intero progetto. Il modello
linguistico non produce assegnazioni, non produce numeri liberi e non
produce codice: produce soltanto istanze delle classi definite qui. Ogni
campo e' tipizzato, ogni enumerazione e' chiusa, ogni valore numerico ha un
intervallo dichiarato. Quello che non entra in questo schema non entra nel
sistema.

Il motivo e' la verificabilita': un modello matematico generato a ogni ordine
non si puo' collaudare una volta per tutte, e un vincolo dimenticato non
produce un errore ma un piano troppo buono. La
conseguenza di progetto e' che il modello matematico deve essere scritto a
mano una volta sola, e al modello linguistico resta un compito piu' stretto:
riconoscere quale vincolo, fra quelli gia' implementati, il coordinatore sta
chiedendo, e con quali parametri.

Il tipo `UNSUPPORTED` non e' un ripiego: e' la risposta corretta quando
l'ordine esce dal repertorio. Un traduttore che non sa astenersi e' peggiore
di uno che copre meno casi, perche' produce vincoli plausibili e falsi.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# --------------------------------------------------------------------------
# Enumerazioni di supporto
# --------------------------------------------------------------------------


class ConstraintKind(str, Enum):
    """Repertorio chiuso dei vincoli che il solver sa applicare.

    Aggiungere un valore qui significa: (1) implementarlo in optimize/,
    (2) implementarne la verifica in validate/, (3) aggiungere casi al
    benchmark in benchmarks/. Nessun valore puo' esistere senza le tre cose.
    """

    PRIORITIZE_ZONE = "PRIORITIZE_ZONE"
    RESERVE_VEHICLE = "RESERVE_VEHICLE"
    EXCLUDE_VEHICLE = "EXCLUDE_VEHICLE"
    FORCE_ASSIGNMENT = "FORCE_ASSIGNMENT"
    FORBID_ASSIGNMENT = "FORBID_ASSIGNMENT"
    MAX_RESPONSE_TIME = "MAX_RESPONSE_TIME"
    MIN_VEHICLES_ON_FIRE = "MIN_VEHICLES_ON_FIRE"
    VEHICLE_TYPE_QUOTA = "VEHICLE_TYPE_QUOTA"
    TIME_WINDOW = "TIME_WINDOW"
    OBJECTIVE_WEIGHT = "OBJECTIVE_WEIGHT"
    PROTECT_INFRASTRUCTURE = "PROTECT_INFRASTRUCTURE"
    UNSUPPORTED = "UNSUPPORTED"


class Enforcement(str, Enum):
    """Un vincolo duro rende infattibile il piano che lo viola.

    Un vincolo morbido entra nella funzione obiettivo con una penalita'. La
    distinzione va fatta dal traduttore perche' e' presente nel linguaggio:
    "l'elicottero resta di riserva" e' un ordine, "se possibile tieni un
    mezzo libero" e' una preferenza.
    """

    HARD = "hard"
    SOFT = "soft"


class ZoneKind(str, Enum):
    WUI = "wui"
    COMUNE = "comune"
    PROVINCIA = "provincia"
    PROTECTED_AREA = "protected_area"
    LAND_COVER = "land_cover"
    RADIUS = "radius"


class ObjectiveTerm(str, Enum):
    """I termini della funzione obiettivo che il coordinatore puo' ripesare."""

    COVERAGE = "coverage"
    RESPONSE_TIME = "response_time"
    WUI_PROTECTION = "wui_protection"
    WATER_FULFILMENT = "water_fulfilment"
    COST = "cost"


class SelectorField(str, Enum):
    """Su quale attributo si seleziona un insieme di mezzi o di focolai."""

    VEHICLE_ID = "vehicle_id"
    VEHICLE_KIND = "vehicle_kind"
    VEHICLE_DOMAIN = "vehicle_domain"
    BASE_ID = "base_id"
    FIRE_ID = "fire_id"
    FIRE_COMUNE = "fire_comune"
    FIRE_PROVINCIA = "fire_provincia"
    FIRE_SEVERITY = "fire_severity"
    FIRE_IS_WUI = "fire_is_wui"
    FIRE_IN_PROTECTED_AREA = "fire_in_protected_area"
    ALL = "all"


# --------------------------------------------------------------------------
# Selettori
# --------------------------------------------------------------------------


class Selector(BaseModel):
    """Insieme di entita' descritto per attributo, mai per enumerazione.

    Il coordinatore dice "gli elicotteri", non l'elenco delle matricole. Il
    selettore conserva quella forma: viene risolto contro lo scenario al
    momento della pianificazione, quindi resta valido anche se la flotta
    disponibile cambia fra una finestra e l'altra.
    """

    field: SelectorField
    values: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _values_required_unless_all(self) -> "Selector":
        if self.field is not SelectorField.ALL and not self.values:
            raise ValueError(
                f"il selettore su '{self.field.value}' richiede almeno un valore"
            )
        return self

    def describe(self) -> str:
        if self.field is SelectorField.ALL:
            return "tutti"
        return f"{self.field.value} in {self.values}"


# --------------------------------------------------------------------------
# Vincoli
# --------------------------------------------------------------------------


class BaseConstraint(BaseModel):
    """Campi comuni a ogni vincolo.

    `source_span` e' il frammento letterale dell'ordine da cui il vincolo e'
    stato tratto. Serve a due cose: mostrarlo al coordinatore perche' possa
    smentirlo prima di lanciare il solver, e permettere al benchmark di
    distinguere un vincolo corretto da uno inventato.
    """

    constraint_id: str = Field(min_length=1, max_length=64)
    enforcement: Enforcement = Enforcement.HARD
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_span: str = Field(default="", max_length=400)
    rationale: str = Field(default="", max_length=400)

    model_config = {"extra": "forbid"}


class PrioritizeZone(BaseConstraint):
    """Alza il peso dei focolai che ricadono in una zona.

    Traduce ordini come "prima le zone vicino alle case" o "guardate al
    Pollino". Non forza nessuna assegnazione: cambia solo i pesi
    nell'obiettivo, quindi e' sempre `SOFT` per costruzione.
    """

    kind: Literal[ConstraintKind.PRIORITIZE_ZONE] = ConstraintKind.PRIORITIZE_ZONE
    zone_kind: ZoneKind
    zone_values: list[str] = Field(default_factory=list)
    center_lat: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    center_lon: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    radius_km: Optional[float] = Field(default=None, gt=0.0, le=300.0)
    weight_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)

    @model_validator(mode="after")
    def _radius_needs_center(self) -> "PrioritizeZone":
        if self.zone_kind is ZoneKind.RADIUS:
            if self.center_lat is None or self.center_lon is None or self.radius_km is None:
                raise ValueError(
                    "zone_kind=radius richiede center_lat, center_lon e radius_km"
                )
        elif self.zone_kind is not ZoneKind.WUI and not self.zone_values:
            raise ValueError(f"zone_kind={self.zone_kind.value} richiede zone_values")
        return self


class ReserveVehicle(BaseConstraint):
    """Toglie un mezzo dalla disponibilita' del piano corrente.

    Traduce "l'elicottero resta di riserva". Il mezzo non sparisce: resta
    nello scenario con stato RESERVE, cosi' la demo puo' mostrarlo fermo a
    terra e il validatore puo' verificare che nessuna assegnazione lo usi.
    """

    kind: Literal[ConstraintKind.RESERVE_VEHICLE] = ConstraintKind.RESERVE_VEHICLE
    selector: Selector
    count: Optional[int] = Field(default=None, ge=1, le=50)
    until_minute: Optional[int] = Field(default=None, ge=0, le=1440)


class ExcludeVehicle(BaseConstraint):
    """Rende un mezzo non impiegabile (guasto, manutenzione, equipaggio a terra)."""

    kind: Literal[ConstraintKind.EXCLUDE_VEHICLE] = ConstraintKind.EXCLUDE_VEHICLE
    selector: Selector


class ForceAssignment(BaseConstraint):
    """Impone che un insieme di mezzi intervenga su un focolaio indicato."""

    kind: Literal[ConstraintKind.FORCE_ASSIGNMENT] = ConstraintKind.FORCE_ASSIGNMENT
    vehicle_selector: Selector
    fire_selector: Selector
    min_count: int = Field(default=1, ge=1, le=50)


class ForbidAssignment(BaseConstraint):
    """Vieta un accoppiamento mezzo-focolaio.

    Serve per ordini del tipo "i Canadair non sull'incendio di Cirella":
    a volte per ragioni di sicurezza (linee elettriche, presenza di persone),
    a volte perche' il coordinatore ha informazioni che il sistema non ha.
    """

    kind: Literal[ConstraintKind.FORBID_ASSIGNMENT] = ConstraintKind.FORBID_ASSIGNMENT
    vehicle_selector: Selector
    fire_selector: Selector


class MaxResponseTime(BaseConstraint):
    """Impone un tetto al tempo di arrivo sul focolaio."""

    kind: Literal[ConstraintKind.MAX_RESPONSE_TIME] = ConstraintKind.MAX_RESPONSE_TIME
    minutes: int = Field(ge=1, le=600)
    fire_selector: Selector = Field(default_factory=lambda: Selector(field=SelectorField.ALL))


class MinVehiclesOnFire(BaseConstraint):
    """Impone un numero minimo di mezzi su un focolaio."""

    kind: Literal[ConstraintKind.MIN_VEHICLES_ON_FIRE] = ConstraintKind.MIN_VEHICLES_ON_FIRE
    fire_selector: Selector
    count: int = Field(ge=1, le=20)
    vehicle_selector: Optional[Selector] = None


class VehicleTypeQuota(BaseConstraint):
    """Fissa quanti mezzi di un tipo possono o devono essere impiegati."""

    kind: Literal[ConstraintKind.VEHICLE_TYPE_QUOTA] = ConstraintKind.VEHICLE_TYPE_QUOTA
    selector: Selector
    min_used: Optional[int] = Field(default=None, ge=0, le=100)
    max_used: Optional[int] = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _at_least_one_bound(self) -> "VehicleTypeQuota":
        if self.min_used is None and self.max_used is None:
            raise ValueError("serve almeno uno fra min_used e max_used")
        if (
            self.min_used is not None
            and self.max_used is not None
            and self.min_used > self.max_used
        ):
            raise ValueError("min_used non puo' superare max_used")
        return self


class TimeWindow(BaseConstraint):
    """Finestra temporale entro cui un insieme di mezzi puo' operare.

    Copre il caso ricorrente del volo diurno: gli aerei antincendio non
    lavorano al tramonto, e il coordinatore lo dice con frasi come "gli aerei
    fino alle otto".
    """

    kind: Literal[ConstraintKind.TIME_WINDOW] = ConstraintKind.TIME_WINDOW
    selector: Selector
    from_minute: int = Field(ge=0, le=1440)
    to_minute: int = Field(ge=0, le=1440)

    @model_validator(mode="after")
    def _ordered(self) -> "TimeWindow":
        if self.from_minute >= self.to_minute:
            raise ValueError("from_minute deve precedere to_minute")
        return self


class ObjectiveWeight(BaseConstraint):
    """Ripesa un termine della funzione obiettivo.

    Traduce ordini sulle priorita' generali: "conta piu' arrivare presto che
    risparmiare ore di volo". Sempre `SOFT`: cambiare i pesi non rende nulla
    infattibile.
    """

    kind: Literal[ConstraintKind.OBJECTIVE_WEIGHT] = ConstraintKind.OBJECTIVE_WEIGHT
    term: ObjectiveTerm
    weight: float = Field(ge=0.0, le=100.0)


class ProtectInfrastructure(BaseConstraint):
    """Dichiara prioritaria la difesa di un punto sensibile.

    Un punto sensibile non e' un focolaio: e' un impianto, un ospedale, una
    strada, un tralicciato. Il vincolo alza il peso dei focolai entro un
    raggio dal punto.
    """

    kind: Literal[ConstraintKind.PROTECT_INFRASTRUCTURE] = (
        ConstraintKind.PROTECT_INFRASTRUCTURE
    )
    name: str = Field(min_length=1, max_length=120)
    lat: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    lon: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    buffer_km: float = Field(default=5.0, gt=0.0, le=50.0)
    weight_multiplier: float = Field(default=3.0, ge=1.0, le=10.0)


class Unsupported(BaseConstraint):
    """L'ordine e' stato compreso ma non e' rappresentabile.

    Va emesso ogni volta che il coordinatore chiede qualcosa che il repertorio
    non copre. Tenerlo esplicito consente due cose: mostrare al coordinatore
    che quella parte dell'ordine non e' stata applicata, e misurare nel
    benchmark quanto spesso il traduttore si astiene quando deve.
    """

    kind: Literal[ConstraintKind.UNSUPPORTED] = ConstraintKind.UNSUPPORTED
    reason: str = Field(min_length=1, max_length=400)
    enforcement: Enforcement = Enforcement.SOFT


AnyConstraint = Annotated[
    Union[
        PrioritizeZone,
        ReserveVehicle,
        ExcludeVehicle,
        ForceAssignment,
        ForbidAssignment,
        MaxResponseTime,
        MinVehiclesOnFire,
        VehicleTypeQuota,
        TimeWindow,
        ObjectiveWeight,
        ProtectInfrastructure,
        Unsupported,
    ],
    Field(discriminator="kind"),
]


# --------------------------------------------------------------------------
# Insieme di vincoli
# --------------------------------------------------------------------------


class ConstraintSet(BaseModel):
    """Il risultato completo di una traduzione.

    E' l'unico oggetto che attraversa il confine fra il modello linguistico e
    il resto del sistema. Se questo oggetto valida, tutto cio' che sta a valle
    lavora su dati tipizzati; se non valida, la traduzione viene rifiutata e
    si ricade sul traduttore deterministico.
    """

    constraint_set_id: str = Field(min_length=1, max_length=64)
    source_text: str = Field(default="", max_length=4000)
    constraints: list[AnyConstraint] = Field(default_factory=list)
    translator: str = Field(default="llm")
    model_name: str = Field(default="")
    translation_ms: float = Field(default=0.0, ge=0.0)
    warnings: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @field_validator("constraints")
    @classmethod
    def _unique_ids(cls, v: list) -> list:
        ids = [c.constraint_id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError("i constraint_id devono essere distinti")
        return v

    @property
    def hard(self) -> list:
        return [c for c in self.constraints if c.enforcement is Enforcement.HARD]

    @property
    def soft(self) -> list:
        return [c for c in self.constraints if c.enforcement is Enforcement.SOFT]

    @property
    def unsupported(self) -> list:
        return [c for c in self.constraints if c.kind is ConstraintKind.UNSUPPORTED]

    def of_kind(self, kind: ConstraintKind) -> list:
        return [c for c in self.constraints if c.kind is kind]

    def is_empty(self) -> bool:
        return not self.constraints

    def summary_lines(self) -> list[str]:
        """Riassunto leggibile, per mostrarlo al coordinatore prima del solver."""
        out = []
        for c in self.constraints:
            tag = "duro" if c.enforcement is Enforcement.HARD else "morbido"
            out.append(f"[{c.kind.value}] ({tag}) {c.rationale or c.source_span}")
        return out
