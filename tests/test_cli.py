"""Test della riga di comando.

Un adattatore da terminale si rompe in modi che i test degli altri
sottosistemi non vedono, perche' non sono rotture di calcolo: un'opzione
rinominata, un argomento che non si converte piu', una tabella che chiede un
campo che il dominio non espone piu'. Il risolutore continua a funzionare e il
comando muore alla prima riga. Per questo qui si verifica soprattutto una cosa
apparentemente banale, cioe' che ogni comando parta davvero e finisca con
codice zero sul percorso senza chiavi, che e' il percorso con cui chiunque
cloni il repository lo vede la prima volta.

Attorno a quella verifica ce ne sono altre tre.

La prima riguarda le premesse del progetto. L'assenza di una chiave non e' un
errore: il programma lo dice in una riga, spiega che usa le fixture locali e il
traduttore a regole, e prosegue. Se un giorno qualcuno trasformasse quella riga
in un'uscita con errore, il progetto smetterebbe di essere eseguibile da chi lo
clona, e il test qui sotto e' il posto dove ci si accorge.

La seconda riguarda l'onesta' di cio' che si stampa. L'avvertenza che
Tagliafuoco e' un simulatore sta in testa e non in fondo; `traduci` mostra il
frammento letterale dell'ordine da cui ogni vincolo e' stato tratto, perche' e'
l'unico controllo umano previsto su cio' che il traduttore ha capito;
`pianifica` stampa l'esito del validatore anche, e soprattutto, quando il piano
e' respinto.

La terza riguarda gli usi scorretti. Un periodo invertito, una data illeggibile,
una baseline che non esiste devono finire in un'uscita con codice diverso da
zero e un messaggio in italiano, non in una traccia di stack.

Nessun test tocca la rete e nessuno richiede chiavi: una fixture le azzera per
tutta la durata del modulo, cosi' l'esito non cambia fra una macchina che ha un
file .env e una che non ce l'ha.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from typer.testing import CliRunner

from tagliafuoco import config
from tagliafuoco.cli.main import app

#: Giorno dentro la stagione coperta dalle fixture sintetiche.
GIORNO = "2025-08-06"

#: Istante di mezzogiorno dello stesso giorno: i mezzi aerei volano, quindi il
#: piano non e' vuoto per costruzione come sarebbe di notte.
ISTANTE = "2025-08-06T12:00"

#: Ordine che il traduttore a regole sa riconoscere per intero. Serve a
#: verificare i frammenti di origine senza dipendere da un modello linguistico.
ORDINE = "Tieni un elicottero di riserva, prima le zone vicino alle case"

#: Parametri che tengono i comandi lenti sotto i pochi secondi. Il valore dei
#: piani non c'entra: questi test guardano che la catena giri, non quanto bene.
VELOCE = ["--max-secondi", "2", "--max-focolai", "2"]

#: I comandi che leggono dati, con argomenti gia' piccoli. `servizio` non e'
#: qui perche' bloccherebbe la suite su una porta: ha un test suo.
INVOCAZIONI: tuple[tuple[str, list[str]], ...] = (
    ("scarica", ["scarica", "--da", GIORNO, "--a", GIORNO, "--limite", "3"]),
    ("focolai", ["focolai", "--da", GIORNO, "--a", GIORNO, "--limite", "5"]),
    ("traduci", ["traduci", ORDINE]),
    ("pianifica", ["pianifica", "--ordine", ORDINE, "--istante", ISTANTE, *VELOCE]),
    ("confronta", ["confronta", "--istante", ISTANTE, *VELOCE]),
    (
        "rigioca",
        [
            "rigioca",
            "--da",
            GIORNO,
            "--a",
            GIORNO,
            "--max-finestre",
            "1",
            "--max-secondi",
            "2",
            "--no-salva",
        ],
    ),
)

#: Tutti i comandi dichiarati, per la prova che ognuno ha una guida.
COMANDI: tuple[str, ...] = (
    "scarica",
    "focolai",
    "traduci",
    "pianifica",
    "confronta",
    "rigioca",
    "servizio",
)


# --------------------------------------------------------------------------
# Ambiente di prova
# --------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def ambiente_di_prova() -> Iterator[None]:
    """Fissa le due cose che farebbero cambiare esito ai test fra due macchine.

    La prima sono le chiavi. Non basta che la macchina non le abbia: se chi
    esegue i test tiene un file .env con `FIRMS_MAP_KEY`, gli stessi comandi
    proverebbero a chiamare la rete. Qui il percorso senza chiavi e' imposto,
    ed e' anche l'unico che SPEC.md dichiara obbligatorio.

    La seconda e' la larghezza del terminale. Le tabelle di rich tagliano le
    colonne per stare nello spazio disponibile, quindi senza una larghezza
    fissata un'asserzione su una parola stampata passerebbe o fallirebbe a
    seconda della finestra di chi esegue i test.

    L'ambito e' il modulo, non la funzione, perche' anche le fixture di modulo
    che eseguono un comando devono trovarlo gia' preparato.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "FIRMS_MAP_KEY", "", raising=False)
        mp.setattr(config, "ANTHROPIC_API_KEY", "", raising=False)
        mp.setenv("COLUMNS", "200")
        mp.setenv("TERM", "dumb")
        yield


@pytest.fixture
def runner() -> CliRunner:
    """Il CliRunner di Typer: esegue il comando in processo e cattura l'uscita."""
    return CliRunner()


def esegui(runner: CliRunner, argv: list[str]) -> Any:
    """Esegue il comando e, se fallisce in modo imprevisto, lo racconta.

    Senza questo, un'eccezione dentro un comando arriva al test come un
    `exit_code` uguale a 1 e nient'altro, e si perde mezz'ora a capire quale
    riga sia esplosa.
    """
    result = runner.invoke(app, argv)
    if result.exit_code != 0 and result.exception is not None:
        import traceback

        traccia = "".join(
            traceback.format_exception(
                type(result.exception),
                result.exception,
                result.exception.__traceback__,
            )
        )
        result.traccia = traccia  # type: ignore[attr-defined]
    return result


# --------------------------------------------------------------------------
# Guida e intestazione
# --------------------------------------------------------------------------


def test_la_guida_generale_elenca_tutti_i_comandi(runner: CliRunner) -> None:
    """`tagliafuoco --help` deve mostrare la catena intera, non una parte."""
    result = esegui(runner, ["--help"])
    assert result.exit_code == 0
    for comando in COMANDI:
        assert comando in result.output, f"il comando '{comando}' non e' in guida"


def test_la_guida_generale_dice_che_non_e_uno_strumento_operativo(
    runner: CliRunner,
) -> None:
    """Chi legge la guida deve sapere cosa sta per usare prima di usarlo."""
    result = esegui(runner, ["--help"])
    testo = result.output.lower()
    assert "simulatore" in testo
    assert "non e' uno strumento operativo" in testo


@pytest.mark.parametrize("comando", COMANDI)
def test_ogni_comando_ha_una_guida_in_italiano(
    runner: CliRunner, comando: str
) -> None:
    """Un comando senza guida e' un comando che nessuno usera' due volte."""
    result = esegui(runner, [comando, "--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output or "Uso" in result.output
    assert "Options" in result.output or "Opzioni" in result.output
    # La guida di ogni comando e' il suo docstring: se e' vuota, il comando e'
    # stato scritto senza spiegare a che serve.
    assert len(result.output) > 200


def test_l_intestazione_dichiara_la_simulazione_per_prima(
    runner: CliRunner,
) -> None:
    """L'avvertenza sta in testa, non in fondo.

    Una tabella ordinata di mezzi e orari somiglia a un ordine di servizio. La
    riga che nega che lo sia deve essere la prima che si legge.
    """
    result = esegui(runner, ["traduci", ORDINE])
    assert result.exit_code == 0
    prima_riga = result.output.strip().splitlines()[0].lower()
    assert "simulatore" in prima_riga
    assert "non e' uno strumento operativo" in prima_riga


def test_l_assenza_di_chiave_e_una_riga_non_un_errore(runner: CliRunner) -> None:
    """Il progetto gira senza chiavi: dirlo non deve significare fermarsi.

    Il comando deve uscire con zero e spiegare in una riga cosa usa al posto
    delle chiavi mancanti, cioe' le fixture locali e il traduttore a regole.
    """
    result = esegui(runner, ["traduci", ORDINE])
    assert result.exit_code == 0
    testo = result.output
    assert "senza chiavi" in testo.lower()
    assert "fixture" in testo
    assert "traduttore a regole" in testo


def test_silenzioso_toglie_l_intestazione(runner: CliRunner) -> None:
    """Chi incanala l'uscita in un file vuole i dati, non l'avvertenza ripetuta."""
    result = esegui(runner, ["--silenzioso", "traduci", ORDINE])
    assert result.exit_code == 0
    assert "SIMULATORE a scopo di studio" not in result.output
    assert "Traduttore usato" in result.output


# --------------------------------------------------------------------------
# Ogni comando parte
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nome,argv", INVOCAZIONI, ids=[nome for nome, _ in INVOCAZIONI]
)
def test_ogni_comando_esce_con_zero_senza_chiavi(
    runner: CliRunner, nome: str, argv: list[str]
) -> None:
    """La prova di base: il percorso senza chiavi funziona per tutti i comandi.

    E' il test che protegge la premessa del progetto. Se cade, chi clona il
    repository non riesce a eseguire niente, e nessun altro test se ne
    accorgerebbe.
    """
    result = esegui(runner, argv)
    assert result.exit_code == 0, getattr(result, "traccia", result.output)
    assert result.output.strip(), "il comando non ha stampato niente"


@pytest.mark.parametrize(
    "nome,argv", INVOCAZIONI, ids=[nome for nome, _ in INVOCAZIONI]
)
def test_ogni_comando_ricorda_che_e_una_simulazione(
    runner: CliRunner, nome: str, argv: list[str]
) -> None:
    """L'avvertenza non e' una decorazione del comando piu' innocuo."""
    result = esegui(runner, argv)
    assert "SIMULATORE" in result.output


# --------------------------------------------------------------------------
# scarica
# --------------------------------------------------------------------------


def test_scarica_dice_da_dove_vengono_i_rilevamenti(runner: CliRunner) -> None:
    """Senza chiave i dati sono sintetici, e va detto dove si guarda il numero.

    Un conteggio di rilevamenti senza la sua provenienza si scambia per un dato
    osservato, ed e' esattamente lo scambio che questo progetto deve evitare.
    """
    result = esegui(runner, ["scarica", "--da", GIORNO, "--a", GIORNO])
    assert result.exit_code == 0
    assert "rilevamenti" in result.output
    assert "fixture sintetiche locali" in result.output


def test_scarica_esporta_un_json_rileggibile(
    runner: CliRunner, tmp_path: Path
) -> None:
    """L'esportazione serve a lavorare altrove: deve produrre JSON valido."""
    destinazione = tmp_path / "rilevamenti.json"
    result = esegui(
        runner,
        [
            "scarica",
            "--da",
            GIORNO,
            "--a",
            GIORNO,
            "--esporta",
            str(destinazione),
        ],
    )
    assert result.exit_code == 0
    assert destinazione.exists()
    payload = json.loads(destinazione.read_text(encoding="utf-8"))
    assert isinstance(payload, list) and payload
    primo = payload[0]
    assert {"detection_id", "lat", "lon", "acquired_at", "frp_mw"} <= set(primo)


def test_un_periodo_senza_rilevamenti_non_e_un_guasto(runner: CliRunner) -> None:
    """Zero focolai a gennaio e' il risultato giusto, non un errore.

    Le fixture coprono la stagione estiva. Un comando che uscisse con errore su
    un periodo vuoto insegnerebbe a chi prova il progetto che qualcosa si e'
    rotto, quando invece ha appena funzionato.
    """
    result = esegui(runner, ["scarica", "--da", "2025-01-01", "--a", "2025-01-02"])
    assert result.exit_code == 0
    assert "Nessun rilevamento" in result.output


# --------------------------------------------------------------------------
# focolai
# --------------------------------------------------------------------------


def test_focolai_stampa_la_tabella_arricchita(runner: CliRunner) -> None:
    """La tabella dei focolai deve mostrare il contesto territoriale.

    Il raggruppamento da solo produce dei centroidi. Quello che rende un
    focolaio un'unita' di lavoro e' l'arricchimento: comune, copertura del
    suolo, interfaccia urbano-foresta, fabbisogno d'acqua, priorita'.
    """
    result = esegui(runner, ["focolai", "--da", GIORNO, "--a", GIORNO])
    assert result.exit_code == 0
    for intestazione in ("focolaio", "comune", "copertura", "WUI", "acqua m3", "prio"):
        assert intestazione in result.output
    assert "interfaccia urbano-foresta" in result.output


def test_focolai_periodo_invertito_e_un_uso_scorretto(runner: CliRunner) -> None:
    """Un periodo alla rovescia si rifiuta prima di leggere qualunque file."""
    result = esegui(runner, ["focolai", "--da", "2025-08-07", "--a", "2025-08-05"])
    assert result.exit_code != 0
    assert "invertito" in result.output


def test_una_data_illeggibile_dice_quale_formato_serve(runner: CliRunner) -> None:
    """Il messaggio deve dire come si scrive la data, non solo che e' sbagliata."""
    result = esegui(runner, ["focolai", "--da", "6 agosto", "--a", GIORNO])
    assert result.exit_code != 0
    assert "AAAA-MM-GG" in result.output


# --------------------------------------------------------------------------
# traduci
# --------------------------------------------------------------------------


def test_traduci_mostra_i_frammenti_dell_ordine(runner: CliRunner) -> None:
    """La colonna `source_span` e' il punto del comando.

    E' il frammento letterale da cui il vincolo e' stato tratto, e permette al
    coordinatore di smentirlo prima che entri nel solver. Un vincolo senza il
    suo frammento e' un vincolo di cui bisogna fidarsi.
    """
    result = esegui(runner, ["--silenzioso", "traduci", ORDINE])
    assert result.exit_code == 0
    assert "frammento dell'ordine" in result.output
    assert "RESERVE_VEHICLE" in result.output
    assert "elicottero di riserva" in result.output


def test_traduci_dichiara_quale_traduttore_ha_lavorato(runner: CliRunner) -> None:
    """Senza chiave traduce il traduttore a regole, e va scritto.

    E' la differenza fra "un modello linguistico ha letto la frase" e "un
    programma deterministico ha riconosciuto delle espressioni". Chi guarda
    deve sapere quale delle due sta guardando.
    """
    result = esegui(runner, ["--silenzioso", "traduci", ORDINE])
    assert result.exit_code == 0
    assert "Traduttore usato: rules" in result.output


def test_traduci_dichiara_l_astensione(runner: CliRunner) -> None:
    """Un ordine fuori repertorio produce UNSUPPORTED, e il comando lo evidenzia.

    L'astensione e' la risposta corretta, non un fallimento: va mostrata, se no
    chi legge crede che quella parte dell'ordine sia stata applicata.
    """
    result = esegui(
        runner,
        [
            "--silenzioso",
            "traduci",
            "Coordina con la Protezione Civile della Campania e avvisa i sindaci",
        ],
    )
    assert result.exit_code == 0
    assert "UNSUPPORTED" in result.output
    # La frase si accorda al numero ("1 parte ... non e' rappresentabile",
    # "2 parti ... non sono rappresentabili"), quindi si verifica cio' che deve
    # comparire in ogni caso: che quella parte non e' rappresentabile e che non
    # verra' applicata.
    assert "rappresentabil" in result.output
    assert "applicat" in result.output


def test_traduci_con_un_istante_costruisce_lo_scenario(runner: CliRunner) -> None:
    """Con `--istante` l'ordine si traduce avendo sotto gli occhi lo scenario."""
    result = esegui(
        runner,
        ["--silenzioso", "traduci", ORDINE, "--istante", ISTANTE, "--max-focolai", "2"],
    )
    assert result.exit_code == 0, getattr(result, "traccia", result.output)
    assert "Vincoli estratti dall'ordine" in result.output


# --------------------------------------------------------------------------
# pianifica
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def uscita_pianifica(ambiente_di_prova: None) -> str:
    """Una sola esecuzione di `pianifica`, riusata dai test che la leggono.

    Il comando fa girare il risolutore e il validatore: ripeterlo per ogni
    asserzione costerebbe decine di secondi senza verificare niente di piu'.
    """
    result = CliRunner().invoke(
        app,
        ["--silenzioso", "pianifica", "--ordine", ORDINE, "--istante", ISTANTE, *VELOCE],
    )
    assert result.exit_code == 0, result.output
    return result.output


def test_pianifica_stampa_lo_scenario_che_ha_usato(uscita_pianifica: str) -> None:
    """Un piano senza lo scenario da cui viene non si puo' rileggere."""
    assert "Scenario" in uscita_pianifica
    assert "focolai" in uscita_pianifica
    assert "mezzi" in uscita_pianifica


def test_pianifica_stampa_i_vincoli_tradotti(uscita_pianifica: str) -> None:
    """L'ordine passato deve comparire tradotto, non solo applicato."""
    assert "Ordine tradotto da rules" in uscita_pianifica
    assert "RESERVE_VEHICLE" in uscita_pianifica


def test_pianifica_stampa_il_piano_con_i_minuti_relativi(
    uscita_pianifica: str,
) -> None:
    """I minuti sono contati dall'orizzonte, e la tabella lo dichiara.

    Sono la cosa piu' facile da scambiare per orari: una colonna 'arrivo 18'
    letta come le 18 cambia completamente il senso della riga.
    """
    assert "Piano" in uscita_pianifica
    assert "non sono orari" in uscita_pianifica


def test_pianifica_stampa_sempre_l_esito_del_validatore(
    uscita_pianifica: str,
) -> None:
    """La validazione indipendente non e' opzionale nell'uscita del comando.

    E' cio' che distingue questa catena da un risolutore qualunque: il piano
    arriva con il giudizio di un secondo programma che lo ha ricalcolato da
    zero.
    """
    assert "Validatore indipendente" in uscita_pianifica
    assert ("ACCETTATO" in uscita_pianifica) or ("RESPINTO" in uscita_pianifica)


def test_un_piano_respinto_non_e_un_comando_fallito(
    runner: CliRunner, uscita_pianifica: str
) -> None:
    """Il codice di uscita e' zero anche quando il validatore boccia il piano.

    Il piano bocciato e' un risultato, ed e' quello piu' interessante da
    guardare: e' la prova che il validatore non e' decorativo. Chi vuole il
    comportamento opposto in uno script lo chiede con `--esci-con-errore`, e
    questo test verifica le due cose insieme, cosi' non possono divergere.
    """
    respinto = "RESPINTO" in uscita_pianifica

    severo = esegui(
        runner,
        [
            "--silenzioso",
            "pianifica",
            "--ordine",
            ORDINE,
            "--istante",
            ISTANTE,
            *VELOCE,
            "--esci-con-errore",
        ],
    )
    atteso = 1 if respinto else 0
    assert severo.exit_code == atteso, (
        "con --esci-con-errore il codice di uscita deve seguire il verdetto "
        f"del validatore (piano respinto: {respinto})"
    )


def test_pianifica_senza_ordine_funziona(runner: CliRunner) -> None:
    """Senza ordine si ottimizza con i soli pesi di default.

    E' il caso di riferimento dell'ablazione: serve a misurare cosa aggiungono
    i vincoli tradotti, quindi deve restare eseguibile.
    """
    result = esegui(
        runner, ["--silenzioso", "pianifica", "--istante", ISTANTE, *VELOCE]
    )
    assert result.exit_code == 0, getattr(result, "traccia", result.output)
    assert "Nessun ordine" in result.output


def test_pianifica_dichiara_lo_scenario_troncato(runner: CliRunner) -> None:
    """Il tetto sui focolai e' un limite di costo, e va dichiarato.

    Tacere che meta' dei focolai non e' stata nemmeno considerata renderebbe il
    piano incomprensibile e la copertura stampata ingannevole.
    """
    result = esegui(
        runner,
        [
            "--silenzioso",
            "pianifica",
            "--istante",
            ISTANTE,
            "--max-secondi",
            "2",
            "--max-focolai",
            "1",
        ],
    )
    assert result.exit_code == 0, getattr(result, "traccia", result.output)
    assert "troncato" in result.output


# --------------------------------------------------------------------------
# confronta
# --------------------------------------------------------------------------


def test_confronta_su_un_istante_mette_due_colonne(runner: CliRunner) -> None:
    """Il confronto deve mostrare entrambi i pianificatori sulle stesse righe."""
    result = esegui(runner, ["--silenzioso", "confronta", "--istante", ISTANTE, *VELOCE])
    assert result.exit_code == 0, getattr(result, "traccia", result.output)
    assert "cpsat" in result.output
    assert "baseline_nearest" in result.output
    for riga in ("copertura", "risposta media", "ore-mezzo", "piano valido"):
        assert riga in result.output


def test_confronta_dice_che_le_metriche_vengono_dal_validatore(
    runner: CliRunner,
) -> None:
    """Le due colonne vanno misurate dalla stessa fonte, o il confronto e' truccato.

    Confrontare le metriche che un piano dichiara contro quelle che un
    programma esterno ha ricalcolato per l'altro favorirebbe il primo per
    costruzione.
    """
    result = esegui(runner, ["--silenzioso", "confronta", "--istante", ISTANTE, *VELOCE])
    assert "ricalcolate dal validatore" in result.output
    assert "Nessuna colonna dice quale piano e' migliore" in result.output


def test_confronta_su_un_periodo_rigioca_le_finestre(runner: CliRunner) -> None:
    """Sul periodo i due pianificatori rigiocano le stesse finestre."""
    result = esegui(
        runner,
        [
            "--silenzioso",
            "confronta",
            "--da",
            GIORNO,
            "--a",
            GIORNO,
            "--max-finestre",
            "1",
            "--max-secondi",
            "2",
        ],
    )
    assert result.exit_code == 0, getattr(result, "traccia", result.output)
    assert "Rigiocata" in result.output
    assert "finestre" in result.output


def test_confronta_su_un_periodo_vuole_entrambe_le_date(runner: CliRunner) -> None:
    """Mezza richiesta e' un uso scorretto, e il messaggio dice cosa manca."""
    result = esegui(runner, ["confronta", "--da", GIORNO])
    assert result.exit_code != 0
    assert "--da" in result.output and "--a" in result.output


def test_confronta_baseline_sconosciuta_elenca_quelle_disponibili(
    runner: CliRunner,
) -> None:
    """Un nome sbagliato deve portare all'elenco dei nomi giusti."""
    result = esegui(
        runner, ["confronta", "--istante", ISTANTE, "--baseline", "inesistente"]
    )
    assert result.exit_code != 0
    assert "sconosciuta" in result.output
    assert "baseline_nearest" in result.output


# --------------------------------------------------------------------------
# rigioca
# --------------------------------------------------------------------------


def test_rigioca_senza_salvare_non_scrive_niente(
    runner: CliRunner, tmp_path: Path
) -> None:
    """`--no-salva` serve a provare la rigiocata senza sporcare la cartella."""
    result = esegui(
        runner,
        [
            "--silenzioso",
            "rigioca",
            "--da",
            GIORNO,
            "--a",
            GIORNO,
            "--max-finestre",
            "1",
            "--max-secondi",
            "2",
            "--no-salva",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, getattr(result, "traccia", result.output)
    assert list(tmp_path.glob("*.json")) == []
    assert "Rigiocata" in result.output


def test_rigioca_scrive_il_registro_che_la_api_sa_servire(
    runner: CliRunner, tmp_path: Path
) -> None:
    """Il registro della riga di comando e' l'ingresso di `/api/rigiocata`.

    E' il punto in cui i due adattatori si incontrano: uno scrive il file, l'
    altro lo serve al timelapse. Se il formato divergesse, i test dei due
    sottosistemi continuerebbero a passare separatamente e la demo mostrerebbe
    una mappa vuota. Qui si verifica proprio la giunzione, facendo leggere al
    servizio il file appena scritto dal comando.
    """
    from fastapi.testclient import TestClient

    from tagliafuoco.api.app import create_app
    from tagliafuoco.api.routes import Settings, get_settings
    from tagliafuoco.api.schemas import ReplayOut

    result = esegui(
        runner,
        [
            "--silenzioso",
            "rigioca",
            "--da",
            GIORNO,
            "--a",
            GIORNO,
            "--max-finestre",
            "1",
            "--max-secondi",
            "2",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, getattr(result, "traccia", result.output)

    scritti = list(tmp_path.glob("replay_*.json"))
    assert len(scritti) == 1, "la rigiocata deve lasciare un registro solo"
    assert "Registro salvato" in result.output

    api = create_app()
    api.dependency_overrides[get_settings] = lambda: Settings(processed_dir=tmp_path)
    with TestClient(api) as client:
        risposta = client.get("/api/rigiocata")
    assert risposta.status_code == 200
    servita = ReplayOut.model_validate(risposta.json())
    assert servita.name == scritti[0].name
    assert servita.producer == "cpsat"
    assert servita.windows_count >= 1
    assert servita.simulation is True


def test_rigioca_pianificatore_sconosciuto_elenca_quelli_veri(
    runner: CliRunner,
) -> None:
    """Chi sbaglia il nome del pianificatore deve leggere quelli disponibili."""
    result = esegui(
        runner,
        ["rigioca", "--da", GIORNO, "--a", GIORNO, "--pianificatore", "inesistente"],
    )
    assert result.exit_code != 0
    assert "sconosciuto" in result.output
    assert "cpsat" in result.output


# --------------------------------------------------------------------------
# servizio
# --------------------------------------------------------------------------


def test_servizio_avvia_uvicorn_in_locale(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il comando avvia il servizio, e lo fa su 127.0.0.1.

    `uvicorn.run` viene sostituito: far partire davvero un server dentro la
    suite significherebbe occupare una porta e dipendere dalla macchina. Quello
    che va verificato qui e' che il comando arrivi a chiamarlo con i parametri
    giusti, non che uvicorn funzioni.

    L'indirizzo di ascolto conta: il servizio ha CORS aperto verso le origini
    locali e nessuna autenticazione, quindi l'impostazione predefinita deve
    restare la sola interfaccia di loopback.
    """
    import uvicorn

    chiamate: list[dict[str, Any]] = []

    def finto_run(applicazione: str, **kwargs: Any) -> None:
        chiamate.append({"app": applicazione, **kwargs})

    monkeypatch.setattr(uvicorn, "run", finto_run)

    result = esegui(runner, ["servizio", "--porta", "8123"])
    assert result.exit_code == 0, getattr(result, "traccia", result.output)
    assert len(chiamate) == 1
    chiamata = chiamate[0]
    assert chiamata["app"] == "tagliafuoco.api.app:app"
    assert chiamata["host"] == "127.0.0.1"
    assert chiamata["port"] == 8123
    assert "http://127.0.0.1:8123" in result.output
    assert "/api/docs" in result.output


def test_servizio_ricorda_che_la_api_descrive_una_simulazione(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chi avvia il servizio lo legge prima di aprire il browser."""
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    result = esegui(runner, ["servizio"])
    assert result.exit_code == 0
    assert "simulation" in result.output


# --------------------------------------------------------------------------
# Tabelle in un terminale stretto
# --------------------------------------------------------------------------


def disegna(table: Any, larghezza: int) -> str:
    """Disegna una tabella a una larghezza fissata e restituisce il testo.

    `legacy_windows=False` toglie di mezzo la differenza fra la console vecchia
    di Windows e tutte le altre: qui si misura cosa entra nella riga, non con
    quali caratteri e' disegnata la cornice.
    """
    from rich.console import Console

    probe = Console(width=larghezza, record=True, legacy_windows=False)
    probe.print(table)
    return probe.export_text()


@pytest.fixture
def console_stretta(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Sostituisce la console del modulo con una larga 80 colonne.

    Ottanta e' la larghezza predefinita di un terminale e di qualunque uscita
    rediretta su file, cioe' il caso normale, non un caso estremo. Le altre
    prove di questo modulo girano a duecento colonne apposta, per poter
    asserire su parole stampate; qui si verifica il contrario, cioe' che a
    ottanta non si perda nessun numero.
    """
    from rich.console import Console

    from tagliafuoco.cli import main as cli

    stretta = Console(width=80, record=True, legacy_windows=False)
    monkeypatch.setattr(cli, "console", stretta)
    return stretta


def test_a_ottanta_colonne_i_numeri_dei_focolai_restano_interi(
    console_stretta: Any, fire_sila: Any, fire_wui: Any
) -> None:
    """Nessun numero della tabella dei focolai viene accorciato a 80 colonne.

    E' il difetto per cui esiste `build_table`. Prima, a questa larghezza, rich
    accorciava tutte le colonne allo stesso modo e "234.5" diventava un puntino:
    una tabella di numeri senza piu' un numero leggibile. Un nome accorciato
    resta un nome, un numero accorciato e' una bugia.
    """
    from tagliafuoco.cli.main import table_fires

    testo = disegna(table_fires([fire_sila, fire_wui], 10), 80)
    assert "48.0" in testo  # FRP massima del focolaio in Sila
    assert "0.80" in testo  # area in km quadrati
    assert "120" in testo  # fabbisogno in metri cubi
    assert "0.55" in testo and "0.72" in testo  # priorita' di base
    # Il segno di taglio puo' comparire su un nome di comune, che resta
    # riconoscibile; su una cifra no, perche' "234.5" tagliato non dice piu'
    # se valeva duecento o duemila.
    import re

    from tagliafuoco.cli.main import CUT_MARK

    assert re.search(r"[0-9]" + re.escape(CUT_MARK), testo) is None
    assert max(len(riga) for riga in testo.splitlines()) <= 80


def test_le_colonne_tolte_per_larghezza_vengono_dichiarate(
    console_stretta: Any, fire_sila: Any, fire_wui: Any
) -> None:
    """Una tabella che nasconde una colonna deve dire che l'ha nascosta.

    Toglierla in silenzio sarebbe peggio che accorciarla: chi legge non
    avrebbe modo di accorgersi che il dato esiste.
    """
    from tagliafuoco.cli.main import table_fires

    testo = disegna(table_fires([fire_sila, fire_wui], 10), 80)
    assert "Colonne omesse" in testo
    assert "Allarga la finestra" in testo


def test_una_colonna_incomprimibile_ne_fa_togliere_un_altra() -> None:
    """La colonna che non si puo' togliere prenota tutto lo spazio che le serve."""
    from tagliafuoco.cli.main import Column, build_table

    columns = (
        Column("importo", justify="right"),
        Column("nota", droppable=1),
    )
    table = build_table("prova", columns, [["1 234 567 890", "irrilevante"]], width=30)
    testo = disegna(table, 30)
    assert "1 234 567 890" in testo
    assert "irrilevante" not in testo
    assert "Colonne omesse" in testo


def test_i_nomi_troppo_lunghi_si_accorciano_dichiarandolo() -> None:
    """Un nome accorciato porta il segno del taglio, non sparisce e basta."""
    from tagliafuoco.cli.main import CUT_MARK, shorten

    tagliato = shorten("Spezzano della Sila", 16)
    assert len(tagliato) == 16
    assert tagliato.startswith("Spezzano della")
    assert tagliato.endswith(CUT_MARK)
    assert shorten("Cutro", 16) == "Cutro"


# --------------------------------------------------------------------------
# Parametri fuori intervallo
# --------------------------------------------------------------------------


def test_una_finestra_di_zero_ore_non_e_una_traccia_di_stack(
    runner: CliRunner,
) -> None:
    """Il motore rifiuta il parametro; il comando lo racconta in una riga.

    `sim/timeline.py` solleva `TimelineError`, che non discende da
    `ReplayError`: prima sfuggiva alla cattura e arrivava a chi aveva digitato
    il comando come una traccia di stack di trenta righe.
    """
    result = esegui(
        runner,
        ["rigioca", "--da", GIORNO, "--a", GIORNO, "--finestra", "0", "--no-salva"],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "Rigiocata non eseguibile" in result.output or "--finestra" in result.output


def test_un_limite_di_zero_righe_viene_rifiutato(runner: CliRunner) -> None:
    """Una tabella dei primi zero elementi non e' una richiesta sensata."""
    result = esegui(runner, ["focolai", "--da", GIORNO, "--a", GIORNO, "--limite", "0"])
    assert result.exit_code != 0


def test_un_livello_di_log_inesistente_elenca_quelli_veri(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un livello sbagliato non deve far morire uvicorn dentro il logging."""
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    result = esegui(runner, ["servizio", "--livello-log", "verboso"])
    assert result.exit_code != 0
    assert "info" in result.output


def test_ascoltare_fuori_dal_loopback_viene_detto_a_voce_alta(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il servizio non ha autenticazione: esporlo va dichiarato mentre succede."""
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    result = esegui(runner, ["servizio", "--host", "0.0.0.0"])
    assert result.exit_code == 0, getattr(result, "traccia", result.output)
    assert "Attenzione" in result.output
    assert "autenticazione" in result.output


def test_sul_loopback_non_si_stampa_nessun_allarme(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L'avvertenza serve quando serve: sul caso normale sarebbe solo rumore."""
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    result = esegui(runner, ["servizio"])
    assert result.exit_code == 0
    assert "Attenzione" not in result.output


# --------------------------------------------------------------------------
# Numeri che non esistono
# --------------------------------------------------------------------------


def test_un_rapporto_senza_denominatore_si_dichiara_assente() -> None:
    """Zero focolai in interfaccia non vuol dire copertura WUI del cento per cento."""
    from tagliafuoco.cli.main import NOT_AVAILABLE, minutes_text, ratio_text

    assert ratio_text(1.0, 0) == NOT_AVAILABLE
    assert ratio_text(0.5, 4) == "50.0%"
    assert minutes_text(0.0, 0) == NOT_AVAILABLE
    assert minutes_text(12.5, 3) == "12.5"


def test_le_avvertenze_nominano_solo_i_rapporti_davvero_indefiniti() -> None:
    """Le note non devono comparire quando i denominatori ci sono."""
    from tagliafuoco.cli.main import undefined_metrics_notes

    piene = undefined_metrics_notes(
        fires_total=4,
        fires_wui_total=2,
        water_demanded_liters=1000.0,
        responses_counted=3,
    )
    assert piene == []

    vuote = undefined_metrics_notes(
        fires_total=4,
        fires_wui_total=0,
        water_demanded_liters=0.0,
        responses_counted=0,
    )
    assert len(vuote) == 3
    assert any("interfaccia" in nota for nota in vuote)
    assert any("Fabbisogno" in nota for nota in vuote)
    assert any("risposta" in nota for nota in vuote)

    senza_focolai = undefined_metrics_notes(
        fires_total=0,
        fires_wui_total=0,
        water_demanded_liters=0.0,
        responses_counted=0,
    )
    assert len(senza_focolai) == 1


def test_gli_importi_non_si_confondono_con_i_decimali() -> None:
    """Ottantamila euro non deve somigliare a ottanta virgola sei."""
    from tagliafuoco.cli.main import format_eur

    assert format_eur(80693.0) == "80 693"
    assert format_eur(0.0) == "0"


def test_i_messaggi_si_accordano_al_numero() -> None:
    """Un vincolo, due vincoli: chi legge e' una persona."""
    from tagliafuoco.cli.main import count

    assert count(1, "vincolo", "vincoli") == "1 vincolo"
    assert count(0, "vincolo", "vincoli") == "0 vincoli"
    assert count(2, "vincolo", "vincoli") == "2 vincoli"


def test_una_porta_occupata_diventa_una_riga_e_un_codice(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uvicorn chiude il processo da se': il comando aggiunge cosa fare.

    Il caso vero si ottiene occupando la porta, ma un test che apre un socket
    dipende dalla macchina su cui gira. Qui si riproduce cio' che uvicorn fa
    davvero in quel caso, cioe' `sys.exit(1)` dopo aver scritto l'errore nel
    proprio log, e si verifica che il comando non finisca con una traccia ma
    con un consiglio.
    """
    import uvicorn

    def esce(*a: Any, **k: Any) -> None:
        raise SystemExit(1)

    monkeypatch.setattr(uvicorn, "run", esce)
    result = esegui(runner, ["servizio", "--porta", "8123"])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "si e' chiuso con un errore" in result.output
    assert "--porta" in result.output


def test_una_chiusura_pulita_del_servizio_resta_un_successo(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fermare il servizio con Ctrl+C non e' un errore da segnalare."""
    import uvicorn

    def esce_bene(*a: Any, **k: Any) -> None:
        raise SystemExit(0)

    monkeypatch.setattr(uvicorn, "run", esce_bene)
    result = esegui(runner, ["servizio"])
    assert result.exit_code == 0
    assert "si e' chiuso con un errore" not in result.output
