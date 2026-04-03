'use client';

/**
 * EventFeed — live SSE event log for a session.
 *
 * Design rules (from CLAUDE.md):
 *   - Display-only. Stuck-loop alerts surface here but take NO action.
 *   - The orchestrator is the actor; this component is a passive observer.
 */

import { useEffect, useRef, useState } from 'react';

interface AgentEvent {
  agent_role: string;
  event_type: string;
  payload: Record<string, unknown>;
  ts: string;
  source?: 'history' | 'live';
}

const AGENT_COLORS: Record<string, string> = {
  orchestrator: 'text-purple-400',
  coder:        'text-teal-400',
  reviewer:     'text-orange-400',
  qa:           'text-amber-400',
};

const ERROR_EVENT_TYPES = new Set([
  'stuck_detected', 'hang_timeout', 'loop_crashed',
  'json_parse_error', 'iteration_error', 'coder_error',
  'reviewer_error', 'qa_error', 'agent_dead',
]);

const WARN_EVENT_TYPES = new Set([
  'low_pass_rate_alert', 'agent_unhealthy',
]);

function eventSeverity(event: AgentEvent): 'error' | 'warn' | 'ok' {
  if (ERROR_EVENT_TYPES.has(event.event_type)) return 'error';
  if (WARN_EVENT_TYPES.has(event.event_type)) return 'warn';
  return 'ok';
}

function EventRow({ event, index }: { event: AgentEvent; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const severity = eventSeverity(event);
  const agentColor = AGENT_COLORS[event.agent_role] ?? 'text-gray-400';
  const isHistory = event.source === 'history';

  const rowBg =
    severity === 'error' ? 'bg-red-950/40 hover:bg-red-950/60' :
    severity === 'warn'  ? 'bg-yellow-950/30 hover:bg-yellow-950/50' :
                           'hover:bg-gray-800/20';

  const typeColor =
    severity === 'error' ? 'text-red-400 font-semibold' :
    severity === 'warn'  ? 'text-yellow-400 font-semibold' :
                           'text-gray-300';

  return (
    <div className={`border-b border-gray-800/40 transition-colors ${rowBg} ${isHistory ? 'opacity-70' : ''}`}>
      <div
        className="flex gap-3 px-4 py-2 text-xs cursor-pointer select-none"
        onClick={() => setExpanded((v) => !v)}
      >
        {/* Index */}
        <span className="text-gray-700 tabular-nums w-6 shrink-0">{index + 1}</span>

        {/* Severity dot */}
        <span className="shrink-0 w-2 flex items-center">
          {severity === 'error' && <span className="h-1.5 w-1.5 rounded-full bg-red-500 inline-block" />}
          {severity === 'warn'  && <span className="h-1.5 w-1.5 rounded-full bg-yellow-500 inline-block" />}
        </span>

        {/* Timestamp */}
        <span className="text-gray-600 tabular-nums shrink-0 w-[80px]">
          {new Date(event.ts).toLocaleTimeString()}
        </span>

        {/* Agent */}
        <span className={`font-mono font-medium shrink-0 w-24 ${agentColor}`}>
          {event.agent_role}
        </span>

        {/* Event type */}
        <span className={`font-mono shrink-0 w-44 ${typeColor}`}>
          {event.event_type}
        </span>

        {/* Payload preview */}
        <span className="text-gray-500 truncate font-mono flex-1">
          {summarisePayload(event.payload)}
        </span>

        {/* Expand toggle + history tag */}
        <span className="ml-auto shrink-0 flex items-center gap-2">
          {isHistory && <span className="text-gray-700 italic">history</span>}
          {Object.keys(event.payload).length > 0 && (
            <span className="text-gray-600 text-[10px]">{expanded ? '▲' : '▼'}</span>
          )}
        </span>
      </div>

      {/* Expanded payload */}
      {expanded && Object.keys(event.payload).length > 0 && (
        <div className="px-4 pb-3 ml-[calc(1.5rem+0.5rem+80px+0.75rem+96px+0.75rem)]">
          <pre className="text-xs text-gray-300 bg-gray-950 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-words">
            {JSON.stringify(event.payload, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function summarisePayload(payload: Record<string, unknown>): string {
  if (!payload || Object.keys(payload).length === 0) return '';
  // For error events, surface the error field first
  if (payload.error) return `error=${String(payload.error).slice(0, 80)}`;
  if (payload.hint)  return `hint=${String(payload.hint).slice(0, 80)}`;
  const entries = Object.entries(payload)
    .slice(0, 3)
    .map(([k, v]) => {
      const val = typeof v === 'object' ? JSON.stringify(v) : String(v);
      return `${k}=${val.slice(0, 50)}`;
    });
  return entries.join('  ');
}

type Filter = 'all' | 'errors';

interface EventFeedProps {
  sessionId: string;
  initialStatus: string;
}

export function EventFeed({ sessionId, initialStatus }: EventFeedProps) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [connError, setConnError] = useState<string | null>(null);
  const [stuckAlert, setStuckAlert] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>('all');
  const bottomRef = useRef<HTMLDivElement>(null);

  const isTerminal = ['completed', 'failed', 'stuck', 'terminated'].includes(initialStatus);

  useEffect(() => {
    const es = new EventSource(`/api/events/${sessionId}`);

    es.onopen = () => { setConnected(true); setConnError(null); };

    es.onmessage = (e) => {
      try {
        const event: AgentEvent = JSON.parse(e.data);
        setEvents((prev) => [...prev, event]);
        if (ERROR_EVENT_TYPES.has(event.event_type)) {
          const reason =
            (event.payload?.reason as string) ??
            (event.payload?.error as string) ??
            event.event_type;
          setStuckAlert(reason);
        }
      } catch { /* keepalive or malformed */ }
    };

    es.onerror = () => {
      setConnected(false);
      if (!isTerminal) setConnError('Connection lost — retrying…');
    };

    return () => es.close();
  }, [sessionId, isTerminal]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events.length]);

  const errorCount = events.filter((e) => eventSeverity(e) === 'error').length;
  const displayed = filter === 'errors'
    ? events.filter((e) => eventSeverity(e) !== 'ok')
    : events;

  return (
    <div className="flex flex-col gap-3">
      {/* Status bar + filter */}
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <span className={`inline-block h-1.5 w-1.5 rounded-full ${connected ? 'bg-emerald-500 animate-pulse' : 'bg-gray-600'}`} />
          {connected ? 'Live' : isTerminal ? 'Session ended' : 'Connecting…'}
          <span className="text-gray-700">·</span>
          <span>{events.length} events</span>
          {errorCount > 0 && (
            <span className="text-red-400 font-semibold">{errorCount} error{errorCount > 1 ? 's' : ''}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {connError && <span className="text-xs text-yellow-500">{connError}</span>}
          <div className="flex rounded-lg overflow-hidden border border-gray-700 text-xs">
            {(['all', 'errors'] as Filter[]).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1 transition-colors ${
                  filter === f
                    ? 'bg-gray-700 text-white'
                    : 'bg-gray-900 text-gray-500 hover:text-gray-300'
                }`}
              >
                {f === 'all' ? 'All' : `Errors${errorCount > 0 ? ` (${errorCount})` : ''}`}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Error/stuck alert banner */}
      {stuckAlert && (
        <div className="rounded-lg border border-red-800 bg-red-950/50 px-4 py-3 text-sm text-red-300">
          <span className="font-semibold">Error detected:</span>{' '}{stuckAlert}
          <span className="ml-2 text-xs text-red-500">(click an error row to expand details)</span>
        </div>
      )}

      {/* Event log */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 overflow-hidden">
        <div className="flex gap-3 px-4 py-2 text-xs text-gray-600 uppercase tracking-wider border-b border-gray-800 font-medium">
          <span className="w-6">#</span>
          <span className="w-2" />
          <span className="w-[80px]">Time</span>
          <span className="w-24">Agent</span>
          <span className="w-44">Event</span>
          <span>Payload (click to expand)</span>
        </div>

        <div className="max-h-[520px] overflow-y-auto">
          {displayed.length === 0 ? (
            <div className="px-4 py-10 text-center text-gray-600 text-xs">
              {filter === 'errors' ? 'No errors recorded.' : 'Waiting for events…'}
            </div>
          ) : (
            displayed.map((event, i) => (
              <EventRow key={i} event={event} index={i} />
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  );
}
