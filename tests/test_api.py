"""Test del servizio HTTP.

Verificano tre cose diverse, e conviene distinguerle perche' falliscono per
ragioni diverse.

La prima e' che ogni endpoint risponda e che le risposte validino contro i
propri modelli di trasporto. Non basta guardare il codice 200: FastAPI serializza
attraverso `response_model`, quindi un campo mancante diventerebbe un errore del
server e un campo in piu' sparirebbe in silenzio. Per questo ogni risposta viene
riletta con il modello di `api/schemas.py`: e' il modo di accorgersi che il
formato pubblico e' cambiato.

La seconda e' che gli errori abbiano il codice giusto e la forma giusta. Un
periodo invertito e' 400 e non 500, perche' la colpa e' della richiesta; un
campo di troppo e' 422; una rigiocata che non c'e' e' 404. E ogni errore esce
nella forma unica di `ErrorOut`, avvertenza compresa.

La terza e' che `/api/pianifica` restituisca davvero tutta la catena. E' il
punto in cui si nasconderebbe volentieri una scorciatoia: un piano senza
validazione o senza baseline sembra funzionare, e la demo perde esattamente
cio' che la rende diversa da un risolutore qualunque.

Nessun test tocca la rete e nessuno richiede chiavi. Il servizio sotto test usa
le fixture locali e il traduttore a regole, che e' anche la configurazione con
cui chiunque cloni il repository lo vede la prima volta.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from tagliafuoco import config
from tagliafuoco.api import routes
from tagliafuoco.api.app import create_app
from tagliafuoco.api.routes import Settings, get_settings
from tagliafuoco.api.schemas import (
    ErrorOut,
    FiresOut,
    HealthOut,
    PlanResponse,
    ReplayOut,
    StaticsOut,
    TranslationOut,
)

#: Istante dentro la stagione coperta dalle fixture sintetiche.
INSTANT = "2025-08-06T12:00:00Z"

#: Scenario minuscolo, dichiarato a mano: due focolai e quattro mezzi. Serve a
#: tenere i test veloci senza renderli finti, perche' passa comunque per
#: l'arricchimento territoriale, il solver, il validatore e la baseline.
SMALL_SCENARIO: dict[str, Any] = {
    "instant": INSTANT,
    "horizon_minutes": 240,
    "fires": [
        {"fire_id": "F_TEST_SILA", "lat": 39.31, "lon": 16.56, "frp_mw": 60.0, "area_km2": 1.2},
        {"fire_id": "F_TEST_COSTA", "lat": 38.11, "lon": 15.68, "frp_mw": 25.0, "area_km2": 0.4},
    ],
    "vehicle_ids": ["CAN_01", "HEA_01", "MED_01", "MED_02"],
}


@pytest.fixture(scope="module", autouse=True)
def senza_chiavi() -> Iterator[None]:
    """Azzera le chiavi per tutta la durata del modulo.

    Non basta che la macchina non le abbia: se chi esegue i test tiene un file
    .env con `FIRMS_MAP_KEY`, il servizio sotto test proverebbe a chiamare la
    rete e la suite avrebbe esiti diversi su macchine diverse. Qui il percorso
    senza chiavi e' imposto, ed e' anche l'unico che SPEC.md dichiara
    obbligatorio.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "FIRMS_MAP_KEY", "", raising=False)
        mp.setattr(config, "ANTHROPIC_API_KEY", "", raising=False)
        yield


@pytest.fixture(scope="module")
def replay_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Cartella vuota che fara' da archivio delle rigiocate."""
    return tmp_path_factory.mktemp("rigiocate")


@pytest.fixture(scope="module")
def client(senza_chiavi: None, replay_dir: Path) -> Iterator[TestClient]:
    """Client sul servizio, con impostazioni strette apposta per i test.

    Il tetto del risolutore scende a due secondi: i test non devono misurare
    la qualita' del piano, devono vedere che la catena gira tutta.
    """
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        processed_dir=replay_dir,
        solver_seconds_default=1.0,
        solver_seconds_cap=2.0,
    )
    routes.clear_caches()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def replay_file(replay_dir: Path) -> Iterator[Path]:
    """Un registro di rigiocata finto, nella forma che `sim.replay` scrive.

    Finto e non generato davvero: rigiocare anche una sola finestra costa
    secondi, e l'endpoint non fa altro che leggere un JSON dal disco. Quello che
    va verificato e' che lo legga, lo impagini e non si faccia dare un percorso
    arbitrario, non che il motore di rigiocata funzioni: per quello c'e'
    `tests/test_sim.py`.
    """
    path = replay_dir / "replay_cpsat_20250806T0000_20250807T0000.json"
    path.write_text(
        json.dumps(
            {
                "label": "cpsat",
                "producer": "cpsat",
                "start": "2025-08-06T00:00:00+00:00",
                "end": "2025-08-07T00:00:00+00:00",
                "window_hours": 3.0,
                "horizon_minutes": 240,
                "aggregate": {"windows": 2, "coverage_ratio": 0.5},
                "windows": [{"scenario_id": "SC_A"}, {"scenario_id": "SC_B"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    yield path
    path.unlink(missing_ok=True)


def leggi_istante(value: str) -> datetime:
    """Legge un istante ISO emesso dalla API.

    Su Python 3.10 `datetime.fromisoformat` non accetta il suffisso `Z`, che
    invece e' quello che Pydantic emette per un istante in UTC. La sostituzione
    e' qui e non nei test che la usano perche' e' un limite dell'interprete, non
    una particolarita' del caso di prova.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def esito(response: Any) -> dict[str, Any]:
    """Il corpo JSON, con un messaggio utile quando il codice non e' quello atteso."""
    assert response.headers["content-type"].startswith("application/json")
    return response.json()


# --------------------------------------------------------------------------
# Presentazione e salute
# --------------------------------------------------------------------------


def test_la_radice_dichiara_che_e_una_simulazione(client: TestClient) -> None:
    """Chi apre la porta nel browser deve capirlo dalla prima risposta."""
    body = esito(client.get("/"))
    assert body["simulation"] is True
    assert "simulatore" in body["disclaimer"].lower()
    assert body["docs"] == "/api/docs"


def test_salute_dichiara_chiavi_e_versione(client: TestClient) -> None:
    """La salute valida contro il suo schema e dice in che modo sta girando."""
    response = client.get("/api/salute")
    assert response.status_code == 200
    health = HealthOut.model_validate(esito(response))
    assert health.status == "ok"
    assert health.version
    assert health.keys.detections_source in {"firms", "fixtures"}
    assert health.keys.translator in {"llm", "rules"}
    assert health.solver_seconds_cap == 2.0


def test_senza_chiavi_la_salute_lo_dice_senza_chiamarlo_errore(
    client: TestClient,
) -> None:
    """L'assenza delle chiavi e' una configurazione, non un guasto.

    La suite gira per contratto senza chiavi, quindi qui si verifica anche che
    l'ambiente di prova sia quello dichiarato in SPEC.md.
    """
    health = HealthOut.model_validate(esito(client.get("/api/salute")))
    assert health.keys.firms is False
    assert health.keys.llm is False
    assert health.keys.detections_source == "fixtures"
    assert health.keys.translator == "rules"


def test_documentazione_openapi_generabile(client: TestClient) -> None:
    """Uno schema che non si genera e' un contratto che nessuno puo' leggere."""
    body = esito(client.get("/api/openapi.json"))
    percorsi = set(body["paths"])
    assert {
        "/api/salute",
        "/api/statici",
        "/api/focolai",
        "/api/traduci",
        "/api/pianifica",
        "/api/rigiocata",
    } <= percorsi


# --------------------------------------------------------------------------
# Dati statici
# --------------------------------------------------------------------------


def test_statici_restituisce_basi_mezzi_e_acqua(client: TestClient) -> None:
    """E' cio' che la mappa disegna una volta sola: deve esserci tutto."""
    response = client.get("/api/statici")
    assert response.status_code == 200
    statics = StaticsOut.model_validate(esito(response))
    assert statics.bases and statics.vehicles and statics.water_sources
    assert statics.counts["bases"] == len(statics.bases)
    assert statics.counts["vehicles"] == len(statics.vehicles)
    assert statics.counts["water_sources"] == len(statics.water_sources)


def test_statici_non_espone_lo_stato_interno_dei_mezzi(client: TestClient) -> None:
    """I campi della simulazione non appartengono al protocollo."""
    primo = esito(client.get("/api/statici"))["vehicles"][0]
    assert "available_from_minute" not in primo
    assert "minutes_flown_today" not in primo
    assert "current_position" not in primo


# --------------------------------------------------------------------------
# Focolai
# --------------------------------------------------------------------------


def test_focolai_senza_parametri_restituisce_qualcosa(client: TestClient) -> None:
    """Una chiamata nuda deve mostrare dei focolai, non una lista vuota.

    Senza chiave FIRMS il servizio guarda un giorno delle fixture invece di
    oggi: se guardasse oggi, la demo appena aperta sembrerebbe rotta.
    """
    response = client.get("/api/focolai")
    assert response.status_code == 200
    fires = FiresOut.model_validate(esito(response))
    assert fires.source == "fixtures"
    assert fires.detections_total > 0
    assert fires.fires_total > 0
    assert fires.fires


def test_focolai_rispetta_il_limite(client: TestClient) -> None:
    """Il limite taglia la lista ma non i conteggi, che restano veri."""
    fires = FiresOut.model_validate(
        esito(client.get("/api/focolai", params={"da": "2025-08-05", "a": "2025-08-06", "limite": 2}))
    )
    assert len(fires.fires) <= 2
    assert fires.fires_returned == len(fires.fires)
    assert fires.fires_total >= fires.fires_returned


def test_focolai_arricchiti_col_territorio(client: TestClient) -> None:
    """Un focolaio che esce da qui ha gia' comune, copertura e fabbisogno."""
    fires = FiresOut.model_validate(
        esito(client.get("/api/focolai", params={"da": "2025-08-06", "a": "2025-08-06"}))
    )
    primo = fires.fires[0]
    assert primo.comune
    assert primo.land_cover
    assert primo.water_demand_liters > 0
    assert 0.0 <= primo.base_priority <= 1.0


def test_focolai_periodo_invertito_e_400(client: TestClient) -> None:
    """La colpa e' della richiesta, quindi 400 e non 500."""
    response = client.get("/api/focolai", params={"da": "2025-08-10", "a": "2025-08-01"})
    assert response.status_code == 400
    errore = ErrorOut.model_validate(esito(response))
    assert errore.error == "periodo_invertito"
    assert errore.simulation is True


def test_focolai_periodo_troppo_lungo_e_400(client: TestClient) -> None:
    """Un endpoint che puo' macinare una stagione intera non e' un endpoint."""
    response = client.get("/api/focolai", params={"da": "2025-06-01", "a": "2025-09-30"})
    assert response.status_code == 400
    assert esito(response)["error"] == "periodo_troppo_lungo"


def test_focolai_data_illeggibile_e_422(client: TestClient) -> None:
    """Una data che non e' una data non arriva nemmeno alla rotta."""
    response = client.get("/api/focolai", params={"da": "l'altro ieri"})
    assert response.status_code == 422
    assert esito(response)["error"] == "richiesta_non_valida"


# --------------------------------------------------------------------------
# Traduzione
# --------------------------------------------------------------------------


def test_traduci_dichiara_il_traduttore_usato(client: TestClient) -> None:
    """Chi guarda la demo deve sapere se ha parlato un modello o delle regole."""
    response = client.post(
        "/api/traduci",
        json={"order": "Prima le zone vicino alle case, i Canadair restano a terra"},
    )
    assert response.status_code == 200
    out = TranslationOut.model_validate(esito(response))
    assert out.translator == "rules"
    assert out.llm_available is False
    assert out.constraint_count >= 1


def test_traduci_riporta_i_frammenti_di_origine(client: TestClient) -> None:
    """Senza `source_span` il coordinatore non puo' smentire il traduttore.

    E' il controllo umano previsto sulla traduzione: se il frammento non
    tornasse, un vincolo inventato sarebbe indistinguibile da uno corretto.
    """
    out = TranslationOut.model_validate(
        esito(
            client.post(
                "/api/traduci",
                json={"order": "Gli aerei fino alle venti, poi tutti a terra"},
            )
        )
    )
    assert out.constraints
    spans = [c.source_span for c in out.constraints]
    assert any(span for span in spans), "nessun vincolo porta il frammento di origine"
    for constraint in out.constraints:
        if constraint.source_span:
            assert constraint.source_span.lower() in out.order.lower()


def test_traduci_restituisce_un_constraint_set_riutilizzabile(
    client: TestClient,
) -> None:
    """Il `ConstraintSet` deve poter tornare a /api/pianifica senza ritradurre."""
    tradotto = esito(
        client.post("/api/traduci", json={"order": "Due mezzi su ogni focolaio grave"})
    )["constraint_set"]
    response = client.post(
        "/api/pianifica",
        json={
            "scenario": SMALL_SCENARIO,
            "constraint_set": tradotto,
            "include_baseline": False,
        },
    )
    assert response.status_code == 200
    body = esito(response)
    assert any("gia' tradotti" in nota for nota in body["notes"])
    assert body["translation"]["constraint_set"]["constraint_set_id"] == tradotto[
        "constraint_set_id"
    ]


def test_traduci_ordine_vuoto_e_422(client: TestClient) -> None:
    """Un ordine vuoto e' una richiesta malformata, non una traduzione vuota."""
    response = client.post("/api/traduci", json={"order": ""})
    assert response.status_code == 422
    errore = ErrorOut.model_validate(esito(response))
    assert errore.error == "richiesta_non_valida"
    assert errore.detail


def test_traduci_con_istante_costruisce_lo_scenario(client: TestClient) -> None:
    """Con un istante il traduttore vede la flotta e i focolai di quel momento."""
    response = client.post(
        "/api/traduci",
        json={"order": "L'elicottero di Cosenza resta di riserva", "instant": INSTANT},
    )
    assert response.status_code == 200
    assert TranslationOut.model_validate(esito(response)).constraint_count >= 1


# --------------------------------------------------------------------------
# Pianificazione: l'endpoint centrale
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def piano(client: TestClient) -> dict[str, Any]:
    """Una pianificazione completa, calcolata una volta per tutti i test."""
    response = client.post(
        "/api/pianifica",
        json={
            "order": "Prima le zone vicino alle case",
            "scenario": SMALL_SCENARIO,
            "max_seconds": 1.0,
        },
    )
    assert response.status_code == 200, response.text
    return esito(response)


def test_pianifica_valida_contro_il_suo_schema(piano: dict[str, Any]) -> None:
    """La risposta e' quella dichiarata, campo per campo."""
    PlanResponse.model_validate(piano)


def test_pianifica_restituisce_tutta_la_catena(piano: dict[str, Any]) -> None:
    """Ordine, vincoli, scenario, piano, validazione e confronto in una risposta.

    E' il requisito centrale dell'endpoint: spezzarlo costringerebbe il client a
    ricomporre una sequenza che ha senso solo tutta intera.
    """
    assert piano["order"]
    assert piano["translation"]["constraint_count"] >= 1
    assert piano["scenario"]["scenario_id"]
    assert piano["plan"]["plan_id"]
    assert piano["validation"]["plan_id"] == piano["plan"]["plan_id"]
    assert piano["comparison"] is not None


def test_pianifica_include_davvero_la_validazione(piano: dict[str, Any]) -> None:
    """La validazione non e' un campo decorativo: ha girato tutte le regole."""
    validazione = piano["validation"]
    assert validazione["rules_run"], "il validatore non ha eseguito nessuna regola"
    assert validazione["validator_version"]
    assert isinstance(validazione["is_valid"], bool)
    assert validazione["metrics"]["fires_total"] == piano["scenario"]["fires_total"]
    assert validazione["summary_line"]


def test_pianifica_include_davvero_la_baseline(piano: dict[str, Any]) -> None:
    """Il confronto porta il piano della baseline e la sua validazione.

    Il piano del solver e quello della baseline si distinguono dal `producer`:
    se coincidessero, il confronto starebbe mettendo lo stesso piano contro se
    stesso e non se ne accorgerebbe nessuno.
    """
    confronto = piano["comparison"]
    assert confronto["baseline_name"] == "baseline_nearest"
    assert confronto["baseline"]["producer"] != confronto["solver"]["producer"]
    assert confronto["baseline_plan"]["producer"] == confronto["baseline"]["producer"]
    assert confronto["baseline_validation"]["rules_run"]
    assert set(confronto["delta"]) >= {"coverage_ratio", "mean_response_minutes"}


def test_il_confronto_e_misurato_sulla_stessa_fonte(piano: dict[str, Any]) -> None:
    """Entrambi i punteggi vengono dal validatore, non dalle metriche dichiarate."""
    confronto = piano["comparison"]
    atteso = round(
        confronto["solver"]["coverage_ratio"] - confronto["baseline"]["coverage_ratio"],
        4,
    )
    assert confronto["delta"]["coverage_ratio"] == pytest.approx(atteso)


def test_pianifica_dichiara_lo_scenario_che_ha_usato(piano: dict[str, Any]) -> None:
    """I focolai restituiti sono quelli entrati nel piano, non altri."""
    assert piano["scenario"]["fires_total"] == len(piano["fires"])
    ids_scenario = {f["fire_id"] for f in piano["fires"]}
    ids_piano = {a["fire_id"] for a in piano["plan"]["assignments"]}
    assert ids_piano <= ids_scenario


def test_ogni_risposta_ricorda_che_e_una_simulazione(
    client: TestClient, piano: dict[str, Any]
) -> None:
    """Il campo c'e' sempre, anche sul piano, che e' la risposta piu' fraintendibile."""
    assert piano["simulation"] is True
    assert piano["disclaimer"]
    for url in ("/api/salute", "/api/statici", "/api/focolai"):
        body = esito(client.get(url))
        assert body["simulation"] is True
        assert body["disclaimer"]


def test_pianifica_senza_baseline_non_la_calcola(client: TestClient) -> None:
    """Chi non vuole il confronto non deve pagarne il costo."""
    body = esito(
        client.post(
            "/api/pianifica",
            json={
                "scenario": SMALL_SCENARIO,
                "include_baseline": False,
                "max_seconds": 1.0,
            },
        )
    )
    assert body["comparison"] is None
    assert body["plan"]["plan_id"]


def test_pianifica_senza_ordine_funziona(client: TestClient) -> None:
    """Senza ordine si ottimizza con i soli pesi di default.

    E' il caso di riferimento dell'ablazione, quindi deve restare praticabile
    dalla API e non solo dagli script di valutazione.
    """
    body = esito(
        client.post(
            "/api/pianifica",
            json={"scenario": SMALL_SCENARIO, "include_baseline": False, "max_seconds": 1.0},
        )
    )
    assert body["plan"]["solver_status"]
    assert body["translation"]["translator"] == "rules"


def test_il_tetto_sul_tempo_del_risolutore_e_dichiarato(client: TestClient) -> None:
    """Il limite richiesto viene ridotto al tetto, e la risposta lo dice.

    Ridurlo in silenzio sarebbe peggio di rifiutare la richiesta: chi ha chiesto
    sessanta secondi e ne ha avuti due deve poterlo leggere.
    """
    body = esito(
        client.post(
            "/api/pianifica",
            json={
                "scenario": SMALL_SCENARIO,
                "include_baseline": False,
                "max_seconds": 60.0,
            },
        )
    )
    assert body["solver_seconds_requested"] == 60.0
    assert body["solver_seconds_used"] == 2.0
    assert any("tetto del servizio" in nota for nota in body["notes"])


def test_lo_scenario_troncato_lo_dichiara(client: TestClient) -> None:
    """Se lo scenario viene tagliato, i focolai esclusi finiscono nelle note."""
    body = esito(
        client.post(
            "/api/pianifica",
            json={
                "scenario": {**SMALL_SCENARIO, "max_fires": 1},
                "include_baseline": False,
                "max_seconds": 1.0,
            },
        )
    )
    assert body["scenario"]["fires_total"] == 1
    assert any("troncato" in nota for nota in body["notes"])


def test_pianifica_baseline_sconosciuta_e_400(client: TestClient) -> None:
    """Il nome della baseline e' un valore della richiesta: sbagliarlo e' 400."""
    response = client.post(
        "/api/pianifica", json={"scenario": SMALL_SCENARIO, "baseline": "a_occhio"}
    )
    assert response.status_code == 400
    errore = ErrorOut.model_validate(esito(response))
    assert errore.error == "baseline_sconosciuta"
    assert errore.detail["available"]


def test_pianifica_mezzo_sconosciuto_e_400(client: TestClient) -> None:
    """Chiedere un mezzo che non esiste e' un errore del client, non del server."""
    response = client.post(
        "/api/pianifica",
        json={"scenario": {"instant": INSTANT, "vehicle_ids": ["NON_ESISTE"]}},
    )
    assert response.status_code == 400
    assert esito(response)["error"] == "mezzo_sconosciuto"


def test_pianifica_campo_sconosciuto_e_422(client: TestClient) -> None:
    """I modelli di richiesta vietano i campi in piu'.

    Un campo scritto male verrebbe altrimenti ignorato in silenzio, e chi lo ha
    scritto crederebbe di aver configurato qualcosa che non e' mai arrivato.
    """
    response = client.post("/api/pianifica", json={"ordine": "in italiano sbagliato"})
    assert response.status_code == 422
    assert esito(response)["error"] == "richiesta_non_valida"


def test_pianifica_valore_fuori_scala_e_422(client: TestClient) -> None:
    """Gli intervalli dichiarati nello schema valgono davvero."""
    response = client.post(
        "/api/pianifica", json={"scenario": {"horizon_minutes": 99999}}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Rigiocata
# --------------------------------------------------------------------------


def test_rigiocata_assente_e_404_con_istruzioni(client: TestClient) -> None:
    """Quando non c'e' niente da servire, il messaggio dice come produrlo."""
    response = client.get("/api/rigiocata")
    assert response.status_code == 404
    errore = ErrorOut.model_validate(esito(response))
    assert errore.error == "nessuna_rigiocata"
    assert "tagliafuoco rigioca" in errore.message


def test_rigiocata_serve_il_registro_precalcolato(
    client: TestClient, replay_file: Path
) -> None:
    """L'endpoint legge il JSON prodotto dal comando `rigioca`."""
    response = client.get("/api/rigiocata")
    assert response.status_code == 200
    replay = ReplayOut.model_validate(esito(response))
    assert replay.name == replay_file.name
    assert replay.producer == "cpsat"
    assert replay.windows_count == 2
    assert replay.windows is not None and len(replay.windows) == 2
    assert replay.aggregate["windows"] == 2
    assert [entry.name for entry in replay.available] == [replay_file.name]


def test_rigiocata_puo_restituire_i_soli_totali(
    client: TestClient, replay_file: Path
) -> None:
    """Il timelapse vuole la serie, un riassunto no: si paga solo cio' che serve."""
    replay = ReplayOut.model_validate(
        esito(client.get("/api/rigiocata", params={"includi_finestre": "false"}))
    )
    assert replay.windows is None
    assert replay.windows_count == 2


def test_rigiocata_limita_le_finestre(client: TestClient, replay_file: Path) -> None:
    """`max_finestre` taglia la serie servita ma non il conteggio dichiarato."""
    replay = ReplayOut.model_validate(
        esito(client.get("/api/rigiocata", params={"max_finestre": 1}))
    )
    assert replay.windows is not None and len(replay.windows) == 1
    assert replay.windows_count == 2


def test_rigiocata_non_serve_percorsi_arbitrari(
    client: TestClient, replay_file: Path
) -> None:
    """Il nome arriva dalla rete: deve corrispondere a un file gia' elencato.

    Senza questo controllo un `..` nel parametro farebbe leggere file fuori
    dalla cartella dei risultati.
    """
    response = client.get(
        "/api/rigiocata", params={"nome": "../../../etc/passwd"}
    )
    assert response.status_code == 404
    assert esito(response)["error"] == "rigiocata_sconosciuta"


def test_rigiocata_salute_conta_i_registri(
    client: TestClient, replay_file: Path
) -> None:
    """La salute dice quante rigiocate ci sono, cosi' la demo sa se puo' partire."""
    health = HealthOut.model_validate(esito(client.get("/api/salute")))
    assert health.replays_available == 1


# --------------------------------------------------------------------------
# Errori generici
# --------------------------------------------------------------------------


def test_rotta_inesistente_ha_la_forma_di_errore_unica(client: TestClient) -> None:
    """Anche un 404 di Starlette esce nella forma dichiarata."""
    response = client.get("/api/questa-non-esiste")
    assert response.status_code == 404
    errore = ErrorOut.model_validate(esito(response))
    assert errore.simulation is True
    assert errore.message


def test_metodo_sbagliato_e_405(client: TestClient) -> None:
    """Un POST su una rotta di sola lettura e' 405, non 404."""
    response = client.post("/api/salute", json={})
    assert response.status_code == 405
    assert esito(response)["error"].startswith("http_")


def test_l_istante_senza_fuso_viene_letto_come_utc(client: TestClient) -> None:
    """Un istante ambiguo non deve cambiare risultato fra una macchina e l'altra."""
    body = esito(
        client.post(
            "/api/pianifica",
            json={
                "scenario": {**SMALL_SCENARIO, "instant": "2025-08-06T12:00:00"},
                "include_baseline": False,
                "max_seconds": 1.0,
            },
        )
    )
    letto = leggi_istante(body["scenario"]["timestamp"])
    assert letto == datetime(2025, 8, 6, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Numeri che non esistono e dati fuori area
# --------------------------------------------------------------------------


def test_un_focolaio_fuori_dalla_calabria_e_400(client: TestClient) -> None:
    """Un punto fuori area non deve tornare con un comune calabrese addosso.

    L'arricchimento territoriale assegna a qualunque coordinata il comune
    conosciuto piu' vicino, senza un tetto sulla distanza: un focolaio
    dichiarato a latitudine zero tornava indietro con un comune della provincia
    di Reggio Calabria e una distanza dal costruito di quattromilacinquecento
    chilometri. Il confine della API e' il posto giusto per fermarlo, prima che
    un dato inventato entri nella risposta.
    """
    response = client.post(
        "/api/pianifica",
        json={
            "scenario": {"instant": INSTANT, "fires": [{"lat": 0.0, "lon": 0.0}]},
            "include_baseline": False,
        },
    )
    assert response.status_code == 400
    errore = ErrorOut.model_validate(esito(response))
    assert errore.error == "focolaio_fuori_area"
    assert "Calabria" in errore.message


def test_un_focolaio_dentro_la_calabria_resta_accettato(client: TestClient) -> None:
    """Il controllo d'area non deve rifiutare i punti per cui il sistema esiste."""
    response = client.post(
        "/api/pianifica",
        json={
            "scenario": {"instant": INSTANT, "fires": [{"lat": 39.31, "lon": 16.56}]},
            "include_baseline": False,
            "max_seconds": 1.0,
        },
    )
    assert response.status_code == 200, response.text
    body = esito(response)
    assert body["fires"][0]["provincia"]


def test_un_periodo_vuoto_lo_dice_invece_di_restare_muto(client: TestClient) -> None:
    """Zero focolai puo' voler dire due cose diverse, e la risposta distingue."""
    fires = FiresOut.model_validate(
        esito(client.get("/api/focolai", params={"da": "2025-01-01", "a": "2025-01-02"}))
    )
    assert fires.fires_total == 0
    assert fires.notes, "un elenco vuoto senza spiegazione sembra un guasto"
    assert any("fixture" in nota for nota in fires.notes)


def test_l_elenco_troncato_dei_focolai_lo_dichiara(client: TestClient) -> None:
    """Chi chiede un limite deve sapere quanti focolai ha lasciato fuori."""
    fires = FiresOut.model_validate(
        esito(client.get("/api/focolai", params={"limite": 1}))
    )
    assert fires.fires_total > 1
    assert fires.fires_returned == 1
    assert any("troncato" in nota for nota in fires.notes)


def test_senza_focolai_in_interfaccia_il_cento_per_cento_e_dichiarato_convenzione(
    client: TestClient,
) -> None:
    """La copertura WUI vale 1.0 anche quando non c'era niente da proteggere.

    Il numero resta, perche' un client che fa medie deve continuare a trovarlo;
    accanto ci va scritto che denominatore non ce n'era. Senza quella riga,
    "copertura WUI 100%" su una mappa vuol dire che qualcosa e' stato protetto.
    """
    body = esito(
        client.post(
            "/api/pianifica",
            json={
                "scenario": {
                    "instant": INSTANT,
                    # Focolaio in Sila, lontano dal costruito: non e' in
                    # interfaccia urbano-foresta.
                    "fires": [{"lat": 39.31, "lon": 16.56, "frp_mw": 40.0}],
                },
                "include_baseline": False,
                "max_seconds": 1.0,
            },
        )
    )
    assert body["validation"]["metrics"]["fires_wui_total"] == 0
    assert body["validation"]["metrics"]["wui_coverage_ratio"] == 1.0
    assert any("convenzione" in nota for nota in body["notes"])


def test_uno_scenario_senza_focolai_non_si_spaccia_per_una_misura(
    client: TestClient,
) -> None:
    """Gennaio non ha incendi: il piano vuoto e' un risultato, non un guasto."""
    body = esito(
        client.post(
            "/api/pianifica",
            json={
                "scenario": {"instant": "2025-01-15T12:00:00Z"},
                "include_baseline": False,
                "max_seconds": 1.0,
            },
        )
    )
    assert body["scenario"]["fires_total"] == 0
    assert any("non un guasto" in nota for nota in body["notes"])
    assert any("convenzioni" in nota for nota in body["notes"])


# --------------------------------------------------------------------------
# Indice delle rigiocate
# --------------------------------------------------------------------------


def test_le_finestre_servite_si_contano_a_parte(
    client: TestClient, replay_file: Path
) -> None:
    """Chi ha chiesto un tetto deve poter sapere quanto sta guardando."""
    replay = ReplayOut.model_validate(
        esito(client.get("/api/rigiocata", params={"max_finestre": 1}))
    )
    assert replay.windows_count == 2
    assert replay.windows_returned == 1
    solo_totali = ReplayOut.model_validate(
        esito(client.get("/api/rigiocata", params={"includi_finestre": "false"}))
    )
    assert solo_totali.windows_returned == 0


def test_dell_indice_si_dichiara_solo_cio_che_il_nome_dice(
    client: TestClient, replay_file: Path, replay_dir: Path
) -> None:
    """Per le rigiocate non aperte l'indice non inventa il produttore.

    Il nome del file porta l'etichetta e l'intervallo e quelli si leggono
    davvero; chi ha pianificato no, e resta nullo invece di ereditare
    l'etichetta, che e' un'altra cosa.
    """
    altra = replay_dir / "replay_baseline_nearest_20250801T0000_20250802T0000.json"
    altra.write_text("{}", encoding="utf-8")
    try:
        # Si chiede per nome: senza, l'endpoint serve la piu' recente, che qui
        # sarebbe il file appena scritto, e il test misurerebbe l'ordinamento
        # invece dell'indice.
        replay = ReplayOut.model_validate(
            esito(client.get("/api/rigiocata", params={"nome": replay_file.name}))
        )
        per_nome = {entry.name: entry for entry in replay.available}
        servita = per_nome[replay_file.name]
        assert servita.producer == "cpsat"
        non_aperta = per_nome[altra.name]
        assert non_aperta.label == "baseline_nearest"
        assert non_aperta.producer is None
        assert non_aperta.windows is None
        assert non_aperta.start == "2025-08-01T00:00:00+00:00"
        assert non_aperta.end == "2025-08-02T00:00:00+00:00"
    finally:
        altra.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Cache dei rilevamenti
# --------------------------------------------------------------------------


def test_senza_chiave_firms_la_cache_non_scade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le fixture sono file fermi: farle scadere sarebbe rileggerle per niente."""
    monkeypatch.setattr(routes.config, "FIRMS_MAP_KEY", "", raising=False)
    primo = routes._freshness_bucket()
    monkeypatch.setattr(routes.time, "time", lambda: 10_000_000.0)
    assert routes._freshness_bucket() == primo == 0


def test_con_la_chiave_firms_la_cache_scade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con una sorgente viva una cache eterna servirebbe i dati della prima volta."""
    monkeypatch.setattr(routes.config, "FIRMS_MAP_KEY", "finta", raising=False)
    monkeypatch.setattr(routes.time, "time", lambda: 1_000.0)
    primo = routes._freshness_bucket()
    monkeypatch.setattr(routes.time, "time", lambda: 1_000.0 + routes.FIRMS_CACHE_TTL_SECONDS * 2)
    assert routes._freshness_bucket() > primo


def test_troppe_pianificazioni_insieme_diventano_un_503(client: TestClient) -> None:
    """Meglio un rifiuto esplicito di un piano peggiore che non lo dichiara.

    Il tempo di CP-SAT e' tempo di orologio: due modelli sullo stesso
    processore tornano dopo lo stesso tetto con soluzioni piu' scarse, e nella
    risposta non compare nulla che lo dica. Qui i posti vengono occupati a mano
    e si verifica che la richiesta in eccesso riceva un codice, non un piano.
    """
    occupati = [
        routes._solver_slots.acquire(timeout=1)
        for _ in range(routes.MAX_CONCURRENT_SOLVES)
    ]
    assert all(occupati)
    try:
        response = client.post(
            "/api/pianifica",
            json={"scenario": SMALL_SCENARIO, "include_baseline": False},
        )
        assert response.status_code == 503
        errore = ErrorOut.model_validate(esito(response))
        assert errore.error == "risolutore_occupato"
        assert errore.simulation is True
    finally:
        for _ in occupati:
            routes._solver_slots.release()


def test_i_posti_del_risolutore_si_liberano_dopo_la_richiesta(
    client: TestClient,
) -> None:
    """Un posto non restituito trasformerebbe il servizio in un muro dopo N chiamate."""
    response = client.post(
        "/api/pianifica",
        json={"scenario": SMALL_SCENARIO, "include_baseline": False, "max_seconds": 1.0},
    )
    assert response.status_code == 200, response.text
    liberi = []
    try:
        for _ in range(routes.MAX_CONCURRENT_SOLVES):
            liberi.append(routes._solver_slots.acquire(timeout=1))
        assert all(liberi)
    finally:
        for preso in liberi:
            if preso:
                routes._solver_slots.release()
