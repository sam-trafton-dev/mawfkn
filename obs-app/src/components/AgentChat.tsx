'use client';

/**
 * AgentChat — group chat UI showing agent summaries as chat bubbles.
 *
 * Seeds from historical iteration data, then subscribes to live SSE events
 * to append new messages in real-time as the loop progresses.
 *
 * Display-only. No loop control.
 */

import { useEffect, useRef, useState } from 'react';

// ── Types ──────────────────────────────────────────────────────────────────────

interface Iteration {
  loop_n: number;
  outputs: {
    coder?: { summary?: string; notes?: string; files?: Record<string, string> };
    reviewer?: { critical?: string[]; major?: string[]; minor?: string[]; summary?: string };
    qa?: { pass_rate?: number; passed?: string[]; failed?: string[]; notes?: string };
  };
}

interface ChatMessage {
  id: string;
  agent: 'coder' | 'reviewer' | 'qa' | 'orchestrator';
  loop_n: number;
  text: string;
  meta?: string; // e.g. "3 files · loop 2" or "87% pass rate"
  ts?: string;
}

interface AgentChatProps {
  sessionId: string;
  initialStatus: string;
  iterations: Iteration[];
}

// ── Agent styling ──────────────────────────────────────────────────────────────

const AGENT_META: Record<string, { label: string; avatar: string; bubble: string; align: string }> = {
  coder: {
    label: 'Coder',
    avatar: 'bg-teal-500/20 text-teal-300 border-teal-500/30',
    bubble: 'bg-teal-950/60 border-teal-800/50 text-teal-100',
    align: 'flex-row',
  },
  reviewer: {
    label: 'Reviewer',
    avatar: 'bg-orange-500/20 text-orange-300 border-orange-500/30',
    bubble: 'bg-orange-950/60 border-orange-800/50 text-orange-100',
    align: 'flex-row-reverse',
  },
  qa: {
    label: 'QA',
    avatar: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
    bubble: 'bg-amber-950/60 border-amber-800/50 text-amber-100',
    align: 'flex-row',
  },
  orchestrator: {
    label: 'Orchestrator',
    avatar: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
    bubble: 'bg-purple-950/60 border-purple-800/50 text-purple-100',
    align: 'flex-row-reverse',
  },
};

const AGENT_INITIALS: Record<string, string> = {
  coder: 'C',
  reviewer: 'R',
  qa: 'Q',
  orchestrator: 'O',
};

// ── Helpers ────────────────────────────────────────────────────────────────────

function iterationsToMessages(iterations: Iteration[]): ChatMessage[] {
  const msgs: ChatMessage[] = [];
  for (const iter of iterations) {
    const { loop_n, outputs } = iter;

    if (outputs?.coder) {
      const c = outputs.coder;
      const text = c.summary || c.notes || '(no summary)';
      const fileCount = c.files ? Object.keys(c.files).length : 0;
      msgs.push({
        id: `hist-coder-${loop_n}`,
        agent: 'coder',
        loop_n,
        text,
        meta: fileCount > 0 ? `${fileCount} file${fileCount !== 1 ? 's' : ''} written` : undefined,
      });
    }

    if (outputs?.reviewer) {
      const r = outputs.reviewer;
      const critCount = r.critical?.length ?? 0;
      const majorCount = r.major?.length ?? 0;
      const text = r.summary || '(no summary)';
      const parts = [];
      if (critCount > 0) parts.push(`${critCount} critical`);
      if (majorCount > 0) parts.push(`${majorCount} major`);
      if (critCount === 0 && majorCount === 0) parts.push('clean');
      msgs.push({
        id: `hist-reviewer-${loop_n}`,
        agent: 'reviewer',
        loop_n,
        text,
        meta: parts.join(' · '),
      });
    }

    if (outputs?.qa) {
      const q = outputs.qa;
      const text = q.notes || '(no notes)';
      const passRate = q.pass_rate != null ? `${Math.round(q.pass_rate * 100)}% pass` : undefined;
      const failCount = q.failed?.length ?? 0;
      const parts = [passRate, failCount > 0 ? `${failCount} failing` : undefined].filter(Boolean);
      msgs.push({
        id: `hist-qa-${loop_n}`,
        agent: 'qa',
        loop_n,
        text,
        meta: parts.join(' · ') || undefined,
      });
    }
  }
  return msgs;
}

function eventToMessage(event: {
  agent_role: string;
  event_type: string;
  payload: Record<string, unknown>;
  ts: string;
}): ChatMessage | null {
  const { agent_role, event_type, payload, ts } = event;

  // Only care about result_received events for chat messages
  if (!event_type.endsWith('_result_received')) return null;

  // Derive the agent from event_type (e.g. "coder_result_received" → "coder")
  const agent = event_type.replace('_result_received', '') as ChatMessage['agent'];
  if (!AGENT_META[agent]) return null;

  const loop_n = (payload.loop_n as number) ?? 0;

  let text = '';
  let meta: string | undefined;

  if (agent === 'coder') {
    text = (payload.summary as string) || '(no summary)';
    const fileCount = (payload.file_count as number) ?? 0;
    if (fileCount > 0) meta = `${fileCount} file${fileCount !== 1 ? 's' : ''} written`;
  } else if (agent === 'reviewer') {
    text = (payload.summary as string) || '(no summary)';
    const crit = (payload.critical_count as number) ?? 0;
    const major = (payload.major_count as number) ?? 0;
    const parts = [];
    if (crit > 0) parts.push(`${crit} critical`);
    if (major > 0) parts.push(`${major} major`);
    if (crit === 0 && major === 0) parts.push('clean');
    meta = parts.join(' · ');
  } else if (agent === 'qa') {
    text = (payload.summary as string) || '(no notes)';
    const passRate = payload.pass_rate != null ? `${Math.round((payload.pass_rate as number) * 100)}% pass` : undefined;
    const failed = (payload.failed_count as number) ?? 0;
    const parts = [passRate, failed > 0 ? `${failed} failing` : undefined].filter(Boolean);
    meta = parts.join(' · ') || undefined;
  }

  return {
    id: `live-${agent}-${loop_n}-${ts}`,
    agent,
    loop_n,
    text,
    meta,
    ts,
  };
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function ChatBubble({ msg }: { msg: ChatMessage }) {
  const style = AGENT_META[msg.agent];
  if (!style) return null;

  return (
    <div className={`flex gap-3 items-end ${style.align}`}>
      {/* Avatar */}
      <div
        className={`flex-shrink-0 w-8 h-8 rounded-full border flex items-center justify-center text-xs font-bold ${style.avatar}`}
        title={style.label}
      >
        {AGENT_INITIALS[msg.agent]}
      </div>

      {/* Bubble */}
      <div className="max-w-[75%] space-y-1">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="text-xs font-semibold text-gray-400">{style.label}</span>
          <span className="text-[10px] text-gray-600 font-mono">loop #{msg.loop_n}</span>
          {msg.ts && (
            <span className="text-[10px] text-gray-700">
              {new Date(msg.ts).toLocaleTimeString()}
            </span>
          )}
        </div>
        <div className={`rounded-2xl border px-4 py-2.5 text-sm leading-relaxed ${style.bubble}`}>
          {msg.text}
        </div>
        {msg.meta && (
          <p className="text-[10px] text-gray-500 px-1">{msg.meta}</p>
        )}
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function AgentChat({ sessionId, initialStatus, iterations }: AgentChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>(() =>
    iterationsToMessages(iterations)
  );
  const [connected, setConnected] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const seenIds = useRef(new Set(messages.map((m) => m.id)));

  useEffect(() => {
    const es = new EventSource(`/api/events/${sessionId}`);

    es.onopen = () => setConnected(true);

    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        // Skip history replay — already seeded from iterations prop
        if (event.source === 'history') return;

        const msg = eventToMessage(event);
        if (!msg || seenIds.current.has(msg.id)) return;

        seenIds.current.add(msg.id);
        setMessages((prev) => [...prev, msg]);
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => setConnected(false);

    return () => es.close();
  }, [sessionId]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const isActive = initialStatus === 'running' || initialStatus === 'pending';

  return (
    <div className="flex flex-col rounded-xl border border-gray-800 bg-gray-900 overflow-hidden h-[520px]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
            Agent Group Chat
          </span>
          <span className="text-[10px] text-gray-600">
            {messages.length} message{messages.length !== 1 ? 's' : ''}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-emerald-400' : 'bg-gray-600'}`}
          />
          <span className="text-[10px] text-gray-500">
            {connected ? 'live' : isActive ? 'connecting…' : 'idle'}
          </span>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-5">
        {messages.length === 0 ? (
          <p className="text-xs text-gray-600 text-center pt-8">
            Waiting for agents to check in…
          </p>
        ) : (
          messages.map((msg) => <ChatBubble key={msg.id} msg={msg} />)
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
