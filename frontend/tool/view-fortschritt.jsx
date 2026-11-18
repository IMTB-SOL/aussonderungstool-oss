// SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
//
// SPDX-License-Identifier: EUPL-1.2

// Fortschritt-View — Live-Prüflauf mit Bestätigungs-Schleife + Protokoll-Split-View
// Echte Backend-Anbindung: SSE-Stream + schrittweiser Agentenstart.
const { useState: useStateF, useEffect: useEffectF, useMemo: useMemoF, useRef: useRefF } = React;

// Backend-Status → Frontend-Vokabular (pending|active|done|skipped|failed).
// "wartet" bedeutet im Backend: Agent fertig, wartet auf Bestätigung — im
// schrittweisen Frontend-Ablauf zählt das als abgeschlossener Schritt ("done").
function mapAgentStatus(backendStatus) {
  switch (String(backendStatus || "")) {
    case "laufend":        return "active";
    case "ok":             return "done";
    case "wartet":         return "done";
    case "fehler":         return "failed";
    case "uebersprungen":  return "skipped";
    default:               return "pending";
  }
}

function FortschrittView({ jobId, enabledAgents, agentStates, setAgentStates, currentAgent, setCurrentAgent, focusAgent, setFocusAgent, onAllDone }) {
  const { AGENTS } = window.AUSSONDERUNG_DATA;
  const activeAgents = AGENTS.filter((a) => enabledAgents[a.id]);
  const [protokollTab, setProtokollTab] = useStateF("dok");
  const [logLines, setLogLines] = useStateF({});  // per agent: Live-Log aus SSE-Messages
  const [anmerkung, setAnmerkung] = useStateF("");
  const [anmerkungStatus, setAnmerkungStatus] = useStateF("");
  const [fehler, setFehler] = useStateF("");
  const [startLaeuft, setStartLaeuft] = useStateF(false);
  const [ergebnisse, setErgebnisse] = useStateF({});   // { agentId: ergebnisJSON }
  const [protokollDaten, setProtokollDaten] = useStateF(null);
  const [protokollLaedt, setProtokollLaedt] = useStateF(false);
  const [tiefpruefEvent, setTiefpruefEvent] = useStateF(null);  // letztes Tiefenprüfungs-SSE-Event

  // Reparatur-/Konvertier-/Neukonvertier-Aktionen — Fortschrittsmarkierung.
  const [repairs, setRepairs] = useStateF({});        // { az|'__alle': 'running'|'repaired'|'failed' }
  const [conversions, setConversions] = useStateF({}); // { dateiname|'__alle': '...' }
  const [regenerations, setRegenerations] = useStateF({});

  const [alleFertig, setAlleFertig] = useStateF(false);

  // Bewertungs-/Erschließungsebene (Akte/Teilakte/Vorgang) — nur im Paket
  // vorhandene Ebenen werden angeboten; feinste ist vorausgewählt.
  const [ebenen, setEbenen] = useStateF([]);
  // Abbruch/Zurücksetzen: Bestandsjobs ohne aufbewahrtes Original-ZIP können
  // nur abgebrochen, nicht zurückgesetzt werden.
  const [zuruecksetzbar, setZuruecksetzbar] = useStateF(false);
  const [abbruchLaeuft, setAbbruchLaeuft] = useStateF(false);
  const [resetDialog, setResetDialog] = useStateF(false);
  const [ebene, setEbene] = useStateF(null);

  const sseRef = useRefF(null);
  const allDoneGemeldet = useRefF(false);
  const erstesGewaehltesGesendet = useRefF(false);

  // Aktiver Agent für Protokoll-Anzeige
  const viewAgent = focusAgent || currentAgent || activeAgents[0]?.id;
  const viewAgentObj = AGENTS.find((a) => a.id === viewAgent);

  // --- SSE-Verbindung: Live-Status aller Agenten ---------------------------
  useEffectF(() => {
    if (!jobId) return;
    allDoneGemeldet.current = false;

    const quelle = Api.sse(jobId, (ev) => {
      if (!ev || !ev.agent) return;

      // System-Event: alle Agenten durch.
      if (ev.agent === "system") {
        if (ev.status === "abgeschlossen") pruefeObAllesFertig();
        return;
      }

      // Tiefenprüfungs-Events NICHT als Agentenstatus behandeln — sonst würde
      // der bereits fertige Bewertungsagent auf "Wartend" zurückspringen.
      // Sie speisen nur die Live-Anzeige im Bewertungsprotokoll.
      if (ev.status === "tiefenpruefung") {
        setTiefpruefEvent(ev);
        return;
      }

      const feState = mapAgentStatus(ev.status);

      // Status nur für tatsächlich aktivierte Agenten übernehmen.
      if (enabledAgents[ev.agent]) {
        setAgentStates((st) => {
          const alt = st[ev.agent];
          // Übersprungene Agenten nicht überschreiben.
          if (alt === "skipped") return st;
          return { ...st, [ev.agent]: feState };
        });
      }

      // Live-Log: jede nicht-leere Message anhängen.
      if (ev.message) {
        setLogLines((s) => {
          const bisher = s[ev.agent] || [];
          if (bisher[bisher.length - 1] === ev.message) return s;
          return { ...s, [ev.agent]: [...bisher, ev.message] };
        });
      }

      // Agent fertig (wartet/ok = abgeschlossen, fehler = mit Fehlern).
      if (ev.status === "wartet" || ev.status === "ok" || ev.status === "fehler") {
        setCurrentAgent((cur) => (cur === ev.agent ? null : cur));
        ladeErgebnis(ev.agent);
        if (ev.agent === viewAgentRef.current) ladeProtokoll(ev.agent);
      }

      // Agent läuft → Fokus mitführen.
      if (ev.status === "laufend") {
        setCurrentAgent(ev.agent);
      }

      // Abgebrochen → Schritt ist wieder startbar, Fokus freigeben.
      if (ev.status === "abgebrochen") {
        setAgentStates((st) => ({ ...st, [ev.agent]: "pending" }));
        setCurrentAgent((cur) => (cur === ev.agent ? null : cur));
      }
    });
    sseRef.current = quelle;
    quelle.onerror = () => {
      // EventSource versucht selbst Reconnect; nur dezent melden.
      logFehler("Verbindung zum Server unterbrochen — versuche erneut…");
    };

    return () => {
      try { quelle.close(); } catch (_) { /* ignore */ }
      sseRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // viewAgent in Ref spiegeln, damit SSE-Callback aktuellen Wert sieht.
  const viewAgentRef = useRefF(viewAgent);
  useEffectF(() => { viewAgentRef.current = viewAgent; }, [viewAgent]);

  // Beim (Wieder-)Einhängen — z.B. nach Wechsel zu Admin/Historie und zurück —
  // bereits vorhandene Agenten-Ergebnisse aus dem Backend nachladen, damit die
  // Ansicht nicht leer ist (Agent-Zustände kommen persistiert via Props).
  useEffectF(() => {
    if (jobId) ladeErgebnis();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // Verfügbare Bewertungs-/Erschließungsebenen aus der Paketstruktur laden.
  useEffectF(() => {
    if (!jobId) return;
    let aktiv = true;
    Api.get(`job/${jobId}/struktur`)
      .then((d) => {
        if (!aktiv) return;
        const verf = (d && d.bewertungsebenen) || [];
        setEbenen(verf);
        setZuruecksetzbar(!!(d && d.zuruecksetzbar));
        setEbene((cur) => cur && verf.includes(cur) ? cur : (d && d.bewertungsebene_default) || verf[verf.length - 1] || null);
      })
      .catch(() => { /* Ebenen optional — kein harter Fehler */ });
    return () => { aktiv = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // Protokoll nachladen, wenn der fokussierte Agent wechselt.
  // Nur für fertige Agenten — ein laufender/wartender hat noch kein Protokoll.
  useEffectF(() => {
    if (!viewAgent) return;
    const st = agentStates[viewAgent];
    if (st === "done" || st === "failed") {
      ladeProtokoll(viewAgent);
    } else {
      setProtokollDaten(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewAgent]);

  function logFehler(text) {
    setFehler(text);
  }

  // --- Ergebnis eines Agenten laden ---------------------------------------
  async function ladeErgebnis(agentId) {
    try {
      const daten = await Api.get(`job/${jobId}/ergebnis`);
      const erg = (daten && daten.ergebnisse) || {};
      setErgebnisse((e) => ({ ...e, ...erg }));
    } catch (e) {
      logFehler("Ergebnis konnte nicht geladen werden: " + e.message);
    }
  }

  // --- Protokoll eines Agenten laden --------------------------------------
  async function ladeProtokoll(agentId) {
    if (!agentId) return;
    setProtokollLaedt(true);
    try {
      const daten = await Api.get(`job/${jobId}/protokoll/${agentId}`);
      setProtokollDaten({ agent: agentId, ...daten });
    } catch (e) {
      // 404 = noch kein Protokoll — kein harter Fehler.
      setProtokollDaten(null);
    } finally {
      setProtokollLaedt(false);
    }
  }

  // --- Prüfen, ob alle aktivierten Agenten fertig sind --------------------
  function pruefeObAllesFertig() {
    setAgentStates((st) => {
      const fertig = activeAgents.every((a) => {
        const s = st[a.id] || "pending";
        return s === "done" || s === "skipped" || s === "failed";
      });
      if (fertig && !allDoneGemeldet.current) {
        allDoneGemeldet.current = true;
        // Kein automatischer Seitenwechsel mehr — der Archivar entscheidet
        // selbst, wann er zur Ergebnisübersicht wechselt (Button unten).
        setAlleFertig(true);
      }
      return st;
    });
  }

  // Nach jeder agentStates-Änderung prüfen, ob alles durch ist.
  useEffectF(() => {
    pruefeObAllesFertig();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentStates]);

  // --- Nächsten ausstehenden Schritt manuell starten ----------------------
  const nextAgent = activeAgents.find((a) => (agentStates[a.id] || "pending") === "pending");
  const isRunning = !!currentAgent && agentStates[currentAgent] === "active";

  async function startNext(agentId) {
    const ziel = agentId || (nextAgent && nextAgent.id);
    if (!ziel || isRunning || startLaeuft) return;
    setFehler("");
    setStartLaeuft(true);
    // Optimistisch auf "active" — SSE bestätigt/korrigiert gleich.
    setAgentStates((st) => ({ ...st, [ziel]: "active" }));
    setCurrentAgent(ziel);
    setFocusAgent(ziel);
    setLogLines((s) => ({ ...s, [ziel]: [] }));
    try {
      // Beim ersten Start die Agentenauswahl ans Backend übergeben,
      // damit es abgewählte Agenten überspringen kann.
      let body = undefined;
      if (!erstesGewaehltesGesendet.current) {
        body = { gewaehlte_agenten: activeAgents.map((a) => a.id) };
        erstesGewaehltesGesendet.current = true;
      }
      // Gewählte Bewertungs-/Erschließungsebene mitsenden (gilt für beide).
      if (ebene && (ziel === "bewertung" || ziel === "erschliessung")) {
        body = { ...(body || {}), bewertungsebene: ebene };
      }
      await Api.post(`job/${jobId}/run/${ziel}`, body);
    } catch (e) {
      logFehler("Schritt konnte nicht gestartet werden: " + e.message);
      setAgentStates((st) => ({ ...st, [ziel]: "pending" }));
      setCurrentAgent(null);
    } finally {
      setStartLaeuft(false);
    }
  }

  // --- Abbrechen und Zurücksetzen ------------------------------------------

  /** Stoppt den laufenden Prüfschritt. Vorherige Schritte bleiben erhalten. */
  async function schrittAbbrechen() {
    setAbbruchLaeuft(true);
    setFehler("");
    try {
      const d = await Api.post(`job/${jobId}/abbrechen`);
      const agent = d && d.agent;
      if (agent) setAgentStates((st) => ({ ...st, [agent]: "pending" }));
      setCurrentAgent(null);
      await ladeErgebnis();
    } catch (e) {
      logFehler("Abbruch fehlgeschlagen: " + e.message);
    } finally {
      setAbbruchLaeuft(false);
    }
  }

  /** Setzt den Vorgang auf den Zustand nach dem Upload zurück. */
  async function vorgangZuruecksetzen() {
    setResetDialog(false);
    setAbbruchLaeuft(true);
    setFehler("");
    try {
      await Api.post(`job/${jobId}/zuruecksetzen`);
      window.location.reload();
    } catch (e) {
      logFehler("Zurücksetzen fehlgeschlagen: " + e.message);
      setAbbruchLaeuft(false);
    }
  }

  // --- Reparatur-Aktionen --------------------------------------------------
  async function repariereXml() {
    setRepairs((r) => ({ ...r, __alle: "running" }));
    setFehler("");
    try {
      await Api.post(`job/${jobId}/reparatur/xml`);
      setRepairs((r) => ({ ...r, __alle: "repaired" }));
      await ladeErgebnis("formal");
      await ladeProtokoll("formal");
    } catch (e) {
      setRepairs((r) => ({ ...r, __alle: "failed" }));
      logFehler("XML-Reparatur fehlgeschlagen: " + e.message);
    }
  }

  async function konvertierePdfa(zielformat) {
    setConversions((c) => ({ ...c, __alle: "running" }));
    setFehler("");
    try {
      await Api.post(`job/${jobId}/konvertieren-pdfa`, zielformat ? { zielformat } : {});
      setConversions((c) => ({ ...c, __alle: "converted" }));
      await ladeErgebnis("format");
      await ladeProtokoll("format");
    } catch (e) {
      setConversions((c) => ({ ...c, __alle: "failed" }));
      logFehler("PDF/A-Konvertierung fehlgeschlagen: " + e.message);
    }
  }

  async function neukonvertiereDocx(dateiname) {
    setRegenerations((r) => ({ ...r, [dateiname]: "running" }));
    setFehler("");
    try {
      await Api.post(`job/${jobId}/neukonvertierung`, { dateiname });
      setRegenerations((r) => ({ ...r, [dateiname]: "regenerated" }));
      await ladeErgebnis("inhalt");
      await ladeProtokoll("inhalt");
    } catch (e) {
      setRegenerations((r) => ({ ...r, [dateiname]: "failed" }));
      logFehler("Neukonvertierung fehlgeschlagen: " + e.message);
    }
  }

  // --- Anmerkung zum Protokoll speichern ----------------------------------
  async function speichern() {
    const text = anmerkung.trim();
    if (!text || !viewAgent) return;
    setAnmerkungStatus("Speichert…");
    try {
      await Api.post(`job/${jobId}/protokoll/${viewAgent}/anmerkung`, { anmerkung: text });
      setAnmerkungStatus("Gespeichert • " + new Date().toLocaleTimeString("de-DE").slice(0, 5));
      setTimeout(() => setAnmerkungStatus(""), 2400);
    } catch (e) {
      setAnmerkungStatus("Fehler: " + e.message);
    }
  }

  // Status counts
  const doneCount = activeAgents.filter((a) => {
    const s = agentStates[a.id];
    return s === "done" || s === "skipped" || s === "failed";
  }).length;

  return (
    <div className="view-enter">
      {resetDialog && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="reset-titel"
          onClick={() => setResetDialog(false)}
          style={{
            position: "fixed", inset: 0, zIndex: 50,
            background: "rgba(60,64,88,.45)",
            display: "flex", alignItems: "center", justifyContent: "center", padding: 16,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "var(--surface)", borderRadius: 10, padding: "20px 22px",
              maxWidth: 460, width: "100%", boxShadow: "0 12px 32px rgba(0,0,0,.22)",
            }}
          >
            <h2 id="reset-titel" style={{ margin: "0 0 10px", fontSize: 16 }}>
              Vorgang wirklich zurücksetzen?
            </h2>
            <p style={{ margin: "0 0 10px", fontSize: 13.5, color: "var(--fg-muted)" }}>
              Der Vorgang wird auf den Zustand direkt nach dem Hochladen zurückgesetzt.
              Verworfen werden:
            </p>
            <ul style={{ margin: "0 0 14px 18px", fontSize: 13, color: "var(--fg-muted)" }}>
              <li>alle Prüfergebnisse und Protokolle</li>
              <li>alle Bewertungen und Erschließungsdaten</li>
              <li>Anmerkungen und die Archivar-Entscheidung</li>
              <li>alle Reparaturen am Paket</li>
            </ul>
            <p style={{ margin: "0 0 16px", fontSize: 13, color: "var(--fg-muted)" }}>
              Das hochgeladene Paket und der Verlauf bleiben erhalten.
            </p>
            <div className="btn-row" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn--ghost" onClick={() => setResetDialog(false)}>
                Abbrechen
              </button>
              <button className="btn btn--primary" onClick={vorgangZuruecksetzen}>
                Zurücksetzen
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="flex justify-between items-start mb-4" style={{ gap: 16 }}>
        <div>
          <h1 className="view-title">{alleFertig ? "Prüfung abgeschlossen" : "Prüfung läuft"}</h1>
          <p className="view-sub" style={{ marginBottom: 0 }}>
            <strong style={{ color: "var(--imtb-anthrazit)" }}>{doneCount} / {activeAgents.length}</strong>{" "}
            {alleFertig ? "Prüfschritte abgeschlossen — bereit für die Ergebnisübersicht." : "abgeschlossen."}
          </p>
        </div>
        <div className="btn-row">
          {nextAgent && !isRunning && (
            <button className="btn btn--primary" onClick={() => startNext()} disabled={startLaeuft}>
              <Icons.Play size={13} /> Nächsten Schritt starten: {nextAgent.titel}
            </button>
          )}
          {isRunning && (
            <>
              <span className="badge badge--running">
                <Icons.Clock size={12} /> {AGENTS.find((a) => a.id === currentAgent)?.titel || "Prüfschritt"} läuft…
              </span>
              <button
                className="btn btn--ghost"
                onClick={schrittAbbrechen}
                disabled={abbruchLaeuft}
                title="Stoppt den laufenden Prüfschritt. Bereits abgeschlossene Schritte bleiben erhalten."
              >
                {abbruchLaeuft ? "Wird abgebrochen…" : "Abbrechen"}
              </button>
            </>
          )}
          {zuruecksetzbar && !alleFertig && (
            <button
              className="btn btn--ghost"
              onClick={() => setResetDialog(true)}
              disabled={abbruchLaeuft}
              title="Setzt den Vorgang auf den Zustand nach dem Hochladen zurück."
            >
              <Icons.Refresh size={12} /> Vorgang zurücksetzen
            </button>
          )}
          {alleFertig && !isRunning && (
            <button className="btn btn--primary" onClick={() => onAllDone && onAllDone()}>
              Zur Ergebnisübersicht <Icons.ArrowRight size={13} />
            </button>
          )}
        </div>
      </div>

      {fehler && (
        <div style={{
          marginBottom: 14,
          border: "1px solid var(--err)",
          background: "var(--err-bg, #FDECEC)",
          borderRadius: 8,
          padding: "10px 14px",
          display: "flex", alignItems: "center", gap: 8,
          fontSize: 12.5, color: "var(--imtb-anthrazit)",
        }}>
          <Icons.AlertTriangle size={14} style={{ color: "var(--err)" }} />
          <span>{fehler}</span>
        </div>
      )}

      {/* Ebenen-Wahl: erscheint, wenn als nächstes die Bewertung läuft — oder
          (falls Bewertung abgewählt) vor der Erschließung. Gilt für beide. */}
      {(() => {
        const naechster = nextAgent && nextAgent.id;
        const relevant = !isRunning && !alleFertig && ebenen.length > 1 && (
          naechster === "bewertung" ||
          (naechster === "erschliessung" && !enabledAgents.bewertung)
        );
        if (!relevant) return null;
        const LABEL = { akte: "Akte", teilakte: "Teilakte", vorgang: "Vorgang" };
        const wofuer = naechster === "bewertung" ? "Bewertung" : "Erschließung";
        return (
          <div className="surface" style={{ padding: "12px 16px", marginBottom: 14, display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
            <div style={{ minWidth: 0 }}>
              <strong style={{ fontSize: 13, color: "var(--fg-strong)" }}>Auf welcher Ebene {wofuer.toLowerCase()}?</strong>
              <div className="text-xs text-muted">Jede Einheit dieser Ebene wird einzeln {wofuer === "Bewertung" ? "bewertet" : "erschlossen"}.</div>
            </div>
            <div className="chip-group" style={{ marginLeft: "auto" }}>
              {ebenen.map((e) => (
                <button
                  key={e}
                  className={"chip" + (ebene === e ? " is-active" : "")}
                  style={{ fontWeight: 600 }}
                  onClick={() => setEbene(e)}
                >
                  {LABEL[e] || e}
                </button>
              ))}
            </div>
          </div>
        );
      })()}

      <div className="split" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1.5fr)", gap: 20 }}>
        {/* LEFT — Agent-Karten + Log */}
        <div className="flex-col" style={{ gap: 16 }}>
          <div className="agent-list">
            {activeAgents.map((a) => (
              <AgentCard
                key={a.id}
                agent={a}
                state={agentStates[a.id] || "pending"}
                isFocused={viewAgent === a.id}
                isNext={nextAgent && nextAgent.id === a.id && !isRunning}
                logs={logLines[a.id] || []}
                ergebnis={ergebnisse[a.id]}
                onFocus={() => setFocusAgent(a.id)}
                onStart={() => startNext(a.id)}
                startDisabled={startLaeuft}
              />
            ))}
          </div>

          <details className="surface" style={{ border: "1px solid var(--rule)" }}>
            <summary style={{
              padding: "12px 16px",
              fontSize: 12, fontWeight: 600, color: "var(--fg-muted)",
              cursor: "pointer", letterSpacing: ".02em",
              borderBottom: "1px solid var(--rule)",
              display: "flex", alignItems: "center", gap: 8,
            }}>
              <Icons.ChevronRight size={12} /> Technisches Log anzeigen
            </summary>
            <pre style={{
              margin: 0, padding: 14,
              maxHeight: 240, overflow: "auto",
              fontFamily: "JetBrains Mono, SF Mono, Menlo, monospace",
              fontSize: 11.5,
              color: "var(--fg-muted)",
              lineHeight: 1.6,
              background: "#FBFAF7",
              whiteSpace: "pre-wrap",
            }}>
{(() => {
  const zeilen = [];
  zeilen.push(`[Job ${jobId || "—"}] Live-Protokoll`);
  activeAgents.forEach((a) => {
    (logLines[a.id] || []).forEach((l) => zeilen.push(`[${a.id}] ${l}`));
  });
  return zeilen.length > 1 ? zeilen.join("\n") : "Noch keine Log-Ausgaben — starten Sie den ersten Prüfschritt.";
})()}
            </pre>
          </details>
        </div>

        {/* RIGHT — Protokoll Dokument */}
        <div className="protokoll" style={{ position: "sticky", top: 20 }}>
          <div className="protokoll__head">
            <div className="protokoll__title">
              <Icons.FileCheck size={14} />
              Protokoll — {viewAgentObj ? viewAgentObj.titel : "Prüfschritt"}
            </div>
            {viewAgent && (
              <span className={
                "badge " + (
                  agentStates[viewAgent] === "done" ? "badge--ok" :
                  agentStates[viewAgent] === "failed" ? "badge--err" :
                  agentStates[viewAgent] === "active" ? "badge--running" :
                  "badge--info"
                )
              }>
                {agentStates[viewAgent] === "done" ? "Abgeschlossen"
                  : agentStates[viewAgent] === "failed" ? "Mit Fehlern"
                  : agentStates[viewAgent] === "active" ? "Läuft…"
                  : "Wartend"}
              </span>
            )}
          </div>
          <div className="protokoll__tabs">
            <button className={"protokoll__tab" + (protokollTab === "dok" ? " is-active" : "")} onClick={() => setProtokollTab("dok")}>Dokument</button>
            <button className={"protokoll__tab" + (protokollTab === "befunde" ? " is-active" : "")} onClick={() => setProtokollTab("befunde")}>Befunde</button>
            <button className={"protokoll__tab" + (protokollTab === "raw" ? " is-active" : "")} onClick={() => setProtokollTab("raw")}>XML</button>
          </div>
          <div className="protokoll__body">
            {protokollLaedt
              ? <div className="protokoll__empty"><div className="icon-bg"><Icons.Clock size={22} /></div><div style={{ fontWeight: 600, color: "var(--imtb-anthrazit)" }}>Protokoll wird geladen…</div></div>
              : (agentStates[viewAgent] === "done" || agentStates[viewAgent] === "failed" || agentStates[viewAgent] === "active")
                ? <ProtokollContent
                    agent={viewAgent}
                    jobId={jobId}
                    tab={protokollTab}
                    tiefpruefEvent={tiefpruefEvent}
                    state={agentStates[viewAgent]}
                    protokoll={protokollDaten && protokollDaten.agent === viewAgent ? protokollDaten : null}
                    ergebnis={ergebnisse[viewAgent]}
                    repairs={repairs}
                    onRepairXml={repariereXml}
                    conversions={conversions}
                    onConvertPdfa={konvertierePdfa}
                    regenerations={regenerations}
                    onRegenerate={neukonvertiereDocx}
                  />
                : <ProtokollEmpty />
            }
          </div>
          <div className="protokoll__foot">
            <textarea
              className="textarea"
              placeholder="Eigene Anmerkung zum Protokoll … (z. B. Kontext, Folgeaktion, abweichende Bewertung)"
              value={anmerkung}
              onChange={(e) => setAnmerkung(e.target.value)}
              rows={2}
            ></textarea>
            <div className="protokoll__foot-actions">
              <button className="btn btn--secondary btn--sm" disabled={!anmerkung.trim() || !viewAgent} onClick={speichern}>
                <Icons.Save size={12} /> Anmerkung speichern
              </button>
              <span className="protokoll__foot-status">{anmerkungStatus}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function AgentCard({ agent, state, isFocused, isNext, logs, ergebnis, onFocus, onStart, startDisabled }) {
  const Ico = window.Icons[agent.icon];
  const cls = "agent-card" + (state === "active" ? " is-active" : "") + (state === "done" ? " is-done" : "");
  const stateLabel = {
    pending: "Wartend",
    active:  "Läuft…",
    done:    "Abgeschlossen",
    skipped: "Übersprungen",
    failed:  "Fehler",
  }[state];

  // Kurze Ergebnis-Kennzahlen aus dem echten Backend-Ergebnis.
  let zusammenfassung = null;
  if ((state === "done" || state === "failed") && ergebnis) {
    if (Array.isArray(ergebnis.paare)) {
      zusammenfassung = `${ergebnis.paare.length} Dokumentpaar(e) verglichen`;
    } else if (Array.isArray(ergebnis.dateien)) {
      zusammenfassung = `${ergebnis.dateien.length} Datei(en) geprüft`;
    } else if (Array.isArray(ergebnis.fehler) && ergebnis.fehler.length > 0) {
      zusammenfassung = `${ergebnis.fehler.length} Fehler gemeldet`;
    } else if (ergebnis.status === "ok") {
      zusammenfassung = "Ohne Beanstandung abgeschlossen";
    }
  }

  return (
    <div className={cls} onClick={onFocus} style={{ cursor: "pointer", borderColor: isFocused ? "var(--imtb-nachtblau)" : undefined }}>
      <div className="agent-card__head">
        <div className="agent-card__num">{agent.nr}</div>
        <div style={{ width: 32, height: 32, borderRadius: 6, background: "var(--page-deep)", color: "var(--imtb-nachtblau)", display: "grid", placeItems: "center" }}>
          <Ico size={16} />
        </div>
        <div className="agent-card__title">
          {agent.titel}
          <div className="text-xs text-muted" style={{ fontWeight: 400, marginTop: 1 }}>{agent.kurz}</div>
        </div>
        <span className={
          "badge " + (
            state === "done" ? "badge--ok" :
            state === "active" ? "badge--running" :
            state === "skipped" ? "badge--info" :
            state === "failed" ? "badge--err" :
            "badge--info"
          )
        }>
          {state === "done" && <Icons.Check size={11} />}
          {state === "failed" && <Icons.AlertTriangle size={11} />}
          {stateLabel}
        </span>
      </div>

      {state === "active" && logs.length > 0 && (
        <>
          <div className="agent-card__progress"></div>
          <div className="agent-card__live">
            {logs.slice(-5).map((l, i) => (
              <div key={i} style={{ opacity: i === logs.slice(-5).length - 1 ? 1 : 0.55 }}>
                {l}
              </div>
            ))}
          </div>
        </>
      )}

      {(state === "done" || state === "failed") && zusammenfassung && (
        <div className="text-sm text-muted" style={{ display: "flex", gap: 14 }}>
          <span>
            {state === "failed"
              ? <Icons.AlertTriangle size={11} />
              : <Icons.Check size={11} />} {zusammenfassung}
          </span>
        </div>
      )}

      {isNext && (
        <div className="agent-card__actions" onClick={(e) => e.stopPropagation()}>
          <div className="agent-card__actions-info">
            Bereit zum Start.
          </div>
          <button className="btn btn--primary btn--sm" onClick={onStart} disabled={startDisabled}>
            <Icons.Play size={12} /> Schritt starten
          </button>
        </div>
      )}
    </div>
  );
}

function ProtokollEmpty() {
  return (
    <div className="protokoll__empty">
      <div className="icon-bg"><Icons.FileCheck size={22} /></div>
      <div style={{ fontWeight: 600, color: "var(--imtb-anthrazit)" }}>
        Noch kein Protokoll
      </div>
    </div>
  );
}

function ProtokollContent({ agent, jobId, tab, state, protokoll, ergebnis, tiefpruefEvent, repairs, onRepairXml, conversions, onConvertPdfa, regenerations, onRegenerate }) {
  const meldungen = (protokoll && protokoll.meldungen) || [];

  // --- XML-Tab: Roh-XML des Protokolls bzw. Meldungsliste -----------------
  if (tab === "raw") {
    return (
      <pre className="mono" style={{ fontSize: 11.5, lineHeight: 1.55, margin: 0, color: "var(--fg-muted)" }}>
{protokoll
  ? `<protokoll agent="${agent}" status="${protokoll.status || "unbekannt"}" erstellt="${protokoll.erstellt || ""}">\n`
    + meldungen.map((m) => `  <Meldung typ="${m.typ}">${m.text}</Meldung>`).join("\n")
    + `\n</protokoll>`
  : "Kein Protokoll-XML verfügbar."}
      </pre>
    );
  }

  // --- Befunde-Tab: Meldungen aus dem echten Protokoll --------------------
  if (tab === "befunde") {
    if (meldungen.length === 0) {
      return <div className="text-muted">Keine Befunde aufgezeichnet.</div>;
    }
    return (
      <div className="flex-col gap-3">
        {meldungen.map((m, i) => {
          const typ = m.typ === "fehler" ? "err" : m.typ === "warnung" ? "warn" : "ok";
          return (
            <div key={i} style={{
              border: "1px solid var(--rule)",
              borderLeft: "3px solid " + (typ === "warn" ? "var(--warn)" : typ === "err" ? "var(--err)" : "var(--ok)"),
              padding: "10px 14px",
              borderRadius: "0 6px 6px 0",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <strong style={{ fontSize: 13, color: "var(--imtb-anthrazit)" }}>
                  {m.typ === "fehler" ? "Fehler" : m.typ === "warnung" ? "Warnung" : "Hinweis"}
                </strong>
              </div>
              <div className="text-sm text-muted">{m.text}</div>
            </div>
          );
        })}
      </div>
    );
  }

  // --- Dokument-Tab: agentenspezifische Aufbereitung + Aktions-Karten -----
  if (agent === "formal") {
    return <FormalProtokoll protokoll={protokoll} ergebnis={ergebnis} repairs={repairs} onRepairXml={onRepairXml} />;
  }
  if (agent === "format") {
    return <FormatProtokoll protokoll={protokoll} ergebnis={ergebnis} conversions={conversions} onConvertPdfa={onConvertPdfa} />;
  }
  if (agent === "inhalt") {
    return <InhaltProtokoll protokoll={protokoll} ergebnis={ergebnis} regenerations={regenerations} onRegenerate={onRegenerate} />;
  }
  if (agent === "bewertung") {
    return <BewertungProtokoll protokoll={protokoll} jobId={jobId} state={state} tiefpruefEvent={tiefpruefEvent} />;
  }
  if (agent === "erschliessung") {
    return <ErschliessungProtokoll protokoll={protokoll} ergebnis={ergebnis} state={state} />;
  }
  return <GenerischesProtokoll agent={agent} protokoll={protokoll} state={state} />;
}

// Erschließung: Ergebnisse pro Einheit (auf der gewählten Ebene) direkt anzeigen.
function ErschliessungProtokoll({ protokoll, ergebnis, state }) {
  const einheiten = (ergebnis && ergebnis.einheiten) || [];
  if (!einheiten.length) {
    if (state === "active") {
      return <div className="text-muted">Erschließung läuft — Ergebnisse folgen pro Einheit…</div>;
    }
    return <GenerischesProtokoll agent="erschliessung" protokoll={protokoll} state={state} />;
  }
  const ebene = ergebnis && ergebnis.ebene;
  return (
    <div>
      <h1>Erschließung</h1>
      <div className="meta">
        {ebene && ebene !== "auto" && <span>Ebene: {ebene}</span>}
        <span>Einheiten: {einheiten.length}</span>
        {protokoll && protokoll.erstellt && <span>Erstellt: {protokoll.erstellt}</span>}
      </div>
      <h2>Erschließungsergebnisse ({einheiten.length})</h2>
      <div className="flex-col gap-3">
        {einheiten.map((u, i) => {
          const sw = Array.isArray(u.schlagworte) ? u.schlagworte : [];
          const leer = !u.titel && !u.inhaltsangabe && sw.length === 0;
          return (
            <div key={u.einheit_id || i} style={{ border: "1px solid var(--rule)", borderRadius: 6, padding: "10px 12px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Icons.Tags size={12} style={{ color: "var(--accent)" }} />
                <strong style={{ fontSize: 13, color: "var(--imtb-anthrazit)" }}>
                  {u.betreff || u.aktenzeichen || "(ohne Betreff)"}
                </strong>
                {u.archivar_bearbeitet && (
                  <span className="badge" style={{ background: "var(--accent)", color: "var(--imtb-weiss)" }}>
                    <Icons.Edit size={9} /> bearbeitet
                  </span>
                )}
              </div>
              {leer ? (
                <div className="text-xs text-muted" style={{ marginTop: 6 }}>
                  Keine Erschließung erzeugt — in der Ergebnisübersicht manuell ergänzbar.
                </div>
              ) : (
                <>
                  {u.titel && (
                    <div className="text-sm" style={{ marginTop: 4, color: "var(--imtb-anthrazit)" }}>
                      <strong>Titel:</strong> {u.titel}
                    </div>
                  )}
                  {u.inhaltsangabe && (
                    <div className="text-xs text-muted" style={{ marginTop: 6, lineHeight: 1.5, whiteSpace: "pre-line" }}>
                      {u.inhaltsangabe}
                    </div>
                  )}
                  {sw.length > 0 && (
                    <div className="text-xs text-muted" style={{ marginTop: 6 }}>
                      Schlagworte: {sw.join(", ")}
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Generische Protokollanzeige für Agenten ohne Reparatur-Flow.
function GenerischesProtokoll({ agent, protokoll, state }) {
  if (!protokoll) {
    return state === "active"
      ? <div className="text-muted">Prüfschritt läuft — Protokoll folgt nach Abschluss.</div>
      : <ProtokollEmpty />;
  }
  const meldungen = protokoll.meldungen || [];
  return (
    <div>
      <h1>{agent === "bewertung" ? "Archivbewertung" : agent === "erschliessung" ? "Erschließung" : "Prüfprotokoll"}</h1>
      <div className="meta">
        <span>Status: {protokoll.status || "unbekannt"}</span>
        {protokoll.erstellt && <span>Erstellt: {protokoll.erstellt}</span>}
        <span>Meldungen: {meldungen.length}</span>
      </div>
      {meldungen.length === 0
        ? <p>Keine Protokollmeldungen aufgezeichnet.</p>
        : (
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
            {meldungen.map((m, i) => (
              <li key={i} style={{
                padding: "6px 10px", background: "var(--surface)", borderRadius: 4,
                fontSize: 12.5, color: "var(--fg-muted)",
              }}>
                <strong style={{ color: "var(--imtb-anthrazit)" }}>
                  {m.typ === "fehler" ? "Fehler" : m.typ === "warnung" ? "Warnung" : "Hinweis"}:
                </strong>{" "}{m.text}
              </li>
            ))}
          </ul>
        )}
    </div>
  );
}

// --- Archivbewertung: Protokoll + echte A/B/V-Bewertungsvorschläge --------
function BewertungProtokoll({ protokoll, jobId, state, tiefpruefEvent }) {
  const [vorgaenge, setVorgaenge] = useStateF([]);
  const [laedt, setLaedt] = useStateF(false);
  const [fehler, setFehler] = useStateF(null);
  const [pruefend, setPruefend] = useStateF(null);  // id des gerade tief geprüften Vorgangs
  const [tpFehler, setTpFehler] = useStateF(null);

  // Echte Bewertungsvorschläge laden.
  async function ladeVorgaenge() {
    if (!jobId) return;
    setLaedt(true);
    try {
      const d = await window.Api.get(`bewertung/vorgaenge/${jobId}`);
      setVorgaenge(Array.isArray(d) ? d : []);
      setFehler(null);
    } catch (e) {
      setFehler(e && e.message ? e.message : "Bewertungsergebnisse nicht ladbar.");
    } finally {
      setLaedt(false);
    }
  }

  useEffectF(() => {
    ladeVorgaenge();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, state]);

  // Tiefenprüfung direkt im Bewertungsschritt anstoßen.
  async function tieferPruefen(v) {
    setPruefend(v.id);
    setTpFehler(null);
    try {
      await window.Api.post(`bewertung/vorgang/${v.id}/tiefenpruefung`, {});
      await ladeVorgaenge();
    } catch (e) {
      setTpFehler(e && e.message ? e.message : "Tiefenprüfung fehlgeschlagen.");
    } finally {
      setPruefend(null);
    }
  }

  // A/B/V-Klassifikation direkt im Bewertungsschritt ändern (Human-in-the-Loop).
  // Bei Abweichung vom KI-Vorschlag ist eine Begründung Pflicht.
  async function aendereKlassifikation(v, neueKlasse) {
    const vorschlag = String(v.klassifikation_vorschlag || "");
    if (neueKlasse === String(v.klassifikation_final || v.klassifikation_vorschlag || "")) return;
    let begruendung = "";
    if (neueKlasse !== vorschlag) {
      begruendung = window.prompt(
        `Begründung für die abweichende Klassifikation (${vorschlag} → ${neueKlasse}):`,
        v.override_begruendung || "",
      );
      if (begruendung === null) return; // abgebrochen
      if (!begruendung.trim()) {
        window.alert("Bei abweichender Klassifikation ist eine Begründung Pflicht.");
        return;
      }
    }
    setTpFehler(null);
    try {
      await window.Api.post(`bewertung/vorgang/${v.id}/entscheidung`, {
        klassifikation_final: neueKlasse,
        begruendung: begruendung || "",
      });
      await ladeVorgaenge();
    } catch (e) {
      setTpFehler(e && e.message ? e.message : "Bewertung konnte nicht gespeichert werden.");
    }
  }

  const meldungen = (protokoll && protokoll.meldungen) || [];

  return (
    <div>
      <h1>Archivbewertung</h1>
      <div className="meta">
        <span>Status: {protokoll ? (protokoll.status || "unbekannt") : (state === "active" ? "läuft" : "—")}</span>
        <span>Vorgänge: {vorgaenge.length}</span>
        {protokoll && protokoll.erstellt && <span>Erstellt: {protokoll.erstellt}</span>}
      </div>

      {laedt && <p className="text-muted">Bewertungsvorschläge werden geladen…</p>}
      {fehler && <p className="text-sm" style={{ color: "var(--err)" }}>{fehler}</p>}
      {tpFehler && <p className="text-sm" style={{ color: "var(--err)" }}>{tpFehler}</p>}
      {!laedt && !fehler && vorgaenge.length === 0 && (
        <p className="text-muted">Noch keine Bewertungsvorschläge vorhanden.</p>
      )}

      {vorgaenge.length > 0 && (
        <>
          <h2>Bewertungsvorschläge ({vorgaenge.length})</h2>
          <div className="flex-col gap-3">
            {vorgaenge.map((v) => {
              const klass = String(v.klassifikation_final || v.klassifikation_vorschlag || "?");
              const geaendert = v.klassifikation_final &&
                String(v.klassifikation_final) !== String(v.klassifikation_vorschlag || "");
              const erfuellt = (v.treffer || []).filter((t) => t.erfuellt);
              // Live-Status der Tiefenprüfung — nur für den gerade geprüften Vorgang.
              const tp = (tiefpruefEvent && Number(tiefpruefEvent.vorgang_bewertung_id) === Number(v.id))
                ? tiefpruefEvent : null;
              const tpMessage = tp && tp.message ? tp.message : "Tiefenprüfung läuft…";
              const tpProgress = tp && typeof tp.progress === "number" ? tp.progress : 0;
              return (
                <div key={v.id} style={{ border: "1px solid var(--rule)", borderRadius: 6, padding: "10px 12px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className={"klass klass--" + klass.toLowerCase()} style={{ fontSize: 12, padding: "2px 8px" }}>{klass}</span>
                    <strong style={{ fontSize: 13, color: "var(--imtb-anthrazit)" }}>{v.aktenzeichen || v.betreff || "(ohne Aktenzeichen)"}</strong>
                    {geaendert && (
                      <span className="badge" style={{ background: "var(--accent)", color: "var(--imtb-weiss)" }}>
                        <Icons.Edit size={9} /> geändert
                      </span>
                    )}
                    <span className="text-xs text-muted" style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>
                      Score {v.finaler_score != null ? v.finaler_score : "—"}
                    </span>
                  </div>
                  {v.betreff && <div className="text-sm text-muted" style={{ marginTop: 4 }}>{v.betreff}</div>}
                  {v.llm_begruendung && (
                    <div className="text-xs text-muted" style={{ marginTop: 6, lineHeight: 1.5, whiteSpace: "pre-line" }}>{v.llm_begruendung}</div>
                  )}
                  {erfuellt.length > 0 && (
                    <div className="text-xs text-muted" style={{ marginTop: 6 }}>
                      Erfüllte Indizien: {erfuellt.map((t) => t.bezeichnung || t.schluessel).join(", ")}
                    </div>
                  )}
                  <div style={{ marginTop: 8 }}>
                    {v.tiefenpruefung_gemacht ? (
                      <span className="text-xs" style={{ color: "var(--ok)", display: "inline-flex", alignItems: "center", gap: 4 }}>
                        <Icons.CheckCircle size={11} /> Tiefenprüfung erfolgt
                      </span>
                    ) : pruefend === v.id ? (
                      <div>
                        <div className="text-xs" style={{ color: "var(--imtb-nachtblau)", display: "flex", alignItems: "center", gap: 6 }}>
                          <Icons.Search size={11} /> {tpMessage}
                        </div>
                        <div style={{ height: 4, background: "var(--page-deep)", borderRadius: 2, marginTop: 5, overflow: "hidden" }}>
                          <div style={{ height: "100%", width: tpProgress + "%", background: "var(--imtb-nachtblau)", transition: "width .3s" }}></div>
                        </div>
                      </div>
                    ) : (
                      <button
                        className="btn btn--secondary btn--sm"
                        onClick={() => tieferPruefen(v)}
                      >
                        <Icons.Search size={12} /> Tiefer prüfen
                      </button>
                    )}
                  </div>
                  {/* A/B/V-Entscheidung des Archivars */}
                  <div className="override-row" style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                    <span className="text-xs text-muted">Bewertung ändern:</span>
                    {["A", "B", "V"].map((k) => (
                      <button
                        key={k}
                        className={"chip" + (klass === k ? " is-active" : "")}
                        style={{ fontWeight: 700 }}
                        onClick={() => aendereKlassifikation(v, k)}
                      >
                        {k}
                      </button>
                    ))}
                    {geaendert && (
                      <button
                        className="btn btn--ghost btn--sm"
                        style={{ marginLeft: "auto" }}
                        onClick={() => aendereKlassifikation(v, String(v.klassifikation_vorschlag || "B"))}
                      >
                        <Icons.Refresh size={11} /> Auf Vorschlag zurück
                      </button>
                    )}
                  </div>
                  {geaendert && v.override_begruendung && (
                    <div className="text-xs text-muted" style={{ marginTop: 6, fontStyle: "italic" }}>
                      Begründung der Änderung: {v.override_begruendung}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {meldungen.length > 0 && (
        <>
          <h2>Protokoll</h2>
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
            {meldungen.map((m, i) => (
              <li key={i} style={{ padding: "6px 10px", background: "var(--surface)", borderRadius: 4, fontSize: 12.5, color: "var(--fg-muted)" }}>
                <strong style={{ color: "var(--imtb-anthrazit)" }}>
                  {m.typ === "fehler" ? "Fehler" : m.typ === "warnung" ? "Warnung" : "Hinweis"}:
                </strong>{" "}{m.text}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

// --- Formale Prüfung: Protokoll + XML-Reparatur-Karte ---------------------
function FormalProtokoll({ protokoll, ergebnis, repairs, onRepairXml }) {
  const meldungen = (protokoll && protokoll.meldungen) || [];
  const reparierbar = !!(ergebnis && (ergebnis.reparierbar || ergebnis.xml_reparierbar));
  const fehlerListe = (ergebnis && Array.isArray(ergebnis.fehler)) ? ergebnis.fehler : [];
  const istFehler = (ergebnis && ergebnis.status === "fehler") || fehlerListe.length > 0;
  const repStatus = repairs.__alle;  // running | repaired | failed | undefined
  const repariert = repStatus === "repaired" || (ergebnis && ergebnis.xml_repariert);

  // Was genau würde repariert? Ohne diese Punkte müsste die Karte pauschal
  // "XML-Fehler" melden — auch dann, wenn die Prüfung ohne Beanstandung
  // durchlief und nur ein bekanntes DMS-Kompatibilitätsproblem vorliegt.
  const optionen = (ergebnis && ergebnis.reparaturoptionen) || {};
  const reparaturPunkte = [
    ...(Array.isArray(optionen.xsd) ? optionen.xsd : []),
    ...(Array.isArray(optionen.referenzen)
      ? optionen.referenzen.map((d) => `Referenz auf fehlende Datei entfernen: ${d}`)
      : []),
    ...(optionen.encoding ? ["Zeichenkodierung der XML-Datei bereinigen"] : []),
  ];
  // Punkte der Karte: bei echten Fehlern deren Liste, sonst die Reparaturpunkte.
  const kartenPunkte = fehlerListe.length > 0 ? fehlerListe : reparaturPunkte;
  const anzahl = kartenPunkte.length;
  const kartenTitel = istFehler
    ? `${anzahl || ""} XML-Fehler — automatische Reparatur verfügbar`.trim()
    : `${anzahl || ""} Kompatibilitätsproblem${anzahl === 1 ? "" : "e"} — automatisch korrigierbar`.trim();

  return (
    <div>
      <h1>Formale Prüfung — XDOMEA</h1>
      <div className="meta">
        <span>Status: {protokoll ? (protokoll.status || "unbekannt") : (ergebnis && ergebnis.status) || "—"}</span>
        {protokoll && protokoll.erstellt && <span>Erstellt: {protokoll.erstellt}</span>}
        <span>Meldungen: {meldungen.length}</span>
      </div>

      {/* Reparatur-Karte — nur wenn Backend Reparatur anbietet. */}
      {(reparierbar || repariert) && (
        <div style={{
          marginTop: 6, marginBottom: 14,
          border: "1px solid " + (repariert ? "var(--ok)" : "var(--warn)"),
          background: repariert ? "var(--ok-bg)" : "var(--warn-bg)",
          borderRadius: 8,
          padding: "12px 14px",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            {repariert
              ? <Icons.CheckCircle size={14} style={{ color: "var(--ok)" }} />
              : <Icons.AlertTriangle size={14} style={{ color: "var(--warn)" }} />}
            <strong style={{ fontSize: 13, color: "var(--imtb-anthrazit)" }}>
              {repariert ? "XML-Datei(en) repariert" : kartenTitel}
            </strong>
            {!repariert && (
              <button
                className="btn btn--primary btn--sm"
                style={{ marginLeft: "auto" }}
                onClick={onRepairXml}
                disabled={repStatus === "running"}
              >
                <Icons.Refresh size={11} /> {repStatus === "running" ? "Repariert…" : "XML reparieren"}
              </button>
            )}
          </div>
          {!repariert && kartenPunkte.length > 0 && (
            <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
              {kartenPunkte.map((f, i) => (
                <li key={i} style={{
                  padding: "6px 10px", background: "var(--surface)", borderRadius: 4,
                  fontSize: 12.5, color: "var(--fg-muted)",
                }}>{typeof f === "string" ? f : (f.text || JSON.stringify(f))}</li>
              ))}
            </ul>
          )}
          {repStatus === "failed" && (
            <div className="text-xs" style={{ marginTop: 8, color: "var(--err)" }}>
              Reparatur fehlgeschlagen — bitte Protokoll prüfen.
            </div>
          )}
        </div>
      )}

      <h2>Zusammenfassung</h2>
      <p>
        XDOMEA-Struktur gegen das XSD-Schema validiert.
        {istFehler && !repariert
          ? " Es wurden Schema-Verletzungen gefunden — bitte vor Freigabe reparieren."
          : reparierbar && !repariert
            ? ` Keine kritischen Schema-Verletzungen. ${reparaturPunkte.length || ""} bekannte${reparaturPunkte.length === 1 ? "s" : ""} Kompatibilitätsproblem${reparaturPunkte.length === 1 ? "" : "e"} des Exportsystems ${reparaturPunkte.length === 1 ? "lässt" : "lassen"} sich automatisch korrigieren.`.replace(/\s+/g, " ")
            : " Keine Schema-Verletzungen festgestellt."}
      </p>

      {meldungen.length > 0 && (
        <>
          <h2>Befunde</h2>
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
            {meldungen.map((m, i) => (
              <li key={i} style={{ padding: "6px 10px", background: "var(--surface)", borderRadius: 4, fontSize: 12.5, color: "var(--fg-muted)" }}>
                <strong style={{ color: "var(--imtb-anthrazit)" }}>
                  {m.typ === "fehler" ? "Fehler" : m.typ === "warnung" ? "Warnung" : "Hinweis"}:
                </strong>{" "}{m.text}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

// --- Formatprüfung: Protokoll + PDF/A-Konvertier-Karte --------------------

// Fallback-Mapping für den Fall, dass eine ältere Backend-Antwort noch kein
// dateityp-Feld liefert.
const MIME_FALLBACK_LABELS = {
  "application/pdf": "PDF",
  "image/jpeg": "JPEG",
  "image/png": "PNG",
  "image/tiff": "TIFF",
  "text/plain": "Text",
  "text/xml": "XML",
  "application/xml": "XML",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX",
};

function dateitypLabel(d) {
  if (d.dateityp) return d.dateityp;
  if (d.mime_typ && MIME_FALLBACK_LABELS[d.mime_typ]) return MIME_FALLBACK_LABELS[d.mime_typ];
  if (d.mime_typ && d.mime_typ.includes("/")) return d.mime_typ.split("/")[1].toUpperCase();
  return "—";
}

function statusBadge(d) {
  // Reihenfolge: PDF/A-Status > Archivfähig > Unbekannt
  if (d.pdfa_valid === true) {
    const text = d.pdfa_profil ? `PDF/A-${d.pdfa_profil} konform` : "PDF/A-konform";
    return <span className="badge badge--ok"><Icons.Check size={10} /> {text}</span>;
  }
  if (d.pdfa_valid === false) {
    return <span className="badge badge--warn"><Icons.AlertTriangle size={10} /> nicht PDF/A-konform</span>;
  }
  if (d.archivfaehig === true) {
    return <span className="badge badge--ok"><Icons.Check size={10} /> langzeitarchivfähig</span>;
  }
  if (d.archivfaehig === false && d.mime_typ) {
    return <span className="badge badge--warn"><Icons.AlertTriangle size={10} /> nicht archivfähig</span>;
  }
  return <span className="badge badge--info">unbekannt</span>;
}

function FormatProtokoll({ protokoll, ergebnis, conversions, onConvertPdfa }) {
  const meldungen = (protokoll && protokoll.meldungen) || [];
  const dateien = (ergebnis && Array.isArray(ergebnis.dateien)) ? ergebnis.dateien : [];
  const nichtKonform = dateien.filter((d) => d.archivfaehig === false || d.pdfa_valid === false);
  const convStatus = conversions.__alle;
  const konvertiert = convStatus === "converted" || (nichtKonform.length === 0 && dateien.length > 0 && ergebnis && ergebnis.status === "ok");
  const [zielformat, setZielformat] = useStateF("2b");
  // Aufklapp-Zustand pro Dateiname.
  const [offen, setOffen] = useStateF({});
  const toggle = (name) => setOffen((s) => ({ ...s, [name]: !s[name] }));

  return (
    <div>
      <h1>Formatprüfung — PDF/A-Konformität</h1>
      <div className="meta">
        <span>Status: {protokoll ? (protokoll.status || "unbekannt") : (ergebnis && ergebnis.status) || "—"}</span>
        <span>Dateien: {dateien.length}</span>
        {protokoll && protokoll.erstellt && <span>Erstellt: {protokoll.erstellt}</span>}
      </div>

      {/* Konvertier-Karte — nur wenn nicht-konforme Dateien vorhanden. */}
      {(nichtKonform.length > 0 || convStatus) && (
        <div style={{
          marginTop: 6, marginBottom: 14,
          border: "1px solid " + (convStatus === "converted" ? "var(--ok)" : "var(--warn)"),
          background: convStatus === "converted" ? "var(--ok-bg)" : "var(--warn-bg)",
          borderRadius: 8,
          padding: "12px 14px",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
            {convStatus === "converted"
              ? <Icons.CheckCircle size={14} style={{ color: "var(--ok)" }} />
              : <Icons.AlertTriangle size={14} style={{ color: "var(--warn)" }} />}
            <strong style={{ fontSize: 13, color: "var(--imtb-anthrazit)" }}>
              {convStatus === "converted"
                ? "Dateien nach PDF/A konvertiert"
                : `${nichtKonform.length} Datei(en) nicht PDF/A-konform`}
            </strong>
            {convStatus !== "converted" && (
              <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
                <select
                  className="select"
                  style={{ padding: "3px 26px 3px 8px", fontSize: 12 }}
                  value={zielformat}
                  onChange={(e) => setZielformat(e.target.value)}
                  disabled={convStatus === "running"}
                >
                  <option value="1b">PDF/A-1b</option>
                  <option value="2b">PDF/A-2b</option>
                  <option value="3b">PDF/A-3b</option>
                </select>
                <button
                  className="btn btn--primary btn--sm"
                  onClick={() => onConvertPdfa(zielformat)}
                  disabled={convStatus === "running"}
                >
                  {convStatus === "running" ? "Konvertiert…" : "Nach PDF/A konvertieren"}
                </button>
              </span>
            )}
          </div>
          {convStatus !== "converted" && nichtKonform.length > 0 && (
            <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
              {nichtKonform.map((d, i) => (
                <li key={i} style={{
                  display: "grid", gridTemplateColumns: "1fr auto", gap: 12, alignItems: "center",
                  padding: "6px 10px", background: "var(--surface)", borderRadius: 4, fontSize: 12.5,
                }}>
                  <strong style={{ color: "var(--imtb-anthrazit)" }}>{d.dateiname || d.name || "Datei"}</strong>
                  <span className="text-xs text-muted">{dateitypLabel(d)} — {d.details || "nicht konform"}</span>
                </li>
              ))}
            </ul>
          )}
          {convStatus === "failed" && (
            <div className="text-xs" style={{ marginTop: 8, color: "var(--err)" }}>
              Konvertierung fehlgeschlagen — bitte Protokoll prüfen.
            </div>
          )}
        </div>
      )}

      {dateien.length > 0 && (
        <>
          <h2>Geprüfte Dateien</h2>
          <table>
            <thead>
              <tr>
                <th>Datei</th>
                <th>Dateityp</th>
                <th>Status</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {dateien.map((d, i) => {
                const name = d.dateiname || d.name || "—";
                const fehlerDetails = Array.isArray(d.fehler_details) ? d.fehler_details : [];
                const hatDetails = d.pdfa_valid === false && fehlerDetails.length > 0;
                const istOffen = !!offen[name];
                return (
                  <React.Fragment key={i}>
                    <tr>
                      <td><strong>{name}</strong></td>
                      <td>{dateitypLabel(d)}</td>
                      <td>{statusBadge(d)}</td>
                      <td>
                        {hatDetails ? (
                          <button
                            className="btn btn--ghost btn--sm"
                            onClick={() => toggle(name)}
                            style={{ padding: "2px 8px", fontSize: 12 }}
                          >
                            {istOffen ? "▾ verbergen" : "▸ anzeigen"}
                          </button>
                        ) : null}
                      </td>
                    </tr>
                    {hatDetails && istOffen && (
                      <tr>
                        <td colSpan={4} style={{ background: "var(--surface)", padding: "10px 14px" }}>
                          <div className="text-xs text-muted" style={{ marginBottom: 6 }}>
                            {d.fehler_anzahl || fehlerDetails.length} Regelverletzung(en)
                          </div>
                          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
                            {fehlerDetails.map((r, j) => (
                              <li key={j} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                                <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                                  <span className="badge badge--info" style={{ fontSize: 11 }}>
                                    Regel {r.clause || "?"}
                                  </span>
                                  <span style={{ fontSize: 12.5 }}>{r.description || r.text || ""}</span>
                                </div>
                                {r.specification && (
                                  <span className="text-xs text-muted" style={{ paddingLeft: 4 }}>
                                    {r.specification}
                                  </span>
                                )}
                              </li>
                            ))}
                          </ul>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </>
      )}

      {meldungen.length > 0 && (
        <>
          <h2>Befunde</h2>
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
            {meldungen.map((m, i) => (
              <li key={i} style={{ padding: "6px 10px", background: "var(--surface)", borderRadius: 4, fontSize: 12.5, color: "var(--fg-muted)" }}>
                <strong style={{ color: "var(--imtb-anthrazit)" }}>
                  {m.typ === "fehler" ? "Fehler" : m.typ === "warnung" ? "Warnung" : "Hinweis"}:
                </strong>{" "}{m.text}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

// --- Inhaltsvergleich: Protokoll + Neukonvertier-Karte --------------------
// Wort-Diff zweier Texte farbig darstellen (nur DOCX / nur PDF / gleich).
function TextDiff({ segmente }) {
  return (
    <div style={{
      border: "1px solid var(--rule)", borderRadius: 6, padding: "10px 12px",
      background: "var(--surface)", fontSize: 12.5, lineHeight: 1.7,
    }}>
      <div className="text-xs text-muted" style={{ marginBottom: 6, display: "flex", gap: 14 }}>
        <span><span style={{ background: "#FDE0E0", padding: "1px 4px", borderRadius: 3 }}>nur im DOCX</span> (fehlt im PDF)</span>
        <span><span style={{ background: "#DCEBFB", padding: "1px 4px", borderRadius: 3 }}>nur im PDF</span> (zusätzlich)</span>
      </div>
      <div style={{ wordBreak: "break-word" }}>
        {segmente.map((s, i) => {
          if (s.typ === "info") return <em key={i} className="text-muted"> {s.text} </em>;
          if (s.typ === "nur_docx") return <span key={i} style={{ background: "#FDE0E0", color: "#9B1C1C", padding: "1px 3px", borderRadius: 3, textDecoration: "line-through" }}>{s.text} </span>;
          if (s.typ === "nur_pdf") return <span key={i} style={{ background: "#DCEBFB", color: "#1E429F", padding: "1px 3px", borderRadius: 3 }}>{s.text} </span>;
          return <span key={i} className="text-muted">{s.text} </span>;
        })}
      </div>
    </div>
  );
}

function InhaltProtokoll({ protokoll, ergebnis, regenerations, onRegenerate }) {
  const [diffOffen, setDiffOffen] = useStateF({});
  const meldungen = (protokoll && protokoll.meldungen) || [];
  const paare = (ergebnis && Array.isArray(ergebnis.paare)) ? ergebnis.paare : [];
  const abweichend = paare.filter((p) => p.identisch === false || (Array.isArray(p.abweichungen) && p.abweichungen.length > 0));
  const offen = abweichend.filter((p) => {
    const st = regenerations[p.name];
    return st !== "regenerated" && !p.repariert;
  });

  return (
    <div>
      <h1>Inhaltsvergleich — DOCX vs. PDF</h1>
      <div className="meta">
        <span>Status: {protokoll ? (protokoll.status || "unbekannt") : (ergebnis && ergebnis.status) || "—"}</span>
        <span>Paare: {paare.length} DOCX/PDF</span>
        {protokoll && protokoll.erstellt && <span>Erstellt: {protokoll.erstellt}</span>}
      </div>

      {/* Neukonvertier-Karte — nur bei inhaltlichen Abweichungen. */}
      {abweichend.length > 0 && (
        <div style={{
          marginTop: 6, marginBottom: 14,
          border: "1px solid " + (offen.length === 0 ? "var(--ok)" : "var(--warn)"),
          background: offen.length === 0 ? "var(--ok-bg)" : "var(--warn-bg)",
          borderRadius: 8,
          padding: "12px 14px",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            {offen.length === 0
              ? <Icons.CheckCircle size={14} style={{ color: "var(--ok)" }} />
              : <Icons.AlertTriangle size={14} style={{ color: "var(--warn)" }} />}
            <strong style={{ fontSize: 13, color: "var(--imtb-anthrazit)" }}>
              {offen.length === 0
                ? `${abweichend.length} PDF(s) aus aktueller DOCX neu generiert`
                : `${offen.length} Paar(e) mit inhaltlicher Abweichung`}
            </strong>
          </div>
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
            {abweichend.map((p) => {
              const st = regenerations[p.name];
              const fertig = st === "regenerated" || p.repariert;
              const laeuft = st === "running";
              const fehlerhaft = st === "failed";
              return (
                <li key={p.name} style={{
                  display: "grid", gridTemplateColumns: "1fr auto", gap: 12, alignItems: "start",
                  padding: "8px 10px", background: "var(--surface)", borderRadius: 4, fontSize: 12.5,
                }}>
                  <div>
                    <strong style={{ color: "var(--imtb-anthrazit)" }}>{p.name}</strong>
                    <div className="text-xs text-muted">
                      {Array.isArray(p.abweichungen) && p.abweichungen.length > 0
                        ? p.abweichungen.slice(0, 3).join(" · ")
                        : (p.begruendung || "Inhaltliche Abweichung erkannt.")}
                    </div>
                    {Array.isArray(p.text_diff) && p.text_diff.length > 0 && (
                      <button
                        className="btn btn--ghost btn--sm"
                        style={{ marginTop: 4, marginLeft: -8 }}
                        onClick={() => setDiffOffen((o) => ({ ...o, [p.name]: !o[p.name] }))}
                      >
                        <Icons.Search size={11} /> {diffOffen[p.name] ? "Unterschiede verbergen" : "Unterschiede anzeigen"}
                      </button>
                    )}
                    {fehlerhaft && <div className="text-xs" style={{ color: "var(--err)", marginTop: 2 }}>Neukonvertierung fehlgeschlagen.</div>}
                  </div>
                  {fertig
                    ? <span className="badge badge--ok"><Icons.Check size={10} /> Neu generiert</span>
                    : <button className="btn btn--secondary btn--sm" onClick={() => onRegenerate(p.name)} disabled={laeuft}>
                        <Icons.Refresh size={11} /> {laeuft ? "Konvertiert…" : "PDF neu generieren"}
                      </button>
                  }
                  {diffOffen[p.name] && Array.isArray(p.text_diff) && (
                    <div style={{ gridColumn: "1 / -1", marginTop: 4 }}>
                      <TextDiff segmente={p.text_diff} />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {offen.length > 0 && (
            <div className="text-xs text-muted" style={{ marginTop: 8 }}>
              Das PDF wird aus der aktuellen DOCX neu erzeugt (LibreOffice).
            </div>
          )}
        </div>
      )}

      {paare.length > 0 && (
        <>
          <h2>Übersicht</h2>
          <table>
            <thead><tr><th>Dokumentpaar</th><th>Übereinstimmung</th><th>Status</th></tr></thead>
            <tbody>
              {paare.map((p) => {
                const abw = p.identisch === false || (Array.isArray(p.abweichungen) && p.abweichungen.length > 0);
                const fixed = regenerations[p.name] === "regenerated" || p.repariert;
                return (
                  <tr key={p.name}>
                    <td><strong>{p.name}</strong></td>
                    <td>{p.aehnlichkeit != null ? p.aehnlichkeit + " %" : "—"}</td>
                    <td>
                      {!abw
                        ? <span className="badge badge--ok"><Icons.Check size={10} />übereinstimmung</span>
                        : fixed
                          ? <span className="badge badge--ok"><Icons.Check size={10} />neu generiert</span>
                          : <span className="badge badge--warn"><Icons.AlertTriangle size={10} />abweichung</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}

      {meldungen.length > 0 && (
        <>
          <h2>Befunde</h2>
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
            {meldungen.map((m, i) => (
              <li key={i} style={{ padding: "6px 10px", background: "var(--surface)", borderRadius: 4, fontSize: 12.5, color: "var(--fg-muted)" }}>
                <strong style={{ color: "var(--imtb-anthrazit)" }}>
                  {m.typ === "fehler" ? "Fehler" : m.typ === "warnung" ? "Warnung" : "Hinweis"}:
                </strong>{" "}{m.text}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

Object.assign(window, { FortschrittView });
