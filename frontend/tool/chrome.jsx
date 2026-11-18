// SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
//
// SPDX-License-Identifier: EUPL-1.2

// Chrome — Topbar, Subhead, Pipeline-Visualisierung, Chat-Panel.
const { useState, useEffect, useRef } = React;

function Topbar({ view, setView, jobActive }) {
  // Angemeldeten Nutzer laden (E-Mail/Passwort bzw. Entwicklungsmodus).
  const [benutzer, setBenutzer] = useState(null);
  useEffect(() => {
    let aktiv = true;
    window.Api.get("auth/me")
      .then((u) => { if (aktiv && u) setBenutzer(u); })
      .catch(() => {});
    return () => { aktiv = false; };
  }, []);
  const nutzerName = (benutzer && benutzer.name) || "—";
  const initialen = nutzerName !== "—"
    ? nutzerName.trim().split(/\s+/).map((t) => t[0] || "").slice(0, 2).join("").toUpperCase()
    : "··";

  const navItems = [
    { id: "upload",     label: "Aussonderung prüfen" },
    { id: "historie",   label: "Historie" },
    ...(benutzer && benutzer.is_admin ? [{ id: "admin", label: "Admin" }] : []),
  ];
  return (
    <header className="topbar">
      <div className="topbar__inner">
        <span className="topbar__product">Aussonderungstool</span>

        <div className="topbar__spacer"></div>

        <nav className="topbar__nav">
          {navItems.map((it) => {
            const active = view === it.id || (it.id === "upload" && ["fortschritt","ergebnisse","download"].includes(view));
            return (
              <button
                key={it.id}
                className={active ? "is-active" : ""}
                onClick={() => setView(it.id)}
              >
                {it.label}
              </button>
            );
          })}
          <button onClick={() => setView("about")}>Über</button>
        </nav>

        <div className="topbar__user">
          <span>{nutzerName}</span>
          <div className="avatar">{initialen}</div>
          <a
            href={new URL("api/auth/logout", document.baseURI).href}
            className="topbar__logout"
            title="Abmelden"
          >
            Abmelden
          </a>
        </div>
      </div>
    </header>
  );
}

function Subhead({ view, jobName, jobStarted, agentStates, onJump }) {
  const showPipeline = ["fortschritt","ergebnisse","download"].includes(view);
  const showCrumbs = ["upload","fortschritt","ergebnisse","download"].includes(view);
  if (!showCrumbs) return null;

  const crumbs = [
    { id: "upload",      label: "1. Hochladen"   },
    { id: "fortschritt", label: "2. Prüfung" },
    { id: "ergebnisse",  label: "3. Ergebnisse & Abschluss" },
  ];
  // "download" wird in die Ergebnis-/Abschlussansicht zusammengeführt.
  const activeIdx = crumbs.findIndex((c) => c.id === view || (c.id === "ergebnisse" && view === "download"));

  return (
    <div className="subhead">
      <div className="subhead__inner">
        <div className="subhead__row">
          <nav className="crumbs">
            {crumbs.map((c, i) => (
              <React.Fragment key={c.id}>
                {i > 0 && <span className="crumbs__sep">/</span>}
                <span className={i === activeIdx ? "crumbs__active" : ""}>{c.label}</span>
              </React.Fragment>
            ))}
          </nav>
          {jobName && (
            <>
              <div style={{ flex: 1 }}></div>
              <div className="vorgangs-meta">
                <span className="vorgangs-meta__title">{jobName}</span>
                <div className="vorgangs-meta__sub">
                  <span>Job <strong>#A-2026-0042</strong></span>
                  <span>XDOMEA <strong>2.4</strong></span>
                </div>
              </div>
            </>
          )}
        </div>
        {showPipeline && <Pipeline states={agentStates} onJump={onJump} />}
      </div>
    </div>
  );
}

function Pipeline({ states, onJump }) {
  const { AGENTS } = window.AUSSONDERUNG_DATA;
  return (
    <div className="pipeline">
      {AGENTS.map((a, i) => {
        const state = states[a.id] || "pending";
        const Ico = window.Icons[a.icon];
        return (
          <button
            key={a.id}
            className="pipeline__node"
            data-state={state}
            onClick={() => onJump && onJump(a.id)}
          >
            <div className="pipeline__dot">
              {state === "done" ? <Icons.Check size={14} />
                : state === "skipped" ? <Icons.SkipForward size={12} />
                : state === "failed" ? <Icons.X size={12} />
                : a.nr}
            </div>
            <div className="pipeline__body">
              <div className="pipeline__label">{a.titel}</div>
            </div>
          </button>
        );
      })}
    </div>
  );
}

function ChatPanel({ open, onClose, currentAgent, jobId }) {
  const { AGENTS } = window.AUSSONDERUNG_DATA;
  const [step, setStep] = useState(currentAgent || "bewertung");
  const [messages, setMessages] = useState([
    { from: "bot", text: "Sie können Rückfragen zum aktuellen Prüfschritt stellen — ich beziehe das Protokoll mit ein." },
  ]);
  const [input, setInput] = useState("");
  const [warte, setWarte] = useState(false);

  useEffect(() => {
    if (currentAgent) setStep(currentAgent);
  }, [currentAgent]);

  const submit = async () => {
    if (!input.trim() || warte) return;
    const q = input.trim();
    setMessages((m) => [...m, { from: "user", text: q }]);
    setInput("");

    if (!jobId) {
      setMessages((m) => [...m, {
        from: "bot",
        text: "Es ist noch kein Prüfvorgang aktiv. Bitte laden Sie zuerst eine Datei hoch.",
      }]);
      return;
    }

    setWarte(true);
    try {
      const antwort = await window.Api.post("job/" + jobId + "/chat/" + step, { nachricht: q });
      setMessages((m) => [...m, { from: "bot", text: (antwort && antwort.antwort) || "Keine Antwort erhalten." }]);
    } catch (e) {
      setMessages((m) => [...m, { from: "bot", text: "Fehler bei der Rückfrage: " + (e.message || e) }]);
    } finally {
      setWarte(false);
    }
  };

  if (!open) return null;
  const agentLabel = AGENTS.find((a) => a.id === step)?.titel || "Allgemein";

  return (
    <div className="chatpanel" role="dialog" aria-label="Rückfragen">
      <div className="chatpanel__head">
        <Icons.MessageCircle size={16} />
        <div style={{ flex: 1 }}>
          <div className="chatpanel__head-title">Rückfragen</div>
          <div className="chatpanel__head-sub">Kontext: {agentLabel}</div>
        </div>
        <button className="chatpanel__close" onClick={onClose} aria-label="Schließen">
          <Icons.X size={16} />
        </button>
      </div>
      <div className="chatpanel__step">
        <span className="text-muted text-xs">Prüfschritt:</span>
        <select className="select" style={{ padding: "3px 26px 3px 8px", fontSize: 12, flex: 1 }}
          value={step} onChange={(e) => setStep(e.target.value)}>
          {AGENTS.map((a) => <option key={a.id} value={a.id}>{a.nr}. {a.titel}</option>)}
        </select>
      </div>
      <div className="chatpanel__body">
        {messages.map((m, i) => (
          <div key={i} className={"msg msg--" + m.from}>{m.text}</div>
        ))}
      </div>
      <div className="chatpanel__foot">
        <input
          className="input"
          placeholder={warte ? "Antwort wird erstellt…" : "Frage eingeben…"}
          value={input}
          disabled={warte}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
        />
        <button className="btn btn--primary" onClick={submit} disabled={warte}>
          <Icons.Send size={14} />
        </button>
      </div>
    </div>
  );
}

function ChatFab({ onClick }) {
  return (
    <button className="fab" onClick={onClick} title="Rückfragen">
      <Icons.MessageCircle size={16} />
      Rückfragen
      <span className="fab__dot"></span>
    </button>
  );
}

Object.assign(window, { Topbar, Subhead, Pipeline, ChatPanel, ChatFab });
