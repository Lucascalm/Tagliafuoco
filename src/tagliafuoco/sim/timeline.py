"""Finestre di rigiocata e visibilita' dei dati: come si evita di barare.

PERCHE' QUESTO MODULO ESISTE
Rigiocare una stagione passata e' facile da fare male. Il modo sbagliato e'
prendere tutti i rilevamenti della stagione, costruire un unico grande scenario
e pianificare: il risultato sembra ottimo, ma il pianificatore ha visto incendi
che al momento della decisione non erano ancora accaduti. E' fuga di
informazione dal futuro, e rende il numero finale privo di significato, perche'
un sistema reale quell'informazione non l'avrebbe mai.

Qui la regola e' una sola e vive tutta in questo file: a ogni istante di
decisione `t` il pianificatore vede i rilevamenti con `acquired_at <= t`, mai
uno di piu'. La stagione si percorre a finestre di REPLAY_WINDOW_HOURS ore, e
ogni finestra guarda solo all'indietro.

E' lo stesso problema del backtesting in finanza, con lo stesso nome tecnico
(look-ahead bias). Una strategia provata su serie storiche ottiene rendimenti
irreali se in qualunque punto del calcolo entra un prezzo successivo alla
decisione, anche per errori piccoli: una media mobile centrata invece che
ritardata, un dato rivisto a posteriori, un titolo uscito dall'indice e non
considerato. La difesa e' la stessa: un solo punto nel codice decide che cosa e'
visibile a che ora, e tutto il resto passa da li'.

Il filtro all'indietro ha anche un limite dichiarato: oltre
CLUSTER_TIME_WINDOW_HOURS un rilevamento non descrive piu' lo stesso focolaio
(il satellite ripassa e trova un incendio nuovo), quindi la finestra di
visibilita' e' limitata anche nel passato.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from tagliafuoco.config import (
    CLUSTER_TIME_WINDOW_HOURS,
    DAYLIGHT_END_MINUTE,
    DAYLIGHT_START_MINUTE,
    DEFAULT_HORIZON_MINUTES,
    REPLAY_WINDOW_HOURS,
)
from tagliafuoco.domain.models import Detection, FireCluster


class TimelineError(ValueError):
    """Intervallo di rigiocata incoerente."""


# Quante finestre al massimo si accetta di costruire in una sola chiamata. E'
# una taratura difensiva, non un limite del modello: la stagione 2025 a finestre
# di tre ore ne produce meno di mille, e anche un passo di dieci minuti ne
# produce meno di ventimila. Un numero piu' grande di questo non e' una
# rigiocata piu' fine, e' quasi sempre un passo sbagliato (minuti scambiati per
# ore, una divisione al posto di una moltiplicazione). Senza il controllo la
# chiamata non fallisce: riempie la memoria e la macchina si ferma senza dire
# perche', che e' il modo peggiore in cui un parametro sbagliato puo'
# manifestarsi.
MAX_WINDOWS_PER_CALL = 100_000


def as_utc(value: datetime) -> datetime:
    """Riporta un istante a UTC consapevole del fuso.

    I rilevamenti FIRMS sono in UTC ma le date scritte a mano nei test e sulla
    riga di comando spesso sono ingenue. Confrontare un istante con fuso con uno
    senza fuso in Python solleva TypeError: normalizzare in un punto solo evita
    di scoprirlo a meta' di una rigiocata lunga.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReplayWindow:
    """Un istante di decisione e l'intervallo che copre.

    `start` e' l'istante in cui il coordinatore decide: e' anche il minuto zero
    del piano, perche' le assegnazioni sono in minuti dall'inizio
    dell'orizzonte e non in orari assoluti.

    Gli estremi si normalizzano a UTC alla costruzione, anche quando la finestra
    viene costruita a mano invece che da `build_windows`. Senza questo, una
    finestra con estremi ingenui e un istante con fuso facevano esplodere
    `contains()` con "can't compare offset-naive and offset-aware datetimes", e
    l'etichetta usata negli identificativi dei piani sarebbe cambiata con le
    impostazioni della macchina.
    """

    index: int
    start: datetime
    end: datetime
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES

    def __post_init__(self) -> None:
        # La dataclass e' congelata di proposito (una finestra non si modifica a
        # meta' rigiocata), quindi la normalizzazione passa da object.
        object.__setattr__(self, "start", as_utc(self.start))
        object.__setattr__(self, "end", as_utc(self.end))
        if self.end < self.start:
            raise TimelineError(
                f"finestra invertita: start={self.start.isoformat()} "
                f"end={self.end.isoformat()}"
            )

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0

    @property
    def label(self) -> str:
        """Etichetta stabile e leggibile, usata negli identificativi."""
        return self.start.strftime("%Y%m%dT%H%M")

    def contains(self, instant: datetime) -> bool:
        """Vero se l'istante cade nella finestra (estremo destro escluso)."""
        t = as_utc(instant)
        return self.start <= t < self.end

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "horizon_minutes": self.horizon_minutes,
        }


def build_windows(
    start: datetime,
    end: datetime,
    *,
    window_hours: float = REPLAY_WINDOW_HOURS,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
) -> list[ReplayWindow]:
    """Sequenza di istanti di decisione fra `start` (incluso) e `end` (escluso).

    L'orizzonte del piano (quanto avanti guarda il piano) e l'ampiezza della
    finestra (ogni quanto si ripianifica) sono grandezze diverse e restano
    separate: si ripianifica ogni 3 ore ma si pianifica su 4, perche' una
    missione iniziata ora finisce dopo la fine della finestra.

    L'ultima finestra puo' essere piu' corta delle altre, quando l'intervallo
    non e' un multiplo esatto: si taglia a `end` invece di sconfinare, altrimenti
    l'ultimo piano pianificherebbe su un pezzo di stagione fuori dall'esperimento.
    """
    if window_hours <= 0:
        raise TimelineError("window_hours deve essere positivo")
    # Un orizzonte nullo o negativo non produce un errore visibile: produce
    # piani vuoti a ogni finestra, perche' nessuna missione ci sta dentro. Sono
    # numeri che sembrano un risultato ("copertura zero") e invece sono un
    # parametro sbagliato, quindi si ferma qui.
    if int(horizon_minutes) <= 0:
        raise TimelineError(
            f"horizon_minutes deve essere positivo, ricevuto {horizon_minutes}"
        )
    s = as_utc(start)
    e = as_utc(end)
    if e <= s:
        raise TimelineError(
            f"intervallo vuoto o invertito: start={s.isoformat()} end={e.isoformat()}"
        )

    step = timedelta(hours=float(window_hours))
    # Il conteggio si fa prima di costruire le finestre, non a meta' del ciclo:
    # se il passo e' assurdo si deve fallire subito e con un messaggio che dice
    # quale numero lo e', invece di allocare finche' la memoria regge.
    previste = math.ceil((e - s) / step)
    if previste > MAX_WINDOWS_PER_CALL:
        raise TimelineError(
            f"window_hours={window_hours} produrrebbe {previste} finestre "
            f"sull'intervallo richiesto, oltre il tetto di "
            f"{MAX_WINDOWS_PER_CALL}: il passo e' quasi certamente sbagliato "
            f"(minuti al posto di ore?)"
        )

    windows: list[ReplayWindow] = []
    cursor = s
    index = 0
    while cursor < e:
        stop = min(cursor + step, e)
        windows.append(
            ReplayWindow(
                index=index,
                start=cursor,
                end=stop,
                horizon_minutes=int(horizon_minutes),
            )
        )
        cursor = stop
        index += 1
    return windows


def visible_detections(
    detections: Iterable[Detection],
    as_of: datetime,
    *,
    lookback_hours: float = CLUSTER_TIME_WINDOW_HOURS,
) -> list[Detection]:
    """I rilevamenti che a quell'istante erano gia' stati acquisiti.

    Questa funzione e' l'unico punto del sistema autorizzato a rispondere alla
    domanda "che cosa si sapeva alle 14:00". Il confronto e' `<=`: un
    rilevamento acquisito nello stesso istante della decisione e' noto, uno
    acquisito un secondo dopo no.

    Il risultato e' ordinato per istante e identificativo: la rigiocata deve
    essere riproducibile, e un ordine che dipende dalla sorgente dei dati non
    lo sarebbe.

    `lookback_hours` nullo o negativo significa "nessun limite all'indietro":
    si tiene tutto il passato. Prima un valore negativo metteva il limite nel
    futuro e scartava in silenzio ogni rilevamento, cioe' produceva scenari
    vuoti che si leggevano come una stagione senza incendi.
    """
    t = as_utc(as_of)
    lookback = float(lookback_hours)
    floor = t - timedelta(hours=lookback) if lookback > 0.0 else None
    out = []
    for d in detections:
        acquired = as_utc(d.acquired_at)
        if acquired > t:
            continue  # futuro: non esiste ancora
        if floor is not None and acquired < floor:
            continue  # troppo vecchio per descrivere lo stesso focolaio
        out.append(d)
    return sorted(out, key=lambda d: (as_utc(d.acquired_at), d.detection_id))


def visible_fires(
    fires: Iterable[FireCluster],
    as_of: datetime,
    *,
    lookback_hours: float = CLUSTER_TIME_WINDOW_HOURS,
) -> list[FireCluster]:
    """Come `visible_detections`, ma per focolai gia' raggruppati.

    Serve quando il clustering e' stato fatto una volta sola fuori dalla
    rigiocata: la regola di visibilita' resta la stessa, altrimenti il
    pianificatore vedrebbe dal cluster cio' che dai rilevamenti non vedrebbe.

    Un focolaio e' visibile se il suo primo rilevamento e' avvenuto entro
    l'istante di decisione, e se l'ultimo non e' cosi' vecchio da renderlo un
    incendio ormai concluso.

    Un focolaio senza nessuna data (ne' `first_seen`, ne' `last_seen`, ne'
    rilevamenti) resta visibile: non si sa quando sia nato, e scartarlo
    significherebbe far sparire dall'esperimento un incendio per un dato
    mancante. La stessa convenzione di `lookback_hours` di `visible_detections`:
    zero o negativo vuol dire nessun limite all'indietro.
    """
    t = as_utc(as_of)
    lookback = float(lookback_hours)
    floor = t - timedelta(hours=lookback) if lookback > 0.0 else None
    out: list[FireCluster] = []
    for f in fires:
        first = _first_seen(f)
        if first is not None and first > t:
            continue
        last = _last_seen(f) or first
        if floor is not None and last is not None and last < floor:
            continue
        out.append(f)
    return sorted(out, key=lambda f: (_sort_stamp(f, t), f.fire_id))


def _first_seen(fire: FireCluster) -> Optional[datetime]:
    if fire.first_seen is not None:
        return as_utc(fire.first_seen)
    stamps = [as_utc(d.acquired_at) for d in fire.detections]
    return min(stamps) if stamps else None


def _last_seen(fire: FireCluster) -> Optional[datetime]:
    if fire.last_seen is not None:
        return as_utc(fire.last_seen)
    stamps = [as_utc(d.acquired_at) for d in fire.detections]
    return max(stamps) if stamps else None


def _sort_stamp(fire: FireCluster, fallback: datetime) -> float:
    first = _first_seen(fire)
    return (first or fallback).timestamp()


def is_night_at(instant: datetime, *, tz_offset_minutes: int = 60) -> bool:
    """Vero se a quell'istante i mezzi aerei sono a terra.

    Gli orari di alba e tramonto in config sono minuti dalla mezzanotte LOCALE,
    mentre i rilevamenti sono in UTC. Lo scostamento e' un parametro con valore
    di riferimento +60 minuti (ora solare italiana): e' una semplificazione
    dichiarata, non si insegue il cambio dell'ora legale ne' la variazione
    stagionale dell'alba, che sposterebbero il confine di qualche decina di
    minuti senza cambiare il senso del confronto.

    Il confine e' chiuso a sinistra e aperto a destra: alle 05:30 locali in
    punto si vola gia', alle 20:30 locali in punto non si vola piu'. Detto
    perche' un confronto sbagliato a un estremo si nota solo su una finestra
    ogni tanto, ed e' il tipo di differenza che si scambia per rumore.
    """
    local = as_utc(instant) + timedelta(minutes=int(tz_offset_minutes))
    minute_of_day = local.hour * 60 + local.minute
    return not (DAYLIGHT_START_MINUTE <= minute_of_day < DAYLIGHT_END_MINUTE)


def span_of(windows: Sequence[ReplayWindow]) -> tuple[Optional[datetime], Optional[datetime]]:
    """Primo inizio e ultima fine di una sequenza di finestre.

    Si legge il primo e l'ultimo elemento, non il minimo e il massimo: le
    finestre arrivano sempre da `build_windows`, che le produce gia' in ordine.
    Su una sequenza vuota si restituisce `(None, None)` invece di sollevare, cosi'
    chi stampa un riassunto non deve avvolgere la chiamata in un try.
    """
    if not windows:
        return (None, None)
    return (windows[0].start, windows[-1].end)
