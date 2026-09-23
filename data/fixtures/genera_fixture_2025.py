"""Generatore delle fixture sintetiche della stagione 2025.

ATTENZIONE: i CSV prodotti da questo script NON sono dati satellitari. Sono
dati generati. Servono a far girare l'intero progetto senza la chiave gratuita
di NASA FIRMS, come impone SPEC.md, e a rendere i test riproducibili. Chi
ottiene la chiave puo' sostituirli con i dati veri: le istruzioni stanno nel
README di questa cartella.

Perche' un generatore e non quattro righe scritte a mano: la catena a valle
(clustering, arricchimento, ottimizzazione, valutazione) va esercitata su una
stagione intera, con la forma statistica giusta. Un file di prova con dieci
righe farebbe passare i test e non direbbe niente su come si comporta il
sistema quando in una giornata di agosto ci sono quaranta focolai contemporanei
e la flotta e' insufficiente. Le proprieta' riprodotte qui sono quattro:

1. stagionalita': luglio e agosto concentrano circa il settanta per cento degli
   inneschi, con tre ondate di calore che li addensano ulteriormente;
2. ciclo giornaliero: circa settanta rilevamenti diurni ogni trenta notturni,
   ottenuto non per decreto ma facendo dipendere dall'ora la probabilita' che
   il passaggio veda il fuoco e la lunghezza del fronte attivo;
3. geografia: Sila, Aspromonte, Pollino, Serre, marchesato crotonese e fascia
   costiera tirrenica, cioe' dove la serie storica regionale concentra gli
   eventi;
4. distribuzione della potenza radiativa a coda lunga (lognormale): molti
   focolai da pochi megawatt e rari eventi da oltre mille.

Il formato di uscita e' quello esatto dell'API per area di FIRMS, una colonna
per colonna, un file per prodotto. E' una scelta deliberata: lo stesso parser
serve le fixture e i dati veri, quindi se il parser si rompe sui dati veri si
rompe anche qui e i test lo vedono.

Lo script usa solo la libreria standard e `random.Random` con il seed preso da
`fixture_config.json`: non importa il pacchetto `tagliafuoco` e non richiede
l'ambiente virtuale del progetto. Due esecuzioni con lo stesso seed producono
file identici byte per byte.

Uso:
    .venv/Scripts/python.exe data/fixtures/genera_fixture_2025.py
    .venv/Scripts/python.exe data/fixtures/genera_fixture_2025.py --seed 7
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "fixture_config.json"

# Gradi di latitudine per chilometro sulla sfera terrestre. Serve solo a
# disperdere i pixel attorno al centro dell'evento: non e' geodesia.
KM_PER_DEG_LAT = 110.574


# --------------------------------------------------------------------------
# Utilita' statistiche
# --------------------------------------------------------------------------


def weighted_choice(rng: random.Random, items: list[dict], key: str = "weight") -> dict:
    """Estrae un elemento con probabilita' proporzionale al campo `key`."""
    total = sum(float(item[key]) for item in items)
    threshold = rng.random() * total
    cumulative = 0.0
    for item in items:
        cumulative += float(item[key])
        if threshold <= cumulative:
            return item
    return items[-1]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def daily_weights(cfg: dict) -> tuple[list[date], list[float]]:
    """Peso di innesco per ogni giorno della stagione.

    Si parte dal peso mensile diviso per i giorni del mese (stagione piatta
    dentro il mese) e si sommano i bulbi gaussiani delle ondate di calore. Il
    risultato non e' normalizzato: `weighted_choice` non ne ha bisogno.
    """
    season = cfg["season"]
    start = date.fromisoformat(season["start"])
    end = date.fromisoformat(season["end"])
    month_weights = {int(k): float(v) for k, v in cfg["month_weights"].items()}

    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)

    days_in_month: dict[int, int] = {}
    for day in days:
        days_in_month[day.month] = days_in_month.get(day.month, 0) + 1

    waves = [
        (date.fromisoformat(w["peak"]), float(w["sigma_days"]), float(w["amplitude"]))
        for w in cfg.get("heat_waves", [])
    ]

    weights: list[float] = []
    for day in days:
        base = month_weights.get(day.month, 0.0) / max(1, days_in_month[day.month])
        bump = 0.0
        for peak, sigma, amplitude in waves:
            delta = (day - peak).days
            bump += amplitude * math.exp(-0.5 * (delta / sigma) ** 2)
        weights.append(base * (1.0 + bump))
    return days, weights


def pick_day(rng: random.Random, days: list[date], weights: list[float]) -> date:
    total = sum(weights)
    threshold = rng.random() * total
    cumulative = 0.0
    for day, weight in zip(days, weights):
        cumulative += weight
        if threshold <= cumulative:
            return day
    return days[-1]


# --------------------------------------------------------------------------
# Geografia
# --------------------------------------------------------------------------


def point_in_zone(rng: random.Random, zone: dict) -> tuple[float, float]:
    """Estrae un punto dentro una zona, ellittica o costiera.

    Le ellissi sono gaussiane bivariate troncate a due sigma: troncare evita
    le code che finirebbero in mare o fuori regione. La fascia costiera e' una
    polilinea: si estrae un punto su un segmento e lo si sposta verso l'interno
    (a est, perche' e' la costa tirrenica, cioe' quella occidentale).
    """
    if zone.get("shape") == "coastline":
        waypoints = zone["waypoints"]
        index = rng.randrange(len(waypoints) - 1)
        t = rng.random()
        lat_a, lon_a = waypoints[index]
        lat_b, lon_b = waypoints[index + 1]
        lat = lat_a + (lat_b - lat_a) * t
        lon = lon_a + (lon_b - lon_a) * t
        low, high = zone["inland_offset_deg"]
        lon += rng.uniform(float(low), float(high))
        lat += rng.gauss(0.0, float(zone.get("lateral_jitter_deg", 0.02)))
        return lat, lon

    sigma_lat = float(zone["sigma_lat"])
    sigma_lon = float(zone["sigma_lon"])
    lat = float(zone["lat"]) + clamp(rng.gauss(0.0, sigma_lat), -2 * sigma_lat, 2 * sigma_lat)
    lon = float(zone["lon"]) + clamp(rng.gauss(0.0, sigma_lon), -2 * sigma_lon, 2 * sigma_lon)
    return lat, lon


def scatter_pixel(
    rng: random.Random, lat: float, lon: float, spread_km: float
) -> tuple[float, float]:
    """Disperde un pixel attorno al centro dell'evento, entro `spread_km`.

    La dispersione sta sotto `CLUSTER_EPS_KM` (1.5 km) apposta: i pixel dello
    stesso evento devono finire nello stesso focolaio, altrimenti la fixture
    metterebbe alla prova il clustering su un problema che non esiste.
    """
    radius_km = spread_km * math.sqrt(rng.random())
    angle = rng.uniform(0.0, 2.0 * math.pi)
    d_lat = (radius_km * math.cos(angle)) / KM_PER_DEG_LAT
    cos_lat = max(0.1, math.cos(math.radians(lat)))
    d_lon = (radius_km * math.sin(angle)) / (KM_PER_DEG_LAT * cos_lat)
    return lat + d_lat, lon + d_lon


# --------------------------------------------------------------------------
# Passaggi satellitari
# --------------------------------------------------------------------------


def overpasses(
    cfg: dict, start: datetime, duration_hours: float, rng: random.Random
) -> list[dict]:
    """Elenca i passaggi satellitari che cadono nella vita dell'evento.

    Gli orari sono locali e vengono riportati in UTC togliendo
    `local_utc_offset_hours`: durante la stagione la Calabria e' in ora legale
    (UTC+2) per intero, quindi uno scostamento fisso basta.
    """
    offset = timedelta(hours=float(cfg["local_utc_offset_hours"]))
    jitter = int(cfg.get("jitter_minutes", 6))
    end = start + timedelta(hours=duration_hours)

    passes: list[dict] = []
    day = (start - timedelta(days=1)).date()
    last_day = (end + timedelta(days=1)).date()
    while day <= last_day:
        for product in cfg["products"]:
            for satellite in product["satellites"]:
                for phase in ("day", "night"):
                    local_hour = float(satellite[f"{phase}_local_hour"])
                    local_dt = datetime(day.year, day.month, day.day) + timedelta(
                        hours=local_hour, minutes=rng.uniform(-jitter, jitter)
                    )
                    moment = local_dt - offset
                    if start <= moment <= end:
                        passes.append(
                            {
                                "moment": moment,
                                "product": product,
                                "satellite": satellite["code"],
                                "phase": phase,
                            }
                        )
        day += timedelta(days=1)
    passes.sort(key=lambda p: (p["moment"], p["product"]["source"], p["satellite"]))
    return passes


# --------------------------------------------------------------------------
# Attributi radiometrici
# --------------------------------------------------------------------------


def sample_frp(rng: random.Random, cfg: dict, scale: float) -> float:
    """Potenza radiativa di un pixel, lognormale con coda lunga."""
    spec = cfg["frp"]
    value = math.exp(rng.gauss(float(spec["lognormal_mu"]), float(spec["lognormal_sigma"])))
    return round(clamp(value * scale, float(spec["min_mw"]), float(spec["max_mw"])), 1)


def brightness_for(rng: random.Random, frp_mw: float, instrument: str) -> float:
    """Temperatura di brillanza coerente con la potenza radiativa.

    Non e' la relazione fisica (che passa per la legge di Stefan-Boltzmann e
    per la frazione di pixel occupata dalla fiamma): e' una curva monotona che
    sale col logaritmo della FRP e satura dove saturano i sensori, 367 K per
    la banda I4 di VIIRS e circa 500 K per MODIS. Serve a che i valori siano
    plausibili, non a ricavare la FRP dalla temperatura.
    """
    if instrument == "VIIRS":
        floor, ceiling = 300.0, 367.0
    else:
        floor, ceiling = 300.0, 500.0
    fraction = math.log1p(frp_mw) / math.log1p(400.0)
    value = floor + (ceiling - floor) * clamp(fraction, 0.0, 1.0)
    return round(clamp(value + rng.gauss(0.0, 3.0), floor, ceiling), 1)


def confidence_for(rng: random.Random, frp_mw: float, style: str) -> str:
    """Confidenza nel formato del prodotto: categorica VIIRS, numerica MODIS.

    Cresce con la potenza radiativa, perche' un pixel molto caldo si stacca
    dallo sfondo senza ambiguita', mentre un pixel tiepido puo' essere una
    nube calda o un riflesso.
    """
    strength = clamp(math.log1p(frp_mw) / math.log1p(60.0), 0.0, 1.0)
    if style == "categorical":
        roll = rng.random()
        if strength > 0.72:
            return "h" if roll < 0.80 else "n"
        if strength > 0.38:
            return "n" if roll < 0.82 else ("h" if roll < 0.93 else "l")
        return "l" if roll < 0.55 else "n"
    value = 25.0 + 70.0 * strength + rng.gauss(0.0, 9.0)
    return str(int(clamp(value, 0.0, 100.0)))


# --------------------------------------------------------------------------
# Generazione
# --------------------------------------------------------------------------


def generate_rows(cfg: dict, rng: random.Random) -> dict[str, list[dict[str, Any]]]:
    """Costruisce le righe CSV, raggruppate per prodotto FIRMS."""
    days, weights = daily_weights(cfg)
    ignition = cfg["ignition"]
    detection = cfg["detection"]
    offset_hours = float(cfg["local_utc_offset_hours"])
    spread_km = float(cfg["pixel_spread_km"])
    rows: dict[str, list[dict[str, Any]]] = {p["source"]: [] for p in cfg["products"]}

    for _ in range(int(cfg["events"])):
        zone = weighted_choice(rng, cfg["zones"])
        size = weighted_choice(rng, cfg["size_classes"])
        centre_lat, centre_lon = point_in_zone(rng, zone)

        day = pick_day(rng, days, weights)
        local_hour = clamp(
            rng.gauss(float(ignition["peak_local_hour"]), float(ignition["sigma_hours"])),
            float(ignition["min_local_hour"]),
            float(ignition["max_local_hour"]),
        )
        start = datetime(day.year, day.month, day.day) + timedelta(
            hours=local_hour - offset_hours
        )
        low, high = size["duration_hours"]
        duration = rng.uniform(float(low), float(high))

        seen = False
        for overpass in overpasses(cfg, start, duration, rng):
            is_night = overpass["phase"] == "night"
            probability = float(
                detection["night_probability" if is_night else "day_probability"]
            )
            if rng.random() > probability:
                continue
            seen = True
            elapsed_h = (overpass["moment"] - start).total_seconds() / 3600.0
            decay = math.exp(-elapsed_h / float(detection["decay_tau_hours"]))
            factor = float(detection["night_pixel_factor"]) if is_night else 1.0
            # Il pixel MODIS e' sette volte piu' grosso di quello VIIRS: lo
            # stesso fronte produce molti meno pixel. Senza questo correttivo
            # l'archivio MODIS risulterebbe il piu' ricco dei tre, che e'
            # l'opposto di quello che si osserva nei dati veri.
            factor *= float(overpass["product"].get("pixel_factor", 1.0))
            count = max(
                1,
                int(round(int(size["pixels"]) * decay * factor * rng.uniform(0.55, 1.25))),
            )
            rows[overpass["product"]["source"]].extend(
                _pixel_rows(
                    rng,
                    cfg,
                    overpass,
                    count,
                    centre_lat,
                    centre_lon,
                    spread_km,
                    float(size["frp_scale"]) * decay,
                )
            )
        if not seen:
            # Un evento che nessun passaggio vede non esiste nel dato: se
            # capita si forza il primo passaggio diurno, altrimenti il conteggio
            # degli eventi perderebbe significato.
            candidates = [
                p for p in overpasses(cfg, start, max(duration, 24.0), rng)
                if p["phase"] == "day"
            ]
            if candidates:
                overpass = candidates[0]
                rows[overpass["product"]["source"]].extend(
                    _pixel_rows(
                        rng,
                        cfg,
                        overpass,
                        max(1, int(size["pixels"]) // 2),
                        centre_lat,
                        centre_lon,
                        spread_km,
                        float(size["frp_scale"]),
                    )
                )
    return rows


def _pixel_rows(
    rng: random.Random,
    cfg: dict,
    overpass: dict,
    count: int,
    centre_lat: float,
    centre_lon: float,
    spread_km: float,
    frp_scale: float,
) -> list[dict[str, Any]]:
    """Righe CSV di un singolo passaggio su un singolo evento."""
    product = overpass["product"]
    moment: datetime = overpass["moment"]
    bbox = cfg.get("bbox")
    season = cfg["season"]
    out: list[dict[str, Any]] = []
    for index in range(count):
        lat, lon = scatter_pixel(rng, centre_lat, centre_lon, spread_km)
        if bbox and not (
            bbox[1] <= lat <= bbox[3] and bbox[0] <= lon <= bbox[2]
        ):
            continue
        day = moment.strftime("%Y-%m-%d")
        if not (season["start"] <= day <= season["end"]):
            # Un evento acceso il 30 settembre puo' bruciare a ottobre, ma la
            # fixture copre la stagione dichiarata e basta: righe fuori
            # stagione confonderebbero chi legge il file.
            continue
        # Il primo pixel del passaggio e' la testa del fronte, e sui fronti di
        # una certa larghezza irraggia molto piu' dei fianchi. E' cio' che fa
        # divergere max_frp_mw dalla media e che da' senso al centroide pesato.
        boost = 1.0
        if index == 0 and count >= 4:
            low, high = cfg.get("head_boost", [1.0, 1.0])
            boost = rng.uniform(float(low), float(high))
        frp = sample_frp(rng, cfg, frp_scale * boost)
        scan_low, scan_high = product["scan_range"]
        track_low, track_high = product["track_range"]
        row = {
            "country_id": "ITA",
            "latitude": f"{lat:.5f}",
            "longitude": f"{lon:.5f}",
            product["brightness_column"]: f"{brightness_for(rng, frp, product['instrument']):.1f}",
            "scan": f"{rng.uniform(float(scan_low), float(scan_high)):.2f}",
            "track": f"{rng.uniform(float(track_low), float(track_high)):.2f}",
            "acq_date": moment.strftime("%Y-%m-%d"),
            # FIRMS scrive l'ora come intero HHMM senza zero iniziale:
            # le 00:48 diventano "48". Il parser deve saperlo gestire.
            "acq_time": str(moment.hour * 100 + moment.minute),
            "satellite": overpass["satellite"],
            "instrument": product["instrument"],
            "confidence": confidence_for(rng, frp, product["confidence_style"]),
            "version": product["version"],
            product["second_brightness_column"]: f"{brightness_for(rng, frp * 0.35, product['instrument']) - 15.0:.1f}",
            "frp": f"{frp:.1f}",
            "daynight": "N" if overpass["phase"] == "night" else "D",
        }
        out.append(row)
    return out


def write_csv(path: Path, product: dict, rows: list[dict[str, Any]]) -> None:
    """Scrive un CSV nell'ordine di colonne esatto dell'API per area di FIRMS."""
    header = [
        "country_id",
        "latitude",
        "longitude",
        product["brightness_column"],
        "scan",
        "track",
        "acq_date",
        "acq_time",
        "satellite",
        "instrument",
        "confidence",
        "version",
        product["second_brightness_column"],
        "frp",
        "daynight",
    ]
    ordered = sorted(
        rows,
        key=lambda r: (
            r["acq_date"],
            int(r["acq_time"]),
            r["latitude"],
            r["longitude"],
        ),
    )
    # newline="" e lineterminator="\n" per avere lo stesso file su Windows e
    # su Linux: le fixture sono versionate, un CRLF le farebbe divergere.
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(ordered)


def summarise(rows: Iterable[dict[str, Any]]) -> str:
    """Riepilogo leggibile, per controllare a occhio che la forma sia giusta."""
    rows = list(rows)
    if not rows:
        return "nessuna riga"
    day = sum(1 for r in rows if r["daynight"] == "D")
    by_month: dict[str, int] = {}
    for r in rows:
        month = r["acq_date"][:7]
        by_month[month] = by_month.get(month, 0) + 1
    frps = sorted(float(r["frp"]) for r in rows)
    median = frps[len(frps) // 2]
    months = " ".join(f"{k}={v}" for k, v in sorted(by_month.items()))
    return (
        f"{len(rows)} righe, {100.0 * day / len(rows):.0f}% diurne, "
        f"FRP mediana {median:.1f} MW massima {frps[-1]:.1f} MW, {months}"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--seed", type=int, default=None, help="sovrascrive il seed del file di configurazione"
    )
    parser.add_argument(
        "--config", type=Path, default=CONFIG_PATH, help="file di configurazione"
    )
    parser.add_argument(
        "--out", type=Path, default=HERE, help="cartella di destinazione dei CSV"
    )
    args = parser.parse_args(argv)

    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    cfg.setdefault("bbox", [15.55, 37.85, 17.25, 40.20])
    seed = args.seed if args.seed is not None else int(cfg["seed"])
    rng = random.Random(seed)

    rows = generate_rows(cfg, rng)
    args.out.mkdir(parents=True, exist_ok=True)

    total = 0
    year = cfg["season"]["year"]
    for product in cfg["products"]:
        source = product["source"]
        path = args.out / f"{source}_calabria_{year}.csv"
        write_csv(path, product, rows[source])
        total += len(rows[source])
        print(f"{path.name}: {summarise(rows[source])}")
    print(f"totale: {total} rilevamenti sintetici, seed {seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
