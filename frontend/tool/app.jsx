// Main App
const { useState: useStateApp, useEffect: useEffectApp } = React;

// Persistenter App-Zustand (localStorage) — überlebt Reiter-Wechsel und Reload,
// damit ein laufender Prüfvorgang nicht verloren geht.
const APP_STATE_KEY = "aussonderung.appState.v1";
function ladeAppState() {
  try { return JSON.parse(localStorage.getItem(APP_STATE_KEY)) || {}; }
  catch (_) { return {}; }
}
const WORKFLOW_VIEWS = ["fortschritt", "ergebnisse", "download"];
// Einmalig beim Laden der Seite aus localStorage lesen (nicht bei jedem Render).
const _g = ladeAppState();

function App() {
  // Routes: upload | fortschritt | ergebnisse | download | admin | historie | about
  const [view, setView] = useStateApp(_g.view || "upload");
  const [jobName, setJobName] = useStateApp(_g.jobName != null ? _g.jobName : null);
  const [jobStarted, setJobStarted] = useStateApp(_g.jobStarted || null);
  // Echte Backend-Job-ID — nach erfolgreichem Upload gesetzt.
  const [jobId, setJobId] = useStateApp(_g.jobId || null);
  // Gewählter Bewertungsmodus (metadaten | volltext) — fürs Backend-Routing.
  const [bewertungsmodus, setBewertungsmodus] = useStateApp(_g.bewertungsmodus || "metadaten");

  // Agent-Konfiguration
  const [enabledAgents, setEnabledAgents] = useStateApp(_g.enabledAgents || {
    formal: true, format: true, inhalt: true, bewertung: true, erschliessung: true,
  });
  // Agent-Zustände
  const [agentStates, setAgentStates] = useStateApp(_g.agentStates || {});
  const [currentAgent, setCurrentAgent] = useStateApp(_g.currentAgent || null);
  const [focusAgent, setFocusAgent] = useStateApp(_g.focusAgent || null);
  // Zuletzt besuchte Workflow-Ansicht (für Rücksprung aus Admin/Historie).
  const [lastWorkflowView, setLastWorkflowView] = useStateApp(
    WORKFLOW_VIEWS.includes(_g.lastWorkflowView) ? _g.lastWorkflowView : "fortschritt"
  );

  // Chat
  const [chatOpen, setChatOpen] = useStateApp(false);

  // Zustand bei jeder relevanten Änderung persistieren.
  useEffectApp(() => {
    try {
      localStorage.setItem(APP_STATE_KEY, JSON.stringify({
        view, jobName, jobStarted, jobId, bewertungsmodus,
        enabledAgents, agentStates, currentAgent, focusAgent, lastWorkflowView,
      }));
    } catch (_) { /* localStorage nicht verfügbar — kein hartes Problem */ }
  }, [view, jobName, jobStarted, jobId, bewertungsmodus, enabledAgents, agentStates, currentAgent, focusAgent, lastWorkflowView]);

  // Navigation: merkt sich die letzte Workflow-Ansicht; ein Klick auf
  // "Aussonderung prüfen" bei aktivem Job springt zurück in die laufende
  // Prüfung statt einen neuen Upload zu starten.
  const navigate = (ziel) => {
    if (WORKFLOW_VIEWS.includes(ziel)) setLastWorkflowView(ziel);
    if (ziel === "upload" && jobId) {
      setView(lastWorkflowView || "fortschritt");
      return;
    }
    setView(ziel);
  };

  // Der Upload ist bereits in UploadView passiert (inkl. Paket-Browser).
  // Hier wird nur noch der Prüflauf gestartet.
  const startJob = ({ jobId: echteJobId, fileName, enabled, bewertungsmodus: modus }) => {
    setJobId(echteJobId);
    setJobName(fileName || "");
    setJobStarted(new Date().toLocaleTimeString("de-DE").slice(0, 5));
    setEnabledAgents(enabled);
    setBewertungsmodus(modus || "metadaten");

    // Agent-Zustände initialisieren (Design-Vokabular: pending|active|done|skipped|failed)
    const init = {};
    const ordered = ["bewertung","formal","format","inhalt","erschliessung"];
    const firstEnabled = ordered.find((id) => enabled[id]);
    ordered.forEach((id) => {
      if (!enabled[id]) init[id] = "skipped";
      else if (id === firstEnabled) init[id] = "active";
      else init[id] = "pending";
    });
    setAgentStates(init);
    setCurrentAgent(firstEnabled);
    setFocusAgent(firstEnabled);
    setLastWorkflowView("fortschritt");
    setView("fortschritt");
  };

  const onAllDone = () => {
    // Vom Archivar aktiv ausgelöst (Button in der Fortschrittsansicht) —
    // kein automatischer Seitenwechsel.
    setLastWorkflowView("ergebnisse");
    setView("ergebnisse");
  };

  const showChrome = view !== "about";

  // Frische Aussonderung starten: Zustand vollständig zurücksetzen.
  const neueAussonderung = () => {
    setJobName(null);
    setJobId(null);
    setJobStarted(null);
    setAgentStates({});
    setCurrentAgent(null);
    setFocusAgent(null);
    setLastWorkflowView("fortschritt");
    try { localStorage.removeItem(APP_STATE_KEY); } catch (_) { /* egal */ }
    setView("upload");
  };

  return (
    <div className="app">
      <Topbar view={view} setView={navigate} jobActive={!!jobId} />
      <Subhead
        view={view}
        jobName={["fortschritt","ergebnisse","download"].includes(view) ? jobName : null}
        jobStarted={jobStarted}
        agentStates={agentStates}
        onJump={(id) => setFocusAgent(id)}
      />

      <main className="main">
        {view === "upload" && <UploadView onStart={startJob} />}
        {view === "fortschritt" && (
          <FortschrittView
            jobId={jobId}
            bewertungsmodus={bewertungsmodus}
            enabledAgents={enabledAgents}
            agentStates={agentStates}
            setAgentStates={setAgentStates}
            currentAgent={currentAgent}
            setCurrentAgent={setCurrentAgent}
            focusAgent={focusAgent}
            setFocusAgent={setFocusAgent}
            onAllDone={onAllDone}
          />
        )}
        {(view === "ergebnisse" || view === "download") && (
          <ErgebnisseView
            jobId={jobId}
            onNew={neueAussonderung}
            onHistorie={() => setView("historie")}
          />
        )}
        {view === "admin" && <AdminView />}
        {view === "historie" && <HistorieView onOpen={(id) => { setJobId(id); setLastWorkflowView("ergebnisse"); setView("ergebnisse"); }} />}
        {view === "about" && <AboutView onBack={() => navigate("upload")} />}
      </main>

      {/* Chat */}
      {["fortschritt","ergebnisse"].includes(view) && !chatOpen && (
        <ChatFab onClick={() => setChatOpen(true)} />
      )}
      <ChatPanel
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        jobId={jobId}
        currentAgent={focusAgent || currentAgent}
      />

      <footer style={{
        borderTop: "1px solid var(--rule)",
        background: "var(--surface)",
        padding: "16px 28px",
        fontSize: 12,
        color: "var(--fg-muted)",
        display: "flex", justifyContent: "space-between",
      }}>
        <span>Aussonderungstool · Open Source unter EUPL-1.2</span>
        <span>
          <a href="/impressum" target="_blank" rel="noopener" style={{ color: "var(--fg-muted)" }}>Impressum</a>  · 
          <a href="/datenschutz" target="_blank" rel="noopener" style={{ color: "var(--fg-muted)" }}>Datenschutz</a>
        </span>
      </footer>
    </div>
  );
}

function HistorieView({ onOpen }) {
  const [jobs, setJobs] = useStateApp([]);
  const [laedt, setLaedt] = useStateApp(true);
  const [fehler, setFehler] = useStateApp(null);
  const [suche, setSuche] = useStateApp("");

  useEffectApp(() => {
    let aktiv = true;
    setLaedt(true);
    window.Api.get("jobs")
      .then((d) => { if (aktiv) { setJobs((d && d.jobs) || []); setFehler(null); } })
      .catch((e) => { if (aktiv) setFehler(e.message); })
      .finally(() => { if (aktiv) setLaedt(false); });
    return () => { aktiv = false; };
  }, []);

  const formatDatum = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleDateString("de-DE") + " · " + d.toLocaleTimeString("de-DE").slice(0, 5);
  };

  const q = suche.trim().toLowerCase();
  const gefiltert = jobs.filter(
    (j) => !q || (j.dateiname || "").toLowerCase().includes(q) || (j.id || "").toLowerCase().includes(q)
  );

  return (
    <div className="view-enter">
      <h1 className="view-title">Prüfhistorie</h1>
      <p className="view-sub">Alle bisherigen Prüfvorgänge.</p>

      <div className="toolbar">
        <div className="toolbar__search">
          <Icons.Search size={14} />
          <input
            className="input"
            placeholder="Nach Dateiname oder Job-ID suchen…"
            value={suche}
            onChange={(e) => setSuche(e.target.value)}
          />
        </div>
      </div>

      {laedt && <div className="surface" style={{ padding: 20 }}>Lädt…</div>}
      {fehler && (
        <div className="surface" style={{ padding: 20, color: "var(--fg-muted)" }}>
          Historie konnte nicht geladen werden: {fehler}
        </div>
      )}
      {!laedt && !fehler && (
        <div className="surface">
          <table className="data-table">
            <thead>
              <tr><th>Datei</th><th>Datum</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>
              {gefiltert.length === 0 && (
                <tr>
                  <td colSpan={4} className="text-muted" style={{ padding: 16 }}>
                    Keine Prüfvorgänge gefunden.
                  </td>
                </tr>
              )}
              {gefiltert.map((j) => (
                <tr key={j.id} style={{ cursor: "pointer" }} onClick={() => onOpen(j.id)}>
                  <td>
                    <strong>{j.dateiname || "—"}</strong>
                    <div className="text-xs text-muted mono">#{(j.id || "").slice(0, 8)}</div>
                  </td>
                  <td className="text-sm">{formatDatum(j.erstellt_am)}</td>
                  <td>
                    <span className={"badge " + (j.status === "abgeschlossen" ? "badge--ok" : j.status === "fehler" ? "badge--err" : "")}>
                      {j.status || "—"}
                    </span>
                  </td>
                  <td>
                    <Icons.ChevronRight size={14} style={{ color: "var(--fg-muted)" }} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function AboutView({ onBack }) {
  return (
    <div className="view-enter">
      <button className="btn btn--ghost btn--sm" onClick={onBack}>
        <Icons.ChevronLeft size={13} /> Zurück
      </button>
      <h1 className="view-title mt-3">Über das Aussonderungstool</h1>
      <p className="view-sub">
        Automatisiertes Prüf-, Bewertungs- und Erschließungstool für digitale Akten im XDOMEA-Format.
      </p>
    </div>
  );
}

Object.assign(window, { App, HistorieView, AboutView });

// Mount
ReactDOM.createRoot(document.getElementById("root")).render(<App />);
