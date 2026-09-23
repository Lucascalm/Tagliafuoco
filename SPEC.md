# Tagliafuoco — specifica tecnica condivisa

Documento di riferimento per chiunque scriva codice in questo repository.
Va letto prima di toccare qualsiasi file.

---

## 1. Cosa fa il sistema

Tagliafuoco e' un **simulatore di supporto alle decisioni** per l'impiego dei
mezzi antincendio boschivo in Calabria. Non e' uno strumento operativo e non
va usato per dirigere interventi reali.

Il flusso e' questo:

```
NASA FIRMS (o fixture locale)
        |
        v
  normalizzazione rilevamenti
        |
        v
  clustering spaziotemporale  -->  arricchimento territoriale
        |                            (comune, copertura, WUI, quota)
        v
     Scenario ------------------------------+
        ^                                   |
        |                                   v
ordine del coordinatore (testo)        modello CP-SAT
        |                                   |
        v                                   v
  traduttore LLM  --> ConstraintSet -->   Piano
        |                                   |
   (fallback deterministico)                v
                                    validatore indipendente
                                            |
                              +-------------+-------------+
                              v                           v
                         piano accettato            piano respinto
                                                    (con diagnostica)
```

## 2. Il principio di progetto

**Il modello linguistico non decide mai.** Traduce, e basta.

Nella letteratura corrente il modello linguistico viene spesso messo a fare il
comandante delle operazioni, oppure a stimare quante risorse servono. Qui no.
Qui il suo unico output ammesso e' un `ConstraintSet` tipizzato, definito in
`src/tagliafuoco/nlu/schema.py`. Non emette assegnazioni, non emette codice,
non emette numeri fuori dagli intervalli dichiarati.

La ragione e' misurabile: quando un modello linguistico deve formulare da solo
un modello di ottimizzazione, l'accuratezza regge sugli esercizi da manuale e
crolla sui problemi realistici (la mediana riportata passa da circa il 94 per
cento a circa il 22 per cento). Per questo il modello matematico e' scritto a
mano una volta sola, e al modello linguistico resta il compito piu' stretto di
riconoscere quale vincolo gia' implementato l'ordine sta chiedendo.

**Il validatore e' indipendente.** Non importa nulla da `optimize/`. Ricalcola
da zero i tempi di volo, i cicli d'acqua, l'autonomia e i turni. Se il solver
ha un errore di modellazione, il validatore deve poterlo scoprire: per questo
il codice non si condivide, nemmeno per le formule.

## 3. Contratti gia' scritti: non modificarli senza dirlo

Due file sono il contratto del progetto e sono gia' presenti e testati:

- `src/tagliafuoco/domain/models.py` — entita' di dominio (dataclass)
- `src/tagliafuoco/nlu/schema.py` — schema dei vincoli (modelli Pydantic v2)

Chiunque lavori su un sottosistema **importa da qui e non ridefinisce niente**.
Se ti serve un campo che non c'e', aggiungilo con valore di default (per non
rompere gli altri) e annotalo nel tuo report finale.

Entita' principali: `GeoPoint`, `BoundingBox`, `Detection`, `FireCluster`,
`WaterSource`, `Base`, `Vehicle`, `Assignment`, `PlanMetrics`, `Plan`,
`Scenario`. Enum: `VehicleKind`, `VehicleStatus`, `Domain`, `WaterSourceKind`,
`LandCover`, `Severity`.

Vincoli: `ConstraintSet` contiene una lista di `AnyConstraint`, unione
discriminata sul campo `kind` fra `PrioritizeZone`, `ReserveVehicle`,
`ExcludeVehicle`, `ForceAssignment`, `ForbidAssignment`, `MaxResponseTime`,
`MinVehiclesOnFire`, `VehicleTypeQuota`, `TimeWindow`, `ObjectiveWeight`,
`ProtectInfrastructure`, `Unsupported`.

## 4. Ambiente

- Python **3.10.4**, interprete del progetto: `.venv/Scripts/python.exe`
  (su Windows; da Git Bash si invoca `./.venv/Scripts/python.exe`).
- `ortools==9.11.4210`. **Non aggiornare**: la 9.15 va in segmentation fault
  su questa macchina durante `Solve()`.
- Altre dipendenze installate: pydantic 2.13, fastapi, uvicorn, httpx, pandas,
  numpy 2.2, scikit-learn 1.7, pytest, typer, rich, anthropic, python-dotenv.
- Il codice sta in `src/`, quindi i test e gli script usano il layout
  `src` (il pacchetto e' installabile in modalita' editable, ma per semplicita'
  i test aggiungono `src` a `sys.path` tramite `tests/conftest.py`).

### Regola non negoziabile sulle chiavi

Il progetto deve **girare interamente senza chiavi API**. Chi clona il repo
deve poter lanciare tutto, test compresi, senza `ANTHROPIC_API_KEY` e senza
`FIRMS_MAP_KEY`. Le chiavi abilitano funzioni aggiuntive (dati satellitari
freschi, traduzione con modello linguistico reale), non sono un prerequisito.
Concretamente:

- `ingest/firms_client.py` legge da `data/fixtures/` quando la chiave manca.
- `nlu/translator.py` ricade su `nlu/rule_translator.py` quando la chiave manca.
- Nessun test unitario puo' richiedere rete. I test che la richiedono vanno
  marcati `@pytest.mark.network` e sono esclusi di default.

## 5. Mappa di proprieta' dei file

Ogni sottosistema ha un proprietario unico. **Non scrivere fuori dalla tua
colonna.** Se ti serve una modifica altrove, scrivila nel report finale.

| Sottosistema | File di cui sei proprietario |
|---|---|
| Dati statici | `data/static/*.json`, `scripts/genera_dati_statici.py` |
| Ingest | `src/tagliafuoco/ingest/*`, `data/fixtures/*`, `tests/test_ingest.py` |
| NLU | `src/tagliafuoco/nlu/translator.py`, `rule_translator.py`, `prompts/*`, `resolver.py`, `tests/test_nlu.py` |
| Optimize | `src/tagliafuoco/optimize/*`, `tests/test_optimize.py` |
| Validate | `src/tagliafuoco/validate/*`, `tests/test_validate.py` |
| Baseline + Sim | `src/tagliafuoco/baseline/*`, `src/tagliafuoco/sim/*`, `tests/test_sim.py` |
| Eval | `src/tagliafuoco/eval/*`, `benchmarks/*`, `tests/test_eval.py` |
| API + CLI | `src/tagliafuoco/api/*`, `src/tagliafuoco/cli/*`, `tests/test_api.py` |
| Web | `web/*` |
| Config | `src/tagliafuoco/config.py`, `pyproject.toml`, `.env.example`, `.gitignore` |
| Docs | `docs/*`, `README.md` |

`src/tagliafuoco/domain/models.py` e `src/tagliafuoco/nlu/schema.py` sono di
**nessuno**: si leggono, non si riscrivono.

## 6. Modello fisico di riferimento

Queste sono le formule che `optimize/` e `validate/` devono ottenere, ognuno
per conto suo. Sono scritte qui perche' entrambi convergano sulla stessa
semantica, non perche' condividano il codice.

### Distanza

Haversine su sfera di raggio 6371 km per i mezzi aerei (volano in linea
retta). Per i mezzi terrestri: haversine moltiplicata per un **fattore di
tortuosita' stradale** di 1.45, che e' il valore che la letteratura sui
trasporti usa per territorio montano. La Calabria e' montuosa: Sila,
Aspromonte e Pollino rendono il rapporto strada/linea d'aria molto peggiore
della pianura.

### Tempo di ciclo di un mezzo aereo

```
t_andata      = d(base, fuoco) / v_crociera * 60
t_ciclo_acqua = d(fuoco, sorgente) * 2 / v_crociera * 60 + t_pescaggio + t_sgancio
t_missione    = t_setup + t_andata + n_lanci * t_ciclo_acqua + t_ritorno
```

Vincolo di autonomia: `t_missione <= endurance_minutes`.
Vincolo di turno: somma delle missioni del giorno `<= max_shift_minutes`.

### Che cosa succede fra due missioni dello stesso mezzo

Questa regola era implicita e ha prodotto due letture diverse in due moduli
diversi, quindi ora e' scritta per esteso.

**Una missione termina con il rientro alla base di partenza.** Il termine
`t_ritorno` nella formula sopra non e' facoltativo: al minuto `release_minute`
il mezzo si trova in base, non sul focolaio.

Ne discendono tre conseguenze, che valgono per il risolutore, per il validatore
e per le baseline allo stesso modo.

1. Fra due missioni consecutive dello stesso mezzo il trasferimento si misura
   **dalla base al focolaio successivo**, non da un focolaio all'altro. E' gia'
   contato dentro `t_andata` della seconda missione, quindi non va aggiunto due
   volte.
2. Prima di ripartire il mezzo consuma il tempo di riattrezzamento della sua
   base, `Base.turnaround_minutes` (rifornimento di carburante, cambio
   equipaggio, controlli). Quindi:

   ```
   departure(missione k+1) >= release(missione k) + turnaround_minutes
   ```

3. Le missioni dello stesso mezzo non si sovrappongono mai. Nel risolutore
   questo si ottiene con `NoOverlap` su variabili intervallo opzionali; nel
   validatore si verifica confrontando gli intervalli a coppie.

Il validatore NON deve pretendere il tempo di volo diretto fra due focolai
consecutivi: quella lettura contraddice la formula e produce falsi allarmi.

### Dove sta il tempo di uscita dalla base

Seconda regola resa esplicita per lo stesso motivo della precedente: era
implicita, e due moduli l'avevano letta in modo diverso.

`setup_minutes` (decollo, uscita dal distaccamento, controlli prima della
partenza) e' consumato **dentro** la missione, fra il minuto di partenza e
l'inizio del trasferimento. Quindi:

```
arrival_minute = departure_minute + setup_minutes + t_andata
```

`departure_minute` e' il momento in cui il mezzo riceve l'ordine e comincia a
muoversi, non il momento in cui stacca da terra. Questo e' coerente con la
formula di `t_missione`, che include `t_setup`, e con la definizione dei
minuti-mezzo, contati dalla partenza al rientro.

Ne segue che il limite inferiore sul tempo di arrivo, quello che il validatore
usa per dire "non ci si poteva arrivare prima", deve comprendere
`setup_minutes`. Ometterlo produce un limite piu' basso del vero su ogni
assegnazione di ogni piano, e quindi un avviso sistematico sul tempo medio di
risposta: e' il modo tipico in cui un controllo automatico diventa rumore e
qualcuno lo spegne.

### Come si confronta una metrica ricalcolata con una dichiarata

Terza regola di confine, che discende dalle due precedenti.

Alcune grandezze che il validatore ricalcola sono **limiti**, non stime: il
tempo di risposta ricostruito e' il minimo fisico, cioe' un limite inferiore, e
i litri erogabili sono un massimo, cioe' un limite superiore. Confrontare un
limite con il valore dichiarato usando una tolleranza simmetrica e' un errore di
categoria: un limite inferiore piu' basso del dichiarato non e' un disaccordo, e'
la relazione che deve valere.

Quindi il confronto su queste grandezze e' **a senso unico**. Sul tempo di
risposta l'unica accusa legittima e' `dichiarato < ricalcolato - tolleranza`,
cioe' un piano che si dichiara piu' veloce di quanto la fisica permetta. Sui
litri e' `dichiarato > ricalcolato + tolleranza`. Le grandezze che sono invece
conteggi esatti (focolai coperti, mezzi impiegati) restano a confronto
simmetrico.

### Fabbisogno d'acqua di un focolaio

Il fabbisogno cresce con l'area e con l'intensita'. Formula di riferimento:

```
litri = area_km2 * 100_000 * coeff_copertura * coeff_intensita
coeff_copertura : conifere 1.4, macchia 1.2, misto 1.0, latifoglie 0.9,
                  prateria 0.6, agricolo 0.5
coeff_intensita = 1 + min(1.0, max_frp_mw / 200)
```

La costante 100.000 litri per km quadrato e' una semplificazione dichiarata:
nella realta' dipende da carico di combustibile, umidita' e vento. E' documentata
in `docs/` e va trattata come parametro di taratura, non come verita' fisica.

### Priorita' di base di un focolaio

```
p = 0.35 * norm(frp) + 0.25 * f_wui + 0.20 * norm(area)
  + 0.10 * f_copertura + 0.10 * f_area_protetta
```

dove `f_wui` vale 1.0 sotto i 2 km dal costruito e decade linearmente fino a
0 a 10 km.

### Convenzioni sulle metriche

Anche queste erano implicite e vanno fissate, perche' moduli diversi le
calcolano e devono ottenere lo stesso numero.

**Tempo di risposta.** Si misura per FOCOLAIO, non per assegnazione, e vale il
tempo di arrivo del PRIMO mezzo che lo raggiunge. E' cio' che conta
operativamente: il secondo mezzo che arriva dopo un'ora non peggiora la risposta,
la rinforza. La media e il novantesimo percentile si calcolano su quell'insieme,
che ha un elemento per focolaio servito.

**Percentile.** Metodo del rango piu' vicino, dichiarato. Con una sola
osservazione il percentile coincide con quella osservazione; con nessuna il
valore e' zero e va distinto dal caso "risposta istantanea" in chi legge.

**Copertura.** Un focolaio e' coperto quando i litri erogati raggiungono il suo
fabbisogno. Se il fabbisogno e' nullo (focolaio non arricchito) basta che un
mezzo intervenga.

**Litri erogati.** Somma di `Assignment.water_delivered_liters` su tutte le
assegnazioni del piano, senza taglio al fabbisogno. Il taglio al fabbisogno
riguarda la funzione obiettivo, non la metrica: se un piano versa il doppio del
necessario, la metrica lo deve mostrare.

**Minuti-mezzo.** Somma di `Assignment.engaged_minutes`, cioe' dal momento della
partenza a quello del rientro. Comprende quindi il volo di ritorno.

**Metriche ricalcolate dal validatore.** Il validatore ricalcola le stesse
grandezze escludendo le assegnazioni che ha giudicato non eseguibili. Questa
esclusione e' voluta (serve a dire quanto del piano regge davvero) ma va tenuta
presente quando si confrontano metriche dichiarate e ricalcolate: uno scarto puo'
significare che il piano dichiara male, oppure che il validatore ha bloccato
un'assegnazione. Il resoconto deve distinguere i due casi.

## 7. Come si valuta

Tre esperimenti, tutti riproducibili con un comando e senza rete.

1. **Rigiocata della stagione 2025.** Si scorre la stagione con finestre di 3
   ore. A ogni finestra si costruisce uno `Scenario` e si producono due piani:
   quello di Tagliafuoco e quello della regola banale "manda il mezzo libero
   piu' vicino". Si confrontano copertura, copertura dei focolai in interfaccia
   urbano-foresta, tempo di risposta medio e al novantesimo percentile, litri
   erogati sul fabbisogno, ore-mezzo spese.

2. **Accuratezza della traduzione.** Un corpus di ordini in italiano con il
   `ConstraintSet` atteso annotato a mano. Metriche: corrispondenza esatta del
   set, precisione e richiamo per tipo di vincolo, tasso di allucinazione
   (vincoli emessi che non corrispondono a nulla nell'ordine), tasso di
   astensione corretta (`UNSUPPORTED` emesso quando e' giusto emetterlo).

3. **Ablazione.** Solver con e senza vincoli tradotti; pipeline con e senza
   validatore. Serve a mostrare cosa aggiunge ciascun pezzo.

Il confronto con la baseline va fatto **sugli stessi scenari e con lo stesso
seed**. Qualsiasi non determinismo va eliminato: il solver gira con
`random_seed` fissato e `num_workers=1` nelle valutazioni.

## 8. Stile del codice

- Italiano nei docstring, nei commenti e nei messaggi all'utente. Inglese nei
  nomi di funzioni, variabili e classi. E' la convenzione gia' usata nei due
  file di contratto.
- Niente em dash nel testo. Virgole, punti, parentesi.
- Ogni modulo comincia con un docstring che spiega **perche'** esiste, non
  solo cosa fa.
- Type hints ovunque. `from __future__ import annotations` in testa.
- Nessuna dipendenza nuova senza dichiararla nel report finale.
- I test usano pytest e devono passare con
  `.venv/Scripts/python.exe -m pytest -q`.

## 9. Tono della documentazione

La documentazione e' scritta per chi arriva al progetto senza conoscere
nessuno dei campi che tocca, e vuole capirlo davvero invece di limitarsi a
eseguirlo. Quindi spiega la teoria, non solo l'uso: perche' CP-SAT e non un risolutore lineare,
cosa fa davvero la propagazione dei vincoli, perche' DBSCAN e non k-means,
cosa misura la FRP, perche' un validatore indipendente e' diverso da un test.

Niente marketing, niente numeri inventati, niente elenchi con la spunta.
Quando un numero e' una taratura arbitraria, va detto che lo e'.
