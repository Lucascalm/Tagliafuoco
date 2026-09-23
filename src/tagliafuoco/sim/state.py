"""Stato del mondo fra una finestra di rigiocata e la successiva.

PERCHE' QUESTO MODULO ESISTE
Una rigiocata non e' una sequenza di problemi indipendenti. Un Canadair che alle
undici parte per la Sila alle due non e' ancora rientrato, ha bruciato due ore
di turno e si trova dove l'ha lasciato la missione precedente. Se ogni finestra
ripartisse da una flotta intatta, la rigiocata misurerebbe un mondo in cui i
mezzi si clonano, e i numeri finali sarebbero gonfiati per entrambi i
pianificatori.

Qui vive l'unica cosa che attraversa le finestre: posizione dei mezzi, minuto da
cui tornano liberi, minuti gia' spesi nel turno, focolai ancora attivi e
focolai considerati spenti.

PERCHE' LO STATO NON SI MODIFICA SUL POSTO
Il difetto piu' insidioso di un simulatore e' lo stato che sanguina: due
rigiocate diverse, lanciate nello stesso processo, che si scambiano un residuo e
producono risultati leggermente diversi a ogni esecuzione. Il confronto fra
solver e baseline si regge sul fatto che partano dallo stesso identico stato,
quindi qui `advance` non tocca mai l'oggetto su cui e' chiamata: restituisce uno
stato nuovo. Lo stato iniziale puo' cosi' essere riusato da entrambe le
rigiocate senza copiarlo a mano e senza ricordarsi di farlo.

`registry.load_fleet()` segue la stessa regola e per la stessa ragione:
restituisce sempre istanze fresche.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Iterable, Optional, Sequence

from tagliafuoco.domain.models import (
    Base,
    FireCluster,
    GeoPoint,
    Plan,
    Scenario,
    Vehicle,
    VehicleStatus,
)
from tagliafuoco.sim.timeline import as_utc

# Riattrezzamento usato quando la base di casa del mezzo non e' nello scenario.
# E' il valore di default che `Base.turnaround_minutes` dichiara in
# domain/models.py, ripetuto qui perche' questo modulo non ha modo di leggerlo
# senza istanziare una base finta. Non si sceglie zero: zero significherebbe
# "il mezzo riparte nell'istante in cui atterra", cioe' inventare una
# prestazione che non esiste.
DEFAULT_TURNAROUND_MINUTES = 10.0


@dataclass(frozen=True)
class VehicleState:
    """Fotografia di un mezzo all'inizio di una finestra.

    I minuti sono relativi all'inizio della finestra corrente, come le
    assegnazioni del piano: `available_from_minute = 40` vuol dire che il mezzo
    e' impegnato per i primi quaranta minuti di questa finestra.
    """

    vehicle_id: str
    position: Optional[GeoPoint] = None
    available_from_minute: float = 0.0
    minutes_flown_today: float = 0.0
    status: VehicleStatus = VehicleStatus.AVAILABLE

    def to_dict(self) -> dict:
        return {
            "vehicle_id": self.vehicle_id,
            "lat": self.position.lat if self.position else None,
            "lon": self.position.lon if self.position else None,
            "available_from_minute": round(self.available_from_minute, 3),
            "minutes_flown_today": round(self.minutes_flown_today, 3),
            "status": self.status.value,
        }


@dataclass
class WorldState:
    """Lo stato che passa da una finestra all'altra.

    Le collezioni sono di proprieta' esclusiva dell'istanza: `advance` ne
    costruisce di nuove invece di modificarle, cosi' lo stato iniziale di una
    rigiocata resta utilizzabile per la rigiocata successiva.
    """

    timestamp: datetime
    vehicles: dict[str, VehicleState] = field(default_factory=dict)
    active_fire_ids: set[str] = field(default_factory=set)
    extinguished_fire_ids: set[str] = field(default_factory=set)
    shift_day: Optional[date] = None

    def __post_init__(self) -> None:
        # Senza giornata di turno dichiarata si prende quella dell'istante: se
        # restasse None, ogni avanzamento sembrerebbe un cambio di giornata e i
        # minuti di turno si azzererebbero a ogni finestra, cioe' il limite di
        # turno non varrebbe mai.
        #
        # La giornata e' quella UTC, non quella locale. E' una semplificazione
        # dichiarata e va detta perche' il modulo accanto fa una scelta diversa:
        # `timeline.is_night_at` sposta l'orario di +60 minuti per leggere alba e
        # tramonto in ora locale. Qui no, quindi il turno dell'equipaggio riparte
        # all'una di notte locale invece che a mezzanotte. In piena stagione a
        # quell'ora non vola nessuno, per cui il confine cade dove non succede
        # niente; allinearlo cambierebbe i numeri di una rigiocata senza
        # migliorare nulla, e il confronto solver-baseline non ne risente perche'
        # la regola e' la stessa per tutti e due.
        self.timestamp = as_utc(self.timestamp)
        if self.shift_day is None:
            self.shift_day = self.timestamp.date()

    # ------------------------------------------------------------------
    # Costruzione
    # ------------------------------------------------------------------

    @classmethod
    def initial(
        cls,
        timestamp: datetime,
        vehicles: Iterable[Vehicle],
        bases: Sequence[Base] = (),
    ) -> "WorldState":
        """Stato all'inizio della stagione: tutti in base, turno intatto."""
        t = as_utc(timestamp)
        by_base = {b.base_id: b.point for b in bases}
        states: dict[str, VehicleState] = {}
        for v in sorted(vehicles, key=lambda x: x.vehicle_id):
            states[v.vehicle_id] = VehicleState(
                vehicle_id=v.vehicle_id,
                position=v.current_position or by_base.get(v.home_base_id),
                available_from_minute=0.0,
                minutes_flown_today=0.0,
                status=v.status,
            )
        return cls(timestamp=t, vehicles=states, shift_day=t.date())

    def copy(self) -> "WorldState":
        """Copia indipendente. `VehicleState` e' immutabile, basta un dizionario nuovo."""
        return WorldState(
            timestamp=self.timestamp,
            vehicles=dict(self.vehicles),
            active_fire_ids=set(self.active_fire_ids),
            extinguished_fire_ids=set(self.extinguished_fire_ids),
            shift_day=self.shift_day,
        )

    # ------------------------------------------------------------------
    # Lettura
    # ------------------------------------------------------------------

    def state_for(self, vehicle_id: str) -> Optional[VehicleState]:
        return self.vehicles.get(vehicle_id)

    def busy_vehicle_ids(self) -> list[str]:
        """Mezzi non liberi al minuto zero della finestra corrente."""
        return sorted(
            vid for vid, st in self.vehicles.items() if st.available_from_minute > 0.0
        )

    # ------------------------------------------------------------------
    # Innesto nello scenario
    # ------------------------------------------------------------------

    def apply_to(self, scenario: Scenario) -> None:
        """Scrive lo stato dentro i mezzi dello scenario.

        Lo scenario deve possedere i propri `Vehicle` (istanze fresche da
        `registry.load_fleet()` o copie): questo metodo li modifica, ed e'
        corretto solo se nessun altro scenario condivide quegli oggetti.
        """
        for v in scenario.vehicles:
            st = self.vehicles.get(v.vehicle_id)
            if st is None:
                continue
            v.current_position = st.position
            v.available_from_minute = max(0.0, float(st.available_from_minute))
            v.minutes_flown_today = float(st.minutes_flown_today)
            v.status = st.status

    # ------------------------------------------------------------------
    # Avanzamento del tempo
    # ------------------------------------------------------------------

    def advance(
        self,
        plan: Plan,
        scenario: Scenario,
        *,
        window_minutes: float,
        next_timestamp: datetime,
    ) -> "WorldState":
        """Applica il piano e restituisce lo stato iniziale della finestra dopo.

        Non modifica `self`: la rigiocata successiva deve poter ripartire dallo
        stesso stato e ottenere lo stesso risultato.

        Tre cose cambiano. (1) I mezzi impiegati spendono minuti di turno e si
        ritrovano alla base di rientro. (2) I minuti si riportano all'origine
        della finestra nuova sottraendo la durata della finestra appena
        conclusa: un mezzo libero al minuto 200 di una finestra di 180 minuti e'
        libero al minuto 20 della successiva. (3) Al cambio di giornata il
        contatore del turno riparte da zero, perche' `max_shift_minutes` e' un
        limite giornaliero dell'equipaggio.

        IL TEMPO A TERRA ATTRAVERSA IL CONFINE DELLA FINESTRA
        `Base.turnaround_minutes` (SPEC.md sezione 6, punto 2) vale fra due
        missioni consecutive dello stesso mezzo, e due missioni restano
        consecutive anche quando cadono in due finestre diverse: il confine
        delle finestre e' una scelta di chi simula, non un fatto del mondo.
        Senza questo termine un mezzo che rientra al minuto 179 di una finestra
        di 180 potrebbe ripartire al minuto 0 della successiva, cioe' fare
        rifornimento e cambio equipaggio in un istante. Il validatore non se ne
        accorgerebbe, perche' guarda un piano alla volta e li' le due missioni
        non compaiono insieme: e' esattamente il tipo di difetto che sopravvive
        a un controllo per finestra, e per questo va chiuso qui. La regola e'
        identica per il risolutore e per la baseline, quindi non sposta il
        confronto, solo il realismo dei suoi numeri.
        """
        nxt = as_utc(next_timestamp)
        window = max(0.0, float(window_minutes))
        base_points = {b.base_id: b.point for b in scenario.bases}
        turnaround_of = {
            b.base_id: max(0.0, float(b.turnaround_minutes)) for b in scenario.bases
        }
        vehicle_by_id = {v.vehicle_id: v for v in scenario.vehicles}

        # Si parte da una copia: gli stati dei mezzi non toccati restano quelli.
        updated: dict[str, VehicleState] = dict(self.vehicles)

        # Ordine stabile: sommare float in ordine diverso darebbe risultati
        # diversi negli ultimi decimali, e la rigiocata deve essere ripetibile.
        for a in sorted(
            plan.assignments, key=lambda x: (x.vehicle_id, x.departure_minute, x.fire_id)
        ):
            current = updated.get(a.vehicle_id) or VehicleState(vehicle_id=a.vehicle_id)
            vehicle = vehicle_by_id.get(a.vehicle_id)
            home = base_points.get(vehicle.home_base_id) if vehicle else None
            ground = (
                turnaround_of.get(vehicle.home_base_id, DEFAULT_TURNAROUND_MINUTES)
                if vehicle is not None
                else DEFAULT_TURNAROUND_MINUTES
            )
            updated[a.vehicle_id] = replace(
                current,
                # A fine missione il mezzo e' rientrato: e' la stessa ipotesi con
                # cui il piano ha contato il tempo di ritorno.
                position=home or current.position,
                available_from_minute=max(
                    current.available_from_minute, float(a.release_minute) + ground
                ),
                minutes_flown_today=current.minutes_flown_today
                + max(0.0, float(a.engaged_minutes)),
            )

        new_day = nxt.date() != self.shift_day
        shifted: dict[str, VehicleState] = {}
        for vid in sorted(updated):
            st = updated[vid]
            shifted[vid] = replace(
                st,
                available_from_minute=max(0.0, st.available_from_minute - window),
                minutes_flown_today=0.0 if new_day else st.minutes_flown_today,
            )

        # `active_fire_ids` si ricalcola dai focolai di QUESTA finestra e non si
        # eredita: un focolaio che la finestra non ha piu' visto (troppo vecchio
        # per la regola di visibilita' di `timeline`) esce dagli attivi senza
        # finire fra gli spenti. Sono due cose diverse e vanno tenute diverse:
        # "non lo vedo piu'" non e' "l'ho spento", e confonderle gonfierebbe la
        # copertura di entrambi i pianificatori. Gli spenti invece si accumulano,
        # perche' un focolaio chiuso non torna.
        extinguished = set(self.extinguished_fire_ids)
        active: set[str] = set()
        for fire in scenario.fires:
            if fire.fire_id in extinguished:
                continue
            delivered = sum(
                int(a.water_delivered_liters) for a in plan.assignments_for_fire(fire.fire_id)
            )
            demand = int(fire.water_demand_liters)
            # Semplificazione dichiarata: un focolaio si considera spento quando
            # ha ricevuto l'acqua che il modello gli attribuisce. Nella realta'
            # dipende da vento, combustibile e bonifica; qui serve solo a
            # impedire che lo stesso focolaio venga ripianificato per sempre, e
            # vale identica per il solver e per la baseline.
            if demand > 0 and delivered >= demand:
                extinguished.add(fire.fire_id)
            else:
                active.add(fire.fire_id)

        return WorldState(
            timestamp=nxt,
            vehicles=shifted,
            active_fire_ids=active,
            extinguished_fire_ids=extinguished,
            shift_day=nxt.date() if new_day else self.shift_day,
        )

    # ------------------------------------------------------------------
    # Serializzazione
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Forma serializzabile e ordinata, per finire nel JSON dei risultati."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "shift_day": self.shift_day.isoformat() if self.shift_day else None,
            "vehicles": [self.vehicles[k].to_dict() for k in sorted(self.vehicles)],
            "active_fire_ids": sorted(self.active_fire_ids),
            "extinguished_fire_ids": sorted(self.extinguished_fire_ids),
        }


def fresh_fleet_snapshot(
    vehicles: Iterable[Vehicle], bases: Sequence[Base] = ()
) -> list[Vehicle]:
    """Copie dei mezzi con lo stato dinamico azzerato.

    Utile a chi costruisce uno scenario a mano: garantisce che i mezzi non si
    portino dietro posizione e minuti volati di una simulazione precedente.
    """
    by_base = {b.base_id: b.point for b in bases}
    out: list[Vehicle] = []
    for v in vehicles:
        out.append(
            replace(
                v,
                current_position=by_base.get(v.home_base_id),
                available_from_minute=0.0,
                minutes_flown_today=0.0,
            )
        )
    return out


def fires_still_burning(
    fires: Iterable[FireCluster], state: WorldState
) -> list[FireCluster]:
    """I focolai che lo stato non considera ancora spenti, in ordine stabile."""
    return sorted(
        (f for f in fires if f.fire_id not in state.extinguished_fire_ids),
        key=lambda f: f.fire_id,
    )
