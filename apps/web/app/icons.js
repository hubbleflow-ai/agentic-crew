"use client";

import { useEffect, useState } from "react";

// ─── role icons · simple, rounded, professional line icons ────────────────
// One per agent role (keyed by ROLE_META.key). Stroke-based, currentColor,
// so they inherit the accent / theme automatically.

const PATHS = {
  // Engineering Manager · clipboard-check (plans + orchestrates)
  em: (
    <>
      <rect x="6" y="4.5" width="12" height="16.5" rx="2.5" />
      <path d="M9 4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V6H9V4.5Z" />
      <path d="m9.3 13.2 2 2 3.4-4" />
    </>
  ),
  // Product Manager · magnifier (research / scope)
  pm: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="m20 20-4.35-4.35" />
    </>
  ),
  // Backend · stacked server / database
  backend: (
    <>
      <rect x="4" y="4.5" width="16" height="6" rx="1.6" />
      <rect x="4" y="13.5" width="16" height="6" rx="1.6" />
      <path d="M7.5 7.5h.01M7.5 16.5h.01" />
    </>
  ),
  // Frontend · browser window
  frontend: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2.2" />
      <path d="M3 9.2h18" />
      <path d="M6.5 7.1h.01M9.3 7.1h.01" />
    </>
  ),
  // QA · shield-check
  qa: (
    <>
      <path d="M12 3 5.5 5.7v4.8c0 4.2 2.9 7.3 6.5 8.5 3.6-1.2 6.5-4.3 6.5-8.5V5.7L12 3Z" />
      <path d="m9.4 11.6 2 2 3-3.4" />
    </>
  ),
  // Reviewer · eye (review the diff)
  reviewer: (
    <>
      <path d="M2.5 12S5.8 5.7 12 5.7 21.5 12 21.5 12 18.2 18.3 12 18.3 2.5 12 2.5 12Z" />
      <circle cx="12" cy="12" r="2.6" />
    </>
  ),
  // Founder · person
  founder: (
    <>
      <circle cx="12" cy="8" r="3.6" />
      <path d="M5.5 20a6.5 6.5 0 0 1 13 0" />
    </>
  ),
  // Team / system · 2x2 grid
  system: (
    <>
      <rect x="4" y="4" width="7" height="7" rx="1.8" />
      <rect x="13" y="4" width="7" height="7" rx="1.8" />
      <rect x="4" y="13" width="7" height="7" rx="1.8" />
      <rect x="13" y="13" width="7" height="7" rx="1.8" />
    </>
  ),
};

export function RoleIcon({ role, size = 18 }) {
  const key = role && PATHS[role] ? role : "system";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[key]}
    </svg>
  );
}

// ─── composer icons · ported verbatim from Session 6 ──────────────────────

export function MicIcon({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M19 11a7 7 0 0 1-14 0M12 18v4M9 22h6" />
    </svg>
  );
}

export function ArrowUpIcon({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 19V5M5 12l7-7 7 7" />
    </svg>
  );
}

// ─── dark / light theme toggle ────────────────────────────────────────────

export function ThemeToggle() {
  const [theme, setTheme] = useState("light");

  useEffect(() => {
    const saved = localStorage.getItem("crew-theme") || "light";
    setTheme(saved);
    document.documentElement.setAttribute("data-theme", saved);
  }, []);

  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("crew-theme", next);
  };

  return (
    <button className="theme-toggle" onClick={toggle} aria-label="Toggle dark mode" title="Toggle theme">
      {theme === "dark" ? (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4.2" />
          <path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4" />
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.6 6.6 0 0 0 10.5 10.5Z" />
        </svg>
      )}
    </button>
  );
}

// ─── blurred binary backdrop (0/1) for the workspace panel ────────────────
// Deterministic (no Math.random) so server + client markup match.

function binaryBlock(rows, cols) {
  const lines = [];
  for (let r = 0; r < rows; r++) {
    let line = "";
    for (let c = 0; c < cols; c++) {
      // cheap deterministic pseudo-noise from indices
      line += ((r * 31 + c * 17 + ((r * c) % 7)) % 3 === 0) ? "1" : "0";
    }
    lines.push(line);
  }
  return lines.join("\n");
}

export function BinaryBackdrop() {
  return (
    <div className="binary-bg" aria-hidden="true">
      <pre>{binaryBlock(40, 60)}</pre>
    </div>
  );
}
