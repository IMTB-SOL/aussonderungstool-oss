// SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
//
// SPDX-License-Identifier: EUPL-1.2

// Stammdaten der Tool-SPA: die fünf Prüfschritte (Agenten) der Pipeline.

const AGENTS = [
  {
    id: "bewertung",
    nr: 1,
    titel: "Archivbewertung",
    kurz: "Bewertungsvorschlag pro Vorgang",
    icon: "Brain",
    beschreibung: "Bewertet jeden Vorgang nach Archivgrundsätzen. Klassifikation A / B / V.",
    dauer: "~5 – 10 min",
  },
  {
    id: "formal",
    nr: 2,
    titel: "Formale Prüfung",
    kurz: "XDOMEA-Struktur & XSD-Validierung",
    icon: "FileCheck",
    beschreibung: "Validiert die XDOMEA-Struktur gegen das XSD-Schema (2.1 / 2.2 / 2.4).",
    dauer: "~30 s",
  },
  {
    id: "format",
    nr: 3,
    titel: "Formatprüfung",
    kurz: "MIME-Typen, PDF/A-Konformität",
    icon: "ShieldCheck",
    beschreibung: "Erkennt MIME-Typen und prüft PDFs auf PDF/A-Konformität (ISO 19005).",
    dauer: "~1 – 2 min",
  },
  {
    id: "inhalt",
    nr: 4,
    titel: "Inhaltsvergleich",
    kurz: "DOCX vs. PDF-Versionen",
    icon: "GitCompare",
    beschreibung: "Vergleicht DOCX und PDF inhaltlich, meldet Abweichungen.",
    dauer: "~3 – 5 min",
  },
  {
    id: "erschliessung",
    nr: 5,
    titel: "Erschließung",
    kurz: "Metadaten generieren",
    icon: "Tags",
    beschreibung: "Erzeugt Titel, Regest, Schlagworte, Personen und Zeitraum.",
    dauer: "~2 – 3 min",
  },
];

window.AUSSONDERUNG_DATA = { AGENTS };
