"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// ─── config ──────────────────────────────────────────────────────────────

// 8000 is where `kubectl port-forward -n crew svc/control-plane 8000:8000`
// puts the API, which is the only way to reach it from a browser · nothing in
// the cluster is exposed otherwise. Override both for a different forward.
export const CONTROL_PLANE =
  process.env.NEXT_PUBLIC_CONTROL_PLANE_URL || "http://localhost:8000";
// Origin only · the path is per project: /projects/{id}/stream
export const WS_BASE =
  process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

// ─── role metadata ───────────────────────────────────────────────────────
// Canonical role names (from the control plane) → display metadata.

export const ROLE_META = {
  engineering_manager: { key: "em", label: "EM", glyph: "▲", role: "Engineering Manager" },
  product_manager: { key: "pm", label: "PM", glyph: "●", role: "Product Manager" },
  backend_engineer: { key: "backend", label: "Backend", glyph: "◆", role: "Backend Engineer" },
  frontend_engineer: { key: "frontend", label: "Frontend", glyph: "▼", role: "Frontend Engineer" },
  qa_engineer: { key: "qa", label: "QA", glyph: "✓", role: "QA Engineer" },
  code_reviewer: { key: "reviewer", label: "Reviewer", glyph: "◇", role: "Code Reviewer" },
  founder: { key: "founder", label: "Founder", glyph: "◈", role: "Founder" },
  "control-plane": { key: "system", label: "System", glyph: "▣", role: "Control Plane" },
};

export function metaForRole(role) {
  return (
    ROLE_META[role] || {
      key: role,
      label: role,
      glyph: "○",
      role,
    }
  );
}

// `from` arrives as "engineering_manager/em-3a8f" (or just "founder").
export function parseFrom(from) {
  if (!from) return { role: "unknown", agentId: "" };
  const slash = from.indexOf("/");
  if (slash === -1) return { role: from, agentId: "" };
  return { role: from.slice(0, slash), agentId: from.slice(slash + 1) };
}

// ─── pricing (Sonnet 4.5 default · $/MTok) ───────────────────────────────

const PRICE = { input: 3.0, output: 15.0 };
export function estimateCost(inTok, outTok) {
  return (inTok / 1e6) * PRICE.input + (outTok / 1e6) * PRICE.output;
}

// ─── status derivation ───────────────────────────────────────────────────

function statusForEvent(type) {
  if (type === "reasoning") return "thinking";
  if (type === "tool_call") return "working";
  if (type === "response") return "idle";
  if (type === "intro") return "idle";
  return "working";
}

// ─── the hook ────────────────────────────────────────────────────────────

const EMPTY = {
  agents: {}, // key (full `from`) → agent
  messages: [], // left-panel chat
  activity: [], // activity feed (newest first)
  usage: { input: 0, output: 0 },
  escalations: [],
};

let _mid = 0;
const nextId = () => `m${++_mid}`;

export function useCrew() {
  const [task, setTask] = useState(null); // {task_id, request, scenario, createdAt}
  const [connected, setConnected] = useState(false);
  const [state, setState] = useState(EMPTY);
  const [error, setError] = useState(null);
  const wsRef = useRef(null);

  // The control plane's envelope is {project_id, kind, source, payload, at, to}
  // (contracts/events.py). This console was written against an older
  // {from, type, ...}. Translate once, here, rather than everywhere below.
  const KIND_TO_TYPE = {
    agent_ready: "intro",
    agent_thinking: "reasoning",
    agent_message: "response",
    tool_call: "tool_call",
    founder_message: "founder_message",
    escalation: "escalation",
    usage: "usage",
    error: "error",
    spawn_refused: "warning",
  };

  const apply = useCallback((raw) => {
    const envelope = raw || {};
    const from = envelope.from ?? envelope.source;
    const type = envelope.type ?? KIND_TO_TYPE[envelope.kind] ?? envelope.kind;
    const { payload = {}, at } = envelope;
    const ts = at ? new Date(at * 1000) : new Date();

    setState((prev) => {
      const next = {
        ...prev,
        agents: { ...prev.agents },
        messages: prev.messages,
        activity: prev.activity,
        usage: prev.usage,
        escalations: prev.escalations,
      };

      const { role, agentId } = parseFrom(from);
      const meta = metaForRole(role);
      const isAgent = !!ROLE_META[role] && role !== "founder" && role !== "control-plane";

      // ensure agent record exists for agent-origin events
      if (isAgent) {
        const key = from;
        const existing = next.agents[key];
        next.agents[key] = {
          key,
          role,
          agentId,
          meta,
          status: statusForEvent(type),
          tools: existing?.tools || payload.tools_available || [],
          startedAt: existing?.startedAt || ts,
          lastText: existing?.lastText || "",
          events: existing ? existing.events : [],
        };
      }

      const pushActivity = (summary) => {
        next.activity = [{ id: nextId(), at: ts, from: from || "system", label: meta.label, summary }, ...next.activity].slice(0, 200);
      };

      const pushAgentEvent = (ev) => {
        const key = from;
        const a = next.agents[key];
        if (!a) return;
        a.events = [{ id: nextId(), at: ts, ...ev }, ...a.events].slice(0, 80);
      };

      switch (type) {
        case "intro": {
          pushActivity(`joined · ${(payload.tools_available || []).length} tools`);
          break;
        }
        case "reasoning": {
          const text = payload.text || "";
          if (isAgent) {
            next.agents[from].lastText = text;
            pushAgentEvent({ kind: "reasoning", text });
          }
          pushActivity("reasoning");
          break;
        }
        case "tool_call": {
          const tool = payload.tool;
          const input = payload.input || {};
          const url = input.url;
          pushAgentEvent({ kind: "tool", tool, input, result: payload.result_preview || "" });
          if (isAgent) {
            const a = next.agents[from];
            // Surface the EXACT link being opened, live.
            a.lastText = url ? `opening ${url}` : `using ${tool}`;
            if (url) a.lastUrl = url;
            // Capture DeepAgents' write_todos so we can render the agent's plan.
            if (tool === "write_todos") {
              const todos = input.todos || input.todo_list || input.items;
              if (Array.isArray(todos)) a.todos = todos;
            }
          }
          pushActivity(url ? `${tool} · ${url}` : `${tool}`);
          break;
        }
        case "response": {
          const text = payload.text || "";
          if (isAgent) {
            next.agents[from].lastText = text;
            pushAgentEvent({ kind: "response", text });
          }
          next.messages = [
            ...next.messages,
            { id: nextId(), side: "ai", cls: meta.key, fromLabel: meta.role, text, at: ts },
          ];
          pushActivity("replied");
          break;
        }
        case "founder_message": {
          next.messages = [
            ...next.messages,
            { id: nextId(), side: "user", cls: "founder", fromLabel: "Founder", text: payload.text || "", at: ts },
          ];
          pushActivity("founder message");
          break;
        }
        case "escalation": {
          const esc = { id: nextId(), at: ts, question: payload.question, context: payload.context, options: payload.options || [] };
          next.escalations = [esc, ...next.escalations];
          next.messages = [
            ...next.messages,
            {
              id: nextId(),
              side: "ai",
              cls: "escalation",
              fromLabel: "Escalation → Founder",
              text: payload.question + (payload.context ? `\n\n${payload.context}` : ""),
              options: payload.options || [],
              at: ts,
            },
          ];
          pushActivity("escalation raised");
          break;
        }
        case "usage": {
          next.usage = {
            input: prev.usage.input + (payload.input_tokens || 0),
            output: prev.usage.output + (payload.output_tokens || 0),
          };
          break;
        }
        case "error": {
          const detail = payload.error ? ` · ${payload.error}` : "";
          pushActivity(`error · ${payload.context || ""}${detail}`);
          break;
        }
        case "warning": {
          pushActivity(`warning · ${payload.reason || ""}`);
          break;
        }
        case "component_update":
        case "founder_request":
          break;
        default:
          pushActivity(type || "event");
      }

      return next;
    });
  }, []);

  // open the websocket once we have a task · auto-reconnect if it drops
  useEffect(() => {
    if (!task?.task_id) return;
    let closed = false;
    let ws = null;
    let retry = null;

    const connect = () => {
      ws = new WebSocket(`${WS_BASE}/projects/${task.task_id}/stream`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => {
        try {
          apply(JSON.parse(e.data));
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 1500);
      };
      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* noop */
        }
      };
    };

    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      if (ws) ws.close();
      wsRef.current = null;
    };
  }, [task?.task_id, apply]);

  // ─── actions ───────────────────────────────────────────────────────────

  const startTask = useCallback(async (request, scenario = "build") => {
    setError(null);
    try {
      const res = await fetch(`${CONTROL_PLANE}/projects`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ request }),
      });
      if (!res.ok) throw new Error(`POST /projects → ${res.status}`);
      const project = await res.json();
      const t = { task_id: project.id, ...project };
      const createdAt = new Date();
      setState({
        ...EMPTY,
        messages: [
          { id: nextId(), side: "user", cls: "founder", fromLabel: "Founder", text: request, at: createdAt },
        ],
      });
      setTask({ task_id: t.task_id, request, scenario, createdAt });
      return t;
    } catch (err) {
      setError(err.message || String(err));
      return null;
    }
  }, []);

  const sendMessage = useCallback(
    async (text) => {
      if (!task?.task_id) return;
      setError(null);
      try {
        const res = await fetch(`${CONTROL_PLANE}/projects/${task.task_id}/messages`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ text }),
        });
        if (!res.ok) throw new Error(`POST message → ${res.status}`);
      } catch (err) {
        setError(err.message || String(err));
      }
    },
    [task?.task_id]
  );

  const spawnAgent = useCallback(
    async ({ role, assignment, override_cap }) => {
      if (!task?.task_id) throw new Error("no task running");
      const res = await fetch(`${CONTROL_PLANE}/projects/${task.task_id}/agents`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          role,
          assignment: assignment || "",
          override: !!override_cap,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `spawn → ${res.status}`);
      }
      return res.json();
    },
    [task?.task_id]
  );

  return { task, connected, state, error, startTask, sendMessage, spawnAgent };
}
