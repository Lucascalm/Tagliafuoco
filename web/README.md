# La demo cartografica

La mappa della Calabria con i focolai della stagione in timelapse, i mezzi che
si muovono lungo le rotte, e il registro operativo che mostra la catena intera:
ordine del coordinatore, vincoli estratti, piano, verdetto del validatore.

Tagliafuoco e' un simulatore a scopo di studio. Non e' uno strumento operativo,
non e' certificato e non va usato per dirigere interventi reali. La pagina lo
dice in alto, accanto al nome, e non in fondo.

## Che cosa serve

HTML, CSS e JavaScript scritti a mano. Nessun framework, nessun passo di
compilazione, nessun `npm install`. La cartella `web/` si puo' pubblicare
com'e' su qualunque hosting statico.

Da rete arrivano tre cose, tutte facoltative:

- i riquadri della carta, da OpenStreetMap;
- i caratteri Archivo e IBM Plex Mono, da Google Fonts;
- la libreria Leaflet, da cdnjs.

Se manca la rete la pagina non si rompe. Senza i riquadri la mappa resta
utilizzabile con i soli segnaposto sopra un fondo neutro, e lo scrive in un
riquadro sulla carta. Senza Leaflet compare al posto della mappa un riquadro
che spiega cosa e' mancato, e il registro operativo continua a funzionare,
perche' e' l'equivalente testuale della mappa. Senza i caratteri si usano le
alternative di sistema dichiarate nel foglio di stile.

## Primo modo: sito statico

Basta questo, ed e' il modo in cui la demo va pubblicata:

```
.venv/Scripts/python.exe -m http.server 8080 --directory web
```

Poi si apre `http://127.0.0.1:8080/`.

In questo modo funziona tutto tranne il pulsante **Pianifica**: il timelapse
della stagione, le sei finestre gia' calcolate con i loro piani e i loro
verdetti, la barra del tempo, la legenda, il tema notturno. I dati vengono da
`web/data/stagione.json` e `web/data/scenari.json`, precalcolati dalla pipeline
vera.

Il pulsante Pianifica cerca il servizio HTTP sulla stessa origine della pagina.
Su un server statico quel servizio non c'e', quindi la pagina scrive che non
risponde e continua a mostrare le finestre precalcolate. Il tentativo lascia
una riga 404 nella console del browser: e' la sonda che non trova nessuno, non
un guasto della pagina.

### Aprirla con un doppio clic

Funziona anche cosi', senza nessun server. Con lo schema `file:` il browser
rifiuta `fetch` verso un file locale, quindi la pagina ripiega sui file
`web/data/*.js`, che contengono lo stesso contenuto dei JSON assegnato a una
variabile globale. Li genera lo stesso script, dallo stesso dizionario.

## Secondo modo: con il servizio HTTP

Serve per scrivere un ordine nuovo nel campo di testo e vedere la catena
lavorare davvero: traduzione, scenario, risolutore CP-SAT, validatore
indipendente, baseline di confronto. Nessuna chiave API e' necessaria. Senza
`ANTHROPIC_API_KEY` l'ordine viene tradotto dal traduttore deterministico a
regole, e la pagina dichiara quale dei due ha lavorato.

Un terminale per il servizio:

```
.venv/Scripts/python.exe -m uvicorn tagliafuoco.api.app:app --app-dir src --port 8000
```

Un secondo terminale per la pagina:

```
.venv/Scripts/python.exe -m http.server 8080 --directory web
```

Poi si apre la pagina indicando una volta sola dove sta il servizio:

```
http://127.0.0.1:8080/?api=http://127.0.0.1:8000/api
```

L'indirizzo resta memorizzato nel browser, quindi dalla seconda volta basta
`http://127.0.0.1:8080/`. Per dimenticarlo si apre la pagina con `?api=` vuoto.

La porta 8080 non e' scelta a caso. Il servizio ammette per CORS un elenco
chiuso di origini locali (3000, 5173, 8000, 8080, piu' `null` per il doppio
clic), quindi da 8080 il browser lascia passare le chiamate. Servendo la
pagina da un'altra porta il browser le blocca e il pannello scrive che il
servizio non risponde, il che sarebbe vero solo a meta'. Per usare una porta
diversa si dichiara all'avvio del servizio:

```
# Git Bash
TAGLIAFUOCO_CORS_ORIGINS=http://127.0.0.1:9000 ./.venv/Scripts/python.exe -m uvicorn tagliafuoco.api.app:app --app-dir src --port 8000

# PowerShell
$env:TAGLIAFUOCO_CORS_ORIGINS = "http://127.0.0.1:9000"
.venv\Scripts\python.exe -m uvicorn tagliafuoco.api.app:app --app-dir src --port 8000
```

La documentazione interattiva delle rotte sta su
`http://127.0.0.1:8000/api/docs`.

La finestra calcolata al momento non finisce in un pannello a parte: entra
nell'elenco degli esempi come settima finestra, si posiziona sulla barra del
tempo e disegna i suoi focolai sulla mappa come le altre.

## Rigenerare i dati precalcolati

```
.venv/Scripts/python.exe web/data/genera_dati_demo.py
```

Lo script esegue la pipeline vera: rilevamenti dalle fixture, clustering
DBSCAN, arricchimento territoriale, traduzione degli ordini, risolutore,
baseline e validatore. Scrive quattro file in `web/data/`: `stagione.json` e
`scenari.json`, piu' i due `.js` gemelli per il caso del doppio clic.

Impiega circa due minuti, perche' concede venti secondi di risolutore a ogni
finestra. Per una prova rapida:

```
.venv/Scripts/python.exe web/data/genera_dati_demo.py --secondi 5
```

I piani cambiano con il limite di tempo, quindi i file generati con `--secondi`
basso non sono quelli da pubblicare.

Se una finestra produce un piano che il validatore respinge, il file riporta il
piano respinto e le sue violazioni. La demo mostra anche i fallimenti: un
validatore che approvasse sempre non servirebbe a niente.

## Com'e' fatta

```
web/
  index.html        la console: mappa, registro operativo, barra del tempo
  note.html         fonti dei dati, limiti della simulazione, memoria del browser
  DESIGN.md         il piano di design, letto prima di scrivere il codice
  css/console.css   token di colore, tipografia, impaginazione, registro, tempo
  css/mappa.css     segnaposto, rotte, legenda, controlli di Leaflet
  css/note.css      la pagina delle note, che e' un documento e non una console
  js/dati.js        le due sorgenti: file precalcolati e servizio HTTP
  js/mappa.js       i segni sulla carta, l'unica parte che dipende da Leaflet
  js/registro.js    ordine, vincoli, piano, verdetto
  js/tempo.js       istogramma della stagione, scorrevole, play e pausa
  js/app.js         chi parla con chi: l'unico file da leggere per il flusso
  data/genera_dati_demo.py   il precalcolo
  data/*.json       i dati della demo
  data/*.js         gli stessi dati per l'apertura con doppio clic
```

## Come si legge la mappa

La legenda in basso a sinistra e' fissa e non si chiude. In breve: il cerchio
e' un focolaio, il suo raggio cresce con la radice quadrata della potenza
radiante (l'occhio confronta le aree, non i raggi) e l'anello spesso dice che
e' in interfaccia urbano-foresta. Piu' e' trasparente, piu' e' vecchio il
rilevamento. Il quadrato vuoto e' una base, il triangolo un mezzo aereo
orientato secondo la marcia, il quadratino un mezzo terrestre: pieno se in
missione, vuoto se fermo, tratteggiato se tenuto di riserva. La rotta e'
continua sul percorso gia' fatto e tratteggiata su quello previsto.

Due cose vanno sapute prima di guardare il timelapse.

La posizione dei mezzi e' **ricostruita**. Il piano dice quando un mezzo parte,
quando arriva e quando rientra, non dove si trova a meta' strada: quella e'
un'interpolazione lineare lungo la rotta.

La comparsa dei focolai **non** e' ricostruita. Un focolaio compare nel minuto
in cui il satellite lo ha rilevato e non un istante prima.

## Tastiera e lettori di schermo

Il registro operativo a destra e' l'equivalente testuale della mappa: contiene
in forma di testo tutto cio' che la carta disegna. Il primo elemento della
pagina e' un collegamento che ci salta direttamente.

I segnaposto dei focolai si raggiungono con Tab e rispondono a Invio e barra
spaziatrice come al clic. Nessuna informazione e' portata dal solo colore: la
forma, il riempimento e il tratteggio dicono la stessa cosa dei toni.

Con `prefers-reduced-motion` attiva il timelapse non interpola: avanza a scatti
da una finestra di sei ore alla successiva, e la pagina resta pienamente
usabile, perche' lo scorrevole permette comunque di fermarsi su un istante.

## Cosa la pagina memorizza

Due valori in `localStorage`, `tagliafuoco.tema` e `tagliafuoco.servizio`.
Niente cookie, nessuna misurazione, nessun invio a terzi. Il dettaglio sta in
[note.html](note.html), insieme alle fonti dei dati e ai limiti della
simulazione.
