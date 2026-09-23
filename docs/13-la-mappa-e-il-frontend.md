# La mappa e il frontend

Come si disegna una mappa interattiva nel browser, cosa succede quando i punti
da disegnare diventano molti, come si fa un timelapse che non salta, e perche'
la demo ha l'aspetto che ha.

Indice dei paragrafi:
1. Che cosa deve fare questa pagina
2. Come funziona una mappa a tile
3. Dalle coordinate ai pixel dello schermo
4. SVG, canvas e WebGL: quando cambiare tecnica
5. Disegnare dati, non decorazioni
6. Il timelapse: interpolare senza mentire
7. requestAnimationFrame e il tempo nel browser
8. Servire i dati alla pagina
9. Accessibilita' di una mappa
10. Le scelte visive della demo e perche' sono quelle
11. Cosa devi saper spiegare
12. Per approfondire

---

## 1. Che cosa deve fare questa pagina

La demo ha tre compiti, in ordine di importanza.

Deve mostrare dove sta bruciando in un certo istante della stagione 2025. Deve
mostrare che cosa il sistema ha deciso di fare: quali mezzi partono, da quale
base, verso quale focolaio. Deve mostrare come la decisione e' nata: l'ordine del
coordinatore, i vincoli che ne sono stati estratti, e il verdetto del validatore.

Il terzo compito e' quello che distingue questa pagina da una mappa qualunque di
incendi. Una mappa che mostra solo i puntini rossi non dimostra niente del
progetto. La catena "ordine, vincoli, piano, verdetto" e' il progetto.

C'e' anche un vincolo che non e' tecnico: la pagina deve dire subito che e' una
simulazione. Una mappa della Calabria con focolai e mezzi in movimento sembra uno
strumento operativo, e se non e' scritto in alto qualcuno lo scambiera' per tale.

## 2. Come funziona una mappa a tile

Una mappa web non e' un'immagine. E' un mosaico di quadrati da 256 pixel di lato
(o 512, nelle varianti ad alta densita') chiamati tile, organizzati in livelli di
zoom.

Al livello di zoom 0 il mondo intero sta in un tile solo. A ogni livello
successivo ogni tile si divide in quattro, quindi al livello z ci sono 4^z tile,
disposti in una griglia di 2^z per 2^z. Un tile si identifica con tre numeri:
z (lo zoom), x (la colonna) e y (la riga). Gli indirizzi hanno tipicamente la
forma `.../{z}/{x}/{y}.png`.

Il browser calcola quali tile coprono la finestra corrente e scarica solo quelli.
Quando sposti la mappa ne scarica altri e tiene in cache i precedenti. E' il
motivo per cui una mappa del mondo intero si apre in un secondo: non stai
scaricando il mondo, stai scaricando dodici quadrati.

La Calabria intera sta comoda al livello di zoom 8 o 9. Un singolo comune sta al
livello 13 o 14.

I tile possono essere raster (immagini gia' disegnate sul server) o vettoriali
(geometrie compresse che il browser disegna al volo). I tile vettoriali pesano
meno, si possono ristilizzare senza riscaricarli e restano nitidi a ogni densita'
di pixel, ma richiedono che il client sappia disegnarli, e questo significa
WebGL.

Le due librerie che dominano questo campo sono Leaflet, che e' piccola, semplice
e lavora nativamente con i tile raster, e MapLibre GL, che deriva dal codice
aperto di Mapbox, disegna tile vettoriali in WebGL e gestisce rotazione e
inclinazione. Per una mappa con qualche centinaio di segnaposto e uno sfondo
semplice, Leaflet basta e costa meno in peso e in complessita'. Per una mappa che
deve ristilizzare il fondo (per esempio passare al tema notturno senza
riscaricare i tile) MapLibre e' la scelta giusta.

Un punto pratico che vale la pena sapere: i tile di molti servizi hanno condizioni
d'uso e limiti di richieste. Un progetto dimostrativo che sbatte su un servizio
gratuito senza attribuzione e senza limiti e' un progetto che smette di funzionare
il giorno in cui qualcuno lo guarda davvero.

## 3. Dalle coordinate ai pixel dello schermo

Questa e' la conversione che conviene saper spiegare, perche' e' il punto in cui
la geografia incontra il disegno.

Le mappe web usano quasi universalmente la proiezione Web Mercator, identificata
dal codice EPSG:3857. Le formule per passare da latitudine e longitudine in gradi
a coordinate normalizzate fra 0 e 1 sono:

```
x = (lon + 180) / 360

y = (1 - ln(tan(lat_rad) + 1/cos(lat_rad)) / pi) / 2
```

dove `lat_rad` e' la latitudine in radianti. Moltiplicando per la dimensione della
mappa al livello di zoom corrente (256 per 2^z pixel) si ottengono le coordinate
in pixel, e dividendo per 256 si ottiene l'indice del tile.

Due conseguenze meritano attenzione.

La prima: la Mercator e' **conforme**, cioe' conserva gli angoli e quindi le forme
locali, ma deforma le aree in modo crescente verso i poli. Alle latitudini della
Calabria, intorno ai 39 gradi, il fattore di scala vale circa 1 diviso il coseno
di 39 gradi, quindi circa 1.29. Le aree risultano gonfiate di circa il 66 per
cento (il quadrato del fattore). Questo significa che **non si misurano aree
sulla Web Mercator**: la superficie percorsa dal fuoco si calcola sulle coordinate
geografiche con una formula geodetica, o su una proiezione equivalente, e poi si
disegna. Confondere le due cose e' un errore classico.

La seconda: la formula di y diverge ai poli, quindi la Web Mercator taglia il
mondo a circa 85.05 gradi di latitudine. Per la Calabria non e' un problema, ma
spiega perche' le mappe web mostrano l'Antartide come una striscia.

## 4. SVG, canvas e WebGL: quando cambiare tecnica

Quando disegni dei segnaposto sopra la mappa hai tre modi, e la scelta dipende da
quanti sono.

**SVG.** Ogni segnaposto e' un elemento del DOM. Il vantaggio e' enorme: puoi
attaccargli un gestore di eventi, puoi stilizzarlo con CSS, il browser gestisce il
passaggio del mouse e il focus da tastiera, e uno screen reader puo' leggerlo. Lo
svantaggio arriva con i numeri: ogni elemento costa memoria e costa layout, e
sopra qualche migliaio di nodi l'interazione comincia a impuntarsi.

**Canvas 2D.** Disegni pixel su una tela. Il costo per elemento e' molto piu'
basso, quindi reggi decine di migliaia di punti. In cambio perdi tutto quello che
il DOM ti dava gratis: per sapere su cosa ha cliccato l'utente devi cercare tu
il punto piu' vicino, e per l'accessibilita' devi costruire una rappresentazione
parallela.

**WebGL.** Disegni sulla GPU. Reggi centinaia di migliaia di punti. In cambio
scrivi shader, e il codice diventa molto meno leggibile per chi lo eredita.

Per Tagliafuoco i numeri sono chiari. In una finestra di tre ore la Calabria ha
tipicamente da qualche unita' a qualche decina di focolai attivi, e la flotta
conta ventisei mezzi. Sono poche decine di elementi: SVG e' la scelta giusta, e
prendere WebGL "perche' e' piu' veloce" sarebbe ottimizzazione prematura pagata in
leggibilita' e accessibilita'.

La soglia in cui la scelta cambierebbe e' se si volesse mostrare **tutti** i
rilevamenti della stagione insieme, che sono qualche migliaio. Li' conviene
canvas. Vale la pena saper dire dove sta la soglia e perche', invece di difendere
una tecnologia in astratto.

## 5. Disegnare dati, non decorazioni

Un segnaposto ha tre canali visivi che possono portare informazione: la
posizione, la dimensione e il colore. Sprecarli e' il modo piu' comune di fare
una mappa che sembra ricca e non dice niente.

Nella demo il raggio del cerchio di un focolaio cresce con la **radice quadrata**
della potenza radiante, non in modo proporzionale. Il motivo e' percettivo: quando
confrontiamo due cerchi giudichiamo l'area, non il raggio. Se raddoppi il raggio
l'area quadruplica, e un focolaio di 40 megawatt sembra quattro volte peggiore di
uno da 20 invece che due volte. Usare la radice quadrata fa si' che l'area cresca
in modo proporzionale al dato, ed e' la convenzione corretta per i simboli
proporzionali su una mappa.

Il colore invece non codifica l'intensita': codifica una categoria, cioe' se il
focolaio e' in interfaccia urbano-foresta. Mescolare due variabili sullo stesso
canale (per esempio usare il rosso piu' acceso sia per l'intensita' sia per la
vicinanza alle case) rende la mappa illeggibile, perche' chi guarda non sa quale
delle due sta vedendo.

La trasparenza codifica l'eta' del rilevamento: i focolai vecchi sbiadiscono.
Questo risolve un problema reale del dominio, cioe' che un rilevamento di sei ore
fa potrebbe essere gia' spento, e il sistema non ha modo di saperlo.

Per i mezzi la forma codifica il dominio (triangolo per gli aerei, quadrato per i
terrestri), il riempimento codifica lo stato (pieno se in missione, vuoto se
fermo, tratteggiato se tenuto di riserva) e l'orientamento del triangolo codifica
la direzione di marcia. Tre canali, tre informazioni, nessuna ripetizione.

Una legenda fissa non e' facoltativa. Una mappa senza legenda non e' leggibile, e
chiedere all'utente di indovinare cosa significa un contorno tratteggiato e' un
modo elegante di non comunicare niente.

## 6. Il timelapse: interpolare senza mentire

Il timelapse deve mostrare la stagione che scorre. I dati pero' non sono continui:
i satelliti passano a orari discreti, e i piani vengono prodotti una volta ogni
finestra di tre ore.

Qui c'e' una scelta di onesta' visiva. Se interpoli la posizione di un mezzo fra
la base e il focolaio, stai disegnando qualcosa che non e' un dato: il piano dice
che il mezzo parte al minuto 12 e arriva al minuto 31, non dice dove si trova al
minuto 20. L'interpolazione lineare lungo la rotta e' una ricostruzione, ed e'
legittima purche' sia dichiarata, perche' mostra la cosa giusta (il mezzo e' in
viaggio) senza inventarne una falsa.

Quello che invece non si deve interpolare sono i focolai. Un focolaio non
"appare gradualmente": esiste dal momento in cui il satellite lo ha rilevato. Far
comparire in dissolvenza un focolaio prima del suo primo rilevamento significa
mostrare informazione che a quell'istante nessuno aveva. E' la stessa fuga di
informazione dal futuro che il capitolo sulla valutazione discute, solo che qui e'
visiva.

La regola pratica: interpola le grandezze continue che il modello descrive
(posizione lungo una rotta), non interpolare gli eventi discreti (comparsa di un
focolaio).

C'e' anche un requisito di accessibilita'. Con la preferenza
`prefers-reduced-motion` attiva, l'animazione continua va sostituita da un
avanzamento a scatti fra le finestre. La pagina deve restare pienamente usabile:
il timelapse non e' l'unico modo di leggere i dati, lo scorrevole permette di
posizionarsi su un istante e leggerlo fermo.

## 7. requestAnimationFrame e il tempo nel browser

Per animare si usa `requestAnimationFrame`, non `setInterval`.

`setInterval(fn, 16)` chiede al browser di eseguire la funzione circa ogni 16
millisecondi. Il browser non garantisce niente: se il thread e' occupato, le
chiamate si accumulano o si perdono, e se la scheda passa in secondo piano il
timer viene rallentato, quindi l'animazione va fuori sincrono con il tempo reale.

`requestAnimationFrame(fn)` chiede al browser di chiamare la funzione prima del
prossimo ridisegno. Il browser la chiama alla frequenza dello schermo (60 volte
al secondo, o 120 su un pannello veloce), la sospende quando la scheda non e'
visibile, e passa alla funzione un timestamp ad alta risoluzione.

Il punto che conta e' l'uso di quel timestamp. Un'animazione scritta cosi':

```javascript
posizione += 0.5;   // sbagliato
```

avanza di mezzo passo per fotogramma, quindi va al doppio della velocita' su uno
schermo a 120 hertz e rallenta quando il computer e' carico. Un'animazione scritta
cosi':

```javascript
const dt = timestamp - ultimoTimestamp;
posizione += velocita * dt;   // corretto
```

avanza in funzione del tempo trascorso, quindi si comporta allo stesso modo su
qualunque schermo. Si chiama animazione basata sul tempo invece che sui fotogrammi,
ed e' la differenza fra una demo che funziona sulla tua macchina e una che
funziona ovunque.

Un'ultima accortezza: se l'utente lascia la scheda e torna dopo dieci minuti, il
primo `dt` vale seicentomila millisecondi e il timelapse salta alla fine. Si
limita `dt` a un valore massimo ragionevole.

## 8. Servire i dati alla pagina

La demo ha due modi possibili di ottenere i dati, e la scelta ha conseguenze su
chi puo' farla funzionare.

**File statici precalcolati.** Si esegue una volta la rigiocata della stagione,
si salva il risultato in JSON, e la pagina lo carica. Vantaggio: la demo e' un
file HTML, un CSS, un JavaScript e un JSON, e funziona aprendo la pagina, anche
pubblicata su un servizio di pagine statiche. Nessun processo da avviare.

**API viva.** Un servizio FastAPI espone gli endpoint per costruire uno scenario,
tradurre un ordine, risolvere e validare. Vantaggio: l'utente puo' scrivere un
ordine nuovo e vedere il sistema reagire davvero.

Tagliafuoco tiene entrambe le strade perche' servono a due cose diverse. Il
timelapse della stagione legge dati precalcolati, perche' rigiocare la stagione
richiede minuti e non si fa mentre l'utente guarda. Il pannello dell'ordine parla
con la API, perche' li' il punto e' proprio vedere la catena funzionare su una
frase nuova.

Quando la API non e' in esecuzione, il pannello dell'ordine deve dirlo
esplicitamente e continuare a mostrare gli ordini gia' calcolati, invece di
restare bloccato su un caricamento infinito. Un errore mostrato e' informazione;
un caricamento che non finisce e' un guasto.

Sul formato: per i dati geografici lo standard e' GeoJSON, che sia Leaflet sia
MapLibre leggono nativamente. Per le serie temporali della rigiocata conviene un
JSON piu' compatto, perche' GeoJSON ripete le chiavi per ogni elemento e su
qualche migliaio di rilevamenti il peso si sente.

## 9. Accessibilita' di una mappa

Una mappa e' uno dei casi piu' difficili, e vale la pena affrontarlo invece di
ignorarlo.

Il principio e' che la mappa non puo' essere l'unico modo di accedere
all'informazione. Se i focolai attivi esistono solo come cerchi disegnati, chi
naviga con la tastiera o con uno screen reader non li vede. La soluzione e' avere
sempre una rappresentazione testuale equivalente: nella demo il registro
operativo sulla destra elenca le assegnazioni in forma di testo, e quella lista
e' navigabile da tastiera.

Gli elementi interattivi della mappa devono essere raggiungibili con Tab e
attivabili con Invio, non solo cliccabili. Con i segnaposto in SVG questo e'
ottenibile dando loro `tabindex` e un ruolo appropriato; con canvas va costruita
una lista parallela nel DOM.

Il contrasto va verificato sopra il fondo cartografico, che non e' uniforme. Un
testo che passa il controllo di contrasto sopra il beige chiaro puo' fallirlo
sopra una zona scura della mappa. La soluzione usuale e' un alone o un riquadro
dietro le etichette.

Il colore da solo non deve mai portare l'informazione. Nella demo un focolaio in
interfaccia urbano-foresta ha un colore diverso **e** un contorno diverso, cosi'
resta distinguibile anche a chi non discrimina quei due colori. Circa una persona
su dodici fra gli uomini ha qualche forma di deficit nella visione dei colori:
non e' un caso limite.

## 10. Le scelte visive della demo e perche' sono quelle

Le scelte sono fissate in `web/DESIGN.md` e non si improvvisano. Qui basta il
ragionamento che le ha prodotte.

Il riferimento visivo e' la carta topografica, non il cruscotto. Il fondo e' un
avorio caldo, non bianco puro, perche' un fondo bianco pieno sotto una mappa
abbaglia e fa sembrare tutto il resto piu' scuro di quanto sia. L'inchiostro e' un
nero caldo, come quello di stampa.

I colori dei dati vengono dal soggetto: verde bosco per la vegetazione, terracotta
bruciata per i focolai, ocra per l'allerta, ardesia per i mezzi e per le linee. Il
rosso dei focolai non e' un rosso di segnalazione saturo, perche' una mappa con
trenta punti in rosso pieno vibra e diventa illeggibile.

La tipografia sono due famiglie: una grotesca stretta per le etichette, perche' le
etichette su una carta devono occupare poco spazio orizzontale per non coprire il
territorio che descrivono, e un carattere a larghezza fissa per i numeri, perche'
le cifre tabulari tengono allineata una colonna di tempi che si aggiorna a ogni
fotogramma. Un numero che salta di mezzo carattere mentre scorre e' illeggibile.

Le separazioni sono linee da un pixel, non ombre. Un'ombra diffusa sotto un
pannello suggerisce che il pannello galleggi sopra la mappa, e a quel punto la
mappa diventa uno sfondo. Le linee sottili sono il segno che una carta usa da
sempre.

C'e' un solo momento di movimento orchestrato, il timelapse. Tutto il resto
risponde a un'azione dell'utente e dura meno di 150 millisecondi. Una pagina in cui
ogni elemento reagisce al passaggio del mouse costringe l'occhio a inseguire il
movimento invece di leggere i dati.

Lo scorrevole del tempo porta l'istogramma della densita' dei rilevamenti. Non e'
un ornamento: e' il modo di vedere dove sta il picco della stagione prima ancora di
premere play, e senza di esso lo scorrevole sarebbe una barra vuota.

## 11. Cosa devi saper spiegare

**Come fa una mappa web a mostrare il mondo intero senza scaricarlo?**
Perche' non scarica il mondo: scarica i pochi quadrati (tile) che coprono la
finestra al livello di zoom corrente. A ogni livello di zoom il numero di tile
quadruplica, e si identificano con zoom, colonna e riga.

**Perche' non si misurano le aree sulla proiezione delle mappe web?**
Perche' la Web Mercator e' conforme, conserva gli angoli e deforma le aree in modo
crescente con la latitudine. Alle latitudini della Calabria le aree risultano
gonfiate di circa il 66 per cento. Le aree si calcolano sulle coordinate
geografiche o su una proiezione equivalente, e solo dopo si disegnano.

**SVG o canvas per i segnaposto?**
Dipende da quanti sono. Con SVG ogni segnaposto e' un elemento del DOM, quindi hai
gratis gli eventi, il CSS, il focus da tastiera e l'accessibilita', ma sopra
qualche migliaio di nodi l'interazione si impunta. Con canvas reggi decine di
migliaia di punti ma devi implementare a mano il rilevamento del clic e
l'accessibilita'. In questa demo i segnaposto sono poche decine, quindi SVG.

**Perche' il raggio dei cerchi cresce con la radice quadrata del dato?**
Perche' quando confrontiamo due cerchi giudichiamo l'area e non il raggio. Se il
raggio fosse proporzionale al dato, l'area crescerebbe con il quadrato e la mappa
esagererebbe le differenze.

**Perche' requestAnimationFrame e non setInterval?**
Perche' setInterval non garantisce la cadenza, si accumula quando il thread e'
occupato e viene rallentato quando la scheda non e' visibile, cosi' l'animazione
esce dal sincrono con il tempo reale. requestAnimationFrame e' agganciato al
ridisegno del browser e fornisce un timestamp preciso. E l'animazione va
calcolata sul tempo trascorso, non sul numero di fotogrammi, altrimenti va al
doppio della velocita' su uno schermo a 120 hertz.

**C'e' qualcosa che nel timelapse non va interpolato?**
Si', i focolai. La posizione di un mezzo lungo la rotta si puo' interpolare perche'
e' una ricostruzione dichiarata di una grandezza continua. La comparsa di un
focolaio no: esiste dal momento del rilevamento, e farlo apparire prima in
dissolvenza significa mostrare informazione che a quell'istante nessuno aveva.

**Come rendi accessibile una mappa?**
Non rendendola l'unico modo di accedere all'informazione. Accanto alla mappa c'e'
sempre una rappresentazione testuale equivalente, navigabile da tastiera. In piu':
elementi raggiungibili con Tab, contrasto verificato sopra un fondo non uniforme,
e nessuna informazione portata dal solo colore.

**Perche' la demo ha sia file precalcolati sia una API?**
Perche' servono a due cose diverse. La rigiocata della stagione richiede minuti,
quindi si precalcola e la pagina legge un JSON, e cosi' la demo funziona anche
pubblicata come sito statico. Il pannello dell'ordine invece deve mostrare la
catena che lavora su una frase nuova, e per quello serve il servizio vivo.

## 12. Per approfondire

Sulla cartografia web, la documentazione di Leaflet e' breve e si legge in
un'ora; quella di MapLibre GL e' piu' ampia e conviene leggerla solo se si
sceglie quella strada. La specifica dei tile vettoriali di Mapbox e' il
riferimento per capire cosa contiene davvero un tile.

Sulla rappresentazione dei dati su mappa, la letteratura di cartografia tematica
tratta in modo sistematico i simboli proporzionali, le mappe di coropleta e i
loro tranelli. E' la stessa tradizione da cui viene la regola della radice
quadrata.

Sulle proiezioni, il materiale didattico dei corsi di sistemi informativi
geografici e' piu' utile delle pagine divulgative, perche' distingue in modo
preciso fra conforme, equivalente ed equidistante.

Sull'accessibilita', le linee guida WCAG hanno una sezione dedicata ai contenuti
non testuali e una ai criteri di contrasto. Per il caso specifico delle mappe
conviene cercare le raccomandazioni sulle alternative testuali per i contenuti
geografici complessi.
