/* Il registro operativo: ordine, vincoli, piano, verdetto.
 *
 * Perche' questo file esiste: e' la parte che distingue questa pagina da una
 * mappa qualunque di incendi. I puntini rossi non dimostrano niente del
 * progetto; la catena dall'ordine in italiano al verdetto del validatore e'
 * il progetto. Per questo il registro segue l'ordine in cui le cose accadono
 * e non un ordine di importanza visiva.
 *
 * Il registro e' anche l'equivalente testuale della mappa. Chi naviga da
 * tastiera o con un lettore di schermo trova qui, in forma di testo, tutto
 * cio' che la mappa disegna: quali focolai esistono, quali mezzi partono,
 * quando arrivano, quanta acqua portano e se il piano regge.
 */

(function () {
  "use strict";

  var TF = (window.TF = window.TF || {});

  /* Nomi italiani dei tipi di vincolo. Il repertorio e' chiuso: se comparisse
   * un tipo non previsto, si mostra il suo nome tecnico invece di inventare
   * una traduzione. */
  var NOMI_VINCOLO = {
    PRIORITIZE_ZONE: "Priorita' a una zona",
    RESERVE_VEHICLE: "Mezzo tenuto di riserva",
    EXCLUDE_VEHICLE: "Mezzo non impiegabile",
    FORCE_ASSIGNMENT: "Assegnazione imposta",
    FORBID_ASSIGNMENT: "Accoppiamento vietato",
    MAX_RESPONSE_TIME: "Tetto al tempo di risposta",
    MIN_VEHICLES_ON_FIRE: "Minimo di mezzi sul focolaio",
    VEHICLE_TYPE_QUOTA: "Quota per tipo di mezzo",
    TIME_WINDOW: "Finestra oraria",
    OBJECTIVE_WEIGHT: "Peso nell'obiettivo",
    PROTECT_INFRASTRUCTURE: "Difesa di un punto sensibile",
    UNSUPPORTED: "Fuori repertorio",
  };

  var NOMI_TERMINE = {
    coverage: "copertura",
    response_time: "tempo di risposta",
    wui_protection: "difesa delle case",
    water_fulfilment: "acqua erogata",
    cost: "costo",
  };

  function esc(testo) {
    return String(testo === null || testo === undefined ? "" : testo)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function numero(valore, decimali) {
    if (valore === null || valore === undefined) return "n.d.";
    var n = Number(valore);
    if (!isFinite(n)) return "n.d.";
    return n.toLocaleString("it-IT", {
      minimumFractionDigits: decimali || 0,
      maximumFractionDigits: decimali || 0,
    });
  }

  function percentuale(parte, tutto) {
    if (!tutto) return "n.d.";
    return Math.round((100 * parte) / tutto) + "%";
  }

  /* Quante assegnazioni hanno davvero contribuito alle metriche.
   *
   * Serve a una distinzione che i numeri da soli non fanno. Il validatore
   * misura i tempi di arrivo sulle sole assegnazioni che ha giudicato
   * eseguibili; se non ne resta nessuna, la media e il novantesimo percentile
   * valgono zero perche' zero e' il valore neutro di una somma vuota, non
   * perche' qualcuno sia arrivato all'istante. Scrivere "0,0 min" in quel
   * caso sarebbe il numero piu' falso della pagina.
   *
   * Il conteggio arriva dal validatore (`assignments_feasible`). Se manca,
   * per esempio perche' i file precalcolati sono piu' vecchi di questo
   * codice, si ripiega sul numero di righe del piano: e' una stima per
   * eccesso, ma basta a riconoscere il caso del piano vuoto, che e' quello
   * che conta. */
  function conteggioArrivi(metriche, righeDelPiano) {
    if (metriche && typeof metriche.assegnazioni_valide === "number") {
      return metriche.assegnazioni_valide;
    }
    return righeDelPiano;
  }

  function tempoRisposta(valore, arrivi) {
    if (!arrivi) return "nessun arrivo";
    return numero(valore, 1) + " min";
  }

  /* ---------------------------------------------------------------- ordine */

  /* Trova nel testo dell'ordine il frammento da cui ogni vincolo e' stato
   * tratto. Il traduttore lo riporta letteralmente, quindi basta cercarlo;
   * quando non lo trova (un frammento normalizzato, per esempio) il vincolo
   * resta senza evidenziazione invece di ricevere una posizione inventata. */
  function trovaFrammenti(testo, vincoli) {
    var mappa = {};
    var usati = [];
    (vincoli || []).forEach(function (v) {
      var frammento = (v.source_span || "").trim();
      if (!frammento) return;
      var da = testo.indexOf(frammento);
      if (da < 0) {
        da = testo.toLowerCase().indexOf(frammento.toLowerCase());
      }
      if (da < 0) return;
      mappa[v.constraint_id] = [da, da + frammento.length];
      usati.push([da, da + frammento.length]);
    });
    return { perVincolo: mappa, intervalli: usati };
  }

  function fondiIntervalli(intervalli) {
    var ordinati = intervalli.slice().sort(function (a, b) {
      return a[0] - b[0];
    });
    var fusi = [];
    ordinati.forEach(function (i) {
      var ultimo = fusi[fusi.length - 1];
      if (ultimo && i[0] <= ultimo[1]) {
        ultimo[1] = Math.max(ultimo[1], i[1]);
      } else {
        fusi.push([i[0], i[1]]);
      }
    });
    return fusi;
  }

  function rendiTestoOrdine(nodo, testo, intervalli, classe) {
    var fusi = fondiIntervalli(intervalli || []);
    var html = "";
    var cursore = 0;
    fusi.forEach(function (i) {
      if (i[0] > cursore) html += esc(testo.slice(cursore, i[0]));
      html +=
        '<mark class="' + classe + '">' + esc(testo.slice(i[0], i[1])) + "</mark>";
      cursore = i[1];
    });
    html += esc(testo.slice(cursore));
    nodo.innerHTML = html || '<span class="nota">Nessun ordine.</span>';
  }

  /* ---------------------------------------------------------------- vincoli */

  function riassumiVincolo(v) {
    switch (v.kind) {
      case "PRIORITIZE_ZONE":
        return (
          "zona " +
          (v.zone_kind === "wui" ? "in interfaccia urbano-foresta" : v.zone_kind) +
          (v.zone_values && v.zone_values.length ? " (" + v.zone_values.join(", ") + ")" : "") +
          ", peso per " +
          v.weight_multiplier
        );
      case "RESERVE_VEHICLE":
        return "mezzi: " + descriviSelettore(v.selector) + (v.count ? ", quantita' " + v.count : "");
      case "EXCLUDE_VEHICLE":
        return "mezzi: " + descriviSelettore(v.selector);
      case "FORCE_ASSIGNMENT":
        return (
          descriviSelettore(v.vehicle_selector) +
          " su " +
          descriviSelettore(v.fire_selector) +
          ", almeno " +
          v.min_count
        );
      case "FORBID_ASSIGNMENT":
        return descriviSelettore(v.vehicle_selector) + " non su " + descriviSelettore(v.fire_selector);
      case "MAX_RESPONSE_TIME":
        return v.minutes + " minuti su " + descriviSelettore(v.fire_selector);
      case "MIN_VEHICLES_ON_FIRE":
        return (
          "almeno " +
          v.count +
          " mezzi" +
          (v.vehicle_selector ? " fra " + descriviSelettore(v.vehicle_selector) : "") +
          " su " +
          descriviSelettore(v.fire_selector)
        );
      case "VEHICLE_TYPE_QUOTA":
        return (
          descriviSelettore(v.selector) +
          (v.min_used !== null && v.min_used !== undefined ? ", minimo " + v.min_used : "") +
          (v.max_used !== null && v.max_used !== undefined ? ", massimo " + v.max_used : "")
        );
      case "TIME_WINDOW":
        return (
          descriviSelettore(v.selector) +
          " dalle " +
          oraDaMinuti(v.from_minute) +
          " alle " +
          oraDaMinuti(v.to_minute)
        );
      case "OBJECTIVE_WEIGHT":
        return (NOMI_TERMINE[v.term] || v.term) + " con peso " + v.weight;
      case "PROTECT_INFRASTRUCTURE":
        return v.name + ", entro " + v.buffer_km + " km, peso per " + v.weight_multiplier;
      case "UNSUPPORTED":
        return v.reason;
      default:
        return "";
    }
  }

  function descriviSelettore(s) {
    if (!s) return "tutti";
    if (s.field === "all") return "tutti";
    var valori = (s.values || []).join(", ");
    var etichette = {
      vehicle_id: "matricola",
      vehicle_kind: "tipo",
      vehicle_domain: "dominio",
      base_id: "base",
      fire_id: "focolaio",
      fire_comune: "comune",
      fire_provincia: "provincia",
      fire_severity: "gravita'",
      fire_is_wui: "interfaccia urbano-foresta",
      fire_in_protected_area: "area protetta",
    };
    return (etichette[s.field] || s.field) + " " + valori;
  }

  function oraDaMinuti(minuti) {
    var h = Math.floor(minuti / 60);
    var m = minuti % 60;
    return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
  }

  /* ---------------------------------------------------------------- pubblico */

  function Registro(nodi) {
    this.nodi = nodi;
    this.vincoloAcceso = null;
    this.assegnazioneAccesa = null;
    this.frammenti = { perVincolo: {}, intervalli: [] };
    this.suFocolaio = null;
    this.suAssegnazione = null;
    this.testoOrdine = "";
  }

  /* Riquadri scheletro invece di una rotellina: si vede subito quante righe
   * stanno arrivando e dove, e la pagina non cambia altezza quando il
   * contenuto arriva. */
  Registro.prototype.scheletro = function () {
    this.nodi.vincoli.innerHTML =
      '<div class="vincolo"><span class="scheletro medio"></span>' +
      '<span class="scheletro corto"></span></div>' +
      '<div class="vincolo"><span class="scheletro medio"></span>' +
      '<span class="scheletro corto"></span></div>';
    this.nodi.assegnazioni.innerHTML =
      '<div class="assegnazione"><span class="scheletro medio"></span></div>' +
      '<div class="assegnazione"><span class="scheletro medio"></span></div>' +
      '<div class="assegnazione"><span class="scheletro corto"></span></div>';
    this.nodi.validazione.innerHTML = '<div class="scheletro medio"></div>';
    this.nodi.metriche.innerHTML = "";
    this.nodi.scoperti.textContent = "";
    if (this.nodi.fonteMetriche) this.nodi.fonteMetriche.textContent = "";
    if (this.nodi.notePiano) this.nodi.notePiano.innerHTML = "";
  };

  /* Quando nemmeno i dati precalcolati arrivano non resta niente da mostrare,
   * e la pagina lo scrive al posto del contenuto invece di lasciare gli
   * scheletri accesi per sempre. */
  Registro.prototype.guasto = function (messaggio) {
    this.nodi.vincoli.innerHTML = "";
    this.nodi.assegnazioni.innerHTML = "";
    this.nodi.metriche.innerHTML = "";
    this.nodi.scoperti.textContent = "";
    this.nodi.contaVincoli.textContent = "";
    this.nodi.contaPiano.textContent = "";
    if (this.nodi.fonteMetriche) this.nodi.fonteMetriche.textContent = "";
    if (this.nodi.notePiano) this.nodi.notePiano.innerHTML = "";
    this.nodi.validazione.innerHTML =
      '<p class="guasto">' + esc(messaggio) + "</p>";
  };

  Registro.prototype.mostraOrdine = function (testo, vincoli) {
    this.testoOrdine = testo || "";
    this.frammenti = trovaFrammenti(this.testoOrdine, vincoli);
    rendiTestoOrdine(
      this.nodi.ordineReso,
      this.testoOrdine,
      this.frammenti.intervalli,
      ""
    );
  };

  Registro.prototype.accendiFrammento = function (idVincolo) {
    var intervallo = this.frammenti.perVincolo[idVincolo];
    if (!intervallo) {
      rendiTestoOrdine(
        this.nodi.ordineReso,
        this.testoOrdine,
        this.frammenti.intervalli,
        ""
      );
      return false;
    }
    rendiTestoOrdine(this.nodi.ordineReso, this.testoOrdine, [intervallo], "acceso");
    return true;
  };

  Registro.prototype.mostraVincoli = function (insieme) {
    var self = this;
    var vincoli = (insieme && insieme.constraints) || [];
    this.nodi.contaVincoli.textContent =
      vincoli.length === 1 ? "1 vincolo" : vincoli.length + " vincoli";

    if (!vincoli.length) {
      this.nodi.vincoli.innerHTML =
        '<div class="vincolo"><span class="riassunto">Nessun vincolo estratto ' +
        "dall'ordine.</span></div>";
      return;
    }

    this.nodi.vincoli.innerHTML = vincoli
      .map(function (v) {
        var fuori = v.kind === "UNSUPPORTED";
        return (
          '<button type="button" class="vincolo' +
          (fuori ? " non-supportato" : "") +
          '" data-vincolo="' +
          esc(v.constraint_id) +
          '" aria-pressed="false">' +
          '<span class="tipo">' +
          esc(NOMI_VINCOLO[v.kind] || v.kind) +
          "</span> " +
          /* Lo spazio prima di questa parentesi non e' un refuso di
           * formattazione: senza, il nome accessibile del bottone diventa
           * "Tetto al tempo di rispostaduro", perche' i lettori di schermo
           * concatenano i testi adiacenti senza inventare separatori. */
          '<span class="durezza">' +
          (fuori ? "non applicato" : v.enforcement === "hard" ? "duro" : "morbido") +
          (v.confidence !== undefined && v.confidence < 1
            ? ", fiducia " + Math.round(v.confidence * 100) + "%"
            : "") +
          "</span>" +
          (v.source_span
            ? '<span class="frammento">' + esc(v.source_span) + "</span>"
            : "") +
          '<span class="riassunto">' +
          esc(riassumiVincolo(v)) +
          "</span>" +
          "</button>"
        );
      })
      .join("");

    Array.prototype.forEach.call(
      this.nodi.vincoli.querySelectorAll("[data-vincolo]"),
      function (bottone) {
        bottone.addEventListener("click", function () {
          var id = bottone.getAttribute("data-vincolo");
          var gia = self.vincoloAcceso === id;
          Array.prototype.forEach.call(
            self.nodi.vincoli.querySelectorAll("[data-vincolo]"),
            function (b) {
              b.setAttribute("aria-pressed", "false");
            }
          );
          if (gia) {
            self.vincoloAcceso = null;
            self.accendiFrammento(null);
            return;
          }
          self.vincoloAcceso = id;
          bottone.setAttribute("aria-pressed", "true");
          self.accendiFrammento(id);
        });
      }
    );
  };

  Registro.prototype.mostraPiano = function (
    piano,
    focolaiPerId,
    mezziPerId,
    istante,
    noteServizio
  ) {
    var self = this;
    var assegnazioni = (piano && piano.assegnazioni) || [];
    this.nodi.contaPiano.textContent =
      assegnazioni.length +
      (assegnazioni.length === 1 ? " assegnazione" : " assegnazioni") +
      (piano && piano.stato ? ", " + piano.stato.toLowerCase() : "");

    this.mostraNotePiano(piano, noteServizio);

    if (!assegnazioni.length) {
      /* Un piano vuoto ha due cause diverse e vanno separate, perche' la
       * prima e' la normalita' e la seconda e' il risultato interessante:
       * o nella finestra non c'era niente da spegnere, oppure c'era e
       * nessun mezzo ha potuto raggiungerlo. Il motivo per cui nessuno ha
       * potuto sta nelle note del piano, qui sotto. */
      var senzaFocolai = !!(piano && piano.metriche && !piano.metriche.focolai);
      this.nodi.assegnazioni.innerHTML =
        '<div class="assegnazione"><span class="dove">' +
        (senzaFocolai
          ? "Nessun focolaio in questa finestra, quindi nessuna assegnazione."
          : "Nessuna assegnazione: nessun mezzo e' arrivato su un focolaio.") +
        "</span></div>";
    } else {
      this.nodi.assegnazioni.innerHTML = assegnazioni
        .map(function (a, i) {
          var f = focolaiPerId[a.focolaio];
          var mezzo = mezziPerId[a.mezzo];
          var nomeMezzo = (mezzo && mezzo.nome) || a.nome_mezzo || "";
          var dove = (f && f.comune) || a.comune || a.focolaio;
          var inWui = f ? !!f.wui : false;
          var oraArrivo = istante ? oraAssoluta(istante, a.arrivo) : null;
          return (
            '<button type="button" class="assegnazione" data-riga="' +
            i +
            '" data-focolaio="' +
            esc(a.focolaio) +
            '" aria-current="false">' +
            '<span class="mezzo">' +
            esc(a.mezzo) +
            "</span>" +
            '<span class="quando">' +
            (oraArrivo ? oraArrivo + " " : "") +
            "+" +
            numero(a.arrivo, 0) +
            " min</span>" +
            '<span class="dove">' +
            (nomeMezzo ? esc(nomeMezzo) + " verso " : "verso ") +
            esc(dove) +
            (inWui ? ' <span class="wui">interfaccia urbano-foresta</span>' : "") +
            ", " +
            numero(a.litri, 0) +
            " litri in " +
            a.lanci +
            (a.lanci === 1 ? " ciclo" : " cicli") +
            "</span>" +
            "</button>"
          );
        })
        .join("");

      Array.prototype.forEach.call(
        this.nodi.assegnazioni.querySelectorAll("[data-riga]"),
        function (bottone) {
          bottone.addEventListener("click", function () {
            var indice = Number(bottone.getAttribute("data-riga"));
            var gia = self.assegnazioneAccesa === indice;
            Array.prototype.forEach.call(
              self.nodi.assegnazioni.querySelectorAll("[data-riga]"),
              function (b) {
                b.setAttribute("aria-current", "false");
              }
            );
            self.assegnazioneAccesa = gia ? null : indice;
            if (!gia) bottone.setAttribute("aria-current", "true");
            if (self.suFocolaio) {
              self.suFocolaio(gia ? null : bottone.getAttribute("data-focolaio"));
            }
            /* La rotta della riga scelta si ridisegna piu' marcata. Senza
             * questo avviso la mappa se ne accorgerebbe solo al fotogramma
             * successivo, cioe' mai se il timelapse e' fermo. */
            if (self.suAssegnazione) self.suAssegnazione();
          });
        }
      );
    }

    var scoperti = (piano && piano.non_assegnati) || [];
    this.nodi.scoperti.textContent = scoperti.length
      ? "Focolai rimasti scoperti: " +
        scoperti
          .map(function (id) {
            var f = focolaiPerId[id];
            return f ? f.comune || id : id;
          })
          .join(", ") +
        "."
      : "Nessun focolaio rimasto scoperto.";

    var m = piano && piano.metriche;
    if (!m) {
      this.nodi.metriche.innerHTML = "";
      if (this.nodi.fonteMetriche) this.nodi.fonteMetriche.textContent = "";
      return;
    }

    /* Da dove vengono i numeri cambia cosa significano, quindi si scrive. Le
     * metriche dichiarate sono quelle che il piano promette; quelle
     * ricalcolate dal validatore escludono le assegnazioni giudicate non
     * eseguibili, cioe' dicono quanto del piano regge davvero. Uno scarto fra
     * le due non e' un errore di visualizzazione: e' il risultato. */
    if (this.nodi.fonteMetriche) {
      this.nodi.fonteMetriche.textContent =
        piano.fonte_metriche === "validatore"
          ? "Numeri ricalcolati dal validatore, senza le assegnazioni che ha giudicato non eseguibili."
          : "Numeri dichiarati dal piano, prima del controllo del validatore.";
    }

    var arrivi = conteggioArrivi(m, assegnazioni.length);
    var oreMezzo =
      m.minuti_mezzo === null || m.minuti_mezzo === undefined
        ? "n.d."
        : numero(m.minuti_mezzo / 60, 1) + " h";

    this.nodi.metriche.innerHTML = [
      voce(
        "Copertura",
        m.focolai
          ? percentuale(m.coperti, m.focolai) + " (" + m.coperti + "/" + m.focolai + ")"
          : "nessun focolaio"
      ),
      voce(
        "Copertura case",
        m.focolai_wui
          ? percentuale(m.coperti_wui, m.focolai_wui) + " (" + m.coperti_wui + "/" + m.focolai_wui + ")"
          : "nessun focolaio"
      ),
      voce("Risposta media", tempoRisposta(m.risposta_media, arrivi)),
      voce("Risposta al 90", tempoRisposta(m.risposta_p90, arrivi)),
      voce(
        "Acqua",
        m.litri_richiesti
          ? percentuale(m.litri_erogati, m.litri_richiesti) + " del fabbisogno"
          : "nessun fabbisogno stimato"
      ),
      voce("Ore-mezzo", oreMezzo),
    ].join("");
  };

  /* Le note che la pipeline scrive su se stessa.
   *
   * Perche' occupano spazio in pagina invece di restare nel JSON: senza di
   * loro il riquadro del piano puo' mentire per omissione. Un piano con zero
   * assegnazioni accanto a un verdetto "Accettato" fa pensare a una pagina
   * rotta; la nota del risolutore dice che tredici mezzi erano fuori turno di
   * notte e che per gli altri non esisteva nessuna missione praticabile, e a
   * quel punto il riquadro si legge per quello che e'.
   *
   * Sono anche l'unico posto dove compare che un vincolo duro non e' stato
   * applicato. I riquadri dei vincoli, sopra, dicono cosa l'ordine ha
   * chiesto; qui si legge cosa il piano ne ha fatto, e i due possono non
   * coincidere.
   *
   * Le note del servizio restano visibili, quelle del piano stanno in un
   * dettaglio richiudibile: le prime sono poche e riguardano l'esito, le
   * seconde arrivano a una dozzina e comprendono i pesi dell'obiettivo. */
  Registro.prototype.mostraNotePiano = function (piano, noteServizio) {
    var nodo = this.nodi.notePiano;
    if (!nodo) return;
    var delServizio = noteServizio || [];
    var delPiano = (piano && piano.note) || [];

    if (!delServizio.length && !delPiano.length) {
      nodo.innerHTML = "";
      return;
    }

    var html = delServizio
      .map(function (n) {
        return '<p class="nota">' + esc(n) + "</p>";
      })
      .join("");

    if (delPiano.length) {
      html +=
        '<details class="dettaglio-violazioni"><summary>' +
        (delPiano.length === 1
          ? "1 nota del pianificatore"
          : delPiano.length + " note del pianificatore") +
        '</summary><div class="violazioni">' +
        delPiano
          .map(function (n) {
            return '<p class="violazione avviso">' + esc(n) + "</p>";
          })
          .join("") +
        "</div></details>";
    }

    nodo.innerHTML = html;
  };

  function voce(nome, valore) {
    return (
      '<div><span class="voce">' +
      esc(nome) +
      '</span><span class="valore">' +
      esc(valore) +
      "</span></div>"
    );
  }

  function oraAssoluta(istanteIso, minuti) {
    var base = new Date(istanteIso);
    if (isNaN(base.getTime())) return null;
    var quando = new Date(base.getTime() + (minuti || 0) * 60000);
    return (
      String(quando.getUTCHours()).padStart(2, "0") +
      ":" +
      String(quando.getUTCMinutes()).padStart(2, "0")
    );
  }

  Registro.prototype.mostraValidazione = function (v) {
    if (!v) {
      this.nodi.validazione.innerHTML =
        '<p class="nota">Nessun verdetto disponibile per questo piano.</p>';
      return;
    }
    var bloccanti = v.bloccanti || [];
    var avvisi = v.avvisi || [];

    var html =
      '<div class="verdetto" data-esito="' +
      (v.valido ? "accettato" : "respinto") +
      '"><span class="esito">' +
      (v.valido ? "Accettato" : "Respinto") +
      '</span><span class="nota">' +
      (v.valido
        ? avvisi.length + (avvisi.length === 1 ? " avviso" : " avvisi") + ", nessuna violazione bloccante"
        : bloccanti.length +
          (bloccanti.length === 1 ? " violazione bloccante" : " violazioni bloccanti")) +
      "</span></div>";

    /* La riga che il validatore scrive di suo pugno. Porta la copertura
     * ricalcolata accanto al verdetto, e serve proprio nel caso peggiore per
     * chi legge: un piano che non manda nessuno viene accettato, perche' non
     * c'e' niente da invalidare, e senza quella riga "Accettato" da solo
     * suonerebbe come un successo. */
    if (v.riassunto) {
      html += '<p class="nota riassunto-validatore">' + esc(v.riassunto) + "</p>";
    }

    if (bloccanti.length) {
      html +=
        '<div class="violazioni">' +
        bloccanti
          .slice(0, 6)
          .map(function (b) {
            return (
              '<p class="violazione"><span class="regola">' +
              esc(b.rule) +
              "</span>" +
              esc(b.message) +
              "</p>"
            );
          })
          .join("") +
        (bloccanti.length > 6
          ? '<p class="nota">Altre ' + (bloccanti.length - 6) + " violazioni non mostrate.</p>"
          : "") +
        "</div>";
    }

    if (avvisi.length) {
      html +=
        '<details class="dettaglio-violazioni"><summary>' +
        avvisi.length +
        (avvisi.length === 1 ? " avviso non bloccante" : " avvisi non bloccanti") +
        '</summary><div class="violazioni">' +
        avvisi
          .slice(0, 8)
          .map(function (a) {
            return (
              '<p class="violazione avviso"><span class="regola">' +
              esc(a.rule) +
              "</span>" +
              esc(a.message) +
              "</p>"
            );
          })
          .join("") +
        "</div></details>";
    }

    this.nodi.validazione.innerHTML = html;
  };

  Registro.prototype.mostraEsempi = function (scenari, attivo, quandoScelto) {
    this.nodi.esempi.innerHTML = scenari
      .map(function (s, i) {
        return (
          '<button type="button" class="esempio" data-scenario="' +
          i +
          '" aria-current="' +
          (i === attivo ? "true" : "false") +
          '">' +
          esc(s.etichetta) +
          "</button>"
        );
      })
      .join("");
    Array.prototype.forEach.call(
      this.nodi.esempi.querySelectorAll("[data-scenario]"),
      function (bottone) {
        bottone.addEventListener("click", function () {
          quandoScelto(Number(bottone.getAttribute("data-scenario")));
        });
      }
    );
  };

  Registro.prototype.segnaEsempio = function (attivo) {
    Array.prototype.forEach.call(
      this.nodi.esempi.querySelectorAll("[data-scenario]"),
      function (b) {
        b.setAttribute(
          "aria-current",
          Number(b.getAttribute("data-scenario")) === attivo ? "true" : "false"
        );
      }
    );
  };

  TF.registro = {
    crea: function (nodi) {
      return new Registro(nodi);
    },
    nomiVincolo: NOMI_VINCOLO,
  };
})();
