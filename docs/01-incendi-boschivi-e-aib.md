# Capitolo 01. Incendi boschivi e lotta attiva

Questo capitolo spiega il dominio su cui Tagliafuoco lavora: come brucia un bosco, perche' il
tempo del primo attacco pesa piu' di ogni altra variabile, chi comanda cosa nel sistema
antincendio italiano, e quali di queste cose il simulatore rappresenta davvero.

Indice dei paragrafi:

1. Il problema, in due numeri
2. I due triangoli
3. Tipi di incendio e scelta del mezzo
4. Velocita' di propagazione: vento, pendenza, esposizione
5. Perche' il tempo del primo attacco conta piu' di tutto il resto
6. L'interfaccia urbano-foresta, e perche' due chilometri sono una convenzione
7. La catena dell'intervento
8. Chi fa cosa in Italia, e come si articola in Calabria
9. Canadair, elicottero, squadra a terra: ordini di grandezza veri
10. Cosa Tagliafuoco modella e cosa no
11. Cosa devi saper spiegare
12. Per approfondire

---

## 1. Il problema, in due numeri

Due cifre motivano la scelta del tema, e sono il punto di partenza dichiarato del progetto: le ho
ricevute con l'incarico, non le ho misurate io. La prima: al 15 settembre 2025 in Calabria
risultavano bruciati circa 143 chilometri quadrati. La seconda, riferita a un anno diverso: nel
2026, nel periodo fino a giugno, la Calabria era la seconda regione italiana per superficie
forestale percorsa dal fuoco, con circa il 23 per cento del totale nazionale.

Vediamo cosa dicono. 143 chilometri quadrati sono 14.300 ettari, e la Calabria di chilometri
quadrati ne ha 15.222: in quella stagione e' andato a fuoco lo 0,94 per cento dell'intero
territorio regionale. Non lo 0,94 per cento del bosco: di tutto, comprese citta', spiagge e campi
coltivati. Rapportata alla sola superficie forestale la percentuale sale parecchio, ma il valore
esatto dipende da quale definizione di bosco si adotta, e inventario forestale e cartografia di
copertura del suolo danno totali diversi, quindi non scrivo un numero che non so verificare.

La seconda cifra riguarda un altro anno e un'altra grandezza, e per questo non va sommata alla
prima: dice che il peso della Calabria nel bilancio nazionale e' sproporzionato alla sua taglia.
La regione vale circa il 5 per cento della superficie italiana (15.222 chilometri quadrati su circa
302.000) e nel primo semestre del 2026 ha prodotto quasi un quarto della superficie forestale
bruciata nazionale. Un anno storto capita a tutti. Due indicatori che puntano nella stessa
direzione su anni diversi suggeriscono un problema strutturale, ed e' il motivo per cui vale la
pena costruirci sopra qualcosa.

Prima di citarle a voce in un colloquio, pero', riverificale sulla fonte: i bollettini europei e i
rendiconti regionali aggiornano le stime anche a mesi di distanza, perche' il perimetro definitivo
di un incendio si mappa dopo che si e' spento, non mentre brucia.

## 2. I due triangoli

Il triangolo del fuoco risponde a "perche' questa fiamma esiste": combustibile, comburente
(l'ossigeno dell'aria) e calore di innesco. Togline uno e la combustione si ferma, ed e' il
principio di ogni tecnica di spegnimento, perche' l'acqua sottrae calore, terra e schiuma
sottraggono ossigeno, controfuoco e linea tagliafuoco sottraggono combustibile. Il nome del
progetto viene da li': una linea tagliafuoco e' una striscia di terreno ripulita dalla vegetazione
che interrompe la continuita' del combustibile.

Il triangolo del comportamento risponde a una domanda diversa: "dato che la fiamma esiste, dove va
e quanto corre". I lati sono combustibile, meteo e topografia. Il combustibile non e' semplicemente
"il bosco": conta il carico per unita' di superficie, la continuita' verticale (se il sottobosco
arriva alla chioma il fuoco puo' salire) e soprattutto l'umidita' del combustibile morto fine,
quello che si asciuga in un'ora, che separa la giornata in cui un mozzicone si spegne da solo da
quella in cui parte un fronte. Il meteo porta ossigeno, inclina la fiamma in avanti, proietta
faville oltre il fronte e governa quanto e' secco quel combustibile fine. La topografia agisce con
pendenza ed esposizione, e il prossimo paragrafo ci entra dentro.

In Calabria i tre lati sono sfavorevoli insieme: pineta e macchia mediterranea, molto infiammabili;
estate con scirocco caldo e secco e umidita' relativa sotto il 30 per cento; montagna quasi ovunque,
fra Pollino, Sila, Serre e Aspromonte. La costante `ROAD_TORTUOSITY = 1.45` in
`src/tagliafuoco/config.py` traduce quella montuosita' in ingegneria: la strada e' molto piu' lunga
della linea d'aria, quindi i mezzi terrestri arrivano tardi.

## 3. Tipi di incendio e scelta del mezzo

Si classificano per la parte del combustibile che brucia, e la classificazione decide quale mezzo
puo' fare qualcosa e quale spreca ore di volo.

L'incendio radente (*surface fire*) brucia lettiera, erba secca, arbusti bassi e rami caduti, con
fiamme tipicamente sotto i due o tre metri. E' il caso piu' frequente ed e' ancora affrontabile da
terra: una squadra con flabelli, pale e una manichetta contiene un fronte radente di intensita'
moderata, e il lancio dall'alto aiuta ma non e' indispensabile.

L'incendio di chioma (*crown fire*) sale alle cime, e gli serve una scala di combustibile che
colleghi suolo e chioma, condizione tipica delle pinete dense con sottobosco non ripulito, che in
Calabria sono molte. Si distingue in passivo, quando il fuoco accende singole chiome ma continua a
propagarsi lungo il suolo, e attivo, quando il fronte di chioma si autosostiene e corre da solo. I
modelli operativi di uso corrente stimano che un fronte di chioma attivo avanzi nell'ordine di tre
volte il radente corrispondente: e' un fattore di riferimento approssimato, non una misura, ma
produce intensita' a cui nessuna squadra puo' avvicinarsi. Qui il lancio aereo e' l'unico strumento
che opera sul fronte, e il lavoro a terra si sposta sui fianchi e su linee di arresto arretrate.

L'incendio sotterraneo (*ground fire*) brucia torba, lettiera profonda e radici sotto la superficie.
Ha fiamma quasi nulla, avanza di centimetri all'ora ed emette poco calore per unita' di tempo,
quindi si vede male dall'alto, e il lancio d'acqua e' quasi inutile perche' l'acqua non penetra. In
Calabria il caso puro e' raro, ma la coda sotterranea di un incendio di superficie e' comune e
spiega perche' un focolaio si riaccende due giorni dopo la bonifica apparente.

La conseguenza per il progetto e' diretta: `VehicleKind` in `domain/models.py` distingue sette
categorie proprio perche' non sono intercambiabili, e il campo `domain` separa i due modelli fisici,
visto che il mezzo aereo vola in linea retta e fa cicli pescaggio-sgancio mentre il terrestre
percorre strade e si rifornisce da idranti. Onestamente pero' non esiste un campo che dica "questo
focolaio e' di chioma": cio' che si avvicina di piu' e' la combinazione di `land_cover` e
`max_frp_mw`, che e' una correlazione e non una classificazione.

## 4. Velocita' di propagazione: vento, pendenza, esposizione

La velocita' di propagazione, o ROS (*rate of spread*, tasso di avanzamento), e' la velocita' con
cui il fronte avanza, in metri al minuto. I modelli operativi discendono da una formulazione
sviluppata nella ricerca forestale statunitense negli anni settanta, che calcola il ROS come
rapporto fra il calore che arriva al combustibile davanti al fronte e il calore necessario ad
accenderlo:

```
R = I_R * xi * (1 + phi_w + phi_s) / (rho_b * epsilon * Q_ig)
```

Simbolo per simbolo: `R` e' la velocita' di propagazione; `I_R` e' l'intensita' di reazione, il
calore rilasciato per unita' di area e di tempo dalla zona che brucia; `xi` e' il rapporto di
propagazione, la frazione di quel calore che raggiunge il combustibile non ancora acceso; `phi_w`
e' il fattore di vento e `phi_s` il fattore di pendenza, entrambi adimensionali; `rho_b` e' la
densita' apparente del letto di combustibile, cioe' la massa di combustibile per unita' di volume
del letto, aria compresa; `epsilon` e' il numero di riscaldamento efficace, la frazione della massa
di una particella di combustibile che arriva alla temperatura di accensione nel momento in cui
comincia a bruciare (vale circa 1 per un filo d'erba, molto meno per un ramo grosso, che brucia in
superficie mentre dentro resta freddo); `Q_ig` e' l'energia per chilogrammo che porta quel
combustibile, all'umidita' che ha adesso, fino alla combustione.

Sulle unita': la formulazione originale e' scritta in unita' imperiali e da' `R` in piedi al
minuto. La struttura e' un rapporto fra un flusso di calore per unita' di area e un'energia per
unita' di volume, quindi resta valida in qualunque sistema purche' numeratore e denominatore siano
coerenti. Verificalo a mano: `I_R` in kilojoule per metro quadrato al minuto, diviso il prodotto di
`rho_b` in chilogrammi per metro cubo per `Q_ig` in kilojoule per chilogrammo, da' kilojoule al
metro quadrato al minuto diviso kilojoule al metro cubo, cioe' metri al minuto, che e' l'unita'
usata in tutto questo capitolo.

Il pezzo che interessa e' `(1 + phi_w + phi_s)`. Senza vento e in piano i due fattori valgono zero
e il termine vale 1: quello e' il ROS di base del combustibile. Vento e pendenza non aggiungono,
moltiplicano, ed entrambi crescono in modo non lineare. `phi_w` e' una legge di potenza della
velocita' del vento il cui esponente dipende da quanto e' fine il combustibile, e per erba, lettiera
fine e macchia supera l'unita', mentre per combustibili grossolani scende sotto. `phi_s` e'
proporzionale al quadrato della tangente dell'angolo di pendenza, quindi raddoppiare la pendenza
piu' che quadruplica il suo contributo. Il motivo fisico e' lo stesso nei due casi: il vento
inclina la fiamma verso il combustibile non ancora acceso e ne riduce la distanza, e la pendenza
ottiene lo stesso effetto per geometria, perche' su un pendio il combustibile a monte e' piu'
vicino alla fiamma, che resta verticale. Un fuoco che sale un versante e' un fuoco con il vento in
poppa anche senza vento, e la regola spannometrica operativa e' che il ROS raddoppia ogni dieci
gradi di pendenza (utile per ragionare in fretta, non un risultato misurato).

Esempio con numeri di Calabria, dichiarando che i valori di ROS sono plausibili e non misurati.
Macchia mediterranea in agosto, combustibile fine secco: in piano e senza vento il fronte avanza a
circa 5 metri al minuto; con scirocco a 25 chilometri orari sale intorno ai 25 o 30 metri al
minuto; se imbocca un versante a 20 gradi, cioe' due raddoppi secondo la regola spannometrica, si
arriva nell'ordine dei 100 metri al minuto, cioe' 6 chilometri orari. Un uomo a piedi in salita su
terreno con vegetazione non tiene quel passo, ed e' il motivo per cui nelle relazioni degli incendi
gravi si legge che le squadre sono state sorprese sul versante.

L'esposizione agisce su tempi piu' lunghi: in Calabria i versanti a sud e sud-ovest ricevono molta
piu' radiazione, il combustibile fine si asciuga prima e quel versante e' pronto a bruciare
settimane prima del versante nord della stessa montagna. Si aggiunge la brezza di pendio, che di
giorno risale il versante e di notte ridiscende, ed e' anche il motivo per cui gli orari contano:
`DAYLIGHT_START_MINUTE` e `DAYLIGHT_END_MINUTE` fissano la finestra diurna a 05:30 e 20:30, e il
vincolo `TimeWindow` traduce ordini come "gli aerei fino alle otto".

## 5. Perche' il tempo del primo attacco conta piu' di tutto il resto

Questa sezione contiene l'argomento per cui un ottimizzatore di assegnazione ha senso. Un fronte che
avanza a velocita' costante assume una forma che si approssima bene con un'ellisse allineata al
vento e alla pendenza, e se la testa avanza a ROS costante crescono linearmente sia l'asse maggiore
sia quello minore. L'area e':

```
A = pi * (L/2) * (W/2)
```

dove `A` e' l'area, `L` l'asse maggiore (dalla coda alla testa), `W` l'asse minore e `pi` la
costante 3,14159. Se `L` e `W` crescono linearmente il prodotto cresce con il quadrato del tempo,
mentre il perimetro cresce circa linearmente.

In numeri, con ROS 30 metri al minuto e rapporto lunghezza-larghezza 2,5 (tipico con vento
moderato): a 15 minuti `L` vale 450 metri, `W` 180 metri e l'area e' 0,0636 chilometri quadrati,
cioe' 6,36 ettari; a 30 minuti `L` vale 900 metri, `W` 360 metri e l'area e' 25,45 ettari. Quindici
minuti di ritardo hanno quadruplicato l'area. Applichiamo ora la formula del fabbisogno d'acqua di
SPEC.md, tarata in `config.py`:

```
litri = area_km2 * WATER_LITERS_PER_KM2 * coeff_copertura * coeff_intensita
coeff_intensita = 1 + min(1.0, max_frp_mw / FRP_NORMALISATION_MW)
```

dove `area_km2` e' la superficie interessata, `WATER_LITERS_PER_KM2 = 100_000` sono i litri per
chilometro quadrato a intensita' e copertura di riferimento, `coeff_copertura` viene da
`LAND_COVER_WATER_COEFF` (1,4 per `conifer`), `max_frp_mw` e' la potenza radiante massima osservata
in megawatt e `FRP_NORMALISATION_MW = 200.0` e' il valore oltre il quale il coefficiente di
intensita' satura a 2. Assumo che mentre il fronte si allunga cresca anche l'intensita', da 120 a
180 megawatt, quindi il coefficiente passa da 1,60 a 1,90. Per una pineta:

```
a 15 minuti:  0,0636 * 100.000 * 1,4 * 1,60 = 14.246 litri
a 30 minuti:  0,2545 * 100.000 * 1,4 * 1,90 = 67.697 litri
```

Il fabbisogno non e' quadruplicato, e' quasi quintuplicato (4,75 volte), perche' area e intensita'
crescono insieme e si moltiplicano.

Ora la parte che rende l'argomento operativo invece che retorico. Fisso un caso concreto, che
riprendo anche nel paragrafo 9, con le coordinate scritte per esteso cosi' puoi rifare i conti:

```
base      Aeroporto di Lamezia Terme    38,9054 N   16,2423 E
focolaio  Camigliatello Silano          39,3667 N   16,4667 E
mare      al largo di Amantea           39,13   N   16,03   E
lago      Lago di Cecita                39,40   N   16,52   E

distanze haversine su sfera di raggio 6371 km (la regola di SPEC.md per i mezzi aerei)
  base -> focolaio   54,8 km
  focolaio -> mare   45,9 km
  focolaio -> lago    5,9 km
```

Il mezzo e' un Canadair con i valori di default della dataclass `Vehicle` in `domain/models.py`
(6.000 litri, `refill_minutes` 2,0, `drop_minutes` 1,0, `setup_minutes` 5,0, `endurance_minutes`
180), tranne la velocita' di crociera, che porto a 330 chilometri orari perche' il default 200 e'
tarato sugli elicotteri. Applichiamo il tempo di ciclo di SPEC.md con pescaggio in mare:

```
t_andata      = 54,8 / 330 * 60 = 9,96 minuti
t_ciclo_acqua = 45,9 * 2 / 330 * 60 + 2,0 + 1,0 = 19,69 minuti
t_missione    = 5,0 + 9,96 + n * 19,69 + 9,96 = 24,9 + 19,69 * n
```

dove `n` e' il numero di lanci. Il vincolo `t_missione <= endurance_minutes` con
`endurance_minutes = 180` si satura a `n = 7`, perche' `(180 - 24,9) / 19,69 = 7,87`: la missione
dura 162,8 minuti e consegna 42.000 litri.

Quindi a 15 minuti servono 14.246 litri, cioe' 2,4 lanci, e un solo Canadair basta e avanza. A 30
minuti ne servono 67.697, cioe' 11,3 lanci, e quel Canadair non ce la fa piu' in una sola missione,
perche' ne consegna al massimo 42.000. Quindici minuti hanno cambiato la classe di risorse
necessarie: da "un mezzo, una missione" a "due mezzi, oppure due missioni dello stesso mezzo con il
tempo di riattrezzamento della base in mezzo". E' anche il motivo per cui `LATE_RESPONSE_MINUTES =
45` segna nelle metriche la soglia oltre la quale una risposta conta come tardiva.

Ne segue una conseguenza controintuitiva: poiche' l'area cresce con il quadrato del tempo, un
minuto risparmiato vale molto di piu' sul focolaio appena nato che su quello gia' esteso, quindi
minimizzare il tempo medio di risposta indistintamente ottimizza la cosa sbagliata. Per questo la
funzione obiettivo tiene separati `coverage`, `response_time` e `wui_protection` nei pesi
`OBJECTIVE_WEIGHTS`.

## 6. L'interfaccia urbano-foresta, e perche' due chilometri sono una convenzione

L'interfaccia urbano-foresta, in inglese *wildland-urban interface* e quasi sempre abbreviata in
WUI, e' la fascia in cui costruito e vegetazione naturale si toccano, e domina le priorita'
operative per tre motivi: la vita umana, perche' evacuare una frazione isolata su strada di montagna
richiede tempo e mezzi sottratti allo spegnimento; il fatto che l'incendio cambi natura, perche'
brucia anche materiali da costruzione, veicoli e bombole; e un dato statistico solido nella
letteratura internazionale, cioe' che la grande maggioranza degli inneschi in interfaccia ha origine
umana. La WUI non e' solo dove l'incendio fa piu' danno, e' anche dove nasce piu' spesso.

La definizione piu' citata viene dalla cartografia statunitense, distingue l'*intermix* (case
immerse nella vegetazione) dall'*interface* (case raggruppate, vegetazione poco oltre) e combina
densita' di abitazioni, copertura vegetale e distanza da una grande area vegetata, con soglie
nell'ordine di sei abitazioni per chilometro quadrato, del 50 per cento di copertura e di due
chilometri e mezzo, che riporto come ordine di grandezza e non come citazione. Non esiste una
soglia fisica: convenzioni diverse producono mappe diverse sullo stesso territorio.

Tagliafuoco adotta la sua. In `config.py` stanno `WUI_THRESHOLD_KM = 2.0` e `WUI_DECAY_KM = 10.0`,
e in `domain/models.py`:

```python
@property
def is_wui(self) -> bool:
    return self.distance_to_wui_km is not None and self.distance_to_wui_km <= 2.0
```

Due chilometri dal costruito e' la soglia che compare in molti piani antincendio boschivo regionali
italiani, quindi non e' casuale, ma resta una convenzione amministrativa: nessuna legge fisica dice
che una casa a 1.900 metri dal fronte sia in pericolo e una a 2.100 no. Il fattore di priorita'
`f_wui` di SPEC.md attenua il problema perche' e' continuo, dove `d` e' `distance_to_wui_km`, la
distanza del centroide del focolaio dal costruito piu' vicino:

```
f_wui = 1,0                  se d <= 2 km
f_wui = (10 - d) / (10 - 2)  se 2 km < d < 10 km
f_wui = 0,0                  se d >= 10 km
```

I numeri a cavallo della soglia mostrano una tensione che e' onesto dichiarare. Prendi due focolai
identici tranne la distanza dalle case, uno a 1,2 chilometri e uno a 2,1, entrambi 25,45 ettari, FRP
180 megawatt, pineta, dentro un parco nazionale. La priorita' di base e':

```
p = 0,35 * norm(frp) + 0,25 * f_wui + 0,20 * norm(area)
  + 0,10 * f_copertura + 0,10 * f_area_protetta
```

dove `norm(frp)` vale `min(1, frp / 200)` e qui da' 0,90; `norm(area)` vale
`min(1, area_km2 / 5)` con `AREA_NORMALISATION_KM2 = 5.0` e qui da' 0,0509; `f_copertura` e'
l'infiammabilita' della copertura riportata fra 0 e 1, e per la pineta vale 1 perche' le conifere
hanno il coefficiente piu' alto della tabella `LAND_COVER_WATER_COEFF`; `f_area_protetta` vale 1
dentro un'area protetta. Il primo focolaio ottiene 0,7752, il secondo 0,7721: differenza 0,0031,
cioe' niente. Il solo termine che cambia e' `f_wui`, che passa da 1 a `(10 - 2,1) / 8 = 0,9875`,
e pesato 0,25 sposta il totale di tre millesimi.

Un'avvertenza sul simbolo `frp`: SPEC.md scrive `norm(frp)` senza dire se la grandezza sia
`total_frp_mw` o `max_frp_mw`, e `FireCluster` le porta entrambe. Il fabbisogno d'acqua invece dice
esplicitamente `max_frp_mw`. Sono due letture difendibili (il totale misura quanto e' grande
l'incendio, il massimo quanto e' violento il punto peggiore) e quale delle due usi la priorita' e'
una decisione di implementazione che la specifica lascia aperta. Saperlo, e dire quale delle due il
codice ha scelto, e' esattamente il genere di cosa che distingue chi ha letto il proprio progetto.

Resta poi la discontinuita' vera. `is_wui` passa da `True` a `False` fra i due focolai, e da quel
booleano dipendono `PrioritizeZone` con `zone_kind=ZoneKind.WUI`, che applica un
`weight_multiplier` di 2,0, e la metrica `wui_coverage_ratio`. Il progetto ha quindi un fattore
continuo per la priorita' e un booleano netto per ordini e metriche, e i due non coincidono sui casi
di confine. Non e' un bug, perche' il coordinatore parla in booleani, ma e' il genere di cosa che un
esaminatore attento chiede. Nota anche che un focolaio da 25 ettari contribuisce solo 0,01 alla
priorita': il peso nominale di 0,20 sull'area diventa significativo sopra i 250 ettari, dove
`norm(area)` arriva a 0,5.

## 7. La catena dell'intervento

L'avvistamento e' il momento in cui qualcuno si accorge del fuoco, con fonti che hanno latenze
molto diverse: la telefonata puo' arrivare in due minuti, il satellite in tre ore.

L'allertamento e' il passaggio dalla segnalazione alla decisione di muovere qualcuno, e passa da
una sala operativa che verifica, localizza, valuta e decide cosa mandare. E' qui che si colloca
Tagliafuoco: non nel momento in cui si vede il fuoco, ma in quello in cui si decide come
distribuire mezzi che non bastano per tutti i focolai insieme.

Il primo attacco e' l'intervento delle prime squadre e dei primi mezzi, con l'obiettivo di contenere
il fronte prima che superi la soglia oltre la quale le risorse disponibili non bastano, e il
paragrafo 5 ha mostrato perche' qui un minuto vale molto mentre negli anelli successivi vale
progressivamente meno. L'attacco esteso comincia quando il primo attacco fallisce, con piu' mezzi,
una struttura di comando piu' articolata e a volte evacuazioni. La bonifica, infine, e' lo
spegnimento dei focolai residui e il presidio dell'area perimetrata: lunga, quasi solo lavoro a
terra, sistematicamente sottovalutata, tanto che la maggior parte delle riprese di incendi gia'
dichiarati spenti nasce da una bonifica fatta male su una coda sotterranea.

Tagliafuoco modella un solo anello, l'allertamento, come problema di assegnazione su un orizzonte
di quattro ore (`DEFAULT_HORIZON_MINUTES = 240`). Non modella l'avvistamento, perche' i rilevamenti
arrivano gia' fatti da FIRMS, e non modella la bonifica, perche' l'orizzonte e' troppo corto.
L'attacco esteso c'e' solo implicitamente, perche' assegnare piu' mezzi allo stesso focolaio e' di
fatto una scelta di attacco esteso, ma non esiste nessuna transizione di stato esplicita.

## 8. Chi fa cosa in Italia, e come si articola in Calabria

L'impianto normativo nazionale e' la legge quadro in materia di incendi boschivi del 2000, la
numero 353. La competenza e' delle Regioni: previsione, prevenzione e lotta attiva sono materia
regionale, ogni Regione approva un piano antincendio boschivo e lo aggiorna, e non esiste un corpo
nazionale antincendio boschivo ma una ventina di sistemi regionali appoggiati a risorse comuni, ai
quali si aggiungono le Province autonome di Trento e Bolzano, che in questa materia contano come
regioni. La legge
impone inoltre ai Comuni il catasto dei terreni percorsi dal fuoco, con vincoli pluriennali sulla
destinazione d'uso che servono a togliere il movente economico all'incendio doloso, il quale e' un
reato autonomo nel codice penale. I vincoli hanno durate diverse secondo il tipo, nell'ordine dei
dieci e quindici anni, ma i termini esatti vanno letti nel testo di legge e non li riporto a
memoria.

Gli attori operativi sono quattro. Le Regioni dirigono la lotta attiva con le proprie strutture
forestali e di protezione civile, e ognuna ha una sala operativa unificata permanente, la SOUP, da
cui partono gli ordini: la SOUP e' il committente immaginario di Tagliafuoco, perche' il
coordinatore che detta gli ordini a voce sta li'. Il Corpo nazionale dei vigili del fuoco interviene
in forza di convenzioni con le Regioni, ed e' l'attore principale quando il fuoco tocca il
costruito, cioe' proprio in interfaccia. La flotta aerea dello Stato e' coordinata dal Centro
operativo aereo unificato, il COAU, presso il Dipartimento della protezione civile. Il volontariato
di protezione civile fornisce una quota molto grande della manodopera a terra.

Manca un attore che viene sempre chiesto, e la risposta giusta contiene una data. Il Corpo forestale
dello Stato non esiste piu' come corpo autonomo: nel 2016 e' stato assorbito nell'Arma dei
Carabinieri, e oggi i Carabinieri forestali presidiano il territorio, fanno prevenzione e indagano
sugli incendi dolosi, ma non dirigono la lotta attiva, che resta regionale. Alcune Regioni a statuto
speciale hanno invece conservato un corpo forestale proprio, quindi la mappa degli attori non e'
uguale in tutta Italia. La Calabria e' a statuto ordinario e segue il modello generale.

Il punto procedurale importante: una Regione non dispone direttamente di un Canadair. Lo richiede
al COAU tramite la propria sala operativa, e il COAU assegna in base alle richieste che arrivano da
tutta Italia, quindi in una giornata di agosto con incendi in cinque regioni la Calabria compete
con le altre quattro per gli stessi velivoli. Sul numero dei mezzi in flotta non do cifre, perche'
varia di anno in anno con i contratti di noleggio.

In Calabria l'attuazione passa per l'azienda forestale regionale, Calabria Verde, che ha personale
e mezzi terrestri, e per la struttura regionale di protezione civile che gestisce la SOUP e i
rapporti con il COAU; la Regione dispone inoltre di elicotteri in convenzione stagionale. Nel
progetto tutta questa articolazione e' compressa in `Base`, `Vehicle` e `Scenario`: la catena di
comando non esiste nel modello, ed e' una semplificazione forte, perche' nella realta' la
disponibilita' di un Canadair e' essa stessa una decisione presa altrove e non un dato.

## 9. Canadair, elicottero, squadra a terra: ordini di grandezza veri

Il lancio dall'alto non spegne l'incendio: raffredda il fronte, bagna il combustibile davanti alla
fiamma e rallenta l'avanzata abbastanza da permettere a chi sta a terra di avvicinarsi. L'incendio
lo spegne la squadra a terra, con attrezzi manuali e manichette, e soprattutto lo tiene spento con
la bonifica. Un dispositivo fatto solo di mezzi aerei consuma ore di volo e produce un incendio che
si riaccende.

Le prestazioni che seguono sono ordini di grandezza dichiarati, non schede tecniche verificate. Un
velivolo anfibio della classe Canadair porta intorno ai 6.000 litri, vola a circa 330 chilometri
orari e si rifornisce planando sull'acqua in pochi secondi, ma gli serve uno specchio lungo, largo e
profondo, quindi il suo rendimento dipende quasi interamente da quanto e' lontana l'acqua dal fuoco.
Riprendo il caso del paragrafo 5, stesso focolaio in Sila e stesso Canadair da Lamezia, cambiando
solo la sorgente:

```
mare al largo di Amantea, 45,9 km dal fuoco
  t_ciclo_acqua   = 45,9 * 2 / 330 * 60 + 2,0 + 1,0 = 19,69 minuti
  portata a regime = 6.000 / 19,69 = 305 litri al minuto
  lanci entro 180 minuti di autonomia: 7, per 42.000 litri
  portata sull'intera missione = 42.000 / 162,8 = 258 litri al minuto

Lago di Cecita, 5,9 km dal fuoco
  t_ciclo_acqua   = 5,9 * 2 / 330 * 60 + 2,0 + 1,0 = 5,15 minuti
  portata a regime = 6.000 / 5,15 = 1.166 litri al minuto
  lanci entro 180 minuti di autonomia: 30, per 180.000 litri
  portata sull'intera missione = 180.000 / 179,3 = 1.004 litri al minuto
```

Le due portate non misurano la stessa cosa e conviene tenerle distinte. Quella a regime divide i
litri di un lancio per il ciclo pescaggio-sgancio e descrive il mezzo mentre lavora sul fuoco;
quella sull'intera missione divide i litri totali per il tempo dalla partenza al rientro, quindi
paga anche il trasferimento di andata, il ritorno e i 5 minuti di `setup_minutes`. Il solver e il
validatore ragionano sulla seconda, perche' il mezzo e' impegnato per tutto quel tempo ed e'
esattamente cio' che misura `Assignment.engaged_minutes`.

Lo stesso mezzo, lo stesso giorno, rende quasi quattro volte tanto in portata e oltre quattro volte
in litri consegnati per missione: la variabile decisiva non e' l'aereo, e' la sorgente d'acqua. Ecco
perche' `WaterSource` e' un'entita' di primo livello del modello, con la lista `usable_by` che dice
quali mezzi possono attingervi. Il Cecita e' un invaso artificiale dell'altopiano silano e sta gia'
nei dati statici del progetto come `WS_LAG_CECITA`, con `usable_by` che comprende il Canadair e una
`seasonal_reliability` di 0,75 a dire che d'estate il livello cala. Che sia davvero utilizzabile da
un anfibio e' pero' una questione di dimensioni dello specchio e di autorizzazioni che non ho
verificato: quel `usable_by` e' un'ipotesi dei dati statici, non un fatto accertato, ed e'
esattamente il tipo di informazione che va marcata come tale invece di essere data per buona.

Un elicottero pesante della classe gru volante porta intorno ai 9.500 litri, vola intorno ai 200
chilometri orari e aspira acqua da specchi molto piu' piccoli: sul Cecita il ciclo dura
`5,9 * 2 / 200 * 60 + 2,0 + 1,0 = 6,54` minuti, cioe' 1.453 litri al minuto, quindi su un fuoco
interno con acqua vicina batte il Canadair pur essendo piu' lento in trasferimento. Un elicottero
medio con benna da 1.200 litri, alla stessa velocita' e sulla stessa sorgente, chiude il ciclo negli
stessi 6,54 minuti e scende a 184 litri al minuto, ma e' agile e trasporta la squadra.

Un avvertimento su quel confronto, perche' a un colloquio e' il punto su cui ti possono incalzare.
Ho usato per tutti e due i mezzi lo stesso `refill_minutes = 2.0`, che e' il default della dataclass
`Vehicle`. Nella realta' un anfibio riempie i serbatoi planando sull'acqua in una decina di secondi,
mentre un elicottero deve stabilizzarsi in volo stazionario e riempire la benna o pompare con la
sonda, il che richiede di piu'. Un `refill_minutes` uguale per i due mezzi favorisce quindi
l'elicottero, e il risultato "l'elicottero pesante batte il Canadair sull'acqua vicina" regge
comunque, ma con un margine piu' stretto di quello che il conto lascia credere.

C'e' anche un dettaglio di modellazione che conviene vedere adesso, perche' tornera' quando leggerai
il validatore. Il campo `refill_minutes` esiste due volte, su `Vehicle` e su `WaterSource`, e la
formula del ciclo in SPEC.md scrive solo `t_pescaggio` senza dire quale dei due prevalga. Le letture
sensate sono due: il massimo fra i due, se si pensa che il collo di bottiglia sia il piu' lento fra
mezzo e sorgente, oppure quello della sorgente, se si pensa che sia lo specchio d'acqua a dettare la
manovra. Finche' la scelta non e' scritta, risolutore e validatore possono divergere proprio qui, e
una divergenza sul tempo di pescaggio si moltiplica per il numero di lanci.

Un'autobotte da 4.000 litri, infine, dal comando dei vigili del fuoco di Cosenza (39,3125 N,
16,2472 E, che nei dati statici del progetto e' la base `BASE_VVF_CS`) a Camigliatello percorre 19,8
chilometri in linea d'aria, che `ROAD_TORTUOSITY = 1.45` porta a 28,7 chilometri di strada, cioe'
38,3 minuti a 45 chilometri orari medi. Se torna in citta' a riempirsi da idrante, con 15 minuti di
rifornimento (che qui metto a mano, perche' il default di 2 minuti descrive un pescaggio e non un
attacco a un idrante) il ciclo completo dura circa 92 minuti e la portata scende a 44 litri al
minuto.

Confronta le quattro portate, 1.166, 1.453, 184 e 44 litri al minuto (le prime tre a regime sulla
stessa sorgente, la quarta sull'intero ciclo dell'autobotte): sembra la classifica dei mezzi e
invece misura la cosa sbagliata, perche' l'autobotte e la squadra a terra sono le uniche che
spengono davvero, lavorano di notte quando gli aerei sono a terra e restano sul posto per l'intero
turno di 480 minuti (`max_shift_minutes`) invece di rientrare dopo 180. Sui costi do solo l'ordine
di grandezza, senza inventare cifre che non ho verificato: un'ora di volo di un anfibio o di un
elicottero pesante costa nell'ordine delle migliaia di euro, una squadra a terra uno o due ordini di
grandezza in meno, e nel progetto `cost_per_hour_eur` ha default 1.000 euro come taratura per
ordinare i mezzi nel termine `cost` dell'obiettivo.

Un'ultima avvertenza, perche' se apri i file del progetto vedrai numeri diversi da quelli di questo
paragrafo. I conti qui sopra usano i valori di default della dataclass `Vehicle`, che sono
segnaposto generici uguali per tutti i mezzi, con la sola velocita' portata a 330 chilometri orari
per il Canadair. Il dataset in `data/static/mezzi.json` porta invece valori differenziati per
categoria, e almeno uno cambia il risultato in modo visibile: se l'autonomia del Canadair e' 240
minuti invece dei 180 del default, i lanci per missione passano da 7 a 11 e i litri da 42.000 a
circa 67.000. Non ho riscritto gli esempi su quel dataset perche' e' un'ipotesi di taratura che puo'
cambiare, mentre i default della dataclass sono il contratto, e perche' la conclusione del paragrafo
non dipende da quei numeri: qualunque autonomia tu scelga, la distanza dalla sorgente d'acqua resta
la variabile che decide il rendimento. Quando difendi il progetto, di' da dove viene ogni numero che
citi.

## 10. Cosa Tagliafuoco modella e cosa no

Il modello di dominio porta le tracce di tutto questo, e `nlu/schema.py` e' costruito sul
vocabolario di una sala operativa: `PrioritizeZone` per "prima le zone vicino alle case",
`TimeWindow` per "gli aerei fino alle otto", `Unsupported` per cio' che il repertorio non copre.
Adesso la parte che non va ammorbidita.

Tagliafuoco non modella la propagazione del fuoco. Nessun modello di propagazione, nessun automa
cellulare, nessuna simulazione di fronte: la crescita ellittica del paragrafo 5 l'ho usata per
argomentare perche' il primo attacco conta, non perche' sia implementata nel codice. Un focolaio
nello `Scenario` ha l'area osservata in quell'istante e resta quello per tutto l'orizzonte, ed e' la
limitazione piu' grave del progetto, perche' significa che il simulatore non sa che un focolaio non
trattato peggiora. Lo stesso vale per `slope_deg`, che esiste nel modello ma non compare in nessuna
formula, perche' la pendenza governa la propagazione e non il fabbisogno istantaneo.

Tagliafuoco non modella il meteo. Lo `Scenario` ha `wind_kmh`, `temperature_c` e `humidity_pct`, ma
sono tre scalari costanti per l'intero orizzonte e per l'intera Calabria, e servono solo a
verificare l'operabilita' dei mezzi tramite `max_wind_kmh`. Un vento da 40 chilometri orari sulla
costa tirrenica e uno in Sila non sono la stessa cosa, e il modello non li distingue.

Tagliafuoco non modella il comportamento degli equipaggi: niente fatica, esperienza, comunicazione
fra mezzi, guasti, tempo reale fra l'ordine impartito e il rotore che gira. Il campo
`setup_minutes`, default 5 minuti, e' l'unica concessione al fatto che le cose non partono da sole.

Tagliafuoco non modella l'efficacia dello spegnimento: conta i litri erogati contro i litri
richiesti, senza nessuna relazione fra i litri e la probabilita' che il fronte si fermi, e
`WATER_LITERS_PER_KM2 = 100_000` e' dichiarata in `config.py` per quello che e', una taratura e non
una verita' fisica. Infine la costante `DISCLAIMER` lo mette per iscritto: Tagliafuoco e' un
simulatore per studiare un problema di assegnazione sotto vincoli, non uno strumento operativo.

## Cosa devi saper spiegare

**Perche' il tempo del primo attacco conta piu' di tutto il resto? Dammi un numero.**
Perche' l'area cresce circa con il quadrato del tempo e il fabbisogno d'acqua cresce con l'area per
l'intensita'. Con un fronte a 30 metri al minuto e rapporto lunghezza-larghezza 2,5, a 15 minuti
l'area e' 6,36 ettari e la formula del progetto chiede 14.246 litri; a 30 minuti e' 25,45 ettari e
chiede 67.697 litri, cioe' 4,75 volte tanto. Un Canadair da Lamezia che pesca in mare al largo di
Amantea consegna al massimo 42.000 litri per missione, quindi quei quindici minuti spostano il
problema da "un mezzo, una missione" a "due mezzi, o due missioni con il riattrezzamento in mezzo".

**Che differenza c'e' fra il triangolo del fuoco e quello del comportamento?**
Il primo (combustibile, ossigeno, calore) spiega perche' una fiamma esiste ed e' la base delle
tecniche di spegnimento; il secondo (combustibile, meteo, topografia) spiega dove va e quanto corre
un fronte gia' acceso, ed e' quello che conterebbe per stimare l'urgenza. Tagliafuoco non implementa
nessuno dei due: usa la FRP osservata come indicatore sintetico di intensita'.

**Perche' la soglia WUI e' a 2 chilometri?**
E' una convenzione, non una legge fisica: compare in molti piani antincendio boschivo regionali
italiani ed e' stata adottata per coerenza con il linguaggio di chi impartisce gli ordini. Le
definizioni cartografiche internazionali usano criteri diversi, che combinano densita' abitativa e
copertura vegetale con soglie di quell'ordine di grandezza ma non identiche.

**Hai una discontinuita' nel modello WUI: sai qual e'?**
Si'. `f_wui` e' continuo, ma `FireCluster.is_wui` e' un booleano che scatta a 2,0 chilometri esatti.
Due focolai identici a 1,9 e 2,1 chilometri differiscono di 0,0031 in priorita' di base, ma solo il
primo viene selezionato da `PrioritizeZone` con `zone_kind=WUI` e solo il primo entra nel
denominatore di `wui_coverage_ratio`. E' un compromesso deliberato, perche' gli ordini vocali sono
booleani, ma su un caso di confine produce una scelta difficile da giustificare.

**Un Canadair e' sempre meglio di un elicottero?**
No, e la variabile decisiva non e' l'aereo, e' la distanza dalla sorgente d'acqua. Sullo stesso
focolaio in Sila un Canadair che pesca in mare a 45,9 chilometri rende 305 litri al minuto a regime;
se pesca al Lago di Cecita a 5,9 chilometri arriva a 1.166. Un elicottero pesante sulla stessa
sorgente sta intorno ai 1.453 litri al minuto, quindi su un fuoco interno con acqua vicina rende
piu' dell'anfibio pur volando a 200 chilometri orari invece che a 330. Va aggiunto che il confronto
usa lo stesso `refill_minutes` per i due mezzi, mentre l'anfibio riempie molto piu' in fretta: la
conclusione resta, il margine si stringe.

**Il lancio dall'alto spegne l'incendio?**
No. Raffredda il fronte, bagna il combustibile davanti alla fiamma e rallenta l'avanzata abbastanza
perche' chi sta a terra possa lavorare, ma lo spegnimento vero e la bonifica li fa la squadra a
terra. Chi ottimizza solo i litri sganciati ottimizza la variabile sbagliata: per questo la funzione
obiettivo tiene separati i cinque termini di `OBJECTIVE_WEIGHTS`.

**Chi decide se un Canadair va in Calabria?**
Non la Regione da sola. La lotta attiva e' competenza regionale per la legge quadro del 2000, ma i
velivoli della flotta aerea dello Stato sono coordinati dal COAU presso il Dipartimento della
protezione civile: la SOUP inoltra la richiesta e il COAU assegna in base alle richieste di tutta
Italia. Nel modello questa competizione non esiste, perche' lo `Scenario` riceve una flotta gia'
disponibile.

**Qual e' la limitazione piu' grave di Tagliafuoco?**
Che non modella la propagazione: un focolaio ha l'area osservata in quell'istante e resta quello per
tutte le quattro ore dell'orizzonte, quindi il simulatore non sa che un focolaio non trattato
peggiora. Questo indebolisce proprio l'argomento principale del dominio, cioe' che il ritardo costa
in modo non lineare. La risposta onesta e' che il progetto ottimizza l'assegnazione data una
fotografia, e che una versione successiva dovrebbe far crescere l'area dentro l'orizzonte.

## Per approfondire

Per il comportamento del fuoco, cerca la documentazione tecnica del servizio forestale statunitense
sul modello di propagazione degli anni settanta e sui sistemi di previsione che ne discendono: e'
materiale pubblico e dettagliato, e spiega come si passa dalle proprieta' del letto di combustibile
a un valore di ROS. Sul versante operativo, il manuale del sistema di comando degli incidenti usato
nell'antincendio statunitense espone con chiarezza la logica di primo attacco e attacco esteso.

Per il quadro italiano, parti dal testo della legge quadro del 2000 e dal piano antincendio boschivo
della Regione Calabria, nella versione dell'anno corrente perche' viene aggiornato: contiene la
classificazione del rischio per comune, l'organizzazione del dispositivo e le procedure di
attivazione, ed e' la fonte giusta per verificare quale soglia di interfaccia urbano-foresta la
Regione adotti davvero e quali siano i termini esatti dei vincoli sul catasto incendi. Per i dati di
superficie percorsa dal fuoco, il sistema europeo di informazione sugli incendi forestali gestito
dal centro comune di ricerca della Commissione europea pubblica perimetri e statistiche stagionali
per regione, ed e' la fonte con cui riverificare le due cifre del paragrafo 1.

Per l'interfaccia urbano-foresta, cerca i lavori cartografici che hanno introdotto la distinzione
fra intermix e interface a partire dai censimenti abitativi: sono l'origine delle soglie numeriche
che circolano ovunque, e leggerli chiarisce perche' sono scelte di mappatura e non misure. Per i
mezzi, le schede dei costruttori vanno incrociate con i requisiti di specchio d'acqua, che decidono
il rendimento reale di una missione molto piu' di quanto facciano capacita' e velocita' di crociera.
