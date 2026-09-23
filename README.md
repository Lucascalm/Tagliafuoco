# Tagliafuoco

Simulatore di supporto alle decisioni per l'impiego dei mezzi antincendio
boschivo in Calabria. I focolai attivi arrivano dai satelliti, il coordinatore
detta gli ordini a voce, un modello linguistico li traduce in vincoli tipizzati,
un risolutore a vincoli assegna mezzi e squadre, e un validatore scritto a parte
ricalcola tempi e rifornimenti d'acqua e scarta i piani impossibili.

**Questo e' un simulatore a scopo di studio. Non e' uno strumento operativo, non
e' certificato e non va usato per dirigere interventi reali.**

![La demo: mappa della Calabria con i focolai della stagione, i mezzi in
movimento e il registro operativo che mostra ordine, vincoli estratti, piano e
verdetto del validatore](docs/immagini/demo-diurna.png)

La colonna a destra segue l'ordine in cui accadono le cose: l'ordine del
coordinatore, i vincoli che ne sono stati estratti con il frammento di frase da
cui viene ciascuno, il piano, e il verdetto del validatore indipendente. La
barra in basso porta l'istogramma dei rilevamenti della stagione, cosi' il picco
di agosto si vede prima ancora di far partire il timelapse.

---

## Perche' esiste

Al 15 settembre 2025 in Calabria risultavano percorsi dal fuoco circa 143 km
quadrati. Nel 2026, fino a giugno, la Calabria era la seconda regione italiana
per superficie forestale colpita, con circa il 23 per cento del totale
nazionale. Sono le due cifre da cui parte il progetto.

Il problema operativo e' semplice da enunciare e difficile da risolvere: in un
pomeriggio di agosto ci sono piu' focolai che mezzi, i mezzi hanno autonomie e
capacita' diverse, l'acqua sta in punti precisi del territorio, e qualcuno deve
decidere chi va dove. Oggi quella decisione si prende a voce, guardando una
mappa.

## Il punto che distingue il progetto

Il modello linguistico non decide mai. Traduce, e basta.

Nella ricerca corrente il modello linguistico viene messo a fare il comandante
delle operazioni, oppure a stimare da solo quante risorse servono, oppure a
scrivere il modello di ottimizzazione. Qui no: il suo unico canale di uscita e'
un oggetto tipizzato con dodici tipi di vincolo possibili, intervalli numerici
dichiarati e nessun campo libero. Non puo' emettere un'assegnazione nemmeno
volendo, perche' non esiste un posto dove scriverla.

La ragione e' misurabile. Quando un modello linguistico passa dai problemi di
ottimizzazione da manuale a quelli realistici, l'accuratezza con cui formula il
modello crolla, e la mediana riportata in letteratura scende da circa il 94 per
cento a circa il 22 per cento. Se la formulazione e' la parte che il modello
sbaglia, allora la formulazione la scrive una persona, una volta, e la si
sottopone a test. Al modello resta il riconoscimento dell'intento, che e' il
compito in cui i modelli linguistici sono davvero bravi.

Ed e' proprio per questo che serve il validatore. Un traduttore ristretto
sbaglia meno, ma sbaglia ancora, e il risolutore a valle puo' avere un errore
di modellazione tutto suo. Il validatore e' un secondo programma che non
condivide una riga con il risolutore: reimplementa la geodesia in una forma
diversa, ricalcola i cicli d'acqua e l'autonomia per conto proprio, e confronta
cio' che il piano dichiara con cio' che risulta a lui. Quando i due non sono
d'accordo, il piano non passa.

### Il validatore ha gia' fatto il suo lavoro

Questo non e' un argomento teorico. Quando tutti i sottosistemi passavano i
propri test, la prima prova della catena intera ha prodotto questo:

```
il mezzo CAN_01 si sposta da F1 a F0 in 0.0 minuti,
ma servono almeno 27.7 minuti (153.7 km a 333 km/h)
```

Un Canadair che si teletrasporta da Reggio alla Sila. Indagando, il
risolutore non aveva un errore: la specifica non diceva dove si trovi un mezzo
fra due missioni, e i due moduli avevano assunto due risposte diverse. Il
risolutore contava il rientro in base dentro la missione, il validatore
pretendeva il volo diretto fra focolai.

Correggere ha richiesto prima di scrivere la regola mancante nella specifica, e
in quel passaggio e' saltato fuori un difetto vero che nessuno dei due moduli
aveva: il tempo di riattrezzamento della base esisteva nei dati e nessuno lo
usava, quindi un mezzo poteva ripartire senza rifornirsi.

Nessun test unitario avrebbe potuto trovarlo, perche' ciascun modulo testava la
propria convenzione e passava. Il racconto completo sta in
[`docs/09-validazione-indipendente.md`](docs/09-validazione-indipendente.md),
paragrafo 6.1.

## Come funziona

```
NASA FIRMS (o fixture locale)
        |
        v
  normalizzazione dei rilevamenti
        |
        v
  clustering spaziotemporale  --->  arricchimento territoriale
        |                           (comune, copertura, WUI, quota)
        v
     Scenario -------------------------------+
        ^                                    |
        |                                    v
ordine del coordinatore                 modello CP-SAT
        |                                    |
        v                                    v
  traduttore  ---> ConstraintSet --->      Piano
        |                                    |
  (fallback a regole                         v
   quando manca la chiave)          validatore indipendente
                                             |
                               +-------------+-------------+
                               v                           v
                        piano accettato             piano respinto
                                                    con diagnostica
```

Ogni riquadro corrisponde a un pacchetto sotto `src/tagliafuoco/`.

## Avvio rapido

Servono Python 3.10 o superiore e circa 400 MB di spazio per le dipendenze.

```bash
git clone <questo-repository>
cd Tagliafuoco

python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev,llm]"     # Windows
# source .venv/bin/activate && pip install -e ".[dev,llm]"  # Linux e macOS

.venv/Scripts/python.exe -m pytest -q
```

Nessuna chiave API e' necessaria. Il progetto gira per intero senza: legge i
rilevamenti dalle fixture locali e traduce gli ordini con il traduttore
deterministico a regole. Le chiavi abilitano funzioni aggiuntive, non sono un
prerequisito.

Per abilitarle, copia `.env.example` in `.env` e riempi quello che ti serve:

- `FIRMS_MAP_KEY`, chiave gratuita di NASA FIRMS, per scaricare rilevamenti
  veri. Si richiede dalla pagina delle chiavi del servizio FIRMS.
- `ANTHROPIC_API_KEY`, per tradurre gli ordini con un modello linguistico
  invece che con le regole.

### Provalo in un comando

```bash
tagliafuoco pianifica --ordine "Prima le zone vicino alle case, l'elicottero resta di riserva, nessuno oltre i quaranta minuti." --istante 2025-08-06T12:00
```

Stampa i vincoli estratti con il frammento di frase da cui viene ciascuno, il
piano prodotto dal risolutore, e il verdetto del validatore indipendente. Il
piano viene mostrato anche quando il validatore lo respinge: un piano respinto
con la diagnostica accanto e' il caso piu' istruttivo.

Gli altri comandi: `scarica`, `focolai`, `traduci`, `confronta`, `rigioca`,
`servizio`. Ognuno ha `--help` in italiano.

### La demo cartografica

```bash
tagliafuoco servizio            # il servizio HTTP su localhost
python -m http.server -d web    # la pagina, in un altro terminale
```

La pagina funziona anche senza il servizio, leggendo i dati precalcolati in
`web/data/`: in quel caso il timelapse della stagione si vede lo stesso e solo
il pannello dell'ordine resta inattivo, dicendolo.

## Come si valuta

Due esperimenti, entrambi riproducibili senza chiavi e senza rete.

```bash
python scripts/valuta_stagione.py --da 2025-08-05 --a 2025-08-07
python scripts/valuta_traduzione.py --solo-regole
```

Il primo rigioca il periodo a finestre di tre ore e confronta il risolutore con
due baseline sugli **stessi** scenari, riportando differenze appaiate con
intervalli di confidenza bootstrap e test di permutazione. Su tre giorni di
agosto 2025 e tredici finestre appaiate, dodici differenze su quattordici hanno
l'intervallo che contiene lo zero: su quelle metriche i pianificatori non si
distinguono, e il resoconto lo scrive invece di nasconderlo. Le due differenze
che si distinguono dicono che il risolutore concentra le risorse invece di
spargerle, spendendo circa ottocento minuti-mezzo in meno e raggiungendo meno
focolai con almeno un mezzo.

Il secondo misura il traduttore sul corpus di cinquanta ordini annotati a mano
in `benchmarks/ordini_vocali.json`, annotati prima di guardare cosa producesse
un traduttore. Il traduttore a regole, che e' il riferimento riproducibile da
chiunque, ottiene: corrispondenza esatta dell'insieme di vincoli nel 54 per
cento dei casi, astensione corretta in tutti e otto i casi in cui astenersi era
la risposta giusta, e astensione a sproposito nel 33 per cento dei casi in cui
avrebbe dovuto tradurre. Con una chiave Anthropic lo stesso comando misura anche
il traduttore con modello linguistico e li confronta: se il modello non batte le
espressioni regolari, la conclusione onesta e' che non serve.

## Cosa contiene il repository

```
src/tagliafuoco/
  domain/      entita' condivise e caricamento dei dati statici
  ingest/      client FIRMS, normalizzazione, clustering, arricchimento
  nlu/         schema dei vincoli, traduttore con modello, traduttore a regole
  optimize/    modello CP-SAT, funzione obiettivo, risolutore
  validate/    fisica indipendente, regole di validazione, resoconto
  baseline/    la regola banale del mezzo libero piu' vicino
  sim/         finestre temporali e rigiocata della stagione
  eval/        metriche, confronti appaiati, benchmark della traduzione
  api/         servizio HTTP per la demo
  cli/         comandi da terminale
data/          dati statici, fixture della stagione, cache dei download
benchmarks/    corpus di 50 ordini vocali con i vincoli attesi
docs/          la teoria: un capitolo per ogni pezzo del sistema
web/           la demo con la mappa della Calabria
tests/         905 test, tutti eseguibili senza chiavi e senza rete
```

Dati statici della simulazione: 14 basi operative, 26 mezzi (Canadair,
elicotteri pesanti, medi e leggeri, moduli AIB, autobotti, squadre a terra), 18
punti d'acqua fra mare, laghi silani e idranti, 156 comuni e 13 aree protette.
Le fixture della stagione contengono 4.875 rilevamenti fra giugno e settembre
2025, che il clustering raggruppa in 508 focolai.

## La teoria

Il progetto e' scritto per essere studiato, non solo eseguito. La cartella
`docs/` contiene sedici documenti, circa 83.000 parole, con un capitolo per
ciascun argomento che serve per capire il codice e per giustificare le scelte
fatte. Ogni capitolo parte da un problema concreto, introduce la teoria che lo
risolve, torna a come il progetto la usa, e si chiude con le domande ricorrenti
e le risposte.

L'indice sta in [`docs/00-indice.md`](docs/00-indice.md).

Se hai poco tempo, i tre capitoli che contengono le risposte che distinguono
questo progetto sono il [05](docs/05-constraint-programming-e-cpsat.md) su come
funziona davvero un risolutore a vincoli, l'[08](docs/08-perche-il-modello-non-decide.md)
sul perche' il modello linguistico non decide, e il
[09](docs/09-validazione-indipendente.md) sul perche' esiste un secondo
programma che dice di no.

## Regole di progetto verificate automaticamente

La regola che il validatore non importa niente dal risolutore e' il cuore del
progetto, e le regole che valgono solo per disciplina decadono. Questa si
controlla con un comando e fallisce la build se viene violata:

```bash
python scripts/verifica_architettura.py
```

Analizza l'albero sintattico di ogni modulo, estrae le importazioni interne e
verifica quattordici divieti di dipendenza fra sottopacchetti piu' la purezza
del dominio. Lo stesso controllo gira dentro la suite, in
`tests/test_architettura.py`.

## Cosa il simulatore non fa

Vale la pena dirlo prima che ci arrivi il lettore da solo.

Non modella la propagazione del fuoco: i focolai non crescono, non si uniscono,
non cambiano direzione col vento. Non modella l'effetto dell'intervento sul
fuoco: un piano che eroga i litri richiesti non spegne niente nella simulazione,
soddisfa un requisito numerico. Non modella il meteo, il comportamento degli
equipaggi, la viabilita' reale, la disponibilita' effettiva dei mezzi.

Di conseguenza la valutazione dice che cosa il sistema avrebbe proposto, non che
cosa sarebbe successo. E' un limite serio ed e' dichiarato per esteso in
[`docs/10-metodologia-di-valutazione.md`](docs/10-metodologia-di-valutazione.md).

I dati statici (basi, mezzi, punti d'acqua) sono plausibili e costruiti a
tavolino, non sono l'inventario reale del servizio antincendio. Le fixture della
stagione 2025 sono generate in modo deterministico e dichiarato: si sostituiscono
con i dati veri di FIRMS una volta ottenuta la chiave, perche' hanno lo stesso
formato e passano dallo stesso parser.

Le costanti di taratura (il fabbisogno d'acqua per chilometro quadrato, il
fattore di tortuosita' stradale, la soglia dell'interfaccia urbano-foresta, i
pesi della priorita') sono scelte, non misure. Stanno tutte in
`src/tagliafuoco/config.py` con accanto la ragione, cosi' chi legge puo'
cambiarle e rieseguire la valutazione per vedere quanto contano.

## Nota sulle dipendenze

`ortools` e' bloccato alla versione 9.11.4210. Non e' pedanteria: la 9.15 va in
segmentation fault durante `Solve()` sulla macchina di sviluppo, e in piu'
fissare il seed del risolutore non basta a rendere riproducibile un risultato se
cambia la versione, perche' cambiano le euristiche interne.

## Licenza

MIT. Vedi [`LICENSE`](LICENSE).
