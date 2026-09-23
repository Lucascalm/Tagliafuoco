# Modelli linguistici e output strutturato

Come ottenere, da un modello linguistico generativo, dati che un programma puo'
consumare senza sperare. Serve a capire il confine fra `nlu/` e il resto di
Tagliafuoco: perche' esiste `ConstraintSet`, perche' lo schema e' chiuso, e
quali garanzie ci sono davvero.

Indice dei paragrafi:

1. Il problema concreto
2. Cosa fa davvero un modello generativo
3. Temperatura e top-p, con i numeri
4. Perche' l'output non e' sintatticamente garantito
5. Tre strategie per l'output strutturato
6. La forma non e' il contenuto
7. JSON Schema: il vocabolario che conta
8. Pydantic v2: generare lo schema, validare il ritorno
9. Temperatura zero: cosa significa e cosa non significa
10. Il prompt che conta davvero
11. La riparazione, e perche' un solo tentativo
12. Come si valuta un traduttore
13. Costi, latenza e caching del prompt
14. Cosa devi saper spiegare
15. Per approfondire

---

## 1. Il problema concreto

In sala operativa il coordinatore non compila moduli. Dice frasi come questa:

> "L'elicottero di Lorica resta di riserva, e gli aerei non volano dopo le otto.
> Prima le zone vicino alle case."

Il solver CP-SAT non accetta frasi: accetta variabili booleane, coefficienti
interi e vincoli algebrici. Tagliafuoco colma il salto con un oggetto solo, il
`ConstraintSet` definito in `src/tagliafuoco/nlu/schema.py`. L'ordine sopra deve
diventare tre vincoli: un `ReserveVehicle` con selettore su `base_id`, un
`TimeWindow` sul dominio aereo, un `PrioritizeZone` con `zone_kind` uguale a
`wui`.

Il problema e' che l'unico strumento capace di leggere l'italiano parlato e' un
modello linguistico, e un modello linguistico non produce oggetti: produce
testo, un pezzo alla volta. Il perimetro e' stretto per scelta, perche' il
modello non decide quale mezzo va su quale incendio, non stima i litri e non
scrive il modello matematico. Il perche' e' il capitolo 08; qui si tratta il
come.

## 2. Cosa fa davvero un modello generativo

Un modello generativo fa una cosa sola, ripetuta: riceve una sequenza di
simboli, produce una distribuzione di probabilita' sul simbolo successivo, ne
sceglie uno, lo aggiunge in coda e ricomincia. Si chiama generazione
autoregressiva perche' l'output di un passo diventa l'input del passo dopo.

I simboli sono token, frammenti prodotti da un tokenizzatore che ha imparato dal
corpus quali sequenze di caratteri trattare come unita': una parola italiana
comune e' spesso un token solo, una rara si spezza in due o tre, e punteggiatura
e graffe sono token a se'. Ne seguono due cose: `"RESERVE_VEHICLE"` non e' un
atomo ma una sequenza di token da indovinare uno alla volta, e ogni passo e' una
nuova occasione di sbagliare; e il conto dei token e' il conto dei soldi e del
tempo (paragrafo 13).

La rete produce per ogni token del vocabolario un numero reale chiamato logit,
che non e' una probabilita' ma un punteggio non normalizzato, che puo' essere
negativo e non somma a uno. Per trasformarlo in probabilita' si applica la
softmax:

```
p_i = exp(z_i / T) / somma su j di exp(z_j / T)
```

Simbolo per simbolo: `i` e' l'indice del token candidato; `z_i` e' il suo logit,
il punteggio grezzo che la rete gli assegna; `j` scorre su tutti i token del
vocabolario, dell'ordine di centomila; `exp` e' l'esponenziale, che rende
positivi tutti i valori e amplifica le differenze; `T` e' la temperatura, un
numero positivo che regoliamo noi; `p_i` e' la probabilita' finale, e la somma
di tutti i `p_i` fa uno.

## 3. Temperatura e top-p, con i numeri

Il modello ha gia' prodotto `{"kind": "`. I candidati per il token successivo
potrebbero essere `RESERVE` con logit 8.2, `EXCLUDE` con 6.1, `FORCE` con 4.5 e
`TIME` con 3.9. Applicando la formula, questi valori sono calcolati davvero:

| T | p(RESERVE) | p(EXCLUDE) | p(FORCE) | p(TIME) |
|---|---|---|---|---|
| 1.0 | 0.8615 | 0.1055 | 0.0213 | 0.0117 |
| 0.7 | 0.9461 | 0.0471 | 0.0048 | 0.0020 |
| 0.3 | 0.9991 | 0.0009 | 0.0000 | 0.0000 |
| 0.1 | 1.0000 | 0.0000 | 0.0000 | 0.0000 |

Gli zeri della tabella sono arrotondamenti a quattro decimali, non zeri veri: a
`T = 0.3` la probabilita' di `EXCLUDE` vale `exp(-7)`, cioe' circa 0,0009, e a
`T = 0.1` vale `exp(-21)`, circa 7,6 per 10 alla meno decima. La differenza
conta: una probabilita' minuscola su un token solo diventa visibile quando quel
token viene generato milioni di volte.

A temperatura 1 il modello scrivera' `EXCLUDE_VEHICLE` invece di
`RESERVE_VEHICLE` circa una volta su dieci, benche' il logit di `RESERVE` sia
nettamente piu' alto. Dividere il logit per un `T` minore di uno allarga le
distanze fra i punteggi prima dell'esponenziale, quindi concentra la
probabilita' sul candidato migliore; al limite di `T` che tende a zero il
campionamento coincide con prendere il massimo, e si chiama decoding greedy,
cioe' avido. In questo esempio la differenza di logit di 2,1 fra i primi due
candidati lascia poco piu' del dieci per cento all'alternativa sbagliata. Non
prenderlo come una legge: il valore esatto dipende anche da tutti gli altri
token del vocabolario, perche' entrano tutti nel denominatore della softmax, e
la stessa differenza di 2,1 dentro una distribuzione piu' piatta lascia al
secondo candidato molto meno. Qui quel dieci per cento significa dichiarare
guasto un elicottero che il coordinatore voleva solo tenere di riserva.

Il top-p, detto anche nucleus sampling (campionamento dal nucleo), agisce dopo
la softmax: si ordinano i token per probabilita' decrescente, si sommano finche'
il totale non supera la soglia `p`, e si campiona solo dentro quell'insieme,
rinormalizzando. Con `p = 0.9` e la distribuzione a `T = 1` il nucleo contiene
`RESERVE` ed `EXCLUDE` (0.967 cumulato) e gli altri due escono di scena: taglia
la coda lunga delle assurdita' senza toccare le proporzioni fra i candidati
forti. Il top-k, che tiene i primi `k` e basta, e' piu' rozzo perche' il numero
giusto di candidati dipende da quanto e' piatta la distribuzione.

## 4. Perche' l'output non e' sintatticamente garantito

Un modello linguistico non ha un parser dentro: non costruisce un albero
sintattico e non tiene un contatore di parentesi aperte, produce token perche'
nel corpus, dopo token simili, comparivano token simili. La validita' sintattica
del JSON e' una proprieta' emergente e statistica, non una garanzia strutturale.
Da qui i modi tipici di fallimento, tutti osservati in pratica:

- graffa o quadra non chiusa, su output lunghi, quando il modello perde il conto
  dell'annidamento;
- virgola finale prima di una parentesi chiusa, legale in Python, illegale in
  JSON;
- testo di cortesia attorno al JSON, o recinto di codice Markdown da togliere
  prima di parsare;
- valori inventati per campi enumerati, per esempio `"kind": "PRIORITY_ZONE"`,
  che e' JSON valido e schema non valido;
- troncamento, quando la generazione raggiunge il tetto di token e si ferma a
  meta' stringa;
- escaping sbagliato dentro le stringhe, raro ma letale, e probabile qui perche'
  `source_text` e `source_span` contengono ordini pieni di virgolette. Per
  questo a valle non si fa mai confronto testuale sull'input serializzato di uno
  strumento: si parsa con `json.loads` e si lavora sull'oggetto.

Se il tuo sistema dipende dal fatto che un modello produca JSON valido, e la tua
unica difesa e' averlo chiesto per favore nel prompt, il tuo sistema ha un tasso
di guasto che non controlli.

## 5. Tre strategie per l'output strutturato

### 5.1 Chiedere JSON nel prompt e sperare

Scrivi nel prompt "rispondi soltanto con un oggetto JSON conforme a questo
schema" e parsi la risposta. Non c'e' nessun meccanismo: il modello tende a
obbedire perche' e' stato addestrato anche su documentazione e codice, ma la
conformita' viene dalla statistica del corpus, e le garanzie sono zero, ne'
sintattiche ne' semantiche. Resta l'unica strada con un modello locale servito
da un runtime minimale, al prezzo del codice difensivo che ripulisce la
risposta.

### 5.2 Chiamata a strumenti con schema dichiarato

La chiamata a strumenti, in inglese tool use, e' il meccanismo con cui si
dichiara al modello un elenco di funzioni che puo' invocare, ciascuna con nome,
descrizione in linguaggio naturale e `input_schema`, cioe' uno JSON Schema dei
suoi argomenti. Il modello, invece di produrre testo libero, emette un blocco
`tool_use` con un campo `input` che il servizio consegna gia' come oggetto
strutturato: il client riceve una struttura dati, non una stringa da
interpretare, e il problema del parsing sparisce. Lato modello, la presenza
dello schema nel contesto guida fortemente la generazione, perche' il modello e'
addestrato specificamente a riempire schemi di strumenti.

Ci sono due modalita'. In quella ordinaria lo schema e' una descrizione molto
autorevole ma non coercitiva: il modello di solito la rispetta, a volte no. In
quella stretta, attivata con un campo `strict` sulla definizione dello
strumento, il servizio compila lo schema e garantisce che l'input validi
esattamente, ed e' sotto il cofano la terza strategia. Attenzione a dove va
scritto quel campo, perche' e' l'errore che si fa la prima volta: `strict: true`
sta al primo livello della definizione dello strumento, accanto a `name`,
`description` e `input_schema`, e non dentro `tool_choice`. Garanzia della modalita'
ordinaria: ricevi un oggetto e non una stringa da ripulire. Non che l'oggetto
sia conforme allo schema, e soprattutto non che sia giusto.

Resta una domanda pratica: come fai a essere sicuro che il modello chiami lo
strumento invece di rispondere a parole? Con il parametro `tool_choice`. Il
valore di default e' `auto`, cioe' decide il modello; `{"type": "tool", "name":
"emit_constraint_set"}` lo obbliga a chiamare proprio quello. Per un compito
come il nostro la forma forzata e' quella giusta, perche' la risposta ammessa e'
una sola e non c'e' niente da dire a parole. Due avvertenze. La forzatura non e'
disponibile su ogni modello, perche' su alcuni modelli recenti e' stata rimossa
e una richiesta che la usa riceve un errore 400, mentre `claude-sonnet-5`,
quello indicato in `config.py`, la accetta. E obbligare a chiamare lo strumento
non obbliga a riempirlo bene: sposta il problema dalla forma del canale al
contenuto, che e' il paragrafo 6.

### 5.3 Vincolare il decoding a una grammatica

Lo schema si compila in un automa, una macchina a stati che sa in ogni momento
quali token sono ammissibili per continuare un documento valido. A ogni passo,
prima del campionamento, si applica una maschera ai logit: i token che
porterebbero fuori dal linguaggio accettato ricevono logit meno infinito, quindi
probabilita' zero dopo la softmax. Dopo `{"kind": "` l'automa sa che il valore
deve appartenere a `ConstraintKind`, quindi se il modello volesse scrivere
`PRIORITY` l'automa maschera il token `PRIOR` e lascia passare solo i prefissi
di `PRIORITIZE_ZONE`, `PROTECT_INFRASTRUCTURE` e cosi' via: il valore inventato
diventa impossibile, non improbabile.

Garanzia: la conformita' sintattica al linguaggio della grammatica, per
costruzione. Cosa resta fuori: che il documento conforme dica il vero, ed e' il
paragrafo 6. Un'avvertenza pesa su tutto il resto del capitolo: non tutto lo
JSON Schema si compila in modo efficiente, e restano fuori gli schemi ricorsivi
e, per noi soprattutto, i vincoli numerici (`minimum`, `maximum`) e di lunghezza
(`minLength`, `maxLength`), perche' un automa che accetta solo interi fra 1 e
600 e' molto piu' complicato di uno che accetta "una cifra seguita da altre
cifre", e i compilatori attuali scelgono la seconda strada demandando il resto
al client.

Sull'API di Anthropic questa terza strategia ha due porte d'ingresso, che sono
la stessa macchina vista da due lati. Il campo `strict: true` sulla definizione
dello strumento vincola gli argomenti dello strumento; `output_config.format`
sulla chiamata vincola direttamente il corpo della risposta, ed e' la strada
giusta quando lo strumento esisteva solo per farsi restituire del JSON (in
Python l'SDK espone anche `client.messages.parse()`, che manda lo schema e
valida la risposta per te). In entrambi i casi lo schema deve portare
`additionalProperties: false` su ogni oggetto, e la documentazione elenca come
sostenuti `enum`, `const`, `anyOf`, `allOf` e i riferimenti `$ref` e `$defs`,
lasciando fuori gli schemi ricorsivi, i vincoli numerici e quelli di lunghezza.
Un controllo banale da fare prima di progettarci sopra: ne' la modalita' stretta
ne' `output_config.format` esistono su tutti i modelli, la documentazione ne
elenca l'insieme, e `claude-sonnet-5`, quello di `config.py`, ci sta dentro.

Un dettaglio che conviene conoscere prima di stupirsi: gli SDK Python e
TypeScript non falliscono su cio' che non e' sostenuto, tolgono quei vincoli
dallo schema che spediscono e li verificano loro sulla risposta. Quindi un
`maximum` puo' sembrare rispettato anche quando la grammatica non lo conosce,
perche' a farlo rispettare e' stata una riga di Python sulla tua macchina. E'
esattamente la ridondanza che il paragrafo 8 ti chiede di scrivere comunque a
mano: se ti affidi a quella dell'SDK, stai delegando a un'implementazione che
puo' cambiare un controllo che e' il tuo confine di sicurezza.

## 6. La forma non e' il contenuto

La frase da imparare: la chiamata a strumenti garantisce la forma, non il
contenuto.

> "I Canadair non sull'incendio di Cirella, ci sono le linee elettriche."

Il traduttore produce questo, che e' JSON valido, conforme allo schema, con
tutti i campi nei loro intervalli:

```json
{
  "constraint_id": "c1",
  "kind": "FORCE_ASSIGNMENT",
  "vehicle_selector": {"field": "vehicle_kind", "values": ["canadair"]},
  "fire_selector": {"field": "fire_comune", "values": ["Diamante"]},
  "min_count": 1,
  "enforcement": "hard",
  "source_span": "I Canadair non sull'incendio di Cirella"
}
```

Due errori indipendenti, nessuno rilevabile dallo schema. Il primo e'
`FORCE_ASSIGNMENT` invece di `FORBID_ASSIGNMENT`, cioe' il vincolo rovesciato:
il solver mandera' i Canadair esattamente dove il coordinatore ha detto di non
mandarli, e il piano risultera' fattibile perche' soddisfa il vincolo che gli e'
stato dato. Il secondo riguarda il luogo: Cirella e' una frazione di Diamante, e
mappare "Cirella" su "Diamante" e' corretto solo se lo scenario codifica i
focolai per comune, mentre se il focolaio si identifica per `fire_id` quel
selettore non seleziona nulla e il vincolo diventa silenziosamente vuoto. Un
vincolo duro che non seleziona nulla e' il peggiore degli esiti, perche' non
produce errori: produce un piano che ignora l'ordine e sembra sano.

Da qui tre scelte di progetto: `source_span`, che porta il frammento letterale
dell'ordine dentro ogni vincolo; il validatore indipendente, che non si fida dei
passi precedenti; e i selettori per attributo invece che per enumerazione, che
si risolvono contro lo scenario e permettono di accorgersi che un selettore e'
vuoto.

## 7. JSON Schema: il vocabolario che conta

JSON Schema e' un linguaggio, scritto in JSON, per descrivere la forma di
documenti JSON. Servono sei costrutti.

I tipi sono `"integer"`, `"number"`, `"string"`, `"boolean"`, `"array"`,
`"object"` e `"null"`. `MaxResponseTime.minutes` e' un `integer` perche' un
minuto e mezzo non ha senso come tetto al tempo di arrivo; `confidence` e' un
`number` perche' vive fra 0.0 e 1.0. Un campo facoltativo come
`ReserveVehicle.until_minute`, che in Python e' `Optional[int]`, non diventa un
`integer` ma un `anyOf` fra un intero e `null`: e' la prima delle piccole
sorprese che rendono lo schema generato diverso da quello che avresti scritto a
mano.

`required` elenca le proprieta' obbligatorie, e una proprieta' dichiarata ma
assente da `required` e' facoltativa. E' il punto dove Pydantic e la modalita'
stretta litigano, e ci torno fra poco.

`enum` elenca i valori ammessi, `const` ne impone uno solo. Nel nostro schema la
differenza si vede bene, e non e' quella che ci si aspetta: `Enforcement`,
`ZoneKind`, `ObjectiveTerm` e `SelectorField` compaiono come `enum` dentro
`$defs`, mentre `ConstraintKind` non compare affatto. Il motivo e' che ogni
vincolo dichiara il proprio `kind` come `Literal` di un valore solo, e Pydantic
lo traduce in un `const` scritto sul posto. Dentro `ReserveVehicle` il campo
diventa questo:

```json
{"const": "RESERVE_VEHICLE", "default": "RESERVE_VEHICLE",
 "title": "Kind", "type": "string"}
```

Non e' un dettaglio estetico. Quel `const` e' cio' che rende risolvibile in un
passo l'unione discriminata del capoverso seguente, e quel `default` e' la
ragione per cui `kind` non finisce in `required`.

`additionalProperties` con `false` vieta le proprieta' non dichiarate, ed e' la
traduzione dello `extra: forbid` presente su ogni nostro modello. Senza, un
campo inventato come `"priority": 9` finirebbe ignorato in silenzio; con, la
validazione fallisce e il fallimento e' visibile.

Il sesto costrutto sono le unioni discriminate, il cuore di `ConstraintSet`. Una
unione dice "qui puo' stare una cosa fra queste dodici", e un validatore
dovrebbe provarle tutte, producendo dodici messaggi di errore inutili quando
fallisce. Una unione discriminata aggiunge un campo il cui valore dice senza
ambiguita' quale variante e'; nel nostro caso e' `kind`:

```python
AnyConstraint = Annotated[
    Union[PrioritizeZone, ReserveVehicle, ..., Unsupported],
    Field(discriminator="kind"),
]
```

Cosi' la validazione diventa un salto diretto, gli errori diventano leggibili
("in ReserveVehicle manca il campo selector"), e il modello ottiene un segnale
forte, perche' scritto `kind` il resto della struttura e' determinato.

Sul salto diretto serve pero' una precisazione, perche' e' facile attribuirlo
alla cosa sbagliata. Pydantic serializza l'unione come un `oneOf` di dodici
riferimenti piu' una mappa chiamata `discriminator`, e quella parola promette
piu' di quanto mantenga fuori da Python: non appartiene a JSON Schema, viene
dalla specifica OpenAPI, quindi un validatore JSON Schema puro la ignora e prova
le dodici alternative una per una finche' ne trova una che regge. Il salto
diretto lo fa Pydantic dentro il tuo processo; non e' una proprieta' del
documento che spedisci al modello.

Tre note sui limiti, misurate sullo schema reale e non stimate.
`ConstraintSet.model_json_schema()` produce un documento di 17.714 caratteri con
17 definizioni in `$defs`: le dodici classi di vincolo, il `Selector` e le
quattro enumerazioni di supporto (`Enforcement`, `ZoneKind`, `ObjectiveTerm`,
`SelectorField`).

Prima nota: il `required` di primo livello contiene un solo campo,
`constraint_set_id`. Pydantic ci mette soltanto i campi senza valore di default,
e i nostri ne hanno quasi tutti uno. La modalita' stretta chiede due cose: che
ogni oggetto porti `additionalProperties: false` e che ogni proprieta'
dichiarata risulti anche richiesta. La prima e' gia' soddisfatta senza fare
niente, perche' lo `extra: forbid` dei nostri modelli la genera su ogni
definizione. La seconda no, quindi lo schema generato non si puo' spedire cosi'
com'e': va trasformato riempiendo `required`, e la trasformazione va scritta e
testata come qualsiasi altro codice.

Quella trasformazione ha un prezzo che conviene vedere prima di pagarlo.
Rendere obbligatorio `enforcement` e' un guadagno, perche' costringe il modello
a dichiarare se il vincolo e' duro o morbido invece di lasciar decidere al
valore di default (paragrafo 8). Rendere obbligatorio un campo davvero
facoltativo come `ReserveVehicle.until_minute` e' diverso: o ammetti `null` fra
i valori, e Pydantic per fortuna lo genera gia' come `anyOf` fra intero e
`null`, oppure obblighi il modello a inventarsi un minuto che nessuno ha
detto.

Seconda nota: `constraints.items` e' un `oneOf`, e l'elenco dei costrutti
sostenuti che la documentazione dell'API pubblica comprende `enum`, `const`,
`anyOf`, `allOf` e i riferimenti `$ref`/`$defs`, senza nominare `oneOf`. Non
l'ho provato con una chiamata vera, quindi lo tratto come non sostenuto finche'
non risulta il contrario: e' l'ipotesi prudente, e costa poco. La correzione e'
meccanica. `oneOf` chiede che valga esattamente una alternativa, `anyOf` che ne
valga almeno una; ma il discriminante `const` su `kind` rende le dodici
alternative mutuamente esclusive gia' di per se', perche' un documento con
`kind` uguale a `RESERVE_VEHICLE` non puo' soddisfare nessuna delle altre
undici. Quindi qui `anyOf` dice esattamente la stessa cosa, e sostituirlo non
cambia l'insieme dei documenti accettati.

Terza nota, la piu' rilevante: `MaxResponseTime.minutes` e' generato come
`{"type": "integer", "minimum": 1, "maximum": 600}` e la grammatica garantira'
soltanto che li' ci sia un intero, quindi `minutes: 99999` resta producibile
anche in modalita' stretta. La validazione di ritorno non e' una ridondanza
difensiva: e' l'unico posto dove quel controllo esiste davvero.

## 8. Pydantic v2: generare lo schema, validare il ritorno

Pydantic v2 fa due lavori opposti, ed e' il motivo per cui il contratto dei
vincoli e' scritto in Pydantic e non in dataclass come il resto del dominio.
Verso l'esterno genera lo schema dalle stesse classi che il resto del sistema
usa: quando qualcuno aggiunge un campo a `ReserveVehicle`, lo schema mandato al
modello cambia nello stesso commit.

```python
from tagliafuoco.nlu.schema import ConstraintSet

CONSTRAINT_TOOL = {
    "name": "emit_constraint_set",
    "description": (
        "Registra i vincoli operativi contenuti nell'ordine del coordinatore. "
        "Chiamalo esattamente una volta per ogni ordine, sempre, anche quando "
        "l'ordine non contiene nessun vincolo applicabile: in quel caso passa "
        "una lista vuota oppure un vincolo UNSUPPORTED con la motivazione."
    ),
    "input_schema": ConstraintSet.model_json_schema(),
}
```

Due dettagli non decorativi: il nome e' un verbo di registrazione e non di
decisione (`emit_constraint_set`, non `plan_response`), perche' e' la prima cosa
che il modello legge e stabilisce il suo ruolo; e la descrizione dice quando
chiamarlo, non solo cosa fa, ed e' li' che si insegna l'astensione.

Verso l'interno Pydantic valida il ritorno. Il campo `input` del blocco
`tool_use` e' un dizionario Python, dato non fidato come il corpo di una
richiesta HTTP:

```python
from pydantic import ValidationError
from tagliafuoco.nlu.schema import ConstraintSet

# tool_choice forza la chiamata, ma il blocco va cercato con un default: senza,
# next() solleva StopIteration e la traduzione muore per l'eccezione sbagliata.
block = next((b for b in response.content if b.type == "tool_use"), None)
if block is None:
    constraint_set = None          # nessuna chiamata: si ricade sulle regole
else:
    try:
        constraint_set = ConstraintSet.model_validate(block.input)
    except ValidationError as err:
        # err.errors() dice campo per campo cosa non torna
        constraint_set = None
```

Il `try` intercetta cio' che la grammatica non copre: intervalli numerici,
lunghezze di stringa, unicita' dei `constraint_id`, e i validatori scritti a
mano, che sono regole di dominio inesprimibili in uno schema. `TimeWindow`
verifica che `from_minute` preceda `to_minute`, quindi "gli aerei dalle venti
alle otto" viene respinto invece di produrre una finestra vuota; `Selector`
verifica che, se il campo non e' `all`, ci sia almeno un valore, il che
intercetta il selettore vuoto del paragrafo 6.

Resta una cosa che Pydantic non intercetta, e che vale la pena conoscere perche'
e' il genere di trappola che nasce dai valori di default. `BaseConstraint`
dichiara `enforcement: Enforcement = Enforcement.HARD`, quindi un vincolo che
arriva senza quel campo diventa duro. I docstring di `PrioritizeZone` e di
`ObjectiveWeight` dicono che quei due sono sempre morbidi per costruzione, ma il
default ereditato resta `HARD` e nessun validatore lo corregge: se il modello
omette `enforcement`, un "prima le zone vicino alle case" entra nel solver come
vincolo duro. Il prompt deve quindi chiedere `enforcement` esplicito su ogni
vincolo, e chi scrive `nlu/translator.py` farebbe bene a normalizzare quei due
tipi a `SOFT` dopo la validazione. E' un esempio concreto di una regola
generale: un campo con un valore di default non e' obbligatorio, quindi non
finisce in `required`, quindi il modello puo' tacerlo, e il valore che ottieni
e' quello che ha scelto chi ha scritto la classe, non chi ha parlato.

## 9. Temperatura zero: cosa significa e cosa non significa

L'intenzione di progetto e' togliere la varianza del campionamento: lo stesso
ordine, lo stesso scenario e lo stesso prompt devono dare la stessa traduzione,
perche' un benchmark su una cosa che cambia a ogni esecuzione non misura niente.
Nei termini della formula del paragrafo 2, significa portare `T` a zero e
scegliere sempre il token di logit massimo. Seguono tre precisazioni.

Temperatura zero non significa "il modello diventa deterministico", significa
"il passo di campionamento smette di introdurre casualita'", che e' molto meno.
Su infrastruttura distribuita restano altre sorgenti di variabilita': le somme
in virgola mobile non sono associative e l'ordine di riduzione dipende da come
il calcolo viene ripartito sull'acceleratore, la dimensione del batch cambia
quale kernel viene selezionato, due richieste identiche possono finire su nodi
con versioni diverse. Due logit vicinissimi possono scambiarsi di posto: e'
raro, ma se prometti determinismo bit a bit prometti una cosa che non puoi
mantenere.

Non significa nemmeno "il modello smette di sbagliare": se il logit piu' alto e'
quello di `FORCE_ASSIGNMENT` quando la risposta giusta e' `FORBID_ASSIGNMENT`,
il decoding avido sceglie l'errore con la massima sicurezza possibile, ogni
volta. Rende l'errore riproducibile, il che aiuta il debug e non la correttezza.

Terza precisazione, ed e' quella che cambia il codice. Il modello configurato in
`config.py` e' `claude-sonnet-5`, e sui modelli di questa generazione i
parametri di campionamento sono stati rimossi: `temperature`, `top_p` e `top_k`
non esistono piu' come parametri della richiesta, e una chiamata che li porta
viene rifiutata con un errore 400. Questo risulta dalla documentazione corrente
dell'API; non l'ho verificato con una chiamata vera, perche' il repository deve
girare senza chiave. La superficie dell'API cambia da una generazione all'altra,
quindi la regola di condotta e' ricontrollarla quando scrivi
`nlu/translator.py`, non fidarsi di questo paragrafo fra un anno.

La conseguenza e' che "usare temperature 0" non e' piu' una cosa che si scrive:
l'intenzione resta valida e va realizzata con gli strumenti che restano, cioe'
lo schema chiuso, la modalita' stretta, il prompt di sistema fissato byte per
byte e la validazione al ritorno. Vale la pena notare che la rimozione e'
coerente con tutto il resto del capitolo. Quel parametro prometteva un
determinismo che l'infrastruttura non poteva mantenere, e toglierlo e' piu'
onesto che lasciarlo li' a farsi scrivere nei prompt come se bastasse.

## 10. Il prompt che conta davvero

Per un compito di traduzione a repertorio chiuso contano cinque cose, e le altre
no.

La descrizione del compito e del ruolo. Non "sei un assistente utile", ma una
dichiarazione di perimetro: il modello traduce ordini in vincoli, non pianifica,
non stima, non propone. Va detto esplicitamente perche' i modelli sono
addestrati a essere utili, e un modello che vuole essere utile davanti a un
ordine ambiguo tende a colmare i buchi, che e' qui il comportamento da
sopprimere.

Il repertorio chiuso esplicito. I dodici tipi vanno elencati con una riga
ciascuno che dice quando si usa quel tipo e non gli altri, soprattutto per le
coppie confondibili. Le due che generano piu' errori sono `ReserveVehicle`
contro `ExcludeVehicle` (un mezzo tenuto da parte contro un mezzo non
impiegabile) e `PrioritizeZone` contro `ObjectiveWeight` (alzare il peso di una
zona geografica contro ripesare un termine globale dell'obiettivo): "prima le
zone vicino alle case" e' il primo caso, "conta piu' arrivare presto che
risparmiare ore di volo" e' il secondo.

Esempi few-shot scelti per i casi ambigui. Few-shot significa mettere nel prompt
alcuni esempi svolti di input e output attesi, e la regola e' che non servono a
mostrare il caso facile, che il modello risolverebbe comunque, ma a piantare
bandiere sui confini: un ordine con due vincoli in una frase sola, un mezzo
chiamato per soprannome, una preferenza invece di un comando per insegnare la
distinzione fra `hard` e `soft`, un orario parlato ("fino alle otto") da
convertire nei minuti dalla mezzanotte che lo schema richiede.

Gli esempi negativi, che insegnano ad astenersi. Un esempio negativo e' una
coppia in cui l'output atteso e' un `Unsupported` con la motivazione, per
esempio "chiamate la Protezione Civile e fatevi mandare due squadre da Potenza":
nessun vincolo del repertorio rappresenta una richiesta di risorse esterne alla
flotta. Senza esempi negativi il modello impara solo a produrre vincoli, e un
modello che ha imparato solo a produrre vincoli ne produce sempre.

Il contesto dello scenario, senza il quale il resto non funziona. "L'elicottero
di Lorica resta di riserva" non e' traducibile in astratto, perche' identifica
un oggetto solo dentro un certo stato del mondo, quindi il prompt include una
fotografia compatta:

```
Mezzi disponibili adesso:
  CAN_01  canadair            BASE_LMZ      Lamezia Terme (CZ)  disponibile
  CAN_02  canadair            BASE_LMZ      Lamezia Terme (CZ)  in manutenzione
  MED_01  elicottero_medio    BASE_ELI_MOR  Mormanno (CS)       disponibile
  LGT_02  elicottero_leggero  BASE_ELI_MOR  Mormanno (CS)       disponibile
  MED_02  elicottero_medio    BASE_ELI_LOR  Lorica (CS)         disponibile

Focolai attivi:
  F-0412  Longobucco (CS)    2.1 km2  severita' alta       in WUI
  F-0418  San Luca (RC)      0.6 km2  severita' moderata   non in WUI
```

Le matricole e le basi sono quelle vere di `data/static/mezzi.json` e
`data/static/basi.json`. L'ultima colonna no: lo stato di un mezzo appartiene
allo scenario e non al file statico, dove `CAN_02` risulta disponibile, e qui lo
metto in manutenzione per avere un caso concreto sotto gli occhi. Adesso
"l'elicottero di Lorica" e' risolvibile, perche' a Lorica c'e' un aeromobile
solo, `MED_02`, che nei dati si chiama AW139 Sila Grande. Il vincolo corretto e' un
`ReserveVehicle` con `selector` su `vehicle_id`, oppure su `base_id` con valore
`BASE_ELI_LOR`, e la scelta non e' indifferente: il selettore su `base_id`
continua a significare qualcosa anche se la flotta cambia fra una finestra e
l'altra, ed e' piu' fedele a quello che il coordinatore ha detto, perche' lui ha
nominato un posto e non una matricola.

Il contesto serve anche a rendere visibili le ambiguita' vere, e questo scenario
ne contiene una. "L'elicottero di Mormanno" non si risolve, perche' a Mormanno
ce ne sono due, un medio e un leggero, e il coordinatore non ha detto quale. Il
comportamento corretto e' segnalarlo, non sceglierne uno: un traduttore che tira
a indovinare e non lo dice e' peggiore di uno che emette `Unsupported` con la
ragione scritta. Nota infine dove sta questo blocco dentro il prompt: in fondo,
dopo lo schema e gli esempi, perche' e' la parte che cambia a ogni finestra e
non deve invalidare la cache di quella stabile (paragrafo 13).

## 11. La riparazione, e perche' un solo tentativo

Quando `model_validate()` solleva un `ValidationError`, la strada ovvia e'
rimandare l'errore al modello e chiedere una correzione. Si chiama riparazione,
o repair loop. Non si rimanda la frase "errore di validazione", si rimanda il
contenuto di `err.errors()`, che dice per ogni problema il percorso del campo,
il messaggio e il valore ricevuto, insieme all'output precedente, perche' il
modello deve correggere quello e non ricominciare da capo. Sul nostro schema, un
`minutes` a 99999 produce percorso `constraints.0.MAX_RESPONSE_TIME.minutes`,
messaggio "Input should be less than or equal to 600" e input 99999: il nome
della variante compare dentro il percorso perche' l'unione e' discriminata, ed
e' un'informazione in piu' da passare al modello, non rumore da ripulire.

Il primo tentativo recupera la maggior parte dei casi, perche' gli errori tipici
sono meccanici: un campo dimenticato, un enum scritto male, un numero fuori
intervallo. Il secondo recupera molto meno. Dal terzo in poi si osserva la
stessa struttura sbagliata con variazioni cosmetiche, perche' il problema non e'
una svista ma una comprensione errata dell'ordine, e ripetere non cambia la
comprensione. Non ho una misura mia per questa curva: e' un comportamento
riportato in modo consistente da chi costruisce questi sistemi, e lo presento
come tale.

Tagliafuoco si ferma dopo un tentativo per tre ragioni. Esiste un'alternativa
che costa zero, cioe' `nlu/rule_translator.py`, un traduttore deterministico a
regole che copre il sottoinsieme piu' frequente degli ordini: non e' un ripiego
degradato, e' il percorso normale quando manca la chiave API, e deve funzionare
comunque perche' il repository deve girare senza chiavi. C'e' il budget di
tempo, perche' il coordinatore parla e aspetta e ogni tentativo somma secondi,
mentre il traduttore a regole risponde in millisecondi: la stessa logica che
tiene `SOLVER_MAX_SECONDS` a 20 e non a infinito. E infine, la ragione che a un
colloquio pesa di piu', il fallimento ripetuto e' un segnale: se il modello non
produce un `ConstraintSet` valido dopo aver visto l'errore, la spiegazione piu'
probabile e' che l'ordine esca dal repertorio, e allora la risposta giusta e'
l'astensione esplicita con `Unsupported` e `warnings` popolato. Trattare il
fallimento come informazione e non come incidente e' la differenza fra un
sistema che si ferma e uno che mente.

## 12. Come si valuta un traduttore

Il benchmark e' descritto nella sezione 7 di `SPEC.md`: un corpus di ordini in
italiano con il `ConstraintSet` atteso annotato a mano.

Corrispondenza esatta dell'insieme: si confronta l'insieme prodotto con quello
atteso e si conta uno solo se coincidono. Il confronto non puo' guardare i campi
accessori, perche' `constraint_id` e' arbitrario, `rationale` e' testo libero e
`confidence` e' una dichiarazione: va fatto sui campi che cambiano il
comportamento del solver, cioe' `kind`, i selettori risolti e i parametri
numerici.

Precisione e richiamo per tipo di vincolo:

```
precisione = veri positivi / (veri positivi + falsi positivi)
richiamo   = veri positivi / (veri positivi + falsi negativi)
```

dove un vero positivo e' un vincolo di quel tipo prodotto e corretto, un falso
positivo e' prodotto ma non presente nell'ordine, un falso negativo e' presente
nell'ordine e non prodotto. La precisione risponde a "quando dici
RESERVE_VEHICLE, hai ragione?", il richiamo a "quando c'e' un RESERVE_VEHICLE,
te ne accorgi?". Tenerle separate per tipo e' essenziale perche' i tipi non sono
ugualmente frequenti ne' ugualmente pericolosi: `Unsupported` sara' raro,
`PrioritizeZone` frequente, e una media li appiattisce entrambi.

Il tasso di allucinazione e' la frazione di vincoli emessi che non corrispondono
a niente nell'ordine, e `source_span` rende la misura semiautomatica, perche' se
il frammento dichiarato non compare nel testo dell'ordine il vincolo e'
sospetto. Il tasso di astensione corretta e' la frazione di casi fuori
repertorio in cui il traduttore ha emesso `Unsupported`, e richiede che il
corpus contenga davvero ordini fuori repertorio annotati come tali, altrimenti
il benchmark e' cieco su meta' del comportamento che ci interessa.

Perche' la sola accuratezza media nasconde il problema piu' grave. Corpus di
cento ordini, duecento vincoli attesi. Il traduttore A ne produce 190 corretti e
ne manca 10, senza inventarne nessuno; il traduttore B ne produce 190 corretti,
ne manca 10, e in piu' ne inventa 10 che non stanno in nessun ordine. Misurando
"quanti vincoli attesi hai trovato" entrambi fanno 95 per cento e sembrano
equivalenti, ma non lo sono: i dieci mancati di A sono visibili, perche' il
coordinatore guarda l'elenco, vede che il suo ordine sul volo diurno non c'e' e
lo ripete; i dieci inventati di B entrano nel solver, restringono lo spazio
delle soluzioni e producono un piano che rispetta ordini che nessuno ha dato. Se
sono `hard` possono rendere il problema infattibile e far scartare un piano
valido; se sono `soft` spostano l'ottimo in silenzio e nessuno se ne accorge,
perche' un piano leggermente peggiore non ha nessun segno distintivo. E'
l'asimmetria fra errore rumoroso ed errore silenzioso, e in un sistema di
supporto alle decisioni il secondo e' sempre peggiore: per questo servono almeno
tre numeri guardati insieme, cioe' richiamo, allucinazione e astensione
corretta.

## 13. Costi, latenza e caching del prompt

I numeri usano il listino corrente per `claude-sonnet-5`, il modello indicato in
`config.py`: 2,00 dollari per milione di token in ingresso e 10,00 per milione
in uscita. I listini cambiano, quindi il valore duraturo e' il metodo, non la
cifra.

Prima di contare i soldi bisogna contare i token, e conviene separare la
richiesta in due parti. La parte stabile non cambia da una chiamata all'altra:
la definizione dello strumento con dentro lo schema, il prompt di sistema, gli
esempi few-shot. La parte volatile cambia ogni volta: il contesto dello scenario
del paragrafo 10 e la frase del coordinatore.

Dello schema ho una misura vera: `ConstraintSet.model_json_schema()`
serializzato con `json.dumps` occupa 17.714 caratteri. I token invece non li ho
misurati. Per JSON il rapporto fra caratteri e token sta tipicamente fra tre e
quattro a uno, quindi il solo schema vale all'incirca fra 4.400 e 5.900 token, e
sommandoci il prompt di sistema con gli esempi la parte stabile arriva intorno
agli ottomila. Trattalo come una stima dichiarata: il modo corretto di saperlo
e' chiamare `client.messages.count_tokens` sul prompt reale, e non l'ho fatto
perche' il repository deve girare senza chiave. Conto quindi su una chiamata di
riferimento con 8.000 token di parte stabile, 400 di parte volatile e 250 di
uscita:

```
ingresso: (8.000 + 400) token x  2,00 $/1.000.000 = 0,01680 $
uscita:            250 token x 10,00 $/1.000.000 = 0,00250 $
totale                                            = 0,01930 $
```

Meno di due centesimi di dollaro, irrilevante nel caso singolo. Il punto e' che
quegli 8.000 token si pagano identici a ogni chiamata, perche' l'API e' senza
stato e ogni richiesta rimanda tutto il contesto: qui la parte stabile da sola
vale l'82,9 per cento del costo (0,01600 dollari su 0,01930) e non porta nessuna
informazione nuova rispetto alla chiamata precedente.

Qui entra il caching del prompt: si marca una porzione iniziale della richiesta
come memorizzabile, il servizio ne conserva lo stato interno e, alla richiesta
successiva che comincia con esattamente gli stessi byte, riusa quello stato
invece di rielaborarlo. I token memorizzati si pagano circa un decimo del prezzo
normale in lettura, e circa 1,25 volte la prima volta, quando la voce viene
scritta:

```
lettura da cache: 8.000 token x  0,20 $/1.000.000 = 0,00160 $
ingresso nuovo:     400 token x  2,00 $/1.000.000 = 0,00080 $
uscita:             250 token x 10,00 $/1.000.000 = 0,00250 $
totale                                            = 0,00490 $
```

Il risparmio e' del 74,6 per cento per chiamata. Su duecento chiamate, l'ordine
di grandezza di una sessione di benchmark, si passa da 3,86 a circa 1,00
dollari: 0,0233 per la prima, che scrive la voce in cache pagando la parte
stabile a 1,25 volte il prezzo, piu' 0,0049 per ciascuna delle altre 199. Nota
che la proporzione del risparmio dipende da quanto pesa la parte stabile: piu'
lo schema cresce, piu' il caching conta.

Il beneficio piu' rilevante pero' non e' il denaro, sono i millisecondi: i token
letti dalla cache non vengono rielaborati, quindi la latenza del prefisso lungo
scompare quasi del tutto, e in sala operativa la latenza e' la valuta vera. C'e'
una seconda voce di latenza, e riguarda lo schema: la prima richiesta con uno
schema nuovo ne paga la compilazione, e le successive riusano il risultato per
ventiquattro ore. In produzione non si nota, perche' lo schema e' sempre lo
stesso. Si nota il giorno in cui misuri i tempi subito dopo aver aggiunto un
campo a `ReserveVehicle` e leggi un numero peggiore del vero.

Tre condizioni perche' il caching funzioni, tutte trappole classiche. Lavora su
prefisso esatto, byte per byte, quindi un timestamp o un dizionario serializzato
con `json.dumps` senza `sort_keys=True` dentro il prompt di sistema azzerano la
cache senza che nessun errore te lo dica. L'ordine di composizione conta,
perche' gli strumenti vengono prima del prompt di sistema, che viene prima dei
messaggi: il contenuto stabile (schema ed esempi) va davanti, quello volatile
(contesto dello scenario, poi l'ordine) va dietro. Ed esiste un prefisso minimo
sotto il quale la memorizzazione non avviene: per `claude-sonnet-5` la
documentazione indica 1024 token, e altri modelli hanno soglie diverse, fra 512
e 4096. Sotto soglia non ricevi un errore, ricevi zero token letti dalla cache.
Si verifica guardando i contatori d'uso nella risposta,
`cache_creation_input_tokens` e `cache_read_input_tokens`: se le letture restano
a zero su richieste che dovrebbero condividere il prefisso c'e' un invalidatore
silenzioso da cercare, e il sospettato numero uno e' qualcosa che contiene
l'ora.

Una quarta cosa non e' una trappola ma una scelta da fare. La voce in cache dura
cinque minuti per default e si puo' portare a un'ora, al prezzo di una scrittura
che costa il doppio invece di 1,25 volte. Il conto e' semplice: con cinque
minuti bastano due chiamate ravvicinate per rientrare della scrittura, con
un'ora ne servono almeno tre. Un coordinatore che parla a raffica sta dentro i
cinque minuti; una sessione di benchmark che gira mezz'ora con pause fra un caso
e l'altro e' il caso in cui l'ora conviene.

## 14. Cosa devi saper spiegare

**Perche' un modello linguistico non garantisce JSON valido, se poi lo produce
quasi sempre?** Perche' non ha un parser al suo interno: genera un token alla
volta campionando da una distribuzione, e la validita' sintattica e' una
regolarita' statistica appresa dal corpus, non un invariante strutturale. "Quasi
sempre" descrive un comportamento osservato, non una garanzia: c'e' un tasso di
guasto che non controlli e che puo' cambiare quando cambia il modello o quando
si allunga l'output.

**Qual e' la differenza fra chiedere JSON nel prompt, usare tool use e vincolare
il decoding?** Nel primo caso non c'e' nessun meccanismo: chiedi, speri, e
ripulisci la risposta a mano. Nel secondo dichiari uno schema di input e ricevi
un oggetto strutturato invece di una stringa, il che elimina il parsing e guida
fortemente il modello ma non lo obbliga. Nel terzo lo schema diventa un automa
che maschera i token illegali prima del campionamento, quindi l'output fuori
grammatica e' impossibile per costruzione, e sull'API si ottiene con `strict:
true` sullo strumento oppure con `output_config.format` sulla risposta. In tutti
e tre i casi la conformita' semantica resta a carico di qualcun altro, e quel
qualcun altro nel nostro progetto e' Pydantic piu' il validatore indipendente.

**Cosa vuol dire che tool use garantisce la forma e non il contenuto?** Che puoi
ricevere un documento perfettamente conforme allo schema e completamente
sbagliato. L'ordine dice "i Canadair non sull'incendio di Cirella" e il
traduttore emette un `FORCE_ASSIGNMENT` invece di un `FORBID_ASSIGNMENT`: campi
validi, intervalli rispettati, unione discriminata corretta, e il solver manda i
Canadair esattamente dove non dovevano andare, producendo un piano fattibile
perche' soddisfa il vincolo che gli e' stato dato.

**Se usi il decoding vincolato, perche' validi ancora con Pydantic?** Perche' le
implementazioni correnti non sostengono i vincoli numerici ne' quelli di
lunghezza: `MaxResponseTime.minutes` e' dichiarato fra 1 e 600, ma la grammatica
garantisce solo che sia un intero, quindi 99999 resta producibile. Gli SDK
Python e TypeScript quei vincoli li tolgono dallo schema spedito e li verificano
lato client, il che e' comodo e non cambia la sostanza: il controllo esiste
perche' qualcuno lo esegue dopo, non perche' il modello non potesse violarlo. In
piu' i validatori scritti a mano, come quello che impone `from_minute` minore di
`to_minute` o quello che vieta un selettore vuoto, esprimono regole di dominio
che nessuno schema rappresenterebbe.

**Perche' temperature 0, e cosa ti impedisce di dire che il sistema e'
deterministico?** L'obiettivo e' togliere la varianza del campionamento, perche'
un benchmark su un traduttore che cambia risposta a ogni esecuzione non misura
niente. Ma temperatura zero rimuove solo la casualita' di quel passo: restano le
somme in virgola mobile non associative, la dipendenza del kernel dalla
dimensione del batch e le differenze di versione fra nodi, che possono far
scambiare di posto due logit vicini. La formulazione corretta e' "nessuna
varianza di campionamento". Va aggiunto che sul modello configurato in
`config.py` la domanda e' diventata teorica: i parametri di campionamento sono
stati rimossi dall'API su questa generazione di modelli e una richiesta che li
porta riceve un errore 400, quindi l'intenzione si realizza con lo schema
chiuso, la modalita' stretta, il prompt fissato byte per byte e la validazione
al ritorno.

**Perche' il tasso di allucinazione conta piu' dell'accuratezza media, e perche'
riprovi una sola volta?** La media nasconde l'errore peggiore: due traduttori
che trovano entrambi 190 vincoli su 200 sembrano equivalenti, ma se uno ne
inventa anche dieci quei dieci entrano nel solver e restringono lo spazio delle
soluzioni per ordini che nessuno ha dato, e un vincolo inventato `soft` sposta
l'ottimo in silenzio mentre un vincolo mancato e' visibile e il coordinatore lo
ripete. Sulla riparazione: esiste un'alternativa che costa zero (il traduttore a
regole, percorso normale in assenza di chiave API), ogni tentativo aggiunge
secondi a una catena che ha gia' venti secondi di solver, e un fallimento che
persiste dopo aver mostrato l'errore segnala che l'ordine esce dal repertorio,
quindi la risposta giusta e' `Unsupported`.

**Perche' non basta mandare lo schema, e serve anche il contesto dello
scenario?** Perche' molti ordini non sono traducibili in astratto: "l'elicottero
di Lorica" identifica un oggetto solo dentro un certo stato del mondo, quindi
serve sapere quali mezzi esistono adesso, dove sono basati e quali sono
disponibili. Senza contesto il modello o inventa una matricola o produce un
selettore che non seleziona nulla, e un selettore vuoto produce un piano che
ignora l'ordine e sembra sano. Il contesto serve anche al contrario, cioe' a far
emergere le ambiguita': davanti a "l'elicottero di Mormanno", dove il progetto
ne ha due, la risposta giusta non e' sceglierne uno ma dirlo.

## 15. Per approfondire

Sulla meccanica della generazione autoregressiva, parti da una spiegazione
tecnica ma leggibile dei transformer decoder, di quelle che mostrano il
passaggio dai logit alla softmax con i numeri sotto gli occhi, e cerca materiale
che faccia vedere l'effetto della temperatura su una distribuzione reale. Sulla
tokenizzazione il concetto da approfondire e' il byte pair encoding, e la cosa
utile non e' l'algoritmo in se' ma le sue conseguenze, cioe' perche' i numeri
lunghi si spezzano male e perche' l'italiano consuma piu' token dell'inglese per
la stessa informazione, con effetti diretti sul costo dei nostri prompt.

Sul decoding vincolato, cerca la letteratura sulla generazione guidata da
grammatica e le implementazioni open source che compilano JSON Schema in automi
a stati finiti: e' il punto in cui la teoria dei linguaggi formali, studiata per
i compilatori, torna utile in un contesto inaspettato.

Sulla specifica di JSON Schema bastano le sezioni della documentazione ufficiale
su `type`, `enum`, `const`, `required`, `additionalProperties` e le composizioni
`oneOf`, `anyOf` e `allOf`. Su Pydantic v2 servono tre pagine: la generazione
dello JSON Schema, le unioni discriminate, e gli errori di validazione, perche'
la struttura di `ValidationError.errors()` e' quello che rimandi al modello nel
giro di riparazione.

Sull'API dei modelli, la documentazione del fornitore va consultata al momento
in cui scrivi il codice, non ricordata: la superficie cambia, i parametri di
campionamento sono stati rimossi sui modelli recenti, e i listini si aggiornano.
Infine, sulla valutazione, cerca materiale sulla costruzione di insiemi di
valutazione per compiti di estrazione strutturata, con attenzione a come si
annota un corpus in modo che due persone diverse producano la stessa
annotazione: e' il lavoro meno appariscente del progetto e quello che determina
se le metriche del paragrafo 12 significano qualcosa.
