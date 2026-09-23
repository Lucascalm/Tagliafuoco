"""Montaggio del servizio FastAPI: CORS, errori uniformi, documentazione.

Questo file non definisce endpoint: li monta. Sta separato da `routes.py`
perche' le due cose cambiano per ragioni diverse. Le rotte cambiano quando il
sistema guadagna una funzione; il montaggio cambia quando cambia il modo di
servirlo (da dove si accetta il browser, come si presentano gli errori, quale
titolo compare nella documentazione generata). Tenerli insieme significa
toccare il file delle rotte per aggiungere un'origine CORS.

Le tre decisioni prese qui.

**Errori in una forma sola.** Un client deve poter gestire gli errori in un
punto solo del proprio codice. Per questo ogni risposta di errore, che venga da
una rotta, dalla validazione del corpo o da un guasto imprevisto, esce nella
forma di `schemas.ErrorOut`: `error` leggibile da un programma, `message` in
italiano per una persona, `detail` per chi sta sviluppando, piu' i due campi che
ricordano che si tratta di una simulazione. Anche l'errore ricorda che e' una
simulazione, perche' non c'e' motivo di sospendere l'avvertenza proprio quando
qualcosa va storto.

**CORS aperto in locale.** La demo e' una pagina statica aperta dal disco o
servita da un piccolo server su un'altra porta: senza CORS il browser blocca
ogni chiamata e la mappa resta vuota senza spiegazione. Le origini ammesse sono
quelle locali, piu' quelle che si possono aggiungere con
`TAGLIAFUOCO_CORS_ORIGINS`. Non e' una configurazione da esporre su internet, ed
e' scritto qui perche' chi lo facesse lo legga prima.

**Niente lavoro all'avvio.** Il servizio non precarica scenari e non rigioca
stagioni quando parte. Un servizio che impiega un minuto ad accendersi e' un
servizio che in una demo non si accende.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tagliafuoco.api import __version__
from tagliafuoco.api.routes import router
from tagliafuoco.api.schemas import ErrorOut
from tagliafuoco.config import DISCLAIMER
from tagliafuoco.domain.registry import StaticDataError

logger = logging.getLogger(__name__)

#: Origini ammesse per la demo locale. Porte tipiche dei server statici da
#: sviluppo, piu' `null`, che e' l'origine di una pagina aperta con file://.
DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "null",
)

#: Codice per una richiesta sintatticamente valida ma semanticamente no.
#: Starlette ha rinominato la costante da `..._UNPROCESSABLE_ENTITY` a
#: `..._UNPROCESSABLE_CONTENT` e la vecchia emette un avviso di deprecazione:
#: si prende la nuova quando c'e', senza vincolare il progetto a una versione.
HTTP_UNPROCESSABLE = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)

DESCRIPTION = """
Tagliafuoco e' un **simulatore** di supporto alle decisioni per l'impiego dei
mezzi antincendio boschivo in Calabria. Non e' uno strumento operativo, non e'
certificato e non va usato per dirigere interventi reali.

La catena che questa API espone e' sempre la stessa: rilevamenti satellitari
(o fixture locali) -> focolai -> scenario -> ordine in italiano tradotto in
vincoli tipizzati -> piano prodotto da un modello CP-SAT -> validazione fatta
da un programma indipendente -> confronto con una regola banale.

Il modello linguistico non decide mai: traduce l'ordine in un `ConstraintSet`
e si ferma li'. Senza `ANTHROPIC_API_KEY` la traduzione la fa un traduttore
deterministico a regole, e `/api/traduci` lo dichiara nella risposta. Senza
`FIRMS_MAP_KEY` i rilevamenti si leggono dalle fixture locali. Nessuna delle due
chiavi e' un prerequisito.
""".strip()


def _cors_origins() -> list[str]:
    """Origini ammesse: quelle locali piu' quelle dichiarate nell'ambiente."""
    extra = os.getenv("TAGLIAFUOCO_CORS_ORIGINS", "")
    declared = [o.strip() for o in extra.split(",") if o.strip()]
    return list(DEFAULT_CORS_ORIGINS) + declared


def _error_body(
    error: str, message: str, detail: Optional[Any] = None
) -> dict[str, Any]:
    """Corpo di errore nella forma unica, avvertenza compresa."""
    return ErrorOut(error=error, message=message, detail=detail).model_dump()


def create_app() -> FastAPI:
    """Costruisce l'applicazione.

    E' una funzione e non un oggetto di modulo perche' i test ne vogliono
    un'istanza pulita, e perche' un giorno potrebbe servirne una configurata
    diversamente. L'istanza di modulo `app` esiste comunque, perche' uvicorn si
    aspetta di trovare qualcosa da importare.
    """
    application = FastAPI(
        title="Tagliafuoco (simulatore)",
        version=__version__,
        description=DESCRIPTION,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @application.exception_handler(StarletteHTTPException)
    async def _http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """Porta gli errori HTTP nella forma unica.

        Le rotte sollevano `HTTPException` con un dizionario gia' completo; le
        eccezioni sollevate altrove (una rotta inesistente, per esempio) hanno
        un dettaglio testuale e vengono avvolte qui.
        """
        if isinstance(exc.detail, dict) and "message" in exc.detail:
            body = _error_body(
                str(exc.detail.get("error", "errore")),
                str(exc.detail["message"]),
                exc.detail.get("detail"),
            )
        else:
            body = _error_body(
                f"http_{exc.status_code}",
                str(exc.detail) if exc.detail else "Richiesta non soddisfatta.",
            )
        return JSONResponse(status_code=exc.status_code, content=body)

    @application.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """422 quando la richiesta non rispetta lo schema.

        Il dettaglio di Pydantic resta, perche' dice esattamente quale campo e'
        sbagliato, ma davanti ci va una frase in italiano: `detail` e' una lista
        di oggetti annidati, e non e' quello che si mostra a una persona.
        """
        return JSONResponse(
            status_code=HTTP_UNPROCESSABLE,
            content=_error_body(
                "richiesta_non_valida",
                "La richiesta non rispetta lo schema previsto: controlla i "
                "campi segnalati nel dettaglio.",
                exc.errors(),
            ),
        )

    @application.exception_handler(StaticDataError)
    async def _static_data_error(
        request: Request, exc: StaticDataError
    ) -> JSONResponse:
        """503: il servizio e' in piedi ma gli mancano i dati per rispondere.

        Non e' un 500: non si e' rotto niente, manca un file che si rigenera
        con un comando, e il messaggio dice quale.
        """
        logger.warning("dati statici non disponibili: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_error_body(
                "dati_statici_mancanti",
                "I dati statici (basi, mezzi, sorgenti d'acqua, comuni, aree "
                "protette) non sono disponibili: esegui "
                "scripts/genera_dati_statici.py e riavvia il servizio.",
                str(exc),
            ),
        )

    @application.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        """500 con una frase leggibile invece di una pagina di traccia.

        La traccia finisce nel log del servizio, dove serve a chi sviluppa; al
        client va il tipo dell'eccezione, che basta per aprire una segnalazione
        e non espone il contenuto della richiesta.
        """
        logger.exception("errore non gestito su %s %s", request.method, request.url)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(
                "errore_interno",
                "Il servizio ha incontrato un errore non previsto. Il dettaglio "
                "e' nel log del processo.",
                type(exc).__name__,
            ),
        )

    @application.get("/", tags=["servizio"], summary="Presentazione del servizio")
    async def radice() -> dict[str, Any]:
        """Prima cosa che vede chi apre la porta nel browser.

        Dice cosa e' questo servizio, che e' un simulatore, e dove sono la
        documentazione e il controllo di stato.
        """
        return {
            "simulation": True,
            "disclaimer": DISCLAIMER,
            "name": "Tagliafuoco",
            "version": __version__,
            "docs": "/api/docs",
            "health": "/api/salute",
        }

    application.include_router(router)
    return application


#: Istanza usata da uvicorn: `uvicorn tagliafuoco.api.app:app`.
app = create_app()
