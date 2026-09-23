# Il modello matematico di Tagliafuoco

La formulazione per esteso: insiemi, parametri, variabili, vincoli, funzione
obiettivo. E la tabella che dice come ogni ordine del coordinatore diventa un
pezzo di questo modello.

Indice dei paragrafi:
1. Perche' questo capitolo esiste
2. Insiemi e indici
3. Parametri
4. Il profilo di missione: dai dati ai coefficienti
5. Le variabili decisionali
6. I vincoli
7. La funzione obiettivo
8. Come i vincoli del coordinatore entrano nel modello
9. Aritmetica intera e fattore di scala
10. Quello che il modello non dice
11. Cosa devi saper spiegare
12. Per approfondire

---

## 1. Perche' questo capitolo esiste

Il codice in `src/tagliafuoco/optimize/` e' commentato con cura e spiega le
scelte di implementazione. Questo capitolo fa una cosa diversa: scrive il
modello in notazione matematica, separato dal codice.

Serve per tre motivi concreti. Il primo: una formulazione in notazione si
controlla a mano, e gli errori di modellazione si vedono meglio in tre righe di
sommatorie che in trecento righe di chiamate a un'API. Il secondo: il validatore
deve implementare la stessa specifica in modo indipendente, e ha bisogno di una
specifica che non sia il codice del risolutore. Il terzo: a un colloquio ti
chiederanno di scrivere il modello alla lavagna, non di ricordare il nome di un
metodo di OR-Tools.

## 2. Insiemi e indici

```
V        insieme dei mezzi impiegabili            indice v
F        insieme dei focolai attivi               indice f
B        insieme delle basi operative             indice b
W        insieme delle sorgenti d'acqua           indice w
P        insieme delle coppie praticabili         P subset di V x F
```

L'insieme `P` merita attenzione perche' e' la prima ottimizzazione del modello e
non si vede nella notazione ingenua. Non tutte le coppie mezzo-focolaio hanno
senso: un elicottero la cui autonomia non basta per andare, fare almeno un
lancio e tornare non puo' servire quel focolaio; un mezzo aereo senza una
sorgente d'acqua compatibile raggiungibile nemmeno; un mezzo in manutenzione o
fuori turno nemmeno.

Quelle coppie non ricevono variabili. Non e' una potatura euristica che rischia
di tagliare l'ottimo: sono coppie infattibili, quindi nessuna soluzione valida
le userebbe. Il guadagno e' sostanziale, perche' con 26 mezzi e 30 focolai le
coppie possibili sarebbero 780 e le praticabili sono tipicamente molte meno.

`V` contiene i soli mezzi impiegabili, cioe' quelli con stato `AVAILABLE`. I
mezzi in `RESERVE`, `MAINTENANCE` o `OUT_OF_SHIFT` non entrano nel modello.

## 3. Parametri

Dati del problema, noti prima della risoluzione.

```
H                orizzonte di pianificazione, in minuti
d(x, y)          distanza geodetica fra due punti, in km
cruise_v         velocita' di crociera del mezzo v, in km/h
cap_v            capacita' d'acqua del mezzo v, in litri
setup_v          tempo di uscita dalla base del mezzo v, in minuti
drop_v           tempo di sgancio o erogazione, in minuti
refill_v         tempo di pescaggio o riempimento, in minuti
end_v            autonomia continuativa del mezzo v, in minuti
shift_v          limite di turno dell'equipaggio del mezzo v, in minuti
flown_v          minuti gia' consumati oggi dal mezzo v
turn_b           tempo di riattrezzamento della base b, in minuti
cost_v           costo orario del mezzo v, in euro
demand_f         fabbisogno d'acqua del focolaio f, in litri
prio_f           priorita' di base del focolaio f, in [0, 1]
wui_f            1 se il focolaio f e' in interfaccia urbano-foresta, 0 altrimenti
L                soglia di risposta tardiva (config.LATE_RESPONSE_MINUTES)
N                numero massimo di focolai per mezzo nell'orizzonte
```

Le formule che producono `demand_f` e `prio_f` stanno nella sezione 6 di
`SPEC.md` e sono applicate in `ingest/enrichment.py`. Nel modello sono dati, non
variabili: il risolutore non ridiscute quanto serve, decide come coprirlo.

## 4. Il profilo di missione: dai dati ai coefficienti

Prima di scrivere i vincoli conviene precalcolare, per ogni coppia praticabile
`(v, f)`, quattro quantita' derivate. Farlo fuori dal modello e' una scelta
deliberata: sono conti deterministici che non dipendono dalle decisioni, e
lasciarli al risolutore significherebbe dargli variabili in piu' da esplorare
senza nessun guadagno.

Sia `b(v)` la base del mezzo `v` e `w(v, f)` la sorgente d'acqua migliore per la
coppia, cioe' quella compatibile con il tipo del mezzo che minimizza il tempo di
ciclo, tenuto conto dell'affidabilita' stagionale.

**Tempo di avvicinamento.** Per un mezzo aereo la distanza e' quella geodetica;
per un mezzo terrestre si moltiplica per il fattore di tortuosita' stradale.

```
outbound(v,f) = dist(v, b(v), f) / cruise_v * 60
return(v,f)   = outbound(v,f)
fixed(v,f)    = setup_v + outbound(v,f) + return(v,f)
```

**Tempo di un ciclo d'acqua.** Il mezzo va dal focolaio alla sorgente, si
rifornisce, torna, sgancia.

```
cycle(v,f) = 2 * dist(v, f, w(v,f)) / cruise_v * 60 + refill_v + drop_v
```

Per i mezzi che non richiedono una sorgente (le squadre a terra con attrezzi
manuali) il ciclo si riduce al solo tempo di lavoro.

**Numero massimo di cicli.** Deriva dall'autonomia, che deve coprire la parte
fissa piu' tutti i cicli.

```
maxs(v,f) = max(0, floor( (end_v - fixed(v,f)) / cycle(v,f) ))
```

Se `maxs(v,f)` vale zero la coppia non e' praticabile e non entra in `P`. E' qui
che la maggior parte delle coppie viene scartata.

**Litri per ciclo.** Semplicemente `cap_v`. Un mezzo scarica tutto quello che ha.

## 5. Le variabili decisionali

Per ogni coppia praticabile `(v, f)` in `P`:

```
x[v,f]  in {0, 1}          il mezzo v interviene sul focolaio f
s[v,f]  in {0, ..., maxs}  numero di cicli pescaggio-sgancio
dep[v,f] in [0, H]         minuto di partenza dalla base
arr[v,f] in [0, H]         minuto di arrivo sul focolaio
rel[v,f] in [0, H]         minuto di rientro in base
work[v,f] in [0, H]        durata totale della missione
late[v,f] in [0, H]        ritardo oltre la soglia L
I[v,f]                     intervallo opzionale (dep, work, rel), presente se x[v,f]
```

Per ogni mezzo `v` in `V`:

```
u[v]    in {0, 1}          il mezzo e' impiegato almeno una volta
```

Per ogni focolaio `f` in `F`:

```
del[f]    intero >= 0       litri erogati sul focolaio
cov[f]    in {0, 1}         il focolaio e' coperto
unmet[f]  in [0, demand_f]  litri mancanti
```

Due osservazioni che vale la pena saper fare.

La prima: `arr`, `rel` e `work` sono variabili e non espressioni perche'
partecipano a vincoli che il risolutore deve poter propagare in entrambe le
direzioni. Il propagatore di `NoOverlap` restringe `dep` e `rel`, e quella
restrizione deve risalire a `s[v,f]` attraverso `work`.

La seconda: `I[v,f]` e' un intervallo **opzionale**. Non e' zucchero
sintattico. Un intervallo opzionale ha un letterale di presenza, e quando quel
letterale e' falso l'intervallo semplicemente non esiste per i vincoli che lo
coinvolgono. Il letterale qui e' `x[v,f]`: se il mezzo non e' assegnato, la sua
missione non occupa nessuno spazio nella sequenza.

## 6. I vincoli

### 6.1 Legame fra assegnazione e cicli

```
x[v,f] = 1  =>  s[v,f] >= 1
x[v,f] = 0  =>  s[v,f] = 0
```

Senza questo legame il risolutore potrebbe dichiarare assegnato un mezzo che non
versa una goccia, guadagnando sui termini che premiano la presenza senza pagare
il tempo.

### 6.2 Catena dei tempi

Imposta solo quando l'assegnazione esiste. In CP-SAT si scrive con
`OnlyEnforceIf`, che condiziona il vincolo a un letterale senza ricorrere a
costanti grandi.

```
x[v,f] = 1  =>  arr[v,f]  = dep[v,f] + setup_v + outbound(v,f)
x[v,f] = 1  =>  rel[v,f]  = arr[v,f] + cycle(v,f) * s[v,f] + return(v,f)
x[v,f] = 1  =>  work[v,f] = rel[v,f] - dep[v,f]
x[v,f] = 0  =>  dep = arr = rel = work = 0
```

L'ultima riga non e' cosmetica. Quando l'assegnazione non c'e' quei tempi non
significano niente, e lasciarli liberi creerebbe un numero enorme di soluzioni
equivalenti, tutte con lo stesso valore dell'obiettivo e tutte da esplorare.
Fissarli a zero le collassa in una sola. Si chiama rottura di simmetria, ed e'
una delle cose che fa la differenza fra un modello che chiude in secondi e uno
che non chiude.

### 6.3 Autonomia

```
work[v,f] <= end_v                            per ogni (v,f) in P
```

E' gia' implicito nel limite superiore di `s[v,f]`, ma scriverlo dichiara
l'intenzione a chi legge.

### 6.4 Turno dell'equipaggio

```
somma su f di work[v,f]  <=  shift_v - flown_v      per ogni v in V
```

L'autonomia limita la singola missione, il turno limita la giornata. Sono due
cose diverse e servono entrambe.

### 6.5 Numero massimo di focolai per mezzo

```
somma su f di x[v,f]  <=  N                         per ogni v in V
```

`N` vale 2 per impostazione predefinita. Non e' una legge fisica: e' una scelta
che tiene il modello piccolo e riflette il fatto che su un orizzonte di quattro
ore un mezzo difficilmente cambia obiettivo piu' di una volta.

### 6.6 Sequenziamento delle missioni

```
NoOverlap( { I[v,f] : f in F } )                    per ogni v in V
```

Piu' il riattrezzamento fra due missioni consecutive dello stesso mezzo:

```
dep[v,f2] >= rel[v,f1] + turn_b(v)
```

quando entrambe le missioni esistono e `f1` precede `f2`.

Questo vincolo vive su una convenzione che va dichiarata, perche' e' la stessa
che il validatore deve usare: **una missione termina con il rientro in base**.
Il termine `return(v,f)` non e' facoltativo. Al minuto `rel[v,f]` il mezzo e' in
base, non sul focolaio. Ne segue che il trasferimento verso il focolaio
successivo si misura dalla base, ed e' gia' contato dentro `outbound` della
missione seguente: sommarlo di nuovo sarebbe contarlo due volte.

La convenzione e' scritta nella sezione 6 di `SPEC.md`. Prima che lo fosse, il
risolutore e il validatore ne avevano assunte due diverse, e il validatore
segnalava mezzi che si teletrasportavano. Non era un errore di calcolo: era un
buco nella specifica, e la divergenza fra le due implementazioni e' il modo in
cui e' venuto fuori.

### 6.7 Attivazione

```
u[v] = max( x[v,f] : f in F )                       per ogni v in V
```

Serve al costo fisso: far alzare un Canadair costa anche prima di sganciare.

### 6.8 Acqua erogata e copertura

```
del[f] = somma su v di  cap_v * s[v,f]              per ogni f in F

capped[f] = min( del[f], demand_f )
unmet[f]  = demand_f - capped[f]

cov[f] = 1  <=>  del[f] >= demand_f
```

L'ultima riga e' una reificazione, cioe' una variabile booleana legata alla
verita' di una disuguaglianza. In CP-SAT si scrive con due vincoli condizionati:

```
cov[f] = 1  =>  del[f] >= demand_f
cov[f] = 0  =>  del[f] <= demand_f - 1
```

Servono entrambi. Con il solo primo, il risolutore potrebbe mettere `cov[f] = 0`
su un focolaio coperto, il che non gli conviene mai ma rende il modello ambiguo;
con il solo secondo, potrebbe mettere `cov[f] = 1` gratis.

Il taglio con `min` esiste perche' versare piu' del necessario non e' un merito.
Senza, il risolutore accumulerebbe acqua su un focolaio facile invece di
spostarsi su uno scoperto.

### 6.9 Notte

```
x[v,f] = 0    per ogni mezzo aereo v non abilitato al volo notturno,
              quando lo scenario e' notturno
```

Nel codice questo vincolo non compare nemmeno: quei mezzi hanno stato
`OUT_OF_SHIFT` e quindi non entrano in `V`. Toglierli dall'insieme e' preferibile
ad aggiungere un vincolo, perche' il modello resta piu' piccolo.

## 7. La funzione obiettivo

Cinque criteri, sommati con pesi, ciascuno normalizzato in `[0, 1]` dividendo
per il suo massimo teorico. La normalizzazione e' la condizione perche' i pesi
significhino qualcosa: senza, un termine misurato in euro schiaccerebbe un
termine misurato in frazioni.

```
massimizza

    W_cov  * ( somma su f di prio_f * cov[f] ) / ( somma su f di prio_f )

  + W_wui  * ( somma su f di wui_f * cov[f] ) / ( somma su f di wui_f )

  + W_wat  * ( 1 - somma su f di unmet[f] / somma su f di demand_f )

  - W_time * ( somma su (v,f) di late[v,f] ) / late_max

  - W_cost * ( costo totale ) / costo_max
```

dove `late[v,f] >= arr[v,f] - L` quando l'assegnazione esiste, e zero altrimenti.
Il vincolo e' scritto con `>=` e non con l'uguaglianza perche' l'obiettivo
minimizza quel termine, quindi il risolutore lo schiaccia da solo sul suo
minimo. E' un trucco che si usa spesso e vale la pena conoscerlo: si dichiara
solo il lato della disuguaglianza che l'obiettivo non puo' sfruttare.

Il costo totale somma un costo di attivazione per ogni mezzo impiegato e un
costo proporzionale ai minuti di impegno.

I pesi di default stanno in `config.OBJECTIVE_WEIGHTS`: copertura 10,
protezione dell'interfaccia urbano-foresta 8, tempo di risposta 4,
soddisfacimento idrico 3, costo 1. Sono una taratura dichiarata, e il
coordinatore puo' cambiarli a voce tramite il vincolo `OBJECTIVE_WEIGHT`.

**Perche' una somma pesata e non un'ottimizzazione lessicografica.** La
lessicografica ordina i criteri per importanza assoluta e ottimizza uno alla
volta, congelando l'ottimo del precedente. E' corretta quando l'ordine e'
davvero assoluto. Qui non lo e': direbbe che un guadagno infinitesimo di
copertura vale piu' di qualunque risparmio, e che due piani con la stessa
copertura sono indistinguibili anche se uno arriva in venti minuti e l'altro in
due ore. Un coordinatore non ragiona cosi': scambia.

Il difetto noto della somma pesata e' che non raggiunge i punti non supportati
della frontiera di Pareto, cioe' certi compromessi restano invisibili qualunque
peso si scelga. Si accetta, in cambio di un solo passaggio di risoluzione e di
pesi interpretabili.

Se servisse la lessicografica, si otterrebbe risolvendo a stadi: massimizza la
copertura, leggi `z1`, ricostruisci il modello aggiungendo `copertura >= z1 -
epsilon`, massimizza il criterio seguente, ripeti. L'epsilon serve a evitare che
l'ottimalita' esatta del primo stadio renda infattibile il secondo per un'inezia
numerica.

## 8. Come i vincoli del coordinatore entrano nel modello

Ogni tipo di vincolo tradotto diventa o un vincolo vero (se duro) o un termine
di penalita' nell'obiettivo (se morbido). Questa e' la tabella completa.

| Tipo | Se duro | Se morbido |
|---|---|---|
| `PRIORITIZE_ZONE` | sempre morbido | moltiplica `prio_f` dei focolai nella zona |
| `RESERVE_VEHICLE` | `x[v,f] = 0` per ogni f, sui mezzi selezionati | penalita' sull'uso di quei mezzi |
| `EXCLUDE_VEHICLE` | il mezzo non entra in `V` | penalita' sull'uso |
| `FORCE_ASSIGNMENT` | `somma x[v,f] >= min_count` sui selezionati | penalita' se non soddisfatto |
| `FORBID_ASSIGNMENT` | `x[v,f] = 0` sulle coppie selezionate | penalita' su quelle coppie |
| `MAX_RESPONSE_TIME` | `arr[v,f] <= minutes` quando `x[v,f] = 1` | penalita' sul superamento |
| `MIN_VEHICLES_ON_FIRE` | `somma su v di x[v,f] >= count` | penalita' sulla carenza |
| `VEHICLE_TYPE_QUOTA` | `min <= somma u[v] <= max` sul tipo | penalita' fuori intervallo |
| `TIME_WINDOW` | `dep >= from` e `rel <= to` sui mezzi selezionati | penalita' fuori finestra |
| `OBJECTIVE_WEIGHT` | sempre morbido | sostituisce il peso del termine |
| `PROTECT_INFRASTRUCTURE` | sempre morbido | moltiplica `prio_f` entro il raggio |
| `UNSUPPORTED` | ignorato | ignorato, ma registrato nelle note del piano |

Due osservazioni.

La prima: i selettori (`gli elicotteri`, `la provincia di Reggio`) vengono
risolti contro lo scenario concreto da `nlu/resolver.py` prima di diventare
vincoli. Un selettore che non trova nulla non e' un errore: produce un insieme
vuoto, il vincolo diventa vacuo e la cosa viene annotata nel piano. Fallire
silenziosamente sarebbe peggio; fallire rumorosamente su un ordine marginale
anche.

La seconda: se il modello con i vincoli duri risulta infattibile, il risolutore
non restituisce il vuoto. Ritenta trasformando in morbidi i vincoli duri
provenienti dalla traduzione, e annota nel piano quali ha rilassato. Un ordine
contraddittorio, come "tutti i mezzi in campo e tenetene meta' di riserva", deve
produrre un piano con una nota che spiega il conflitto, non un errore.

## 9. Aritmetica intera e fattore di scala

CP-SAT lavora solo su interi. Tutte le grandezze del modello devono quindi
essere discretizzate, e il modo in cui lo si fa e' parte della formulazione.

I tempi sono interi in **decimi di minuto**: il fattore di scala vale 10 e sta in
`geometry.TIME_SCALE`. Sei secondi di risoluzione sono largamente sufficienti
per un problema dove le incertezze vere si misurano in decine di minuti, e
tenere i numeri piccoli aiuta la propagazione.

Il verso dell'arrotondamento non e' indifferente e va scelto in modo
conservativo. Le durate e i tempi di percorrenza si arrotondano **per eccesso**
(`to_units_up`), cosi' il modello non promette di arrivare prima di quanto sia
possibile. I budget, come il residuo di turno, si arrotondano **per difetto**
(`to_units_down`), cosi' il modello non concede piu' tempo di quanto ce ne sia.
Arrotondare tutto allo stesso modo produrrebbe piani leggermente ottimisti, e un
piano ottimista e' un piano che il validatore respinge.

I volumi sono interi in litri, senza scala: un litro e' gia' una risoluzione piu'
fine di qualunque cosa il problema sappia davvero.

I coefficienti dell'obiettivo sono interi ottenuti moltiplicando per un milione
(`OBJECTIVE_PRECISION`) e arrotondando. Con pesi fino a 10, ogni termine resta
sotto `10^7`, quindi la somma sta comodamente dentro l'intero a 64 bit anche
moltiplicata per domini da qualche migliaio.

## 10. Quello che il modello non dice

Un modello si giudica anche da cosa lascia fuori, e questo lascia fuori parecchio.

Il fuoco non si propaga. `demand_f` e' calcolato una volta all'inizio della
finestra e resta costante: un focolaio non cresce mentre i mezzi viaggiano. Per
un orizzonte di quattro ore e' una semplificazione pesante, perche' in quattro
ore un incendio in macchia con vento puo' cambiare completamente scala.

L'acqua non spegne. Coprire un focolaio significa raggiungere un numero di
litri, non estinguere l'incendio. Il modello non sa se il lancio ha funzionato.

I mezzi non interagiscono. Due elicotteri sullo stesso focolaio sommano i litri
in modo lineare, mentre nella realta' devono coordinarsi, aspettare il turno
sulla verticale, e a volte si ostacolano.

Il meteo non c'e'. `max_wind_kmh` esiste nel modello di dominio e non entra nel
modello di ottimizzazione.

La viabilita' e' un fattore moltiplicativo. Per i mezzi terrestri la strada e'
la linea d'aria per 1.45. Una strada chiusa, un tornante, un guado non esistono.

Nessuna di queste assenze e' un difetto nascosto: sono il confine dichiarato di
un simulatore. Il punto da saper difendere e' che il modello e' onesto su cosa
sta ottimizzando, cioe' l'allocazione di risorse date a fabbisogni dati, e non
pretende di simulare un incendio.

## 11. Cosa devi saper spiegare

**Scrivimi il modello alla lavagna.**
Insiemi: mezzi, focolai, coppie praticabili. Variabili: una booleana di
assegnazione per coppia, un intero di cicli, tre tempi (partenza, arrivo,
rientro), un intervallo opzionale per la sequenza, piu' litri erogati e
copertura per focolaio. Vincoli: catena dei tempi condizionata
all'assegnazione, autonomia sulla singola missione, turno sulla giornata,
NoOverlap sulle missioni del mezzo, copertura reificata sui litri. Obiettivo:
somma pesata di cinque termini normalizzati.

**Perche' le coppie infattibili non ricevono variabili?**
Perche' non sono una potatura euristica ma un'impossibilita' fisica: se
l'autonomia non basta per andare, fare un lancio e tornare, nessuna soluzione
valida userebbe quella coppia. Toglierle riduce il modello senza rischiare di
tagliare l'ottimo.

**Perche' fissi a zero i tempi quando l'assegnazione non c'e'?**
Per rompere la simmetria. Se restassero liberi, ogni soluzione esisterebbe in un
numero enorme di copie identiche nel valore dell'obiettivo e diverse in quelle
variabili, e il risolutore le esplorerebbe tutte.

**Perche' `late >= arr - L` e non `late = max(0, arr - L)`?**
Perche' l'obiettivo minimizza quel termine, quindi il risolutore lo porta da
solo al suo minimo, che e' esattamente il massimo fra zero e la differenza.
Dichiarare solo il lato che l'obiettivo non puo' sfruttare costa un vincolo
invece di due e non cambia l'ottimo.

**Perche' la reificazione della copertura richiede due vincoli?**
Perche' una sola implicazione lascia un verso libero. Con il solo
`cov = 1 => del >= demand` il risolutore potrebbe dichiarare scoperto un
focolaio coperto; con il solo `cov = 0 => del < demand` potrebbe dichiarare
coperto un focolaio scoperto e guadagnare gratis.

**Perche' tagli l'acqua in eccesso con un minimo?**
Perche' versare piu' del fabbisogno non e' un merito. Senza il taglio, il
risolutore accumulerebbe cicli su un focolaio comodo, alzando il termine idrico,
invece di andare su uno scoperto.

**Perche' arrotondi i tempi per eccesso e i budget per difetto?**
Perche' l'arrotondamento deve essere conservativo. Se le durate si
arrotondassero per difetto, il piano prometterebbe di arrivare prima del
possibile; se i budget si arrotondassero per eccesso, concederebbe tempo che non
c'e'. In entrambi i casi il piano sarebbe ottimista, e un piano ottimista viene
respinto dal validatore.

**Che cosa succede se il coordinatore da' un ordine contraddittorio?**
Il modello risulta infattibile. Il risolutore non restituisce il vuoto: ritenta
trasformando in morbidi i vincoli duri che vengono dalla traduzione, e annota
nel piano quali ha rilassato. La contraddizione viene mostrata, non nascosta,
e non e' il traduttore a doverla risolvere.

**Perche' non hai usato una formulazione lineare intera classica?**
Perche' il vincolo centrale, cioe' che le missioni di un mezzo non si
sovrappongono, e' una disgiunzione. In forma lineare diventa una variabile di
ordinamento per ogni coppia di missioni piu' vincoli con costanti grandi, che
indeboliscono il rilassamento continuo e rallentano il branch and bound. CP-SAT
ha l'intervallo opzionale e `NoOverlap` come costrutti nativi, con un propagatore
specializzato.

## 12. Per approfondire

Sulla formulazione di problemi di scheduling con risorse, i manuali di ricerca
operativa trattano il job shop e le sue varianti con tempi di trasferimento, che
e' la struttura piu' vicina a quella di questo modello.

Sulla rottura di simmetria nei modelli a vincoli esiste una letteratura
specifica, ed e' una delle poche tecniche che cambia i tempi di risoluzione di
ordini di grandezza con poche righe.

Sulla somma pesata contro la lessicografica e sui punti non supportati della
frontiera di Pareto, il riferimento e' la letteratura di ottimizzazione
multiobiettivo. La distinzione fra punti supportati e non supportati e' spiegata
in qualunque introduzione al tema e vale la pena capirla, perche' e' il limite
preciso della somma pesata.

La documentazione di OR-Tools ha una sezione dedicata allo scheduling che mostra
gli intervalli opzionali e `NoOverlap` su esempi piu' piccoli di questo.
