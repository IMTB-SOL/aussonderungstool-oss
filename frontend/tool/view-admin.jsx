// SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
//
// SPDX-License-Identifier: EUPL-1.2

// Admin-View — Schwellwerte, Indizienkatalog, Behörden, System-Prompts
// An das echte Backend angebunden: /api/admin/*
const { useState: useStateA, useEffect: useEffectA } = React;

// Hierarchiestufen — Backend-Enum-Werte + Anzeigetexte
const HIERARCHIE_STUFEN = [
  { wert: "ministerium",     label: "Ministerium" },
  { wert: "oberbehoerde",    label: "Oberbehörde" },
  { wert: "mittelbehoerde",  label: "Mittelbehörde" },
  { wert: "unterbehoerde",   label: "Unterbehörde" },
];
const hierarchieLabel = (wert) =>
  (HIERARCHIE_STUFEN.find((h) => h.wert === wert) || {}).label || wert || "–";

function AdminView() {
  const [tab, setTab] = useStateA("schwell");
  const [saved, setSaved] = useStateA("");

  const meldeGespeichert = (was) => {
    setSaved(was + " gespeichert • " + new Date().toLocaleTimeString("de-DE").slice(0, 5));
    setTimeout(() => setSaved(""), 2400);
  };

  const tabs = [
    { id: "schwell",  label: "Schwellwerte",    icon: "Hash" },
    { id: "indizien", label: "Indizienkatalog", icon: "ListChecks" },
    { id: "behoerden", label: "Behördenprofile", icon: "Users" },
    { id: "prompts",  label: "System-Prompts",  icon: "Sparkles" },
    { id: "llm",      label: "LLM-Provider",   icon: "Brain" },
    { id: "nutzer",   label: "Nutzerverwaltung", icon: "Users" },
  ];

  return (
    <div className="view-enter">
      <div className="flex justify-between items-start mb-4" style={{ gap: 16 }}>
        <div>
          <h1 className="view-title">Admin</h1>
          <p className="view-sub" style={{ marginBottom: 0 }}>
            Bewertungsverfahren, Behördenprofile, Prompts.
          </p>
        </div>
        {saved && (
          <span className="badge badge--ok" style={{ alignSelf: "center" }}>
            <Icons.Check size={11} /> {saved}
          </span>
        )}
      </div>

      <div className="split" style={{ gridTemplateColumns: "220px 1fr", gap: 24 }}>
        <aside>
          <nav className="flex-col" style={{ gap: 2 }}>
            {tabs.map((t) => {
              const Ico = window.Icons[t.icon];
              return (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className="btn btn--ghost"
                  style={{
                    justifyContent: "flex-start",
                    background: tab === t.id ? "var(--page-deep)" : "transparent",
                    color: tab === t.id ? "var(--imtb-nachtblau)" : "var(--imtb-anthrazit)",
                    fontWeight: tab === t.id ? 600 : 500,
                  }}
                >
                  <Ico size={14} /> {t.label}
                </button>
              );
            })}
          </nav>
          <div style={{
            marginTop: 24, padding: 14,
            background: "var(--page-deep)",
            borderRadius: 8,
            fontSize: 11.5, color: "var(--fg-muted)", lineHeight: 1.5,
          }}>
            <div style={{ fontWeight: 600, color: "var(--imtb-anthrazit)", marginBottom: 4 }}>
              <Icons.Info size={11} /> Hinweis
            </div>
            Änderungen werden ab dem nächsten Prüflauf wirksam und sind versioniert im Audit-Log.
          </div>
        </aside>

        <div>
          {tab === "schwell" && <SchwellwerteTab onSaved={() => meldeGespeichert("Schwellwerte")} />}
          {tab === "indizien" && <IndizienTab onSaved={() => meldeGespeichert("Indizienkatalog")} />}
          {tab === "behoerden" && <BehoerdenTab onSaved={(w) => meldeGespeichert(w)} />}
          {tab === "prompts" && <PromptsTab onSaved={(n) => meldeGespeichert("Prompt „" + n + "“")} />}
          {tab === "llm" && <LLMProviderTab onSaved={() => meldeGespeichert("LLM-Provider")} />}
          {tab === "nutzer" && <NutzerTab onSaved={() => meldeGespeichert("Nutzer")} />}
        </div>
      </div>
    </div>
  );
}

// --- Schwellwerte ----------------------------------------------------------

function SchwellwerteTab({ onSaved }) {
  const [schwellA, setSchwellA] = useStateA(70);
  const [schwellV, setSchwellV] = useStateA(30);
  const [laden, setLaden] = useStateA(true);
  const [fehler, setFehler] = useStateA(null);
  const [original, setOriginal] = useStateA({ a: 70, v: 30 });

  useEffectA(() => {
    let aktiv = true;
    window.Api.get("admin/schwellwerte")
      .then((sw) => {
        if (!aktiv) return;
        setSchwellA(sw.schwelle_a);
        setSchwellV(sw.schwelle_v);
        setOriginal({ a: sw.schwelle_a, v: sw.schwelle_v });
      })
      .catch((e) => { if (aktiv) setFehler(e.message); })
      .finally(() => { if (aktiv) setLaden(false); });
    return () => { aktiv = false; };
  }, []);

  const speichern = async () => {
    setFehler(null);
    try {
      await window.Api.put("admin/schwellwerte", { schwelle_a: schwellA, schwelle_v: schwellV });
      setOriginal({ a: schwellA, v: schwellV });
      onSaved();
    } catch (e) {
      setFehler(e.message || "Speichern fehlgeschlagen.");
    }
  };

  if (laden) {
    return <div className="surface"><div className="surface__body text-muted">Wird geladen…</div></div>;
  }

  return (
    <div className="surface">
      <div className="surface__head">
        <div className="surface__title"><Icons.Hash size={13} /> Schwellwerte für Klassifikation</div>
      </div>
      <div className="surface__body">
        <p className="text-sm text-muted" style={{ marginTop: 0, marginBottom: 22 }}>
          Der Indizien-Score wird mit folgenden Schwellen in eine Klassifikation überführt:
          Score &ge; <strong>{schwellA}</strong> → A, Score &lt; <strong>{schwellV}</strong> → V, dazwischen → B.
        </p>

        <ScoreRange a={schwellA} v={schwellV} />

        <div className="ersch-grid" style={{ marginTop: 22 }}>
          <div className="field col-6">
            <label className="field__label">A-Schwelle (archivieren)</label>
            <input className="input" type="number" min="0" max="200" value={schwellA} onChange={(e) => setSchwellA(+e.target.value)} />
            <span className="field__hint">Score &ge; Wert → A</span>
          </div>
          <div className="field col-6">
            <label className="field__label">V-Schwelle (vernichten)</label>
            <input className="input" type="number" min="0" max="200" value={schwellV} onChange={(e) => setSchwellV(+e.target.value)} />
            <span className="field__hint">Score &lt; Wert → V</span>
          </div>
        </div>

        {fehler && (
          <div className="badge badge--err" style={{ marginTop: 14 }}>
            <Icons.AlertTriangle size={11} /> {fehler}
          </div>
        )}

        <div className="btn-row mt-5">
          <button className="btn btn--primary" onClick={speichern}>
            <Icons.Save size={13} /> Schwellwerte speichern
          </button>
          <button className="btn btn--secondary" onClick={() => { setSchwellA(original.a); setSchwellV(original.v); }}>
            Zurücksetzen
          </button>
        </div>
      </div>
    </div>
  );
}

// --- Indizienkatalog -------------------------------------------------------

function IndizienTab({ onSaved }) {
  const [indizien, setIndizien] = useStateA([]);
  const [laden, setLaden] = useStateA(true);
  const [fehler, setFehler] = useStateA(null);
  const [dirty, setDirty] = useStateA({}); // {id: true} — geänderte Zeilen
  const [speichern, setSpeichern] = useStateA(false);

  useEffectA(() => {
    let aktiv = true;
    window.Api.get("admin/indizien")
      .then((liste) => { if (aktiv) setIndizien(liste || []); })
      .catch((e) => { if (aktiv) setFehler(e.message); })
      .finally(() => { if (aktiv) setLaden(false); });
    return () => { aktiv = false; };
  }, []);

  const aendere = (i, patch) => {
    setIndizien((arr) => {
      const v = [...arr];
      v[i] = { ...v[i], ...patch };
      return v;
    });
    setDirty((d) => ({ ...d, [indizien[i].id]: true }));
  };

  const alleSpeichern = async () => {
    setSpeichern(true);
    setFehler(null);
    try {
      const zuSpeichern = indizien.filter((ind) => dirty[ind.id]);
      for (const ind of zuSpeichern) {
        await window.Api.put("admin/indizien/" + ind.id, {
          punkte: ind.punkte,
          aktiv: ind.aktiv,
          bezeichnung: ind.bezeichnung,
        });
      }
      setDirty({});
      onSaved();
    } catch (e) {
      setFehler(e.message || "Speichern fehlgeschlagen.");
    } finally {
      setSpeichern(false);
    }
  };

  if (laden) {
    return <div className="surface"><div className="surface__body text-muted">Wird geladen…</div></div>;
  }

  return (
    <div className="surface">
      <div className="surface__head">
        <div className="surface__title"><Icons.ListChecks size={13} /> Indizienkatalog</div>
      </div>
      <div style={{ padding: 0 }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Schlüssel</th>
              <th>Bezeichnung</th>
              <th>Typ</th>
              <th style={{ width: 100 }}>Punkte</th>
              <th style={{ width: 70 }}>Aktiv</th>
            </tr>
          </thead>
          <tbody>
            {indizien.map((ind, i) => {
              const typLabel = ind.typ === "ko_deckel" ? "KO-Deckel"
                             : ind.typ === "ok_boden" ? "OK-Boden"
                             : "Boost";
              const typBadge = ind.typ === "ko_deckel" ? "badge--warn"
                             : ind.typ === "ok_boden" ? "badge--ok"
                             : "badge--info";
              return (
                <tr key={ind.id} className="row-edit" title={ind.beschreibung || ""}>
                  <td className="mono">{ind.schluessel}</td>
                  <td>{ind.bezeichnung}</td>
                  <td><span className={"badge " + typBadge}>{typLabel}</span></td>
                  <td>
                    <input
                      type="number"
                      value={ind.punkte}
                      onChange={(e) => aendere(i, { punkte: +e.target.value })}
                    />
                  </td>
                  <td>
                    <button
                      className={"switch" + (ind.aktiv ? " is-on" : "")}
                      onClick={() => aendere(i, { aktiv: !ind.aktiv })}
                    ></button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {fehler && (
        <div className="surface__body">
          <div className="badge badge--err"><Icons.AlertTriangle size={11} /> {fehler}</div>
        </div>
      )}
      <div className="surface__head" style={{ borderTop: "1px solid var(--rule)", borderBottom: 0 }}>
        <span className="text-sm text-muted">{indizien.filter((x) => x.aktiv).length} von {indizien.length} aktiv</span>
        <button className="btn btn--primary btn--sm" onClick={alleSpeichern} disabled={speichern}>
          <Icons.Save size={13} /> {speichern ? "Speichert…" : "Änderungen speichern"}
        </button>
      </div>
    </div>
  );
}

// --- Behördenprofile -------------------------------------------------------

function BehoerdenTab({ onSaved }) {
  const [behoerden, setBehoerden] = useStateA([]);
  const [laden, setLaden] = useStateA(true);
  const [fehler, setFehler] = useStateA(null);
  const [editId, setEditId] = useStateA(null); // id oder "neu" oder null
  const [form, setForm] = useStateA(null);

  const laden_neu = () => {
    setLaden(true);
    window.Api.get("admin/behoerden")
      .then((liste) => setBehoerden(liste || []))
      .catch((e) => setFehler(e.message))
      .finally(() => setLaden(false));
  };

  useEffectA(() => { laden_neu(); }, []);

  const leeresFormular = () => ({
    name: "", kuerzel: "", hierarchie_stufe: "unterbehoerde",
    besondere_oe: "", aktenzeichen_praefix: "",
  });

  const startNeu = () => { setEditId("neu"); setForm(leeresFormular()); };
  const startEdit = (b) => {
    setEditId(b.id);
    setForm({
      name: b.name || "",
      kuerzel: b.kuerzel || "",
      hierarchie_stufe: b.hierarchie_stufe || "unterbehoerde",
      besondere_oe: (b.besondere_oe || []).join(", "),
      aktenzeichen_praefix: b.aktenzeichen_praefix || "",
    });
  };

  const speichern = async () => {
    setFehler(null);
    const body = {
      name: form.name,
      kuerzel: form.kuerzel || null,
      hierarchie_stufe: form.hierarchie_stufe,
      besondere_oe: form.besondere_oe.split(",").map((s) => s.trim()).filter(Boolean),
      aktenzeichen_praefix: form.aktenzeichen_praefix || null,
    };
    try {
      if (editId === "neu") {
        await window.Api.post("admin/behoerden", body);
        onSaved("Behörde");
      } else {
        await window.Api.put("admin/behoerden/" + editId, body);
        onSaved("Behörde");
      }
      setEditId(null);
      setForm(null);
      laden_neu();
    } catch (e) {
      setFehler(e.message || "Speichern fehlgeschlagen.");
    }
  };

  const loeschen = async (b) => {
    if (!window.confirm("Behörde „" + b.name + "“ wirklich löschen?")) return;
    setFehler(null);
    try {
      await window.Api.del("admin/behoerden/" + b.id);
      onSaved("Behörde gelöscht —");
      laden_neu();
    } catch (e) {
      setFehler(e.message || "Löschen fehlgeschlagen.");
    }
  };

  if (laden) {
    return <div className="surface"><div className="surface__body text-muted">Wird geladen…</div></div>;
  }

  return (
    <div className="surface">
      <div className="surface__head">
        <div className="surface__title"><Icons.Users size={13} /> Behördenprofile</div>
        <button className="btn btn--primary btn--sm" onClick={startNeu}>
          <Icons.Plus size={12} /> Neue Behörde
        </button>
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Kürzel</th>
            <th>Hierarchie</th>
            <th>AZ-Präfix</th>
            <th style={{ width: 100 }}>Aktionen</th>
          </tr>
        </thead>
        <tbody>
          {behoerden.map((b) => (
            <tr key={b.id}>
              <td><strong>{b.name}</strong></td>
              <td className="mono">{b.kuerzel || "–"}</td>
              <td><span className="badge badge--info">{hierarchieLabel(b.hierarchie_stufe)}</span></td>
              <td className="mono">{b.aktenzeichen_praefix || "–"}</td>
              <td>
                <button className="btn btn--ghost btn--icon" onClick={() => startEdit(b)}><Icons.Edit size={13} /></button>
                <button className="btn btn--ghost btn--icon" onClick={() => loeschen(b)}><Icons.Trash size={13} /></button>
              </td>
            </tr>
          ))}
          {behoerden.length === 0 && (
            <tr><td colSpan={5} className="text-muted">Noch keine Behördenprofile angelegt.</td></tr>
          )}
        </tbody>
      </table>

      {fehler && (
        <div className="surface__body">
          <div className="badge badge--err"><Icons.AlertTriangle size={11} /> {fehler}</div>
        </div>
      )}

      {/* Bearbeitungs-Drawer */}
      {editId !== null && form && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 40,
          background: "rgba(33, 36, 52, 0.32)",
          display: "flex", justifyContent: "flex-end",
        }} onClick={() => { setEditId(null); setForm(null); }}>
          <div onClick={(e) => e.stopPropagation()} style={{
            width: 480, maxWidth: "100%", height: "100%",
            background: "var(--surface)",
            boxShadow: "-12px 0 32px rgba(33,36,52,.16)",
            display: "flex", flexDirection: "column",
          }}>
            <div style={{
              padding: "18px 22px",
              borderBottom: "1px solid var(--rule)",
              display: "flex", justifyContent: "space-between", alignItems: "center",
            }}>
              <h3 style={{
                margin: 0, fontFamily: "var(--font-display)",
                fontSize: 18, fontWeight: 700, color: "var(--fg-strong)",
              }}>{editId === "neu" ? "Neue Behörde" : "Behörde bearbeiten"}</h3>
              <button className="btn btn--ghost btn--icon" onClick={() => { setEditId(null); setForm(null); }}>
                <Icons.X size={16} />
              </button>
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: "22px" }}>
              <div className="ersch-grid">
                <div className="field col-12">
                  <label className="field__label">Name</label>
                  <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                </div>
                <div className="field col-6">
                  <label className="field__label">Kürzel</label>
                  <input className="input" value={form.kuerzel} onChange={(e) => setForm({ ...form, kuerzel: e.target.value })} />
                </div>
                <div className="field col-6">
                  <label className="field__label">AZ-Präfix</label>
                  <input className="input mono" value={form.aktenzeichen_praefix} onChange={(e) => setForm({ ...form, aktenzeichen_praefix: e.target.value })} />
                </div>
                <div className="field col-12">
                  <label className="field__label">Hierarchiestufe</label>
                  <select className="select" value={form.hierarchie_stufe} onChange={(e) => setForm({ ...form, hierarchie_stufe: e.target.value })}>
                    {HIERARCHIE_STUFEN.map((h) => (
                      <option key={h.wert} value={h.wert}>{h.label}</option>
                    ))}
                  </select>
                </div>
                <div className="field col-12">
                  <label className="field__label">Besondere Organisationseinheiten</label>
                  <input className="input" value={form.besondere_oe} onChange={(e) => setForm({ ...form, besondere_oe: e.target.value })} />
                  <span className="field__hint">Komma-getrennt.</span>
                </div>
              </div>
            </div>
            <div style={{
              padding: "14px 22px",
              borderTop: "1px solid var(--rule)",
              background: "#FBFAF7",
              display: "flex", justifyContent: "flex-end",
            }}>
              <div className="btn-row">
                <button className="btn btn--secondary" onClick={() => { setEditId(null); setForm(null); }}>Abbrechen</button>
                <button className="btn btn--primary" onClick={speichern} disabled={!form.name.trim()}>
                  <Icons.Save size={13} /> Speichern
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// --- System-Prompts --------------------------------------------------------

function PromptsTab({ onSaved }) {
  const [prompts, setPrompts] = useStateA([]);
  const [laden, setLaden] = useStateA(true);
  const [fehler, setFehler] = useStateA(null);
  const [speichernKey, setSpeichernKey] = useStateA(null);

  useEffectA(() => {
    let aktiv = true;
    window.Api.get("admin/prompts")
      .then((liste) => { if (aktiv) setPrompts(liste || []); })
      .catch((e) => { if (aktiv) setFehler(e.message); })
      .finally(() => { if (aktiv) setLaden(false); });
    return () => { aktiv = false; };
  }, []);

  const speichern = async (p) => {
    setSpeichernKey(p.schluessel);
    setFehler(null);
    try {
      await window.Api.put("admin/prompts/" + p.schluessel, { inhalt: p.inhalt });
      onSaved(p.anzeige_name);
    } catch (e) {
      setFehler(e.message || "Speichern fehlgeschlagen.");
    } finally {
      setSpeichernKey(null);
    }
  };

  if (laden) {
    return <div className="surface"><div className="surface__body text-muted">Wird geladen…</div></div>;
  }

  return (
    <div className="flex-col gap-4">
      {fehler && (
        <div className="badge badge--err"><Icons.AlertTriangle size={11} /> {fehler}</div>
      )}
      {prompts.map((p, i) => (
        <div className="surface" key={p.schluessel}>
          <div className="surface__head">
            <div className="surface__title">
              <Icons.Sparkles size={13} style={{ color: "var(--accent)" }} /> {p.anzeige_name}
            </div>
            <span className="badge badge--info mono">{p.schluessel}</span>
          </div>
          <div className="surface__body">
            <textarea
              className="textarea mono"
              rows="10"
              style={{ fontSize: 12.5, lineHeight: 1.55 }}
              value={p.inhalt}
              onChange={(e) => {
                const v = [...prompts];
                v[i] = { ...p, inhalt: e.target.value };
                setPrompts(v);
              }}
            ></textarea>
            <div className="flex justify-between mt-3 items-center">
              <span className="text-xs text-muted">
                {(p.inhalt || "").length} Zeichen · ~{Math.round((p.inhalt || "").split(/\s+/).filter(Boolean).length * 1.3)} Tokens
              </span>
              <div className="btn-row">
                <button className="btn btn--primary btn--sm" onClick={() => speichern(p)} disabled={speichernKey === p.schluessel}>
                  <Icons.Save size={12} /> {speichernKey === p.schluessel ? "Speichert…" : "Speichern"}
                </button>
              </div>
            </div>
          </div>
        </div>
      ))}
      {prompts.length === 0 && (
        <div className="surface"><div className="surface__body text-muted">Keine Prompts verfügbar.</div></div>
      )}
    </div>
  );
}

function ScoreRange({ a, v }) {
  const max = 200;
  const aPct = Math.max(0, Math.min(100, (a / max) * 100));
  const vPct = Math.max(0, Math.min(100, (v / max) * 100));
  return (
    <div>
      <div style={{
        position: "relative",
        height: 28,
        background: "linear-gradient(to right, var(--klass-v-bg) 0%, var(--klass-v-bg) " + vPct + "%, var(--klass-b-bg) " + vPct + "%, var(--klass-b-bg) " + aPct + "%, var(--klass-a-bg) " + aPct + "%, var(--klass-a-bg) 100%)",
        borderRadius: 6,
        border: "1px solid var(--rule)",
      }}>
        <Marker pct={vPct} label={"V " + v} color="var(--klass-v)" />
        <Marker pct={aPct} label={"A " + a} color="var(--klass-a)" />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6, fontSize: 11, color: "var(--fg-muted)" }}>
        <span>0</span><span>50</span><span>100</span><span>150</span><span>200</span>
      </div>
      <div style={{ display: "flex", gap: 10, marginTop: 10, fontSize: 12, color: "var(--imtb-anthrazit)" }}>
        <span><span className="klass klass--v" style={{ fontSize: 11, padding: "1px 6px" }}>V</span> &lt; {v}</span>
        <span><span className="klass klass--b" style={{ fontSize: 11, padding: "1px 6px" }}>B</span> {v} – {a-1}</span>
        <span><span className="klass klass--a" style={{ fontSize: 11, padding: "1px 6px" }}>A</span> &ge; {a}</span>
      </div>
    </div>
  );
}

function Marker({ pct, label, color }) {
  return (
    <div style={{ position: "absolute", left: pct + "%", top: -3, transform: "translateX(-50%)", display: "flex", flexDirection: "column", alignItems: "center" }}>
      <div style={{ fontSize: 10, fontWeight: 700, color, marginBottom: 2, background: "var(--surface)", padding: "0 4px", borderRadius: 3, whiteSpace: "nowrap", border: "1px solid " + color, lineHeight: 1.5 }}>
        {label}
      </div>
      <div style={{ width: 2, height: 36, background: color }}></div>
    </div>
  );
}

// --- LLM-Provider ----------------------------------------------------------

function LLMProviderTab({ onSaved }) {
  const [daten, setDaten] = useStateA(null);
  const [auswahl, setAuswahl] = useStateA("ollama");
  const [modelle, setModelle] = useStateA([]);
  const [modellAuswahl, setModellAuswahl] = useStateA("");
  const [modelleFehler, setModelleFehler] = useStateA(null);
  const [laden, setLaden] = useStateA(true);
  const [fehler, setFehler] = useStateA(null);

  useEffectA(() => {
    let aktiv = true;
    window.Api.get("admin/llm-provider")
      .then((d) => {
        if (!aktiv) return;
        setDaten(d);
        setAuswahl(d.aktiver_provider);
        setModellAuswahl(d.scaleway_modell || "");
        if (d.scaleway_verfuegbar) {
          window.Api.get("admin/scaleway-modelle")
            .then((m) => { if (aktiv) setModelle(m.modelle || []); })
            .catch((e) => {
              if (aktiv) setModelleFehler(e.message || "Modellliste nicht abrufbar.");
            });
        }
      })
      .catch((e) => { if (aktiv) setFehler(e.message); })
      .finally(() => { if (aktiv) setLaden(false); });
    return () => { aktiv = false; };
  }, []);

  const speichern = async () => {
    setFehler(null);
    try {
      const body = { aktiver_provider: auswahl };
      if (auswahl === "scaleway") body.scaleway_modell = modellAuswahl || "";
      await window.Api.put("admin/llm-provider", body);
      setDaten((d) => ({
        ...d,
        aktiver_provider: auswahl,
        scaleway_modell: modellAuswahl || null,
      }));
      onSaved();
    } catch (e) {
      setFehler(e.message || "Speichern fehlgeschlagen.");
    }
  };

  if (laden) {
    return <div className="surface"><div className="surface__body text-muted">Wird geladen…</div></div>;
  }
  if (!daten) {
    return (
      <div className="surface">
        <div className="surface__body">
          <div className="badge badge--err"><Icons.AlertTriangle size={11} /> {fehler || "Laden fehlgeschlagen."}</div>
        </div>
      </div>
    );
  }

  // Speichern aktiv, sobald sich Provider ODER (bei Scaleway) das Modell geändert hat.
  const geaendert =
    auswahl !== daten.aktiver_provider ||
    (auswahl === "scaleway" && (modellAuswahl || "") !== (daten.scaleway_modell || ""));

  return (
    <div className="surface">
      <div className="surface__head">
        <div className="surface__title"><Icons.Brain size={13} /> LLM-Provider</div>
      </div>
      <div className="surface__body">
        <p className="text-sm text-muted" style={{ marginTop: 0, marginBottom: 18 }}>
          Bestimmt, welcher Sprachmodell-Dienst für die KI-Agenten genutzt wird.
        </p>

        <div className="flex-col" style={{ gap: 10 }}>
          {(daten.provider || []).map((p) => (
            <label
              key={p.id}
              className="field"
              style={{
                flexDirection: "row", alignItems: "center", gap: 10,
                padding: 12, border: "1px solid var(--rule)", borderRadius: 8,
                opacity: p.verfuegbar ? 1 : 0.55,
                cursor: p.verfuegbar ? "pointer" : "not-allowed",
              }}
            >
              <input
                type="radio"
                name="llm-provider"
                value={p.id}
                checked={auswahl === p.id}
                disabled={!p.verfuegbar}
                onChange={() => setAuswahl(p.id)}
              />
              <span style={{ fontWeight: 600 }}>{p.name}</span>
              {!p.verfuegbar && (
                <span className="badge badge--warn" style={{ marginLeft: "auto" }}>
                  nicht freigeschaltet
                </span>
              )}
            </label>
          ))}
        </div>

        {auswahl === "scaleway" && (
          <div className="field" style={{ marginTop: 14 }}>
            <label className="field__label">Scaleway-Modell</label>
            {modelleFehler ? (
              <div className="badge badge--err">
                <Icons.AlertTriangle size={11} /> {modelleFehler}
                {daten.scaleway_modell && <span> — aktuell aktiv: {daten.scaleway_modell}</span>}
              </div>
            ) : (
              <select
                className="select"
                value={modellAuswahl}
                onChange={(e) => setModellAuswahl(e.target.value)}
              >
                <option value="">— Modell wählen —</option>
                {modelle.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            )}
          </div>
        )}

        {!daten.scaleway_verfuegbar && (
          <div className="badge badge--info" style={{ marginTop: 14 }}>
            <Icons.Info size={11} /> Scaleway ist erst nach Freigabe (SCALEWAY_ENABLED) und
            datenschutzrechtlicher Prüfung wählbar.
          </div>
        )}

        {fehler && (
          <div className="badge badge--err" style={{ marginTop: 14 }}>
            <Icons.AlertTriangle size={11} /> {fehler}
          </div>
        )}

        <div className="btn-row mt-5">
          <button
            className="btn btn--primary"
            onClick={speichern}
            disabled={!geaendert}
          >
            <Icons.Save size={13} /> Provider speichern
          </button>
        </div>
      </div>
    </div>
  );
}

// --- Nutzerverwaltung ------------------------------------------------------

function NutzerTab({ onSaved }) {
  const [nutzer, setNutzer] = useStateA([]);
  const [laden, setLaden] = useStateA(true);
  const [fehler, setFehler] = useStateA(null);

  const laden_ = () => {
    return window.Api.get("admin/users")
      .then((d) => setNutzer(d.users || []))
      .catch((e) => setFehler(e.message));
  };

  useEffectA(() => {
    let aktiv = true;
    window.Api.get("admin/users")
      .then((d) => { if (aktiv) setNutzer(d.users || []); })
      .catch((e) => { if (aktiv) setFehler(e.message); })
      .finally(() => { if (aktiv) setLaden(false); });
    return () => { aktiv = false; };
  }, []);

  const freischalten = async (id) => {
    try { await window.Api.put(`admin/users/${id}/freischalten`); await laden_(); onSaved(); }
    catch (e) { setFehler(e.message); }
  };
  const rolle = async (id, istAdmin) => {
    try { await window.Api.put(`admin/users/${id}/rolle`, { is_admin: istAdmin }); await laden_(); onSaved(); }
    catch (e) { setFehler(e.message); }
  };
  const loeschen = async (id) => {
    if (!window.confirm("Diesen Nutzer wirklich löschen?")) return;
    try { await window.Api.del(`admin/users/${id}`); await laden_(); onSaved(); }
    catch (e) { setFehler(e.message); }
  };

  if (laden) {
    return <div className="surface"><div className="surface__body text-muted">Wird geladen…</div></div>;
  }

  return (
    <div className="surface">
      <div className="surface__head">
        <div className="surface__title"><Icons.Users size={13} /> Nutzerverwaltung</div>
      </div>
      <div className="surface__body">
        {fehler && <p className="text-sm" style={{ color: "var(--accent-strong)" }}>{fehler}</p>}
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--fg-muted)" }}>
              <th style={{ padding: "6px 8px" }}>E-Mail</th>
              <th style={{ padding: "6px 8px" }}>Status</th>
              <th style={{ padding: "6px 8px" }}>Rolle</th>
              <th style={{ padding: "6px 8px" }}>Aktionen</th>
            </tr>
          </thead>
          <tbody>
            {nutzer.map((u) => (
              <tr key={u.id} style={{ borderTop: "1px solid var(--divider)" }}>
                <td style={{ padding: "6px 8px" }}>{u.email}</td>
                <td style={{ padding: "6px 8px" }}>{u.approved ? "freigeschaltet" : "wartet"}</td>
                <td style={{ padding: "6px 8px" }}>{u.is_admin ? "Admin" : "Nutzer"}</td>
                <td style={{ padding: "6px 8px", display: "flex", gap: 6 }}>
                  {!u.approved && (
                    <button className="btn btn--ghost" onClick={() => freischalten(u.id)}>Freischalten</button>
                  )}
                  <button className="btn btn--ghost" onClick={() => rolle(u.id, !u.is_admin)}>
                    {u.is_admin ? "Admin entziehen" : "Zu Admin"}
                  </button>
                  <button className="btn btn--ghost" onClick={() => loeschen(u.id)}>Löschen</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

Object.assign(window, { AdminView });
