# Fixture della stagione 2025: dati sintetici, non osservazioni

## Leggere prima di usarli

I file `*_calabria_2025.csv` di questa cartella **non sono dati satellitari**.
Sono generati da `genera_fixture_2025.py` con un generatore pseudocasuale
inizializzato con un seed fisso. Nessuna riga corrisponde a un incendio
realmente avvenuto, nessuna coordinata corrisponde a un focolaio reale, nessuna
data corrisponde a un'allerta reale.

Servono a una cosa sola: permettere a chiunque cloni il repository di lanciare
l'intero progetto, test compresi, **senza la chiave di NASA FIRMS**. E' una
regola di SPEC.md, sezione 4: le chiavi abilitano funzioni aggiuntive, non sono
un prerequisito.

Qualunque numero prodotto dal sistema mentre gira su queste fixture (copertura,
tempi di risposta, litri erogati) e' un numero calcolato su dati generati e non
dice niente sulla stagione 2025 vera. Metterlo in un grafico senza scriverlo
accanto sarebbe disonesto.

## Che forma hanno

Il formato e' quello esatto dell'API per area di NASA FIRMS, colonna per
colonna, un file per prodotto:

| File | Prodotto FIRMS | Strumento | Confidenza |
|---|---|---|---|
| `VIIRS_SNPP_SP_calabria_2025.csv` | `VIIRS_SNPP_SP` | VIIRS, pixel 375 m | categorica (`l`/`n`/`h`) |
| `VIIRS_NOAA20_SP_calabria_2025.csv` | `VIIRS_NOAA20_SP` | VIIRS, pixel 375 m | categorica (`l`/`n`/`h`) |
| `MODIS_SP_calabria_2025.csv` | `MODIS_SP` | MODIS, pixel 1 km | numerica (0-100) |

Il formato identico non e' pignoleria: significa che
`src/tagliafuoco/ingest/normalize.py` e' lo stesso codice sia per le fixture sia
per i dati veri. Se il parser si rompesse sull'archivio FIRMS, si romperebbe
anche qui e i test lo vedrebbero.

Colonne dei file VIIRS:

```
country_id,latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,
satellite,instrument,confidence,version,bright_ti5,frp,daynight
```

Colonne del file MODIS (cambiano le due temperature di brillanza):

```
country_id,latitude,longitude,brightness,scan,track,acq_date,acq_time,
satellite,instrument,confidence,version,bright_t31,frp,daynight
```

Come in FIRMS, `acq_date` e `acq_time` sono in **UTC** e `acq_time` e' un
intero `HHMM` senza zero iniziale: le 00:48 UTC si scrivono `48`.

## Come sono generati

Parametri in `fixture_config.json`, seed compreso.

```
seed corrente: 20250601
stagione:      2025-06-01 -> 2025-09-30
eventi:        420 inneschi, ciascuno visto su uno o piu' passaggi satellitari
righe totali:  circa 5000 rilevamenti sui tre prodotti
```

Lo script usa `random.Random(seed)` e solo la libreria standard: due esecuzioni
con lo stesso seed producono file identici byte per byte, su qualunque macchina
e con qualunque versione di numpy installata.

Le proprieta' statistiche riprodotte, e il motivo per cui ciascuna conta:

- **Stagionalita'.** Luglio e agosto concentrano circa il settanta per cento
  degli inneschi (pesi mensili in `month_weights`), con tre ondate di calore
  che li addensano ulteriormente (`heat_waves`). Serve a non valutare il
  sistema su una stagione piatta: gli incendi arrivano a raffiche, e il valore
  di un pianificatore si vede quando la flotta e' insufficiente, non quando
  avanza.
- **Ciclo giornaliero.** Circa tre rilevamenti diurni ogni uno notturno. Non e'
  imposto: e' la conseguenza di tre cose messe nel modello, cioe' l'innesco
  concentrato nel primo pomeriggio, la probabilita' piu' alta che un passaggio
  diurno veda il fuoco, e il fronte attivo piu' corto di notte.
- **Geografia.** Sila, Aspromonte, Pollino, Serre, marchesato crotonese e
  fascia costiera tirrenica, cioe' dove la serie storica regionale concentra gli
  eventi. Le prime sono ellissi attorno ai massicci, la costa e' una polilinea
  con scostamento verso l'interno, perche' li' gli incendi seguono il versante
  e non un centro.
- **Potenza radiativa a coda lunga.** Distribuzione lognormale: mediana intorno
  ai sei megawatt, massimi oltre i mille sui fronti di testa degli eventi
  maggiori. E' la forma vera della FRP, e conta perche' la priorita' e il
  fabbisogno d'acqua dipendono da quel numero.
- **Molti focolai piccoli, pochi grandi.** Il sessanta per cento degli eventi
  produce due o tre pixel, il due per cento ne produce decine su piu' giorni.
- **Testa e fianchi.** Dentro ogni passaggio il pixel piu' caldo riceve un
  moltiplicatore. E' la testa del fronte, e serve a rendere il centroide pesato
  sulla FRP diverso dalla media semplice, che e' il punto della sezione 3 del
  modulo `clustering.py`.

I punti generati restano sulla terraferma per costruzione (sigma strette
attorno ai massicci, scostamento costiero sempre verso est). Non c'e' una
maschera di terra: e' una semplificazione dichiarata, e un rilevamento in mare
verrebbe comunque scartato piu' avanti solo se uscisse dalla bounding box.

### Rigenerarli

```bash
.venv/Scripts/python.exe data/fixtures/genera_fixture_2025.py
```

Con un altro seed, per controllare che i risultati non dipendano da questo:

```bash
.venv/Scripts/python.exe data/fixtures/genera_fixture_2025.py --seed 7
```

Il comando stampa un riepilogo per file (righe, quota diurna, FRP mediana e
massima, righe per mese): serve a vedere a occhio se la forma e' cambiata.

## Sostituirli con i dati veri

La chiave di FIRMS e' gratuita e si richiede qui:
<https://firms.modaps.eosdis.nasa.gov/api/map_key/>. Arriva per posta
elettronica in pochi minuti.

1. Copia `.env.example` in `.env` e metti la chiave in `FIRMS_MAP_KEY`.
2. Da quel momento `ingest/firms_client.fetch_detections` chiama la rete e non
   guarda piu' questa cartella. Non serve cancellare le fixture: smettono
   semplicemente di essere lette. Le risposte vere finiscono nella cache in
   `data/raw/`, che non e' versionata.
3. Se vuoi tenere i dati veri come fixture permanenti, scaricali e salvali qui
   con lo stesso schema di nome, `<PRODOTTO>_calabria_<anno>.csv`, per esempio
   `VIIRS_SNPP_SP_calabria_2024.csv`. Il client li ritrova da solo: filtra i
   file per prefisso del prodotto e per intervallo di date. **In quel caso
   aggiorna questo README**, perche' smetterebbe di essere vero.

Per scaricare a mano una finestra (la chiave sta nell'URL, quindi non va
incollata in una chat o in un ticket):

```
https://firms.modaps.eosdis.nasa.gov/api/area/csv/LA_TUA_CHIAVE/VIIRS_SNPP_SP/15.55,37.85,17.25,40.20/10/2025-08-01
```

L'ordine e' `lon_min,lat_min,lon_max,lat_max`, poi il numero di giorni (massimo
dieci per richiesta) e poi il primo giorno della finestra.

Una nota sulla scelta del prodotto. I prodotti `_NRT` sono disponibili circa
tre ore dopo l'acquisizione, i prodotti `_SP` (standard processing) arrivano
con qualche giorno di ritardo ma sono rielaborati, con geolocalizzazione
corretta dalle effemeridi definitive. Per rigiocare una stagione passata si
usano gli `_SP`: sono il dato migliore e, soprattutto, non cambiano piu' fra
un'esecuzione e l'altra.
