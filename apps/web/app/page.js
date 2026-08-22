"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useCrew, estimateCost, metaForRole, ROLE_META } from "../lib/crew";
import { RoleIcon, ThemeToggle, MicIcon, ArrowUpIcon } from "./icons";

// Render agent/markdown text as formatted markdown (headers, bold, lists,
// code, tables) instead of raw "#"/"**" characters.
function MD({ children }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children || ""}</ReactMarkdown>
    </div>
  );
}

// A long / structured agent message (a plan, scope review, ticket) renders as
// a COLLAPSIBLE artifact card with a title — not a giant raw-markdown bubble.
function isArtifact(text) {
  if (!text) return false;
  // Only a STRUCTURED document (has markdown headings) is an artifact — a long
  // conversational message stays a normal bubble (so a greeting never becomes
  // an artifact title).
  return /^#{1,3}\s/m.test(text);
}
function artifactTitle(text) {
  const h = text && text.match(/^#{1,6}\s+(.+)$/m);
  const raw = h
    ? h[1]
    : (text || "").split("\n").find((l) => l.trim()) || "Document";
  return raw.replace(/[*_`#]/g, "").trim().slice(0, 90);
}
function ArtifactBubble({ title, text }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`artifact ${open ? "open" : ""}`}>
      <button className="artifact-head" onClick={() => setOpen((o) => !o)}>
        <span className="artifact-icon">▤</span>
        <span className="artifact-title">{title}</span>
        <span className="artifact-meta">{open ? "Hide" : "Show"}</span>
        <span className="artifact-chev">{open ? "▾" : "▸"}</span>
      </button>
      {open ? (
        <div className="artifact-body">
          <MD>{text}</MD>
        </div>
      ) : null}
    </div>
  );
}

const SPAWNABLE = [
  "product_manager",
  "backend_engineer",
  "frontend_engineer",
  "qa_engineer",
  "code_reviewer",
];

const fmtTime = (d) =>
  d ? d.toLocaleTimeString("en-GB", { hour12: false }) : "";

function useNow(active) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
}

function elapsedStr(start) {
  if (!start) return "0s";
  const s = Math.max(0, Math.floor((Date.now() - start.getTime()) / 1000));
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}

export default function Console() {
  const { task, connected, state, error, startTask, sendMessage, spawnAgent } = useCrew();
  const [scenario, setScenario] = useState("build");
  const [leftTab, setLeftTab] = useState("chat");
  const [selected, setSelected] = useState("team");
  const [draft, setDraft] = useState("");
  const [spawnOpen, setSpawnOpen] = useState(false);
  useNow(!!task);

  const agents = useMemo(
    () => Object.values(state.agents).sort((a, b) => a.startedAt - b.startedAt),
    [state.agents]
  );

  // keep selection valid; auto-focus first agent once the crew appears
  useEffect(() => {
    if (selected !== "team" && !state.agents[selected]) setSelected("team");
  }, [selected, state.agents]);

  const cost = estimateCost(state.usage.input, state.usage.output);
  const tokens = state.usage.input + state.usage.output;

  const submit = async () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    if (!task) await startTask(text, scenario);
    else await sendMessage(text);
  };

  return (
    <div className="app">
      <Header
        task={task}
        connected={connected}
        scenario={scenario}
        setScenario={setScenario}
        onSpawn={() => setSpawnOpen(true)}
      />

      <main>
        {/* LEFT PANEL */}
        <section className="left-panel">
          <div className="tab-body">
            <ChatTab task={task} messages={state.messages} onPick={sendMessage} />
          </div>

          <InputBar
            value={draft}
            onChange={setDraft}
            onSubmit={submit}
            placeholder={task ? "Reply to the crew…" : "Describe a feature to build…"}
            cta={task ? "Send" : "Start"}
          />
        </section>

        {/* RIGHT PANEL */}
        <section className="right-panel">
          <AgentStrip agents={agents} selected={selected} onSelect={setSelected} />
          {!task ? (
            <RightEmpty />
          ) : selected === "team" ? (
            <TeamView agents={agents} onSelect={setSelected} />
          ) : (
            <AgentView agent={state.agents[selected]} />
          )}
        </section>
      </main>

      {error ? <Toast text={error} /> : null}
      {spawnOpen ? (
        <SpawnModal onClose={() => setSpawnOpen(false)} onSpawn={spawnAgent} />
      ) : null}
    </div>
  );
}

// ─── header ────────────────────────────────────────────────────────────────

function Header({ task, connected, scenario, setScenario, onSpawn }) {
  return (
    <header className="top">
      <div className="brand">
        <div className="logo">H</div>
        Crew
        <span className="project">{task ? task.task_id : "no task"}</span>
      </div>
      <div className="header-right">
        <div className="hdr-scenario">
          {[["build", "🚀 Build"], ["investigate", "🔎 Investigate"], ["refactor", "🔧 Refactor"]].map(([k, l]) => (
            <button
              key={k}
              className={`btn ${scenario === k ? "active" : ""}`}
              disabled={!!task}
              onClick={() => setScenario(k)}
              title={task ? "Scenario locked once a task is running" : ""}
            >
              {l}
            </button>
          ))}
        </div>
        <button className="btn" disabled={!task} onClick={onSpawn}>+ Spawn agent</button>
        <span className="runtime-badge">Docker</span>
        <div className={`live ${connected ? "" : "off"}`}>
          <span className="dot" />
          {connected ? "Live" : "Offline"}
        </div>
        <ThemeToggle />
      </div>
    </header>
  );
}

// ─── KPIs ────────────────────────────────────────────────────────────────

function Kpis({ pods, elapsed, cost, tokens }) {
  const tokStr = tokens > 1000 ? `${(tokens / 1000).toFixed(1)}k` : String(tokens);
  return (
    <div className="kpi-strip">
      <div className="kpi">
        <div className="row"><span className="num warn">{pods}</span></div>
        <div className="lbl">Pods</div>
      </div>
      <div className="kpi">
        <div className="row"><span className="num">{elapsed}</span></div>
        <div className="lbl">Elapsed</div>
      </div>
      <div className="kpi">
        <div className="row"><span className="num">${cost.toFixed(2)}</span></div>
        <div className="lbl">Cost</div>
      </div>
      <div className="kpi">
        <div className="row"><span className="num">{tokStr}</span></div>
        <div className="lbl">Tokens</div>
      </div>
    </div>
  );
}

// ─── chat ──────────────────────────────────────────────────────────────────

function ChatTab({ task, messages, onPick }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [messages.length]);

  return (
    <div className="tab-content active" ref={ref}>
      {!task ? (
        <div className="empty" style={{ height: "auto", paddingTop: 60 }}>
          <div className="glyphs">
            {["em", "pm", "backend", "frontend", "qa", "reviewer"].map((r) => (
              <RoleIcon key={r} role={r} size={22} />
            ))}
          </div>
          <div className="big">Meet your crew</div>
          <div className="hint">
            Crew is a system of autonomous AI agents — Engineering Managers,
            Product Managers, and software engineers (backend &amp; frontend),
            plus QA and review — that collaborate end-to-end to turn your idea
            into shipped work. Describe an objective below and watch them get to it.
          </div>
        </div>
      ) : (
        <div className="chat-day">Task started · {fmtTime(task.createdAt)}</div>
      )}
      {messages.map((m) => {
        const isEscalation = m.options && m.options.length;
        const artifact = m.side !== "user" && !isEscalation && isArtifact(m.text);
        return (
          <div key={m.id} className={`chat-msg ${m.side === "user" ? "user" : `ai ${m.cls}`}`}>
            <div className="from">
              {m.side !== "user" ? <RoleIcon role={m.cls} size={12} /> : null}
              {m.fromLabel}
            </div>
            {artifact ? (
              <ArtifactBubble title={artifactTitle(m.text)} text={m.text} />
            ) : (
              <div className="bubble">
                <MD>{m.text}</MD>
                {isEscalation ? (
                  <div className="escalation-options">
                    {m.options.map((opt) => (
                      <button key={opt} onClick={() => onPick(opt)}>{opt}</button>
                    ))}
                  </div>
                ) : null}
              </div>
            )}
            <div className="when">{fmtTime(m.at)}</div>
          </div>
        );
      })}
    </div>
  );
}

function ActivityTab({ activity }) {
  return (
    <div className="tab-content active">
      <div style={{ fontFamily: "ui-monospace, monospace", fontSize: 11, color: "var(--muted)" }}>
        {activity.length === 0 ? (
          <div style={{ color: "var(--muted-soft)", padding: "8px 0" }}>No activity yet.</div>
        ) : (
          activity.map((a) => (
            <div key={a.id} style={{ marginBottom: 12 }}>
              <div style={{ marginBottom: 4 }}>
                <strong style={{ color: "var(--accent)" }}>
                  {fmtTime(a.at)} · {a.label}
                </strong>
              </div>
              <div style={{ color: "var(--ink)" }}>{a.summary}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function InputBar({ value, onChange, onSubmit, placeholder }) {
  const ref = useRef(null);
  // auto-grow the textarea up to ~5 rows (Session 6 behaviour)
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 132) + "px";
  }, [value]);
  return (
    <div className="input-bar">
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
      >
        <button type="button" className="mic" tabIndex={-1} aria-label="Voice (decorative)"><MicIcon size={17} /></button>
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSubmit();
            }
          }}
          rows={1}
          placeholder={placeholder}
          autoFocus
        />
        <button type="submit" className="send" disabled={!value.trim()} aria-label="Send"><ArrowUpIcon size={17} /></button>
      </form>
      <div className="composer-help">
        Press Enter to send · Shift + Enter for a new line
      </div>
    </div>
  );
}

// ─── agent strip ───────────────────────────────────────────────────────────

function AgentStrip({ agents, selected, onSelect }) {
  return (
    <div className="agent-strip">
      <button
        className={`agent-tab ${selected === "team" ? "active" : ""}`}
        onClick={() => onSelect("team")}
      >
        <span className="glyph"><RoleIcon role="system" size={15} /></span>
        Team
        <span className="status-dot team" />
      </button>
      {agents.map((a) => (
        <button
          key={a.key}
          className={`agent-tab ${selected === a.key ? "active" : ""}`}
          onClick={() => onSelect(a.key)}
        >
          <span className="glyph"><RoleIcon role={a.meta.key} size={15} /></span>
          {a.meta.label}
          <span className={`status-dot ${a.status}`} />
        </button>
      ))}
    </div>
  );
}

function RightEmpty() {
  return (
    <div className="empty">
      <div className="glyphs"><RoleIcon role="system" size={30} /></div>
      <div className="big">Workspace idle</div>
      <div className="hint">
        Start a task from the chat on the left. As agents spin up they'll appear as
        tabs here — click any one to watch its live workspace (code, terminal, browser,
        reasoning).
      </div>
    </div>
  );
}

// ─── team view ─────────────────────────────────────────────────────────────

function TeamView({ agents, onSelect }) {
  return (
    <>
      <div className="agent-header">
        <div className="ident">
          <div className="avatar"><RoleIcon role="system" size={20} /></div>
          <div>
            <div className="role">Team Overview</div>
            <div className="meta">{agents.length} containers running</div>
          </div>
        </div>
        <div className="doing">Click any agent above to see their live workspace.</div>
      </div>
      <div className="workspace-body">
        <div className="team-grid">
          <div>
            <div className="comp">
              <div className="comp-head">
                <span className="title">Roster</span>
                <span className="meta-right">{agents.length} / 14 cap</span>
              </div>
              <div style={{ padding: "12px 14px" }} className="roster-list">
                {agents.length === 0 ? (
                  <div style={{ color: "var(--muted)", fontSize: 12, padding: "8px 0" }}>
                    Waiting for the Engineering Manager to boot…
                  </div>
                ) : (
                  agents.map((a) => (
                    <div key={a.key} className="roster-card" onClick={() => onSelect(a.key)}>
                      <div className="avatar"><RoleIcon role={a.meta.key} size={17} /></div>
                      <div className="info">
                        <div className="role">{a.meta.role}</div>
                        <div className="id">{a.agentId || a.key} · {a.tools.length} tools</div>
                      </div>
                      <div className={`status ${a.status}`}>{a.status}</div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          <div>
            <div className="comp">
              <div className="comp-head">
                <span className="title">Live feed</span>
                <span className="meta-right">latest agent steps</span>
              </div>
              <div style={{ padding: "12px 16px" }}>
                {agents.filter((a) => a.lastText).length === 0 ? (
                  <div style={{ color: "var(--muted)", fontSize: 12 }}>No steps yet.</div>
                ) : (
                  agents
                    .filter((a) => a.lastText)
                    .map((a) => (
                      <div key={a.key} style={{ marginBottom: 12 }}>
                        <div style={{ fontFamily: "ui-monospace, monospace", fontSize: 10.5, color: "var(--accent)", fontWeight: 600, marginBottom: 3 }}>
                          <RoleIcon role={a.meta.key} size={13} /> {a.meta.label}
                        </div>
                        <div style={{ fontSize: 12, color: "var(--ink)", lineHeight: 1.5 }}>
                          {truncate(a.lastText, 220)}
                        </div>
                      </div>
                    ))
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

// ─── single agent view ─────────────────────────────────────────────────────

function AgentView({ agent }) {
  if (!agent) return <RightEmpty />;
  return (
    <>
      <div className="agent-header">
        <div className="ident">
          <div className="avatar"><RoleIcon role={agent.meta.key} size={18} /></div>
          <div>
            <div className="role">{agent.meta.role}</div>
            <div className="meta">
              {agent.agentId || agent.key} · {elapsedStr(agent.startedAt)} uptime · {agent.status}
            </div>
          </div>
        </div>
        <div className="doing">
          <strong>Now:</strong> {agent.lastText ? truncate(agent.lastText, 200) : "waiting for work…"}
        </div>
      </div>
      <div className="workspace-body">
        {agent.lastUrl ? (
          <div className="browse-now">
            <span className="dot" />
            <span className="browse-label">opening</span>
            <a className="browse-url" href={agent.lastUrl} target="_blank" rel="noreferrer">
              {agent.lastUrl}
            </a>
          </div>
        ) : null}
        {agent.todos && agent.todos.length ? <TodosPanel todos={agent.todos} /> : null}
        {agent.events.length === 0 ? (
          <div style={{ color: "var(--muted)", fontSize: 12.5, padding: "8px 2px" }}>
            No workspace activity yet — this agent just booted.
          </div>
        ) : (
          agent.events.map((ev) => <WorkspaceItem key={ev.id} ev={ev} />)
        )}
      </div>
    </>
  );
}

function TodosPanel({ todos }) {
  const norm = (s) => (s || "").toString().toLowerCase();
  const done = todos.filter((t) => norm(t.status).startsWith("complet")).length;
  return (
    <div className="comp">
      <div className="comp-head">
        <span className="title">Plan · Todos</span>
        <span className="meta-right">{done}/{todos.length} done</span>
      </div>
      <div style={{ padding: "10px 14px" }}>
        {todos.map((t, i) => {
          const s = norm(t.status);
          const isDone = s.startsWith("complet");
          const isActive = s.startsWith("in_progress") || s.startsWith("in progress");
          const label = t.content || t.task || t.title || String(t);
          return (
            <div key={i} className={`todo-row ${isDone ? "done" : isActive ? "active" : ""}`}>
              <span className="todo-box">{isDone ? "✓" : isActive ? "◐" : ""}</span>
              <span className="todo-label">{label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// map an agent event → a mockup-style component
function WorkspaceItem({ ev }) {
  if (ev.kind === "reasoning") {
    return (
      <div className="comp">
        <div className="comp-head">
          <span className="title">Reasoning</span>
          <span className="meta-right">{fmtTime(ev.at)}</span>
        </div>
        <div className="comp-reasoning">
          <div className="quote"><MD>{ev.text}</MD></div>
        </div>
      </div>
    );
  }
  if (ev.kind === "response") {
    return (
      <div className="comp">
        <div className="comp-head">
          <span className="title">Message</span>
          <span className="meta-right">{fmtTime(ev.at)}</span>
        </div>
        <div style={{ padding: "12px 16px", fontSize: 13, lineHeight: 1.55 }}>
          <MD>{ev.text}</MD>
        </div>
      </div>
    );
  }
  // tool calls
  return <ToolItem ev={ev} />;
}

function ToolItem({ ev }) {
  const { tool, input = {}, result } = ev;

  if (tool === "sandbox_write_file") {
    return (
      <div className="comp comp-code">
        <div className="comp-head">
          <span className="title">Editor · {input.path || "file"}</span>
          <span className="meta-right">wrote {(input.content || "").split("\n").length} lines</span>
        </div>
        <pre>{input.content || ""}</pre>
      </div>
    );
  }
  if (tool === "sandbox_exec") {
    return (
      <div className="comp comp-term">
        <div className="comp-head">
          <span className="title">Terminal · /workspace</span>
          <span className="meta-right">{fmtTime(ev.at)}</span>
        </div>
        <pre>
          <span className="prompt">$ </span>
          {input.command || ""}
          {result ? `\n${unwrap(result)}` : ""}
        </pre>
      </div>
    );
  }
  if (tool === "navigate" || tool === "screenshot_and_annotate") {
    return (
      <div className="comp comp-browser">
        <div className="comp-head">
          <span className="title">Browser{tool === "screenshot_and_annotate" ? " · evidence" : ""}</span>
          <span className="meta-right">{fmtTime(ev.at)}</span>
        </div>
        <div className="browser-bar">
          <div className="dots"><div className="d" /><div className="d" /><div className="d" /></div>
          <div className="url">{input.url || ""}</div>
        </div>
        <div className="browser-view">
          {input.finding ? (
            <>
              <div style={{ fontFamily: "'Source Serif 4', Georgia, serif", fontSize: 15, marginBottom: 8 }}>
                {input.looking_for || "Research"}
              </div>
              <div style={{ color: "var(--ink)" }}><strong>Finding:</strong> {input.finding}</div>
            </>
          ) : (
            <div style={{ color: "var(--muted)" }}>{truncate(unwrap(result), 400) || "Navigated."}</div>
          )}
        </div>
      </div>
    );
  }
  if (tool === "write_ticket") {
    return (
      <div className="comp comp-doc">
        <div className="comp-head">
          <span className="title">Ticket · {input.title || "TICKET"} (source of truth)</span>
          <span className="meta-right">all agents read this</span>
        </div>
        <div className="doc-body">
          <h2>{input.title || "Task Ticket"}</h2>
          {input.api_contract ? (
            <>
              <h3>API Contract</h3>
              <div className="api-block">{input.api_contract}</div>
            </>
          ) : null}
          {Array.isArray(input.acceptance_criteria) && input.acceptance_criteria.length ? (
            <>
              <h3>Acceptance Criteria</h3>
              <ul>{input.acceptance_criteria.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </>
          ) : null}
          {input.assignments && Object.keys(input.assignments).length ? (
            <>
              <h3>Assignments</h3>
              <ul>
                {Object.entries(input.assignments).map(([r, a]) => (
                  <li key={r}><code>@{metaForRole(r).key}</code> → {a}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      </div>
    );
  }

  // generic action trace for everything else
  return (
    <div className="comp comp-action">
      <div className="comp-head">
        <span className="title">Tool · {tool}</span>
        <span className="meta-right">{fmtTime(ev.at)}</span>
      </div>
      <div className="action-row">
        <div className="when">{fmtTime(ev.at)}</div>
        <div className="what">
          <em>{tool}</em> {summarizeInput(tool, input)}
          {result ? <div style={{ color: "var(--muted)", marginTop: 4 }}>{truncate(unwrap(result), 200)}</div> : null}
        </div>
      </div>
    </div>
  );
}

// ─── footer ────────────────────────────────────────────────────────────────

function Footer({ task, scenario, setScenario, onSpawn }) {
  return (
    <footer className="bot">
      <div className="ctrl-group">
        <span className="group-label">Scenario</span>
        {[["build", "🚀 Build"], ["investigate", "🔎 Investigate"], ["refactor", "🔧 Refactor"]].map(([k, l]) => (
          <button
            key={k}
            className={`btn ${scenario === k ? "active" : ""}`}
            disabled={!!task}
            onClick={() => setScenario(k)}
            title={task ? "Scenario locked once a task is running" : ""}
          >
            {l}
          </button>
        ))}
      </div>
      <div className="ctrl-group">
        <span className="group-label">Actions</span>
        <button className="btn" disabled={!task} onClick={onSpawn}>+ Spawn agent</button>
      </div>
      <div className="ctrl-group">
        <span className="group-label">Runtime</span>
        <button className="btn active">Docker</button>
        <button className="btn" disabled title="K8s runtime is a deploy-time target">K8s</button>
        <span className="time-label">{task ? `started ${fmtTime(task.createdAt)}` : "idle"}</span>
      </div>
    </footer>
  );
}

// ─── spawn modal ─────────────────────────────────────────────────────────

function SpawnModal({ onClose, onSpawn }) {
  const [role, setRole] = useState("backend_engineer");
  const [assignment, setAssignment] = useState("");
  const [override, setOverride] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const go = async () => {
    setBusy(true);
    setErr(null);
    try {
      await onSpawn({ role, assignment: assignment.trim(), override_cap: override });
      onClose();
    } catch (e) {
      setErr(e.message || String(e));
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Spawn an agent</h3>
        <div className="sub">Adds a real container to this task. The control plane enforces spawn caps.</div>

        <label>Role</label>
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {SPAWNABLE.map((r) => (
            <option key={r} value={r}>{metaForRole(r).role}</option>
          ))}
        </select>

        <label>Assignment (optional · delivered on boot)</label>
        <textarea
          value={assignment}
          onChange={(e) => setAssignment(e.target.value)}
          placeholder="e.g. Implement the pagination edge case for >1000 rows…"
        />

        <label style={{ display: "flex", alignItems: "center", gap: 8, textTransform: "none", letterSpacing: 0 }}>
          <input
            type="checkbox"
            checked={override}
            onChange={(e) => setOverride(e.target.checked)}
            style={{ width: "auto" }}
          />
          Override spawn cap (Founder approval)
        </label>

        {err ? <div className="err">{err}</div> : null}

        <div className="modal-actions">
          <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary" onClick={go} disabled={busy}>
            {busy ? "Spawning…" : "Spawn"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Toast({ text }) {
  return (
    <div
      style={{
        position: "fixed", bottom: 64, left: "50%", transform: "translateX(-50%)",
        background: "var(--ink)", color: "white", padding: "9px 16px", borderRadius: 8,
        fontSize: 12, zIndex: 60, maxWidth: 520,
      }}
    >
      {text}
    </div>
  );
}

// ─── helpers ───────────────────────────────────────────────────────────────

function truncate(s, n) {
  if (!s) return "";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

// tool results come through as JSON strings; show something readable
function unwrap(result) {
  if (!result) return "";
  if (typeof result !== "string") return JSON.stringify(result);
  return result;
}

function summarizeInput(tool, input) {
  if (tool === "read_ticket") return `· ${input.ticket_id || ""}`;
  if (tool === "add_ticket_comment") return `· ${truncate(input.body || "", 80)}`;
  if (tool === "github_open_pr") return `· "${input.title || ""}" on ${input.branch || ""}`;
  if (tool === "github_read_pr") return `· PR #${input.pr_number}`;
  if (tool === "github_post_comment") return `· PR #${input.pr_number} ${input.path || ""}`;
  if (tool === "github_approve") return `· PR #${input.pr_number}`;
  if (tool === "github_request_changes") return `· PR #${input.pr_number}`;
  if (tool === "spawn_agent") return `· ${metaForRole(input.role).role}`;
  if (tool === "escalate_to_founder") return `· ${truncate(input.question || "", 90)}`;
  if (tool === "jira_create_issue") return `· ${truncate(input.summary || "", 80)}`;
  if (tool === "sandbox_read_file") return `· ${input.path || ""}`;
  return "";
}
