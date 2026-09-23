# L'architettura del software

Perche' i moduli di Tagliafuoco sono divisi cosi', quali dipendenze sono
ammesse fra loro, e quali regole di progetto non si possono violare senza
perdere le proprieta' che rendono il sistema difendibile.

Indice dei paragrafi:
1. La domanda che l'architettura deve risolvere
2. La direzione delle dipendenze
3. I quattro strati
4. La regola dura: il validatore non importa dal risolutore
5. Dataclass e Pydantic: perche' entrambi e dove
6. Il confine tipizzato come dispositivo di sicurezza
7. Dove stanno i numeri e perche' stanno tutti insieme
8. Iniezione delle dipendenze senza framework
9. Il layout src e perche' non e' pedanteria
10. Cosa cambierei se il progetto crescesse
11. Cosa devi saper spiegare
12. Per approfondire

---

## 1. La domanda che l'architettura deve risolvere

Un progetto di questo tipo puo' essere scritto in un file solo. Funzionerebbe.
La ragione per cui non lo e' non e' estetica, e conviene saperla dire in una
frase: **l'architettura serve a rendere verificabili le affermazioni che il
progetto fa su se' stesso.**

Tagliafuoco fa tre affermazioni forti:

1. Il modello linguistico non decide mai, traduce soltanto.
2. Il validatore e' indipendente dal risolutore.
3. Il sistema funziona per intero senza chiavi API.

Nessuna delle tre e' dimostrabile leggendo la documentazione. Tutte e tre
diventano verificabili se l'architettura le rende strutturali. La prima diventa
vera perche' il modello linguistico ha un unico canale di uscita ed e' tipizzato.
La seconda diventa vera perche' esiste un grafo delle importazioni che si puo'
controllare automaticamente. La terza diventa vera perche' esistono due
implementazioni di ogni punto di ingresso esterno, e quella senza chiave e' la
predefinita.

Un'architettura che non regge nessuna affermazione e' solo un modo di
disporre i file.

## 2. La direzione delle dipendenze

La regola piu' importante in qualunque architettura a strati riguarda la
direzione delle frecce, non il numero di cartelle.

In Tagliafuoco le dipendenze puntano tutte verso il centro:

```
      api/  cli/  web/            (adattatori, il bordo esterno)
             |
             v
  ingest/  optimize/  validate/  baseline/  sim/  eval/   (casi d'uso)
             |
             v
        nlu/schema.py                        (il contratto del linguaggio)
             |
             v
     domain/models.py  config.py             (il centro)
```

`domain/models.py` non importa niente dal progetto. Non sa che esiste CP-SAT, non
sa che esiste FIRMS, non sa che esiste un modello linguistico. Contiene le
entita' del dominio e basta: un `Vehicle` e' un mezzo antincendio, non un mezzo
antincendio come lo vede il risolutore.

Questa e' la direzione che raccomandano sia l'architettura esagonale (che la
chiama separazione fra dominio e adattatori) sia l'architettura pulita (che la
chiama regola della dipendenza). Il nome conta poco, la conseguenza conta molto:
puoi sostituire il risolutore, la fonte dei dati o l'interfaccia senza toccare il
dominio, mentre il contrario non e' vero e non deve esserlo.

La verifica e' meccanica. Se in `domain/models.py` compare `from ortools...`, la
regola e' rotta e si vede con un grep. Le regole architetturali che non si
possono controllare con un comando tendono a decadere entro sei mesi.

## 3. I quattro strati

**Il centro: `domain/` e `config.py`.**
Entita' del dominio e costanti di taratura. Nessuna logica applicativa, nessun
effetto collaterale, nessuna dipendenza in uscita. L'unica eccezione e'
`domain/registry.py`, che legge file JSON dal disco: e' un accesso al filesystem,
quindi in senso stretto sta nel bordo, ed e' li' solo perche' spostarlo avrebbe
aggiunto un modulo per due funzioni. La scelta e' consapevole e dichiarata; un
progetto piu' grande la separerebbe.

**Il contratto: `nlu/schema.py`.**
Sta da solo perche' ha un ruolo particolare. Non e' un caso d'uso ne' un'entita':
e' il confine tipizzato fra un componente non affidabile (il modello linguistico)
e tutto il resto. Ci torno nel paragrafo 6.

**I casi d'uso: `ingest/`, `optimize/`, `validate/`, `baseline/`, `sim/`, `eval/`.**
Qui sta la logica. Ogni pacchetto risponde a una domanda sola. `ingest` risponde
a "che cosa sta bruciando adesso". `optimize` risponde a "chi mando dove".
`validate` risponde a "questo piano sta in piedi". `baseline` risponde a "che cosa
succederebbe senza ottimizzatore". `sim` risponde a "come sarebbe andata sulla
stagione 2025". `eval` risponde a "quanto e' meglio, e di quanto sono sicuro".

Un pacchetto che risponde a due domande si divide. Un pacchetto che non risponde a
nessuna si cancella.

**Il bordo: `api/`, `cli/`, `web/`.**
Adattatori. Traducono fra il mondo esterno e i casi d'uso, e non contengono
logica di dominio. Se in `api/` compare una formula di calcolo dei tempi di volo,
e' finita nel posto sbagliato. La prova pratica: devi poter cancellare tutto `api/`
e `cli/` e i test dei casi d'uso devono continuare a passare tutti.

## 4. La regola dura: il validatore non importa dal risolutore

E' l'unica regola del progetto che non ha eccezioni, e merita di stare in un
paragrafo suo perche' e' l'architettura che protegge l'affermazione piu'
importante.

`src/tagliafuoco/validate/` non puo' importare niente da
`src/tagliafuoco/optimize/`. Ne' la geodesia, ne' i tempi di ciclo, ne' una
funzione di utilita' che sembra innocua.

Il motivo si capisce meglio al contrario. Supponi che `validate/physics.py`
importi `optimize/geometry.py` per calcolare le distanze, perche' la funzione
c'e' gia' ed e' testata. Ora il risolutore produce un piano usando quella
funzione, e il validatore controlla il piano usando la stessa funzione. Se la
funzione ha un errore, per esempio scambia latitudine e longitudine in un ramo,
il risolutore produce piani sbagliati e il validatore li approva tutti, con
entusiasmo e coerenza. Il validatore non sta verificando niente: sta confermando
se stesso.

Per questo `validate/physics.py` riscrive la distanza geodetica in una forma
deliberatamente diversa da quella di `optimize/geometry.py`. Non e' duplicazione
per sbadataggine: e' duplicazione voluta, e nel codice sta scritto che lo e',
perche' il prossimo che passa non la "sistemi" fattorizzandola in un modulo
comune.

Cosa puo' importare il validatore: `domain/` (le entita' sono dati condivisi, non
logica), `config.py` (le costanti di taratura sono dati, e devono essere le stesse
per entrambi, altrimenti il disaccordo direbbe solo che hanno letto numeri
diversi), e da `nlu/` il solo schema e il risolutore dei selettori.

Quest'ultimo punto merita onesta': `nlu/resolver.py` e' condiviso fra `optimize` e
`validate`, quindi un errore li' dentro sfuggirebbe a entrambi. E' un compromesso
accettato consapevolmente, perche' risolvere i selettori in due modi diversi
significherebbe rischiare che i due moduli parlino di insiemi di mezzi diversi, e
quel rischio e' peggiore. La cosa giusta da fare e' saperlo e dirlo, non
nasconderlo.

La stessa onesta' vale per il limite di fondo, spiegato per esteso nel capitolo
sulla validazione indipendente: se la specifica e' sbagliata, entrambe le
implementazioni la sbagliano allo stesso modo e il validatore tace.

## 5. Dataclass e Pydantic: perche' entrambi e dove

Il progetto usa `@dataclass` per le entita' di dominio e modelli Pydantic per i
vincoli. Non e' incoerenza, e la domanda "perche' due?" e' una di quelle che
conviene saper rispondere.

Una `dataclass` e' una comodita' sintattica: il decoratore genera `__init__`,
`__repr__` e `__eq__` a partire dalle annotazioni di tipo. Le annotazioni non
vengono controllate a runtime. Se scrivi `Vehicle(cruise_speed_kmh="molto veloce")`
Python non protesta: l'oggetto nasce con una stringa dentro un campo dichiarato
`float`, e l'errore esplode molto piu' avanti, in un punto che non c'entra.

Un modello Pydantic invece valida davvero, a runtime, e converte quando la
conversione e' sicura. `PrioritizeZone(weight_multiplier=15.0)` solleva un errore
immediato, perche' il campo e' dichiarato con `ge=1.0, le=10.0`.

La regola di scelta e' questa: **si paga la validazione dove il dato attraversa un
confine di fiducia.**

Le entita' di dominio le costruisce il nostro codice, da dati nostri, in processi
che testiamo. Validare ogni `GeoPoint` costruito dentro un ciclo di clustering su
migliaia di rilevamenti costerebbe tempo per proteggere da un errore che i test
prendono meglio. Quindi dataclass.

I vincoli arrivano dall'esterno: li genera un modello linguistico, che e' un
componente non affidabile per costruzione. Li' ogni campo va controllato, ogni
intervallo va imposto, ogni campo estraneo va rifiutato. Quindi Pydantic, con
`extra="forbid"` e validatori espliciti.

Una nota tecnica che vale la pena conoscere: Pydantic v2 ha il nucleo di
validazione scritto in Rust, quindi il costo non e' quello che sarebbe stato in
v1. E il dettaglio che rende Pydantic la scelta giusta qui e' un altro: da un
modello Pydantic si ottiene lo schema JSON con `model_json_schema()`, e quello
schema si passa direttamente al modello linguistico come descrizione dello
strumento da chiamare. La stessa definizione serve a descrivere cosa vogliamo e a
controllare cosa e' arrivato. Se fossero due definizioni separate, prima o poi
divergerebbero.

## 6. Il confine tipizzato come dispositivo di sicurezza

Vale la pena guardare `nlu/schema.py` non come un file di modelli ma come un
dispositivo.

Il modello linguistico puo' produrre qualunque sequenza di caratteri. Puo'
allucinare, puo' essere manipolato da un ordine costruito apposta, puo'
fraintendere. La domanda di progetto non e' "come impedisco al modello di
sbagliare" (non si puo'), ma "che cosa puo' fare, al massimo, il suo output?".

La risposta e' scritta nei tipi. Lo spazio di tutto cio' che il modello puo'
provocare e' esattamente: dodici tipi di vincolo, ciascuno con campi di tipo
dichiarato, enumerazioni chiuse e intervalli numerici limitati, piu' un tipo
`UNSUPPORTED` che non fa niente. Non esiste un campo dove scrivere
un'assegnazione. Non esiste un campo dove scrivere codice. Non esiste un campo di
testo libero che venga interpretato.

Questo si chiama **riduzione della superficie d'attacco**, ed e' lo stesso
principio per cui non si costruisce una query SQL concatenando stringhe. Il caso
B047 del corpus di valutazione e' proprio questo: un ordine che chiede al
traduttore di ignorare le istruzioni e produrre un piano. La risposta corretta e'
astenersi, ma il punto architetturale e' che anche un traduttore che ci cascasse
non avrebbe modo di produrre un piano, perche' il canale non esiste.

Il secondo lavoro dello schema e' la tracciabilita'. Ogni vincolo porta
`source_span`, il frammento letterale dell'ordine da cui e' stato tratto, e
`confidence`. Il coordinatore vede dodici righe con accanto la frase che le ha
generate, e puo' smentirle prima che il risolutore parta. E' una cosa molto
diversa dal chiedergli di validare un piano gia' fatto: dodici righe si leggono,
un piano di quaranta assegnazioni non si puo' ricalcolare a mente.

## 7. Dove stanno i numeri e perche' stanno tutti insieme

Tutte le costanti di taratura stanno in `config.py`, con accanto il motivo per
cui valgono quello che valgono.

Il fattore di tortuosita' stradale vale 1.45. La soglia dell'interfaccia
urbano-foresta vale 2 km. Il fabbisogno d'acqua di riferimento vale 100.000 litri
per km quadrato. I pesi della priorita' sommano a uno e sono cinque.

Nessuno di questi numeri e' una costante fisica. Sono scelte, e alcune sono
scelte piuttosto libere. La conseguenza e' che i risultati del progetto dipendono
da loro, e quindi chi legge deve poterli trovare tutti in un posto, cambiarli, e
rieseguire la valutazione per vedere quanto contano.

Se invece il numero 1.45 stesse dentro una funzione in `optimize/geometry.py`,
succederebbero due cose, entrambe brutte. La prima e' che nessuno saprebbe che
esiste. La seconda e' che il validatore, che deve usare la stessa taratura,
finirebbe per avere il suo 1.45 scritto da un'altra parte, e prima o poi uno dei
due cambierebbe e l'altro no.

Questo e' il motivo per cui condividere `config.py` fra risolutore e validatore
non viola la regola di indipendenza: le costanti sono **dati della specifica**,
non implementazione della specifica. Se i due leggessero costanti diverse, il
loro disaccordo direbbe soltanto che hanno letto numeri diversi, e il validatore
perderebbe di significato per un motivo opposto ma altrettanto grave.

## 8. Iniezione delle dipendenze senza framework

Il motore di rigiocata in `sim/replay.py` non sa chi sia il pianificatore: lo
riceve come parametro.

```python
def replay_season(start, end, planner, ...):
    ...
    plan = planner(scenario)
```

`planner` e' un qualunque oggetto chiamabile che prende uno `Scenario` e
restituisce un `Plan`. Puo' essere il risolutore CP-SAT, puo' essere la baseline
del mezzo piu' vicino, puo' essere la baseline ordinata per priorita', puo' essere
un pianificatore finto che restituisce sempre un piano vuoto per i test.

Questa e' iniezione delle dipendenze, e non serve nessun framework per farla in
Python: basta passare una funzione. Il valore non e' l'eleganza, e' che la
valutazione diventa corretta per costruzione. Il risolutore e la baseline
percorrono **lo stesso identico codice** di rigiocata, vedono gli stessi
scenari, subiscono le stesse finestre temporali e vengono misurati dalle stesse
metriche. L'unica cosa che cambia e' la strategia di pianificazione.

Se `replay_season` avesse dentro un `if producer == "cpsat"`, il confronto
sarebbe contaminato: una differenza nei risultati potrebbe venire dal ramo `if`
invece che dalla strategia, e non si potrebbe distinguere.

Lo stesso schema vale per `validate_plan`, che accetta qualunque `Plan` senza
chiedersi chi lo abbia prodotto, ed e' proprio cio' che permette di validare
anche i piani della baseline.

## 9. Il layout src e perche' non e' pedanteria

Il codice sta in `src/tagliafuoco/` e non in `tagliafuoco/` alla radice.

La differenza pratica e' una sola, e conta. Con il pacchetto alla radice, quando
esegui i test dalla cartella del progetto, Python trova il pacchetto nella
directory corrente e lo importa da li'. Con il layout `src`, questo non succede:
il pacchetto va installato (anche in modalita' modificabile con `pip install -e`)
oppure il percorso va aggiunto esplicitamente.

Sembra un fastidio ed e' una protezione. Significa che i test esercitano il
pacchetto **come lo ricevera' chi lo installa**, non come si vede dalla cartella
di sviluppo. Gli errori che questo previene sono quelli che scopri il giorno
dopo la pubblicazione: un file di dati che non era incluso nel pacchetto, un
modulo che veniva importato per caso perche' era nella directory corrente.

In questo progetto `tests/conftest.py` aggiunge `src` al percorso, cosi' la suite
gira anche senza installazione. E' una comodita' consapevole, e chi vuole la
verifica piu' severa installa il pacchetto e rimuove quella riga.

## 10. Cosa cambierei se il progetto crescesse

Una sezione onesta di autocritica vale piu' di un elenco di scelte difese.

`domain/registry.py` legge file dal disco e sta nel centro. In un progetto piu'
grande diventerebbe un adattatore, con un'interfaccia nel centro e
un'implementazione su file nel bordo, e questo permetterebbe di leggere la flotta
da un database senza toccare niente.

Non c'e' uno strato di persistenza. I risultati della rigiocata finiscono in file
JSON. Va bene per una valutazione riproducibile, non andrebbe bene per uno storico
consultabile.

Il grafo delle dipendenze e' controllato a mano. In un progetto con piu' persone
servirebbe un controllo automatico nella pipeline di integrazione continua, che
faccia fallire la build se `validate` importa da `optimize`. E' poche righe di
lavoro e rende la regola permanente invece che consuetudinaria.

`config.py` e' un modulo di costanti globali. Funziona finche' la configurazione e'
una sola. Appena servissero profili diversi (una taratura per la Calabria e una
per un'altra regione), quelle costanti dovrebbero diventare un oggetto passato
esplicitamente, e la prima cosa che si romperebbe sarebbero le funzioni che oggi
leggono da `config` direttamente invece di ricevere i parametri.

Non c'e' gestione degli errori tipizzata su tutta la catena. Alcuni moduli
sollevano eccezioni specifiche, altri lasciano passare quelle di libreria.

## 11. Cosa devi saper spiegare

**Perche' le dipendenze puntano verso il centro?**
Perche' il dominio deve poter restare identico mentre cambiano il risolutore, la
fonte dei dati e l'interfaccia. Se `domain/` importasse da `optimize/`, cambiare
risolutore significherebbe toccare le entita', e con esse tutto il resto. La
regola si verifica con un grep, ed e' per questo che sopravvive.

**Perche' il validatore non puo' importare dal risolutore?**
Perche' verificherebbe il risolutore con il codice del risolutore. Se la funzione
condivisa ha un errore, il piano e' sbagliato e il validatore lo approva, in modo
perfettamente coerente. La duplicazione fra `optimize/geometry.py` e
`validate/physics.py` e' voluta e dichiarata nel codice, proprio perche' nessuno la
elimini scambiandola per una svista.

**Perche' dataclass per il dominio e Pydantic per i vincoli?**
Perche' si paga la validazione dove il dato attraversa un confine di fiducia. Le
entita' di dominio le costruisce il nostro codice da dati nostri; i vincoli
arrivano da un modello linguistico, che non e' affidabile per costruzione. In
piu' Pydantic genera lo schema JSON che passiamo al modello come descrizione
dello strumento: la stessa definizione descrive cosa vogliamo e controlla cosa e'
arrivato.

**Che cosa impedisce concretamente al modello linguistico di prendere decisioni?**
Il fatto che il suo unico canale di uscita sia `ConstraintSet`, che non ha nessun
campo in cui si possa scrivere un'assegnazione, del codice o del testo che venga
interpretato. Non e' una regola scritta nel prompt, che si potrebbe aggirare: e'
una proprieta' del tipo.

**Perche' il risolutore viene passato come parametro alla rigiocata?**
Perche' cosi' il risolutore e la baseline percorrono lo stesso codice e vedono gli
stessi scenari, e l'unica differenza fra i due esperimenti e' la strategia. Un
`if` dentro la rigiocata renderebbe il confronto contaminato.

**Perche' il layout src?**
Perche' costringe i test a esercitare il pacchetto come lo ricevera' chi lo
installa, invece che come si vede dalla cartella di sviluppo. Previene la classe
di errori che si scopre dopo la pubblicazione, tipicamente file di dati non
inclusi nel pacchetto.

**Se `config.py` e' condiviso fra risolutore e validatore, l'indipendenza non e'
gia' rotta?**
No, perche' `config.py` contiene dati della specifica, non la sua implementazione.
I due devono partire dagli stessi numeri: se leggessero tarature diverse, il loro
disaccordo direbbe solo che hanno letto numeri diversi, e non che uno dei due ha
sbagliato il calcolo. L'indipendenza che conta e' quella del procedimento.

**Qual e' il punto piu' debole di questa architettura?**
Che `nlu/resolver.py` e' condiviso fra risolutore e validatore, quindi un errore
li' dentro sfugge a entrambi. E' un compromesso accettato: risolvere i selettori
in due modi diversi rischierebbe di far ragionare i due moduli su insiemi di mezzi
diversi, e quel rischio e' peggiore. La cosa giusta e' dichiararlo.

## 12. Per approfondire

Sull'architettura esagonale (nota anche come ports and adapters) e
sull'architettura pulita esiste molta divulgazione, di qualita' molto variabile.
Conviene cercare gli articoli originali che introducono i due termini piuttosto
che le rielaborazioni, perche' l'idea di fondo e' semplice e le rielaborazioni
tendono ad aggiungere cartelle senza aggiungere sostanza.

Sulla distinzione fra dataclass e librerie di validazione, la documentazione di
Pydantic v2 ha una pagina dedicata al confronto che e' piu' precisa di qualunque
riassunto.

Sul layout `src` in Python, la documentazione del Python Packaging Authority ha
una discussione chiara del perche' esiste e di che cosa previene.

Sul controllo automatico del grafo delle dipendenze esistono strumenti dedicati
per Python (cercare "import linter" o "architecture testing") che permettono di
dichiarare i confini in un file di configurazione e farli verificare
dall'integrazione continua.
