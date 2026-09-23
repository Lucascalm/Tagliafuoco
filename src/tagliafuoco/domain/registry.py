"""Caricamento delle risorse statiche dal disco.

Le basi, i mezzi e i punti d'acqua non cambiano da una finestra di
pianificazione all'altra: stanno in JSON sotto `data/static/` e si caricano
una volta sola. Questo modulo e' l'unico punto che conosce il formato di quei
file, cosi' se il formato cambia si aggiorna qui e basta.

La funzione `load_fleet` restituisce sempre copie fresche: lo stato dinamico
di un mezzo (dove si trova, da quando e' libero, quanti minuti ha volato) e'
proprieta' dello scenario in corso, non del registro. Condividere le stesse
istanze fra due scenari farebbe sanguinare lo stato da una simulazione
all'altra, ed e' un errore difficile da vedere nei risultati.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from tagliafuoco.config import STATIC_DIR
from tagliafuoco.domain.models import (
    Base,
    Domain,
    GeoPoint,
    Vehicle,
    VehicleKind,
    VehicleStatus,
    WaterSource,
    WaterSourceKind,
)

BASES_FILE = "basi.json"
VEHICLES_FILE = "mezzi.json"
WATER_FILE = "sorgenti_acqua.json"
COMUNI_FILE = "comuni.json"
PROTECTED_FILE = "aree_protette.json"


class StaticDataError(RuntimeError):
    """I dati statici mancano o non rispettano il formato atteso."""


def _read_json(name: str, directory: Optional[Path] = None) -> Any:
    path = (directory or STATIC_DIR) / name
    if not path.exists():
        raise StaticDataError(
            f"manca il file dei dati statici: {path}. "
            f"Rigeneralo con: python scripts/genera_dati_statici.py"
        )
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _geo(d: dict) -> GeoPoint:
    return GeoPoint(lat=float(d["lat"]), lon=float(d["lon"]))


# --------------------------------------------------------------------------
# Basi
# --------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _bases_raw(directory: Optional[str] = None) -> tuple:
    data = _read_json(BASES_FILE, Path(directory) if directory else None)
    return tuple(data)


def load_bases(directory: Optional[Path] = None) -> list[Base]:
    """Legge le basi operative (aeroporti, elisuperfici, distaccamenti)."""
    out: list[Base] = []
    for d in _bases_raw(str(directory) if directory else None):
        out.append(
            Base(
                base_id=d["base_id"],
                name=d["name"],
                point=_geo(d),
                provincia=d["provincia"],
                supports=[VehicleKind(k) for k in d.get("supports", [])],
                has_night_capability=bool(d.get("has_night_capability", False)),
                turnaround_minutes=float(d.get("turnaround_minutes", 10.0)),
            )
        )
    return out


# --------------------------------------------------------------------------
# Mezzi
# --------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _vehicles_raw(directory: Optional[str] = None) -> tuple:
    data = _read_json(VEHICLES_FILE, Path(directory) if directory else None)
    return tuple(data)


def load_fleet(directory: Optional[Path] = None) -> list[Vehicle]:
    """Legge la flotta e restituisce istanze nuove, senza stato residuo."""
    out: list[Vehicle] = []
    for d in _vehicles_raw(str(directory) if directory else None):
        out.append(
            Vehicle(
                vehicle_id=d["vehicle_id"],
                name=d["name"],
                kind=VehicleKind(d["kind"]),
                domain=Domain(d["domain"]),
                home_base_id=d["home_base_id"],
                status=VehicleStatus(d.get("status", "available")),
                cruise_speed_kmh=float(d["cruise_speed_kmh"]),
                water_capacity_liters=int(d["water_capacity_liters"]),
                drop_minutes=float(d.get("drop_minutes", 1.0)),
                refill_minutes=float(d.get("refill_minutes", 2.0)),
                setup_minutes=float(d.get("setup_minutes", 5.0)),
                endurance_minutes=float(d.get("endurance_minutes", 180.0)),
                max_shift_minutes=float(d.get("max_shift_minutes", 480.0)),
                night_capable=bool(d.get("night_capable", False)),
                cost_per_hour_eur=float(d.get("cost_per_hour_eur", 1000.0)),
                max_wind_kmh=float(d.get("max_wind_kmh", 50.0)),
                requires_water_source=bool(d.get("requires_water_source", True)),
            )
        )
    return copy.deepcopy(out)


# --------------------------------------------------------------------------
# Acqua
# --------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _water_raw(directory: Optional[str] = None) -> tuple:
    data = _read_json(WATER_FILE, Path(directory) if directory else None)
    return tuple(data)


def load_water_sources(directory: Optional[Path] = None) -> list[WaterSource]:
    """Legge i punti di pescaggio e rifornimento."""
    out: list[WaterSource] = []
    for d in _water_raw(str(directory) if directory else None):
        cap = d.get("capacity_liters")
        out.append(
            WaterSource(
                source_id=d["source_id"],
                name=d["name"],
                point=_geo(d),
                kind=WaterSourceKind(d["kind"]),
                usable_by=[VehicleKind(k) for k in d.get("usable_by", [])],
                refill_minutes=float(d.get("refill_minutes", 2.0)),
                capacity_liters=int(cap) if cap is not None else None,
                seasonal_reliability=float(d.get("seasonal_reliability", 1.0)),
            )
        )
    return out


# --------------------------------------------------------------------------
# Riferimenti territoriali
# --------------------------------------------------------------------------


@lru_cache(maxsize=4)
def load_comuni(directory: Optional[str] = None) -> tuple:
    """Comuni della Calabria con centroide, popolazione e provincia.

    Ogni voce: {"nome", "provincia", "lat", "lon", "popolazione", "altitudine_m"}.
    Serve all'arricchimento per rispondere alla domanda che interessa al
    coordinatore: questo focolaio, quanto e' vicino alle case?
    """
    return tuple(_read_json(COMUNI_FILE, Path(directory) if directory else None))


@lru_cache(maxsize=4)
def load_protected_areas(directory: Optional[str] = None) -> tuple:
    """Parchi e riserve. Ogni voce: {"nome", "tipo", "lat", "lon", "raggio_km"}.

    La geometria e' approssimata a un cerchio: per la priorita' basta sapere
    se il focolaio ricade dentro un'area tutelata, non serve il poligono
    esatto. E' una semplificazione dichiarata.
    """
    return tuple(_read_json(PROTECTED_FILE, Path(directory) if directory else None))


def clear_cache() -> None:
    """Svuota le cache. Serve ai test che scrivono dati statici finti."""
    for fn in (
        _bases_raw,
        _vehicles_raw,
        _water_raw,
        load_comuni,
        load_protected_areas,
    ):
        fn.cache_clear()


def static_data_available(directory: Optional[Path] = None) -> bool:
    """Vero se tutti i file statici sono presenti."""
    d = directory or STATIC_DIR
    return all(
        (d / n).exists()
        for n in (BASES_FILE, VEHICLES_FILE, WATER_FILE, COMUNI_FILE, PROTECTED_FILE)
    )
