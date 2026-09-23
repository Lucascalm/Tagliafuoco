# Perche' i prompt stanno qui e non nel codice

Il prompt di sistema e' un file di testo, non una stringa dentro un modulo
Python. Le ragioni sono tre e sono tutte pratiche.

**Si legge in un diff.** Un prompt e' la parte piu' fragile di un sistema che
usa un modello linguistico: cambia il comportamento senza cambiare nessun test
unitario. Se vive dentro una tripla virgoletta in mezzo al codice, una modifica
di una riga si perde fra gli import e le annotazioni di tipo. Come file a se'
si vede subito che cosa e' cambiato, e `git log system_it.md` racconta la
storia delle tarature.

**Si puo' versionare accanto ai risultati.** Il benchmark di traduzione
(`benchmarks/`) misura accuratezza, tasso di allucinazione e tasso di
astensione corretta. Quei numeri valgono solo se si sa contro quale prompt sono
stati ottenuti. Con il prompt in un file, il commit che cambia il prompt e il
commit che cambia i numeri sono confrontabili.

**Non ha bisogno di Python per essere letto.** Chi rivede il progetto, o chi lo
racconta a un colloquio, apre un Markdown e capisce che cosa il sistema chiede
al modello. Il codice in `translator.py` e' meccanica di trasporto: il
contenuto del compito e' tutto qui.

## Le scelte di prompting, una per una

### Il ruolo dichiarato in apertura

La prima riga dice "sei un traduttore", non "sei un assistente esperto di
antincendio boschivo". E' voluto. Il rischio principale di questo sistema e'
che il modello si metta a fare il comandante delle operazioni: decida quanti
mezzi servono, stimi i tempi, scelga la tattica. Il prompt toglie quel ruolo in
modo esplicito e ripete la stessa cosa in chiusura, perche' l'istruzione che
conta va detta all'inizio e alla fine, dove l'attenzione e' piu' alta.

### Il repertorio elencato con la semantica, non solo con i nomi

Ogni tipo di vincolo ha accanto una riga che dice che cosa significa e in che
cosa si distingue dal tipo vicino: RESERVE contro EXCLUDE, VEHICLE_TYPE_QUOTA
contro MIN_VEHICLES_ON_FIRE, FORCE_ASSIGNMENT contro PRIORITIZE_ZONE. Sono
proprio le coppie su cui un traduttore sbaglia, perche' in italiano le due
frasi si somigliano molto piu' di quanto si somiglino i due vincoli.

### Dodici esempi, e quattro sono negativi

Gli esempi positivi insegnano la forma. Gli esempi negativi insegnano il
confine, che e' la cosa difficile. Sono costruiti apposta come trappole:

- l'esempio 9 contiene la parola "case", che nel repertorio e' il segnale
  tipico di `PRIORITIZE_ZONE` su `wui`, ma la frase ordina un'evacuazione;
- l'esempio 10 chiede due Canadair alla flotta nazionale, che assomiglia molto
  a un `VEHICLE_TYPE_QUOTA` con `min_used: 2` e invece non e' rappresentabile;
- l'esempio 11 contiene un tempo ("fra un'ora") che non e' un tempo di
  risposta;
- l'esempio 3 contiene una causale ("vento troppo forte") che assomiglia a un
  secondo vincolo e non lo e'.

Ogni esempio negativo e' accompagnato da una riga che dice **perche'** la
traduzione sbagliata sarebbe plausibile. Un'allucinazione implausibile la
scopre chiunque; quelle che fanno danno sono le verosimili.

### L'astensione come risposta corretta, non come fallimento

`UNSUPPORTED` e' presentato come un tipo di vincolo alla pari degli altri, con
il suo posto nell'elenco, non come un errore. La frase che porta il peso e'
questa: un traduttore che non sa astenersi e' peggiore di uno che copre meno
casi, perche' produce vincoli plausibili e falsi. Il benchmark misura
esattamente questo con il tasso di astensione corretta.

### La distinzione duro/morbido ancorata alla lingua

Il prompt non chiede al modello di giudicare quanto sia importante un ordine:
gli chiede di riconoscere una differenza che nell'italiano c'e' gia'.
"Mandate" e' un imperativo, "concentratevi" indica un baricentro,
"se potete" e' una concessiva. Chiedere un giudizio di importanza sarebbe
chiedere una decisione, e le decisioni non sono compito suo.

### `source_span` copiato, non parafrasato

Il prompt insiste che il frammento vada copiato alla lettera. Serve a due cose
diverse. Al coordinatore, che vede accanto a ogni vincolo le proprie parole e
puo' smentirlo prima che il solver parta. Al benchmark, che distingue un
vincolo corretto da uno inventato controllando se il frammento esiste davvero
nell'ordine: un vincolo il cui `source_span` non compare nel testo e'
un'allucinazione, e si conta come tale senza bisogno di annotazione manuale.

### Il contesto dello scenario tenuto corto

Lo scenario viene passato come un blocco compatto (mezzi, basi, focolai attivi
con comune e provincia), non come un dump JSON. Senza di esso il modello non
puo' tradurre "l'elicottero di Cosenza", perche' il `base_id` e' un dato, non
una parola dell'ordine. Con troppo contesto, pero', si rischia il fenomeno
opposto: il modello comincia a ragionare sullo scenario e a scegliere i mezzi.
Il blocco e' volutamente povero, e i dettagli fisici (velocita', autonomia,
capacita' d'acqua) sono esclusi proprio perche' servirebbero solo a decidere.

### Nessuna richiesta di "rispondi in JSON"

Il prompt non chiede un JSON: chiede di chiamare uno strumento. La struttura e'
imposta dallo schema dello strumento e dal `tool_choice` forzato, non dalla
buona volonta' del modello. La ragione tecnica sta nel docstring di
`translator.py`, che e' il posto giusto per spiegarla perche' e' li' che il
meccanismo e' scritto.

## Quando si cambia questo file

Un prompt e' una taratura come le altre del progetto: si cambia con una misura
in mano, non a sensazione. Il giro corretto e' lanciare il benchmark di
traduzione prima, fare la modifica, rilanciarlo, e mettere i due numeri nel
messaggio di commit. Se il tasso di allucinazione sale, la modifica va tolta
anche se l'accuratezza esatta e' salita: un vincolo inventato costa piu' di un
vincolo mancato, perche' quello mancato si vede e quello inventato no.
