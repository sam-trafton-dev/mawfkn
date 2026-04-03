/**
 * obs-app/src/app/sessions/[id]/page.tsx
 *
 * Session detail page — server component shell with client EventFeed island.
 *
 * Views:
 *   - Session header: status, workshop name, timing
 *   - Iterations table: loop_n, pass_rate bar, coder/reviewer/qa summaries
 *   - Live event feed (EventFeed client component)
 */

import { notFound } from 'next/navigation';
import { AgentChat } from '@/components/AgentChat';
import { ArtifactNameModal } from '@/components/ArtifactNameModal';
import { ContinueSession } from '@/components/ContinueSession';
import { EventFeed } from '@/components/EventFeed';
import { LocalTime } from '@/components/LocalTime';
import { PassRateBar } from '@/components/PassRateBar';
import { STATUS_LABELS } from '@/lib/constants';

const ORCHESTRATOR =
  process.env.ORCHESTRATOR_INTERNAL_URL ?? 'http://orchestrator:8000';

// ── Types ─────────────────────────────────────────────────────────────────────

interface Iteration {
  loop_n: number;
  outputs: {
    coder?: { summary?: string; notes?: string };
    reviewer?: { critical?: string[]; major?: string[]; minor?: string[]; summary?: string };
    qa?: { pass_rate?: number; passed?: string[]; failed?: string[]; notes?: string };
  };
  test_pass_rate: number | null;
  created_at: string;
}

interface SessionDetail {
  id: string;
  workshop_name: string;
  status: string;
  task_spec: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  iterations: Iteration[];
}

// ── Data fetching ──────────────────────────────────────────────────────────────

async function fetchSession(id: string): Promise<SessionDetail | null> {
  try {
    const res = await fetch(`${ORCHESTRATOR}/sessions/${id}`, {
      next: { revalidate: 5 },
    });
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  } catch {
    return null;
  }
}

// ── Sub-components ─────────────────────────────────────────────────────────────

const STATUS_BADGE: Record<string, string> = {
  pending:   'bg-yellow-500/20 text-yellow-300 ring-yellow-500/30',
  running:   'bg-blue-500/20 text-blue-300 ring-blue-500/30',
  completed: 'bg-emerald-500/20 text-emerald-300 ring-emerald-500/30',
  failed:    'bg-red-500/20 text-red-300 ring-red-500/30',
  stuck:     'bg-purple-500/20 text-purple-300 ring-purple-500/30',
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${
        STATUS_BADGE[status] ?? 'bg-gray-500/20 text-gray-300 ring-gray-500/30'
      }`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

function IterationsTable({ iterations }: { iterations: Iteration[] }) {
  if (iterations.length === 0) {
    return (
      <p className="text-sm text-gray-500 px-1">
        No iterations yet — loop has not started.
      </p>
    );
  }

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
              <th className="px-5 py-3 text-left font-medium">Loop</th>
              <th className="px-5 py-3 text-left font-medium">Pass Rate</th>
              <th className="px-5 py-3 text-left font-medium">Coder Summary</th>
              <th className="px-5 py-3 text-left font-medium">Reviewer</th>
              <th className="px-5 py-3 text-left font-medium">QA Notes</th>
              <th className="px-5 py-3 text-left font-medium">Time</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/50">
            {iterations.map((iter) => {
              const reviewer = iter.outputs?.reviewer;
              const critCount = reviewer?.critical?.length ?? 0;
              const majorCount = reviewer?.major?.length ?? 0;
              return (
                <tr key={iter.loop_n} className="hover:bg-gray-800/30 transition-colors">
                  <td className="px-5 py-3 font-mono font-semibold text-gray-300">
                    #{iter.loop_n}
                  </td>
                  <td className="px-5 py-3">
                    <PassRateBar rate={iter.test_pass_rate} />
                  </td>
                  <td className="px-5 py-3 text-gray-400 text-xs max-w-[220px] truncate">
                    {iter.outputs?.coder?.summary ?? '—'}
                  </td>
                  <td className="px-5 py-3 text-xs">
                    {reviewer ? (
                      <span className="flex gap-2">
                        {critCount > 0 && (
                          <span className="text-red-400 font-mono">{critCount} crit</span>
                        )}
                        {majorCount > 0 && (
                          <span className="text-orange-400 font-mono">{majorCount} major</span>
                        )}
                        {critCount === 0 && majorCount === 0 && (
                          <span className="text-emerald-400">clean</span>
                        )}
                      </span>
                    ) : (
                      <span className="text-gray-600">—</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-gray-400 text-xs max-w-[200px] truncate">
                    {iter.outputs?.qa?.notes ?? '—'}
                  </td>
                  <td className="px-5 py-3 text-gray-500 text-xs tabular-nums">
                    <LocalTime iso={iter.created_at} dateAndTime={false} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default async function SessionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const session = await fetchSession(id);
  if (!session) notFound();

  const duration = session.updated_at
    ? Math.round(
        (new Date(session.updated_at).getTime() -
          new Date(session.created_at).getTime()) /
          1000,
      )
    : null;

  return (
    <div className="space-y-8">
      {/* Back nav */}
      <a
        href="/"
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-300 transition-colors"
      >
        ← Dashboard
      </a>

      {/* Session header */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-6 space-y-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-xl font-bold tracking-tight">{session.workshop_name}</h1>
            <p className="mt-1 text-xs font-mono text-gray-500">{session.id}</p>
          </div>
          <StatusBadge status={session.status} />
        </div>

        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-3 text-sm">
          <div>
            <dt className="text-xs text-gray-500">Started</dt>
            <dd className="font-medium"><LocalTime iso={session.created_at} /></dd>
          </div>
          <div>
            <dt className="text-xs text-gray-500">Last updated</dt>
            <dd className="font-medium"><LocalTime iso={session.updated_at} /></dd>
          </div>
          <div>
            <dt className="text-xs text-gray-500">Iterations</dt>
            <dd className="font-medium">{session.iterations.length}</dd>
          </div>
          {duration !== null && (
            <div>
              <dt className="text-xs text-gray-500">Duration</dt>
              <dd className="font-medium">{duration}s</dd>
            </div>
          )}
        </dl>

        {/* Artifact output folder */}
        {(() => {
          const artifactName = (session.task_spec as Record<string, unknown>)?.artifact_name as string | undefined;
          const isDone = ['completed', 'failed', 'stuck'].includes(session.status);
          return (
            <div className="flex items-center gap-3 pt-1">
              <div>
                <span className="text-xs text-gray-500">Output folder: </span>
                {artifactName ? (
                  <code className="text-xs text-gray-300 font-mono">
                    ./artifacts/{artifactName}/src/
                  </code>
                ) : (
                  <code className="text-xs text-gray-600 font-mono">
                    ./artifacts/{session.id.slice(0, 8)}…/src/
                  </code>
                )}
              </div>
              <ArtifactNameModal
                sessionId={session.id}
                show={isDone && !artifactName}
              />
            </div>
          );
        })()}

        {/* Input project badge */}
        {(() => {
          const inputPath = (session.task_spec as Record<string, unknown>)?.input_path as string | undefined;
          const mode = (session.task_spec as Record<string, unknown>)?.improvement_mode as string | undefined;
          if (!inputPath) return null;
          const modeColors: Record<string, string> = {
            refactor: 'bg-blue-500/20 text-blue-300 ring-blue-500/30',
            bugfix:   'bg-red-500/20 text-red-300 ring-red-500/30',
            feature:  'bg-emerald-500/20 text-emerald-300 ring-emerald-500/30',
          };
          return (
            <div className="flex items-center gap-2 pt-1">
              <span className="text-xs text-gray-500">Input: </span>
              <code className="text-xs text-gray-300 font-mono">./input/{inputPath}</code>
              {mode && (
                <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${modeColors[mode] ?? 'bg-gray-500/20 text-gray-300 ring-gray-500/30'}`}>
                  {mode}
                </span>
              )}
            </div>
          );
        })()}

        {/* Task spec */}
        {Object.keys(session.task_spec).length > 0 && (
          <details className="mt-2">
            <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-300 transition-colors">
              Task spec
            </summary>
            <pre className="mt-2 text-xs text-gray-400 bg-gray-950 rounded-lg p-3 overflow-x-auto">
              {JSON.stringify(session.task_spec, null, 2)}
            </pre>
          </details>
        )}
      </div>

      {/* Continue session */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          Continue Session
        </h2>
        <ContinueSession sessionId={session.id} status={session.status} />
      </section>

      {/* Agent group chat + Event log — two-column live view */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          Live View
        </h2>
        <div className="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-4">
          <AgentChat
            sessionId={session.id}
            initialStatus={session.status}
            iterations={session.iterations}
          />
          <EventFeed sessionId={session.id} initialStatus={session.status} />
        </div>
      </section>

      {/* Iteration history table */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          Iterations ({session.iterations.length})
        </h2>
        <IterationsTable iterations={session.iterations} />
      </section>
    </div>
  );
}
