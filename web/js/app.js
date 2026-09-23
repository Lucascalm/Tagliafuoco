/* Montaggio della demo: mette insieme dati, mappa, registro e barra del tempo.
 *
 * Perche' questo file esiste: i quattro pezzi precedenti non si conoscono fra
 * loro di proposito. La mappa non sa cosa sia un ordine del coordinatore, il
 * registro non sa cosa sia un tile, la barra del tempo non sa cosa sia un
 * piano. Qui, e solo qui, si decide chi parla con chi, e questo e' anche
 * l'unico posto da leggere per capire il flusso della pagina.
 *
 * La regola che attraversa tutto: la pagina non resta mai in attesa senza
 * dirlo. I dati precalcolati hanno una scadenza, il servizio HTTP ha una
 * scadenza, i tile hanno una scadenza. Alla scadenza si mostra cosa e'
 * mancato e si continua con quello che c'e'.
 *
 * Un piano calcolato adesso dal servizio non e' un caso a parte: diventa una
 * finestra come le altre, con il suo istante sulla barra del tempo e i suoi
 * focolai sulla mappa. Trattarlo come un pannello separato avrebbe prodotto
 * due modi di leggere la stessa cosa, e la mappa non avrebbe mostrato il
 * piano appena calcolato, che e' esattamente il motivo per cui il servizio
 * esiste.
 */

(function () {
  "use strict";

  var TF = (window.TF = window.TF || {});

  var nodi = {};
  var stato = {
    stagione: null,
    scenari: [],
    focolaiPerId: {},
    mezziPerId: {},
    basiPerId: {},
    scenarioCorrente: null,
    scenarioLive: null,
    focolaioAcceso: null,
    pianoMostrato: "piano",
    mappa: null,
    tempo: null,
    registro: null,
  };

  /* ---------------------------------------------------------------- utilita' */

  function id(nome) {
    return document.getElementById(nome);
  }

  function interpola(a, b, f) {
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
  }

  /* Rotta iniziale fra due punti, in gradi da nord. Sulle distanze regionali
   * la differenza con la rotta ortodromica media e' sotto il grado, e serve
   * solo a orientare un triangolo largo venti pixel. */
  function rotta(a, b) {
    var f1 = (a[0] * Math.PI) / 180;
    var f2 = (b[0] * Math.PI) / 180;
    var dl = ((b[1] - a[1]) * Math.PI) / 180;
    var y = Math.sin(dl) * Math.cos(f2);
    var x = Math.cos(f1) * Math.sin(f2) - Math.sin(f1) * Math.cos(f2) * Math.cos(dl);
    return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
  }

  function minutiDaOrigine(iso) {
    var origine = new Date(stato.stagione.origine).getTime();
    var t = new Date(iso).getTime();
    if (isNaN(t) || isNaN(origine)) return 0;
    return Math.round((t - origine) / 60000);
  }

  /* ---------------------------------------------------------------- tema */

  function applicaTema(nome) {
    document.documentElement.setAttribute("data-tema", nome);
    var scuro = nome === "scuro";
    nodi.interruttoreTema.setAttribute("aria-pressed", scuro ? "true" : "false");
    nodi.interruttoreTema.textContent = scuro ? "Tema diurno" : "Tema notturno";
    if (stato.mappa) stato.mappa.cambiaTema(scuro, spegniTile);
  }

  function temaIniziale() {
    var salvato = TF.dati.memoria.leggi(TF.dati.memoria.chiavi.tema, "");
    if (salvato === "scuro" || salvato === "chiaro") return salvato;
    var preferenza =
      window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    return preferenza ? "scuro" : "chiaro";
  }

  /* ---------------------------------------------------------------- tile */

  function spegniTile(motivo) {
    nodi.avvisoTile.hidden = false;
    nodi.avvisoTile.textContent = "";
    var titolo = document.createElement("b");
    titolo.textContent = "Il fondo cartografico non si e' caricato";
    nodi.avvisoTile.appendChild(titolo);
    nodi.avvisoTile.appendChild(
      document.createTextNode(
        " (" +
          String(motivo) +
          "). La mappa resta utilizzabile: i segnaposto sono disegnati sopra " +
          "un fondo neutro, e le posizioni restano esatte. Manca solo il " +
          "disegno di coste, rilievi e strade."
      )
    );
  }

  /* ---------------------------------------------------------------- tempo */

  /* Quale insieme di focolai la mappa disegna. Di norma e' la stagione
   * intera, filtrata per istante. Quando e' attiva una finestra calcolata dal
   * servizio si usano i focolai di quella finestra: sono lo stesso dato letto
   * dalle stesse fixture, ma raggruppati sulla sola finestra, quindi hanno
   * identificativi propri. Mostrare i due insiemi insieme disegnerebbe due
   * cerchi per lo stesso incendio. */
  function sorgenteFocolai(scenario) {
    if (scenario && scenario.focolai_propri) return scenario.focolai_propri;
    return stato.stagione.focolai;
  }

  function focolaiVisibili(minuto, scenario) {
    var finestra = (stato.stagione.visibilita_ore || 12) * 60;
    var fuori = [];
    sorgenteFocolai(scenario).forEach(function (f) {
      if (f.t0 > minuto) return;
      var eta = minuto - f.t1;
      if (eta > finestra) return;
      /* La trasparenza codifica l'eta' del rilevamento: un focolaio visto sei
       * ore fa potrebbe essere gia' spento, e il sistema non ha modo di
       * saperlo. Sbiadire e' il modo onesto di dirlo. */
      var alfa = eta <= 0 ? 0.82 : Math.max(0.16, 0.82 - 0.66 * (eta / finestra));
      fuori.push({ focolaio: f, alfa: alfa });
    });
    return fuori;
  }

  function copre(scenario, minuto) {
    return (
      !!scenario &&
      minuto >= scenario.minuto &&
      minuto <= scenario.minuto + scenario.orizzonte_min
    );
  }

  /* Quale finestra vale a un certo minuto della stagione.
   *
   * La finestra gia' mostrata ha la precedenza finche' copre quel minuto, e la
   * ragione e' concreta: piu' finestre possono insistere sullo stesso istante,
   * e succede sempre quando si preme Pianifica, perche' il servizio calcola
   * sull'istante della finestra corrente. Senza questa precedenza la finestra
   * appena calcolata verrebbe scavalcata dalla prima dell'elenco un istante
   * dopo essere comparsa, e il pannello tornerebbe a mostrare l'esempio
   * precalcolato al posto del piano appena chiesto. */
  function scenarioAttivo(minuto) {
    if (copre(stato.scenarioCorrente, minuto)) return stato.scenarioCorrente;
    for (var i = 0; i < stato.scenari.length; i++) {
      if (copre(stato.scenari[i], minuto)) return stato.scenari[i];
    }
    return null;
  }

  function pianoCorrente(scenario) {
    if (!scenario) return null;
    return stato.pianoMostrato === "baseline" ? scenario.baseline : scenario.piano;
  }

  function validazioneCorrente(scenario) {
    if (!scenario) return null;
    return stato.pianoMostrato === "baseline"
      ? scenario.validazione_baseline
      : scenario.validazione;
  }

  /* Posizione ricostruita di ogni mezzo e rotte da disegnare. Il piano dice
   * quando un mezzo parte e quando arriva, non dove si trova al minuto venti:
   * questa e' una interpolazione lineare lungo la rotta, dichiarata nella
   * pagina e nelle note. Il ritorno dura quanto l'andata, perche' il piano non
   * distingue le due tratte. */
  function ricostruisci(scenario, minuto) {
    var posizioni = {};
    var rotte = [];
    if (!scenario) return { posizioni: posizioni, rotte: rotte };

    var piano = pianoCorrente(scenario);
    if (!piano) return { posizioni: posizioni, rotte: rotte };
    var t = minuto - scenario.minuto;

    (piano.assegnazioni || []).forEach(function (a, indice) {
      if (!a.da || !a.a) return;
      /* Senza i tre istanti non si ricostruisce nessun movimento. Il
       * confronto con `null` in JavaScript lo convertirebbe a zero e il
       * mezzo verrebbe disegnato fermo sul focolaio dal minuto zero, cioe'
       * in un posto in cui non e' mai stato. Meglio non disegnarlo. */
      if (
        typeof a.partenza !== "number" ||
        typeof a.arrivo !== "number" ||
        typeof a.rilascio !== "number"
      ) {
        return;
      }
      var durata = Math.max(0.1, a.arrivo - a.partenza);
      var acceso = stato.registro && stato.registro.assegnazioneAccesa === indice;
      var f = stato.focolaiPerId[a.focolaio];
      var dove = (f && f.comune) || a.comune || a.focolaio;
      var pos = null;
      var verso = null;
      var fatto = null;
      var previsto = null;

      if (t < a.partenza) {
        if (acceso) {
          previsto = [a.da, a.a];
        }
      } else if (t <= a.arrivo) {
        var f1 = (t - a.partenza) / durata;
        pos = interpola(a.da, a.a, f1);
        verso = rotta(a.da, a.a);
        fatto = [a.da, pos];
        previsto = [pos, a.a];
      } else if (t <= a.rilascio) {
        pos = a.a;
        verso = rotta(a.da, a.a);
        fatto = [a.da, a.a];
      } else if (t <= a.rilascio + durata) {
        var f2 = (t - a.rilascio) / durata;
        pos = interpola(a.a, a.da, f2);
        verso = rotta(a.a, a.da);
        fatto = [a.a, pos];
        previsto = [pos, a.da];
      }

      if (pos) {
        posizioni[a.mezzo] = {
          lat: pos[0],
          lon: pos[1],
          rotta: verso,
          classe: "in-missione",
          descrizione:
            (stato.mezziPerId[a.mezzo] ? stato.mezziPerId[a.mezzo].nome : a.mezzo) +
            ", in missione su " +
            dove +
            ", arrivo al minuto " +
            Math.round(a.arrivo),
        };
      }
      if (fatto || previsto) {
        rotte.push({ fatto: fatto, previsto: previsto, acceso: acceso });
      }
    });

    /* I mezzi tenuti di riserva o esclusi dall'ordine restano a terra, ma il
     * loro contorno lo dice: tratteggiato. Lo stato viene dallo scenario
     * vero, risolto in Python dal risolutore di selettori, non indovinato
     * qui dal testo dell'ordine. Una finestra calcolata dal servizio non
     * porta questi stati, perche' la risposta HTTP non dice quale mezzo il
     * modello abbia lasciato libero per soddisfare un vincolo di conteggio:
     * in quel caso i mezzi fermi restano semplicemente fermi. */
    var stati = scenario.stati_mezzi || {};
    Object.keys(stati).forEach(function (idMezzo) {
      if (posizioni[idMezzo]) return;
      var s = stati[idMezzo];
      if (s === "reserve") {
        posizioni[idMezzo] = { classe: "di-riserva" };
      } else if (s === "excluded" || s === "maintenance" || s === "out_of_shift") {
        posizioni[idMezzo] = { classe: "escluso" };
      }
    });

    return { posizioni: posizioni, rotte: rotte };
  }

  function ridisegnaMappa(minuto, scenario) {
    if (!stato.mappa) return;
    stato.mappa.aggiornaFocolai(focolaiVisibili(minuto, scenario));
    var ric = ricostruisci(scenario, minuto);
    stato.mappa.aggiornaMezzi(ric.posizioni);
    stato.mappa.aggiornaRotte(ric.rotte);
  }

  function suIstante(minuto) {
    if (!stato.stagione) return;

    var attivo = scenarioAttivo(minuto);
    if (attivo && attivo !== stato.scenarioCorrente) {
      mostraScenario(attivo, false);
    }
    nodi.finestraAttiva.textContent = attivo
      ? "finestra pianificata: " + attivo.etichetta
      : "";

    ridisegnaMappa(minuto, attivo);
  }

  function ridisegnaOra() {
    if (!stato.tempo) return;
    suIstante(stato.tempo.minuto);
  }

  /* ---------------------------------------------------------------- registro */

  /* `conservaTesto` esiste per un caso solo, e vale la pena dirlo: quando il
   * servizio non risponde la pagina torna a mostrare la finestra precalcolata,
   * ma l'ordine che la persona aveva appena scritto nel campo di testo non e'
   * suo da cancellare. Riscriverlo con l'ordine dell'esempio farebbe perdere
   * il lavoro proprio nel momento del guasto, cioe' quando serve poterlo
   * rimandare. */
  function mostraScenario(scenario, spostaTempo, conservaTesto) {
    stato.scenarioCorrente = scenario;
    stato.registro.segnaEsempio(stato.scenari.indexOf(scenario));
    if (!conservaTesto) nodi.ordineTesto.value = scenario.ordine;

    stato.registro.mostraOrdine(scenario.ordine, scenario.vincoli.constraints);
    stato.registro.mostraVincoli(scenario.vincoli);
    aggiornaPiano();

    if (spostaTempo && stato.tempo) {
      /* Venticinque minuti dentro la finestra: i mezzi sono gia' partiti e la
       * mappa mostra qualcosa che si muove, invece dell'istante zero in cui
       * sono tutti ancora fermi alla base. */
      stato.tempo.ferma();
      stato.tempo.vaiA(scenario.minuto + 25);
    }
  }

  function aggiornaPiano() {
    var scenario = stato.scenarioCorrente;
    if (!scenario) return;
    stato.registro.mostraPiano(
      pianoCorrente(scenario),
      stato.focolaiPerId,
      stato.mezziPerId,
      scenario.istante,
      scenario.note_servizio
    );
    stato.registro.mostraValidazione(validazioneCorrente(scenario));
    nodi.mostraCpsat.setAttribute(
      "aria-pressed",
      stato.pianoMostrato === "piano" ? "true" : "false"
    );
    nodi.mostraBaseline.setAttribute(
      "aria-pressed",
      stato.pianoMostrato === "baseline" ? "true" : "false"
    );
  }

  function accendiFocolaio(idFocolaio) {
    stato.focolaioAcceso = idFocolaio;
    if (!stato.mappa) return;
    stato.mappa.evidenzia(idFocolaio);
    /* Il colore del cerchio si applica al ridisegno successivo. Con il
     * timelapse fermo quel ridisegno non arriverebbe mai, quindi si chiede
     * qui: una selezione che si vede solo dopo aver premuto play non e' una
     * selezione. */
    ridisegnaOra();
    if (idFocolaio) stato.mappa.centraSu(idFocolaio);
  }

  /* ---------------------------------------------------------------- servizio */

  function raccontaServizio(esito, messaggio) {
    nodi.statoServizio.setAttribute("data-esito", esito);
    nodi.statoServizio.textContent = messaggio;
  }

  function sondaServizio() {
    var indirizzo = TF.dati.servizio.indirizzo();
    if (!indirizzo) {
      raccontaServizio(
        "spento",
        "Pagina aperta da file locale: il servizio non e' raggiungibile. " +
          "Restano le sei finestre gia' calcolate."
      );
      return;
    }
    raccontaServizio("ignoto", "Interrogo il servizio su " + indirizzo + "...");
    TF.dati.servizio.sonda().then(
      function (salute) {
        raccontaServizio(
          "acceso",
          "Servizio attivo su " +
            indirizzo +
            ", versione " +
            salute.versione +
            ". Traduttore: " +
            (salute.traduttore === "llm"
              ? "modello linguistico"
              : "regole deterministiche") +
            ". Rilevamenti da " +
            (salute.sorgente === "firms" ? "NASA FIRMS" : "fixture locali") +
            "."
        );
      },
      function (errore) {
        raccontaServizio(
          "spento",
          "Il servizio su " +
            indirizzo +
            " non risponde (" +
            String(errore.message || errore) +
            "). Le sei finestre qui sotto restano quelle gia' calcolate e " +
            "valgono lo stesso."
        );
      }
    );
  }

  /* Il piano che arriva dal servizio non porta le coordinate: porta le
   * matricole. Base di partenza e focolaio di arrivo si ritrovano nei dati
   * statici gia' in pagina, che sono gli stessi che il servizio ha usato.
   * Un'assegnazione che resta senza estremi non viene disegnata, invece di
   * essere disegnata in un posto plausibile. */
  function completaGeometria(piano, focolaiPerId) {
    if (!piano) return;
    (piano.assegnazioni || []).forEach(function (a) {
      var mezzo = stato.mezziPerId[a.mezzo];
      var base = mezzo ? stato.basiPerId[mezzo.base] : null;
      var focolaio = focolaiPerId[a.focolaio];
      a.da = base ? [base.lat, base.lon] : null;
      a.a = focolaio ? [focolaio.lat, focolaio.lon] : null;
    });
  }

  /* La risposta del servizio diventa una finestra come le altre: stesso
   * formato, stesso posto sulla barra del tempo, stessa mappa. Se ce n'era
   * gia' una calcolata prima, viene sostituita: due finestre "calcolate ora"
   * sarebbero indistinguibili nell'elenco. */
  function registraLive(risposta) {
    var focolai = TF.dati.servizio.adattaFocolai(
      risposta.focolaiGrezzi,
      stato.stagione.origine
    );
    var perId = {};
    focolai.forEach(function (f) {
      perId[f.id] = f;
      stato.focolaiPerId[f.id] = f;
    });

    completaGeometria(risposta.piano, perId);
    completaGeometria(risposta.baseline, perId);

    var scenario = {
      id: "LIVE",
      etichetta: "Calcolato ora",
      istante: risposta.istante,
      minuto: minutiDaOrigine(risposta.istante),
      orizzonte_min: risposta.orizzonte_min,
      focolai_propri: focolai,
      stati_mezzi: {},
      ordine: risposta.ordine,
      vincoli: risposta.vincoli,
      piano: risposta.piano,
      baseline: risposta.baseline,
      validazione: risposta.validazione,
      validazione_baseline: risposta.validazione_baseline,
      /* Le note del servizio riguardano il calcolo appena fatto, non il
       * piano: dicono per esempio che in quella finestra non c'era nessun
       * focolaio, e che un piano vuoto li' e' un risultato e non un guasto.
       * Restano appese allo scenario perche' devono ricomparire quando si
       * torna su questa finestra dopo averne guardate altre. */
      note_servizio: risposta.note,
      live: true,
    };

    if (stato.scenarioLive) {
      var vecchio = stato.scenari.indexOf(stato.scenarioLive);
      if (vecchio >= 0) stato.scenari.splice(vecchio, 1);
    }
    stato.scenarioLive = scenario;
    stato.scenari.push(scenario);
    /* La barra del tempo tiene i tratti delle finestre pianificate: sono
     * disegnati una volta al montaggio, quindi una finestra nata dopo non
     * avrebbe il suo. Senza questa riga la pagina promette che la finestra
     * calcolata ora "si posiziona sulla barra del tempo" e poi non la
     * posiziona. */
    if (stato.tempo) stato.tempo.ridisegnaFinestre();
    stato.registro.mostraEsempi(
      stato.scenari,
      stato.scenari.length - 1,
      function (indice) {
        mostraScenario(stato.scenari[indice], true);
      }
    );
    return scenario;
  }

  function tornaAllEsempio(motivo) {
    raccontaServizio("spento", motivo);
    var ripiego = stato.scenarioCorrente || stato.scenari[0];
    if (ripiego) mostraScenario(ripiego, false, true);
  }

  function pianifica() {
    var testo = nodi.ordineTesto.value.trim();
    if (!testo) {
      raccontaServizio("spento", "Scrivi un ordine prima di pianificare.");
      nodi.ordineTesto.focus();
      return;
    }
    var istante = stato.scenarioCorrente ? stato.scenarioCorrente.istante : null;

    nodi.pianifica.disabled = true;
    nodi.pianifica.textContent = "Pianifico...";
    stato.registro.scheletro();
    raccontaServizio(
      "ignoto",
      "Il servizio sta traducendo l'ordine e risolvendo lo scenario. " +
        "Il risolutore ha fino a otto secondi."
    );

    TF.dati.servizio.pianifica(testo, istante, 8).then(
      function (risposta) {
        nodi.pianifica.disabled = false;
        nodi.pianifica.textContent = "Pianifica";
        var scenario = registraLive(risposta);
        stato.pianoMostrato = "piano";
        mostraScenario(scenario, true);
        raccontaServizio(
          "acceso",
          "Pianificato adesso dal servizio in " +
            Number(risposta.secondi_usati || 0).toFixed(0) +
            " s al massimo di risolutore. " +
            (risposta.traduttore === "llm"
              ? "L'ordine e' stato tradotto da un modello linguistico."
              : "L'ordine e' stato tradotto dalle regole deterministiche.") +
            " La finestra e' sulla barra del tempo come le altre."
        );
      },
      function (errore) {
        nodi.pianifica.disabled = false;
        nodi.pianifica.textContent = "Pianifica";
        tornaAllEsempio(
          "Il servizio non ha pianificato (" +
            String(errore.message || errore) +
            "). Resta in pagina la finestra gia' calcolata, che vale lo stesso."
        );
      }
    );
  }

  /* ---------------------------------------------------------------- avvio */

  function raccogliNodi() {
    nodi = {
      interruttoreTema: id("interruttore-tema"),
      avvisoTile: id("avviso-tile"),
      mappaAssente: id("mappa-assente"),
      mappa: id("mappa"),
      esempi: id("esempi"),
      ordineTesto: id("ordine-testo"),
      ordineReso: id("ordine-reso"),
      pianifica: id("pianifica"),
      statoServizio: id("stato-servizio"),
      vincoli: id("vincoli"),
      contaVincoli: id("conta-vincoli"),
      assegnazioni: id("assegnazioni"),
      contaPiano: id("conta-piano"),
      scoperti: id("scoperti"),
      metriche: id("metriche"),
      fonteMetriche: id("fonte-metriche"),
      validazione: id("validazione"),
      provenienza: id("provenienza"),
      mostraCpsat: id("mostra-cpsat"),
      mostraBaseline: id("mostra-baseline"),
      gioca: id("gioca"),
      icona: id("icona-gioca"),
      data: id("tempo-data"),
      cursore: id("tempo-cursore"),
      input: id("tempo-input"),
      istogramma: id("istogramma"),
      velocita: id("velocita"),
      finestraAttiva: id("finestra-attiva"),
    };
  }

  function avvia() {
    raccogliNodi();

    stato.registro = TF.registro.crea({
      esempi: nodi.esempi,
      ordineReso: nodi.ordineReso,
      vincoli: nodi.vincoli,
      contaVincoli: nodi.contaVincoli,
      assegnazioni: nodi.assegnazioni,
      contaPiano: nodi.contaPiano,
      scoperti: nodi.scoperti,
      metriche: nodi.metriche,
      fonteMetriche: nodi.fonteMetriche,
      validazione: nodi.validazione,
    });
    stato.registro.scheletro();

    nodi.interruttoreTema.addEventListener("click", function () {
      var nuovo =
        document.documentElement.getAttribute("data-tema") === "scuro"
          ? "chiaro"
          : "scuro";
      TF.dati.memoria.scrivi(TF.dati.memoria.chiavi.tema, nuovo);
      applicaTema(nuovo);
    });
    applicaTema(temaIniziale());

    nodi.pianifica.addEventListener("click", pianifica);
    nodi.mostraCpsat.addEventListener("click", function () {
      stato.pianoMostrato = "piano";
      aggiornaPiano();
      ridisegnaOra();
    });
    nodi.mostraBaseline.addEventListener("click", function () {
      stato.pianoMostrato = "baseline";
      aggiornaPiano();
      ridisegnaOra();
    });

    TF.dati.carica().then(montaDati, function (errore) {
      stato.registro.guasto(
        "I dati precalcolati non si sono caricati: " +
          String(errore.message || errore) +
          ". Rigenerali con web/data/genera_dati_demo.py, oppure servi la " +
          "cartella web da un server statico."
      );
      raccontaServizio("spento", "Nessun dato in pagina: vedi il riquadro qui sopra.");
    });
  }

  function montaDati(dati) {
    stato.stagione = dati.stagione;
    stato.scenari = ((dati.scenari && dati.scenari.scenari) || []).slice();

    stato.stagione.focolai.forEach(function (f) {
      stato.focolaiPerId[f.id] = f;
    });
    (stato.stagione.mezzi || []).forEach(function (m) {
      stato.mezziPerId[m.id] = m;
    });
    (stato.stagione.basi || []).forEach(function (b) {
      stato.basiPerId[b.id] = b;
    });

    var p = stato.stagione.provenienza || {};
    nodi.provenienza.textContent =
      stato.stagione.rilevamenti_totali +
      " rilevamenti della stagione " +
      stato.stagione.anno +
      ", raggruppati in " +
      stato.stagione.focolai.length +
      " focolai. " +
      (p.fixture || "") +
      " " +
      (p.pipeline || "") +
      " Orari in UTC.";

    if (TF.mappa.disponibile()) {
      stato.mappa = TF.mappa.crea(nodi.mappa, stato.stagione);
      stato.mappa.suFocolaio = function (idFocolaio) {
        accendiFocolaio(idFocolaio === stato.focolaioAcceso ? null : idFocolaio);
      };
      stato.mappa.montaTile(
        document.documentElement.getAttribute("data-tema") === "scuro",
        spegniTile
      );
    } else {
      nodi.mappaAssente.hidden = false;
    }

    stato.registro.suFocolaio = accendiFocolaio;
    stato.registro.suAssegnazione = ridisegnaOra;
    stato.registro.mostraEsempi(stato.scenari, 0, function (indice) {
      mostraScenario(stato.scenari[indice], true);
    });

    stato.tempo = TF.tempo.crea(
      {
        gioca: nodi.gioca,
        icona: nodi.icona,
        data: nodi.data,
        cursore: nodi.cursore,
        input: nodi.input,
        istogramma: nodi.istogramma,
        velocita: nodi.velocita,
      },
      stato.stagione,
      stato.scenari,
      { suIstante: suIstante }
    );

    if (stato.scenari.length) {
      mostraScenario(stato.scenari[0], true);
    } else {
      stato.tempo.vaiA(0);
    }

    sondaServizio();

    window.addEventListener("resize", function () {
      if (stato.mappa) stato.mappa.ridimensiona();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", avvia);
  } else {
    avvia();
  }
})();
