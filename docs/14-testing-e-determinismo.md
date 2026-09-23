# Testing e determinismo

Come si prova che un sistema di ottimizzazione fa quello che dice, quando
nessuno conosce in anticipo la risposta giusta.

Indice dei paragrafi:
1. Il problema: testare codice di cui non conosci l'output
2. Le quattro famiglie di test che usa questo progetto
3. Le fixture di pytest e perche' contano piu' di quanto sembri
4. Test basati su proprieta'
5. Test metamorfici
6. Il determinismo come requisito, non come comodita'
7. Le trappole del non determinismo in Python
8. Copertura: a cosa serve e a cosa non serve
9. I marcatori e la separazione fra test veloci, test di rete e test con modello
10. Come e' organizzata la suite di Tagliafuoco
11. Cosa devi saper spiegare
12. Per approfondire

---

## 1. Il problema: testare codice di cui non conosci l'output

Testare una funzione che somma due numeri e' facile: sai che due piu' tre fa
cinque, scrivi l'asserzione e hai finito. Testare un risolutore di
ottimizzazione non funziona cosi'.

Se avessi un modo indipendente e rapido per sapere quale sia il piano ottimo per
ventisei mezzi e trenta focolai, non ti servirebbe il risolutore. Il fatto che
calcolare la risposta sia difficile e' proprio il motivo per cui hai scritto il
programma, e quindi non puoi usare la risposta come riferimento nel test. Questo
si chiama **problema dell'oracolo**: manca la fonte autorevole con cui
confrontare l'output.

Il problema dell'oracolo compare in tutto il progetto, non solo nel risolutore.
Qual e' il raggruppamento giusto di quattrocento pixel caldi in focolai? Non
esiste una risposta unica: dipende da eps, e eps e' una scelta. Qual e' la
traduzione giusta di una frase ambigua di sala operativa? Spesso ce ne sono due
difendibili.

La via d'uscita non e' una sola, sono quattro, e conviene conoscerle tutte
perche' a un colloquio questa e' esattamente la domanda che distingue chi ha
scritto test per abitudine da chi ha capito a cosa servono.

**Prima via: le proprieta' invarianti.** Non so quale sia il piano ottimo, ma so
che qualunque piano corretto deve rispettare certe cose. Nessun mezzo puo' essere
in due posti nello stesso istante. Nessun mezzo puo' erogare piu' acqua di
quanta ne trasporti moltiplicata per il numero di viaggi. Nessun tempo di
arrivo puo' precedere la partenza. Queste proprieta' si controllano su qualunque
output, anche su uno che non hai previsto.

**Seconda via: l'asimmetria fra risolvere e verificare.** Trovare la soluzione e'
difficile, controllarla e' facile. E' la stessa asimmetria che definisce la
classe NP: se qualcuno ti da' un assegnamento di verita' per una formula
booleana, verificare che la soddisfi costa tempo lineare, mentre trovarlo
sembra costare tempo esponenziale. Nel progetto questa asimmetria e' il fondamento
del validatore: il validatore non pianifica, controlla.

**Terza via: le relazioni metamorfiche.** Non so l'output giusto, ma so come deve
cambiare l'output se cambio l'input in un modo preciso. Ne parlo nel paragrafo 5.

**Quarta via: l'implementazione indipendente.** Due programmi scritti separatamente
dalla stessa specifica. E' il validatore, e ha un capitolo tutto suo
(`09-validazione-indipendente.md`).

## 2. Le quattro famiglie di test che usa questo progetto

**Test di esempio.** Il caso classico: input fissato, output atteso scritto a
mano. Servono dove la risposta giusta e' davvero conoscibile: il parsing di una
riga CSV di FIRMS, la conversione di una confidenza categorica in numerica, la
distanza fra due coordinate note. Sono i piu' leggibili e i piu' fragili, perche'
si rompono a ogni cambio di formato.

**Test di proprieta'.** Non fissano l'output, fissano una relazione che deve
valere sempre. Esempio dal progetto: qualunque piano prodotto dal risolutore,
su qualunque scenario, deve passare la validazione. Non serve sapere quale
piano.

**Test di regressione.** Catturano un comportamento corretto conosciuto per
accorgersi se cambia. Nel progetto sono i test di determinismo: risolvo lo stesso
scenario due volte e pretendo lo stesso piano.

**Test negativi.** Costruisco a mano un input sbagliato e pretendo che il sistema
lo rifiuti. Sono la parte piu' importante della suite del validatore: un
validatore che non ha mai detto di no e' un validatore che non sai se funziona.
In `tests/test_validate.py` i piani sono deliberatamente rotti (un mezzo
teletrasportato, un'autonomia sforata, litri impossibili) e il test verifica che
vengano respinti per il motivo giusto, non solo che vengano respinti.

Quell'ultima precisazione conta. Un test che asserisce `assert not result.is_valid`
passa anche se il validatore rifiuta il piano per un motivo sbagliato. Asserisci
sempre sulla regola violata, non solo sull'esito.

## 3. Le fixture di pytest e perche' contano piu' di quanto sembri

Una fixture in pytest e' una funzione decorata con `@pytest.fixture` che
costruisce un pezzo di stato per il test. Il test la chiede dichiarandola come
parametro, e pytest gliela passa.

```python
@pytest.fixture
def canadair(base_lamezia):
    return Vehicle(vehicle_id="CAN_01", ..., water_capacity_liters=6137)

def test_autonomia(canadair):
    assert canadair.endurance_minutes == 240.0
```

La cosa importante non e' la sintassi, e' il ciclo di vita. Per impostazione
predefinita una fixture ha `scope="function"`: viene ricostruita per ogni test
che la usa. Questo e' voluto e costa poco, e protegge dal problema piu' insidioso
di una suite che cresce, cioe' lo stato che sanguina da un test all'altro.

Se metti `scope="module"` o `scope="session"` per risparmiare tempo, la stessa
istanza viene condivisa. Se un test la modifica, i test successivi partono da uno
stato diverso, e cominciano a passare o fallire a seconda dell'ordine in cui
girano. Un test che fallisce solo quando lo esegui insieme agli altri e' uno dei
bug piu' costosi da inseguire.

Nel progetto questo rischio e' reale e non teorico, perche' `Vehicle` porta stato
dinamico: `available_from_minute`, `minutes_flown_today`, `current_position`. Un
risolutore che aggiorna quei campi su un'istanza condivisa inquina lo scenario
successivo. Per questo `registry.load_fleet()` restituisce sempre copie fresche
con `copy.deepcopy`, e per questo le fixture in `tests/conftest.py` hanno lo
scope di default.

`conftest.py` e' il file che pytest carica automaticamente per tutti i test della
cartella e delle sottocartelle. Ci si mettono le fixture condivise e la
configurazione del percorso dei moduli. Non va importato: pytest lo trova da
solo.

Una fixture puo' anche fare pulizia, usando `yield` al posto di `return`:

```python
@pytest.fixture
def cartella_temporanea(tmp_path):
    d = tmp_path / "statici"
    d.mkdir()
    yield d
    # qui si pulisce, ma tmp_path lo fa gia' da solo
```

## 4. Test basati su proprieta'

Un test di esempio prova un input. Un test basato su proprieta' ne prova
centinaia, generati automaticamente, e verifica che una relazione regga per tutti.
In Python la libreria di riferimento e' Hypothesis.

L'idea si spiega meglio con la geodesia. Non so a memoria la distanza fra due
punti qualsiasi della Calabria, ma so tre cose che devono valere sempre:

1. la distanza da un punto a se stesso e' zero;
2. la distanza da A a B e' uguale a quella da B ad A (simmetria);
3. la distanza da A a C non supera la somma di quella da A a B e da B a C
   (disuguaglianza triangolare).

Queste tre proprieta' definiscono una metrica, e una funzione di distanza che le
viola ha un bug, indipendentemente da quale sia il valore giusto. Testarle su
punti generati a caso dentro la bounding box della Calabria trova errori che
nessun test di esempio avrebbe trovato, per esempio l'instabilita' numerica
dell'arcocoseno sulle distanze piccolissime.

La parte piu' utile di uno strumento come Hypothesis non e' la generazione, e' la
**riduzione** (shrinking): quando trova un controesempio, lo semplifica finche'
resta minimale. Invece di dirti che il test fallisce con latitudine
38.9054172834 e longitudine 16.2423918, ti dice che fallisce con due punti
identici. Il secondo messaggio si legge, il primo no.

Nel progetto le proprieta' che vale la pena testare in questo modo sono: le tre
della metrica; il fatto che il clustering con eps piu' grande non possa produrre
piu' cluster di quello con eps piu' piccolo (monotonia); il fatto che il
fabbisogno d'acqua cresca con l'area; il fatto che ogni piano prodotto dal
risolutore passi il validatore.

Hypothesis non e' fra le dipendenze obbligatorie del progetto, ma i test
enumerativi scritti a mano nella suite seguono la stessa logica: fissano
proprieta', non valori.

## 5. Test metamorfici

Sono il caso speciale piu' potente e il meno conosciuto. Un test metamorfico non
conosce l'output, ma conosce **come l'output deve cambiare** quando trasformi
l'input in un modo preciso.

Esempi presi dal progetto:

Se traslo tutti i focolai e tutte le basi di un chilometro verso est, il piano
ottimo deve restare lo stesso a meno dei nomi, perche' tutte le distanze
relative sono invariate. Se cambia, c'e' una dipendenza da coordinate assolute
che non dovrebbe esserci.

Se raddoppio il fabbisogno d'acqua di ogni focolaio lasciando tutto il resto
uguale, la copertura non puo' aumentare. Puo' calare, puo' restare uguale, non
puo' salire.

Se aggiungo un mezzo alla flotta senza toccare nient'altro, il valore
dell'obiettivo non puo' peggiorare, perche' il risolutore puo' sempre ignorare
il mezzo nuovo e ritrovare la soluzione precedente. Questa e' una proprieta'
di monotonia che vale per costruzione, e se un test la vede violata hai trovato
un errore nel modello, non nel risolutore.

Se rimuovo un vincolo morbido, il valore dell'obiettivo non puo' peggiorare.

Nota bene l'ultimo punto: vale per i vincoli morbidi perche' entrano
nell'obiettivo, e vale anche per quelli duri in termini di spazio delle soluzioni
ammissibili. Sono proprio le proprieta' che un errore di segno nella funzione
obiettivo rompe subito, e che un test di esempio non vedrebbe mai.

## 6. Il determinismo come requisito, non come comodita'

Determinismo significa che lo stesso input produce lo stesso output, sempre.

Sembra una comodita' e invece e' un requisito di correttezza scientifica. Il
progetto confronta i piani prodotti dal risolutore con quelli della baseline
sulle stesse finestre della stagione 2025. Se il risolutore non e' deterministico,
la differenza che misuri contiene due cose mescolate: quanto e' migliore il
metodo, e quanto e' fortunata quella particolare esecuzione. Non puoi separarle,
quindi il numero non significa niente.

C'e' anche una ragione pratica. Un bug che si riproduce una volta su dieci e' un
bug che non sistemerai mai, perche' non saprai se la correzione ha funzionato.

In CP-SAT il non determinismo ha una fonte precisa e ben identificata: la ricerca
a portafoglio. Il risolutore lancia piu' strategie in parallelo su piu' thread e
tiene la prima che arriva. Quale arriva prima dipende dallo scheduler del sistema
operativo, quindi cambia fra esecuzioni. Quando ci sono piu' soluzioni ottime
equivalenti, esecuzioni diverse ne restituiscono una diversa, tutte corrette e
tutte con lo stesso valore dell'obiettivo.

Il rimedio e' fissare `random_seed` e mettere `num_workers` a uno. Si paga in
tempo di calcolo, a volte di parecchio, e per questo il progetto tiene due
modalita': la modalita' normale, veloce e a piu' worker, per l'uso interattivo
della demo; la modalita' deterministica, lenta e a un worker, per le valutazioni.
Le costanti stanno in `config.py` come `SOLVER_NUM_WORKERS` e
`SOLVER_NUM_WORKERS_DETERMINISTIC`.

### La differenza misurata sul progetto

Non e' un discorso astratto e si verifica in un minuto. Prendendo uno scenario
con cinque focolai in punti diversi della Calabria, la flotta completa e lo
stesso ordine del coordinatore, e risolvendolo tre volte per modalita':

```
modalita' deterministica (seed fisso, un worker)
   esecuzione 1: impronta d9e71e46a702d77d  FEASIBLE  obiettivo 5.358315
   esecuzione 2: impronta d9e71e46a702d77d  FEASIBLE  obiettivo 5.358315
   esecuzione 3: impronta d9e71e46a702d77d  FEASIBLE  obiettivo 5.358315

modalita' veloce (piu' worker, senza seed fissato)
   esecuzione 1: impronta db61a2dd8b03419a  OPTIMAL
   esecuzione 2: impronta ca0cf2ddaa4702f5  OPTIMAL
   esecuzione 3: impronta 70082c6d1ca001d8  FEASIBLE
```

L'impronta e' l'hash delle assegnazioni ordinate, con mezzo, focolaio, tempi,
numero di cicli e litri. In modalita' deterministica le tre esecuzioni danno lo
stesso piano fino all'ultimo campo. In modalita' veloce ne danno tre diversi, e
due di quei tre sono addirittura dimostrati ottimi: sono soluzioni ottime
equivalenti, tutte corrette, e il risolutore restituisce quella che arriva
prima.

Vale la pena fermarsi sull'ultima riga, perche' e' il punto che a un colloquio
si capisce male. La modalita' veloce non e' sbagliata: e' non riproducibile. Le
tre soluzioni hanno lo stesso valore dell'obiettivo e sono tutte ammissibili.
Il problema non e' la qualita' del piano, e' che un confronto fra metodi fatto
su piani che cambiano a ogni esecuzione misura anche la fortuna, e non c'e' modo
di separare le due cose a posteriori.

C'e' anche un dettaglio da notare: in modalita' deterministica lo stato e'
FEASIBLE invece di OPTIMAL. Con un solo worker e lo stesso limite di tempo il
risolutore non chiude la prova di ottimalita'. La riproducibilita' si paga, e in
questo caso si paga proprio nella moneta che ci si aspetta.

Un punto che conviene saper dire a voce: fissare il seed non rende il risolutore
riproducibile fra versioni diverse della libreria o fra architetture diverse.
Rende riproducibile la stessa esecuzione sulla stessa macchina con la stessa
versione. Per questo `pyproject.toml` blocca `ortools` a una versione esatta e
non a un intervallo.

## 7. Le trappole del non determinismo in Python

Il risolutore e' la fonte piu' ovvia, ma non la piu' frequente. Queste sono le
trappole che vale la pena conoscere perche' compaiono ovunque.

**L'iterazione sugli insiemi.** L'ordine di iterazione di un `set` non e' l'ordine
di inserimento e dipende dai valori di hash. Per le stringhe, Python applica per
impostazione predefinita la randomizzazione degli hash a ogni avvio del processo
(si controlla con la variabile d'ambiente `PYTHONHASHSEED`). Se costruisci un
elenco di identificativi iterando un insieme di stringhe e poi lo passi al
risolutore, l'ordine delle variabili cambia a ogni esecuzione, e con esso la
soluzione scelta fra le equivalenti. Il rimedio e' ordinare esplicitamente:
`sorted(ids)`.

I dizionari in Python 3.7 e successivi conservano invece l'ordine di inserimento,
quindi iterare un `dict` e' sicuro purche' sia sicuro l'ordine in cui lo hai
riempito.

**Il modulo random senza seed.** Ovvio a dirsi, e comunque il caso piu' comune.
Nel progetto ogni generazione (le fixture della stagione, i dati statici) usa un
seed fisso dichiarato in un file.

**L'orologio.** Un test che usa `datetime.now()` fallisce a mezzanotte, o in un
altro fuso, o quando il codice comincia a girare in un altro paese. Nelle fixture
del progetto c'e' `instant`, un istante fissato al 12 agosto 2025 alle 11:30 UTC,
e i test lo usano invece di leggere l'orologio.

**Datetime con e senza fuso.** In Python confrontare un datetime naive con uno
timezone-aware solleva `TypeError`. Il guaio e' che il confronto puo' stare in un
ramo del codice che i test non percorrono, e allora esplode in produzione. La
regola del progetto e' che ogni datetime che attraversa un confine fra moduli e'
timezone-aware in UTC, e la conversione avviene una volta sola, nel parser dei
rilevamenti.

**La virgola mobile.** `0.1 + 0.2` non fa `0.3`. Non e' non determinismo (lo
stesso calcolo da' sempre lo stesso risultato sbagliato) ma produce lo stesso
sintomo: test che falliscono per uno scarto invisibile. Si confrontano i numeri
in virgola mobile con `pytest.approx` o con una tolleranza esplicita, mai con
l'uguaglianza. Quando l'ordine delle somme puo' cambiare, cambia anche il
risultato, perche' l'addizione in virgola mobile non e' associativa: sommare gli
stessi litri in ordine diverso puo' dare l'ultimo bit diverso.

**Il filesystem.** L'ordine di `os.listdir` e di `glob` non e' garantito.
Ordinalo.

**La concorrenza.** Il progetto la usa solo dentro il risolutore, e li' e'
governata dal parametro dei worker.

## 8. Copertura: a cosa serve e a cosa non serve

La copertura di riga misura quante righe la suite ha eseguito almeno una volta.
Si ottiene con `pytest --cov`.

Serve a una cosa sola, ed e' utile: trovare il codice che nessun test tocca. Se
un intero modulo e' allo zero per cento, lo sai subito.

Non serve a misurare la qualita' dei test, e questo va detto chiaramente perche'
e' un fraintendimento diffuso. Una suite che chiama ogni funzione senza asserire
niente arriva al cento per cento di copertura e non verifica nulla. La copertura
di riga non distingue fra "eseguito" e "verificato".

Sopra la copertura di riga ci sono la copertura di ramo, che chiede che ogni
diramazione sia percorsa in entrambe le direzioni, e la copertura di mutazione,
che introduce piccoli errori nel codice (cambia un maggiore in maggiore uguale,
un piu' in un meno) e controlla quanti test se ne accorgono. La copertura di
mutazione e' la misura onesta della qualita' di una suite, e costa parecchio
tempo di calcolo.

Un obiettivo numerico di copertura imposto dall'alto produce test scritti per
alzare il numero. Nel progetto la copertura si guarda per scoprire i buchi, non
si insegue come metrica.

## 9. I marcatori e la separazione fra test veloci, test di rete e test con modello

Una suite serve se gira spesso, e gira spesso se e' veloce e non ha bisogno di
niente. Ma alcuni test hanno bisogno di qualcosa: la rete per FIRMS, una chiave
API per il traduttore con modello linguistico, molto tempo per una rigiocata
lunga.

pytest risolve questo con i marcatori. Si dichiarano in `pyproject.toml`:

```toml
markers = [
  "network: richiede accesso a internet (escluso di default)",
  "llm: richiede ANTHROPIC_API_KEY (escluso di default)",
  "slow: test lento, escluso dalla verifica rapida",
]
addopts = "-q -m 'not network and not llm'"
```

e si applicano ai test con `@pytest.mark.network`. La riga `addopts` fa si' che
l'esecuzione nuda di `pytest` escluda quelle categorie. Chi vuole eseguirle lo
chiede esplicitamente con `pytest -m network`.

La conseguenza pratica e' quella che conta di piu': chi
clona il repository ed esegue `pytest` vede tutto verde senza avere nessuna
chiave e senza essere connesso. Una suite che fallisce alla prima esecuzione su
una macchina pulita e' una suite che nessuno eseguira' una seconda volta.

## 10. Come e' organizzata la suite di Tagliafuoco

Un file di test per sottosistema, con lo stesso nome del pacchetto che prova.
La separazione non e' estetica: serve a far lavorare persone diverse su
sottosistemi diversi senza toccare gli stessi file.

- `tests/conftest.py`, le fixture condivise: un istante fisso, due basi, tre
  mezzi di categorie diverse, due focolai (uno in Sila lontano dalle case, uno
  in interfaccia urbano-foresta), lo scenario che li mette insieme e la sua
  variante notturna.
- `tests/test_registry.py`, integrita' dei dati statici: riferimenti che esistono,
  coordinate dentro la bounding box, identificativi unici.
- `tests/test_ingest.py`, parsing, clustering, arricchimento.
- `tests/test_nlu.py`, il traduttore a regole su decine di ordini italiani, il
  rifiuto dei valori fuori intervallo, l'astensione.
- `tests/test_optimize.py`, il risolutore su scenari piccoli e verificabili a
  mano, l'effetto di ciascun tipo di vincolo, il determinismo.
- `tests/test_validate.py`, i piani deliberatamente rotti e il piano corretto.
- `tests/test_sim.py`, le finestre che non vedono il futuro, lo stato che non
  sanguina, la ripetibilita' della rigiocata.
- `tests/test_eval.py`, le metriche su piani costruiti a mano di cui il risultato
  e' calcolabile a mente.

La scelta di scenari piccoli in `test_optimize.py` e' deliberata. Due focolai e
tre mezzi sono abbastanza per esercitare ogni tipo di vincolo e abbastanza pochi
perche' una persona possa calcolare a mano quale sia il piano giusto. Testare il
risolutore su trenta focolai non aggiunge fiducia: aggiunge solo un test lento
di cui nessuno sa dire se il risultato sia corretto.

## 11. Cosa devi saper spiegare

**Come si testa un ottimizzatore, se non conosci la risposta giusta?**
Non si testa confrontando con la risposta. Si testano proprieta' invarianti che
ogni soluzione valida deve rispettare, si sfrutta il fatto che verificare una
soluzione e' molto piu' facile che trovarla, si usano relazioni metamorfiche
(come deve cambiare l'output se cambio l'input in un certo modo) e, per le parti
critiche, si scrive una seconda implementazione indipendente. Nel progetto questa
seconda implementazione e' il validatore.

**Che differenza c'e' fra il validatore e i test?**
I test girano in sviluppo su casi scelti da chi ha scritto il codice, quindi
vedono solo gli errori a cui quella persona aveva pensato. Il validatore gira su
ogni piano prodotto, anche in esecuzione reale, e controlla proprieta' che devono
valere sempre. Sono complementari: i test provano che il validatore funziona, il
validatore copre i casi che i test non hanno previsto.

**Perche' il determinismo e' un requisito e non una comodita'?**
Perche' la valutazione confronta il risolutore con una baseline sugli stessi
scenari. Se il risolutore non e' deterministico, la differenza misurata mescola
la qualita' del metodo con la varianza dell'esecuzione, e non si possono
separare. In piu' un bug che si riproduce a intermittenza non si corregge,
perche' non sai se la correzione ha funzionato.

**Da dove viene il non determinismo in CP-SAT e come lo elimini?**
Dalla ricerca a portafoglio: piu' strategie girano in parallelo su piu' thread e
il risolutore tiene la prima che finisce, quindi quale vince dipende dallo
scheduler del sistema operativo. Quando esistono piu' soluzioni ottime
equivalenti, esecuzioni diverse ne restituiscono una diversa. Si elimina fissando
il seed e mettendo i worker a uno, pagandolo in tempo. Il progetto tiene due
modalita' proprio per questo.

**Un test che itera su un set puo' essere non deterministico. Perche'?**
Perche' l'ordine di iterazione di un insieme dipende dagli hash, e Python
randomizza gli hash delle stringhe a ogni avvio del processo. Se quell'ordine
finisce a determinare l'ordine delle variabili nel modello, il risolutore puo'
scegliere una soluzione equivalente diversa a ogni esecuzione. La correzione e'
ordinare esplicitamente.

**Copertura al cento per cento significa che il codice e' testato bene?**
No. La copertura di riga misura cosa e' stato eseguito, non cosa e' stato
verificato: una suite che chiama tutte le funzioni senza nessuna asserzione arriva
al cento per cento. Serve a trovare il codice che nessun test tocca. Per misurare
la qualita' vera servono la copertura di ramo e soprattutto quella di mutazione.

**Perche' i test del modello linguistico sono esclusi di default?**
Perche' richiedono una chiave API e la rete, quindi non girerebbero su una
macchina pulita, sarebbero lenti e costerebbero denaro. Sono marcati e si
eseguono su richiesta. La regola che ne discende e' che il progetto deve essere
verificabile per intero senza nessuna chiave, altrimenti chi lo clona non esegue
mai i test.

**Che cos'e' un test metamorfico? Fammi un esempio da questo progetto.**
Un test che non conosce l'output ma sa come deve cambiare al variare
dell'input. Esempio: se aggiungo un mezzo alla flotta e non tocco nient'altro, il
valore dell'obiettivo non puo' peggiorare, perche' il risolutore puo' sempre
ignorare il mezzo nuovo e ritrovare la soluzione di prima. Se peggiora, c'e' un
errore nel modello.

## 12. Per approfondire

Sul problema dell'oracolo e sul testing metamorfico esiste una letteratura di
ingegneria del software ormai consolidata, nata proprio dal bisogno di testare
programmi scientifici e compilatori, dove la risposta giusta non e' nota. Cercare
i lavori di rassegna sul metamorphic testing e' il punto di ingresso naturale.

Sul testing basato su proprieta' conviene partire dalla documentazione di
Hypothesis, che spiega bene la riduzione dei controesempi, e dalla tradizione di
QuickCheck nel mondo Haskell da cui l'idea proviene.

Sulla documentazione di pytest le due sezioni che ripagano di piu' il tempo sono
quella sulle fixture (in particolare gli scope e la composizione) e quella sui
marcatori.

Sulla riproducibilita' del calcolo scientifico, il tema e' piu' ampio del testing
e tocca il blocco delle versioni delle dipendenze, i seed, gli ambienti isolati e
i contenitori. Il capitolo `10-metodologia-di-valutazione.md` di questo progetto
riprende la parte che riguarda la valutazione.
