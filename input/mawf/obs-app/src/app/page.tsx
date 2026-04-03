/**
 * obs-app/src/app/page.tsx
 *
 * Main dashboard page.
 * Server component — fetches session list from the orchestrator at request time.
 * Client-side SSE stream is handled by the <EventFeed> island (future enhancement).
 */

import { CONSTANTS, STATUS_LABELS } from '@/lib/constants';
import { ChatBox } from '@/components/ChatBox';

interface Session {
  id: string;
  workshop_name: string;
  status: string;
  created_at: string;
  updated_at: string;
}

async function fetchSessions(): Promise<Session[]> {
  // Server component — must use the internal Docker network URL, not the browser-facing one
  const orchestratorUrl =
    process.env.ORCHESTRATOR_INTERNAL_URL ?? 'http://orchestrator:8000';

  try {
    const res = await fetch(`${orchestratorUrl}/sessions`, {
      next: { revalidate: 10 },
    });

    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

function StatusBadge({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    pending:   'bg-yellow-500/20 text-yellow-300 ring-yellow-500/30',
    running:   'bg-blue-500/20 text-blue-300 ring-blue-500/30',
    completed: 'bg-emerald-500/20 text-emerald-300 ring-emerald-500/30',
    failed:    'bg-red-500/20 text-red-300 ring-red-500/30',
    stuck:     'bg-purple-500/20 text-purple-300 ring-purple-500/30',
  };

  const classes = colorMap[status] ?? 'bg-gray-500/20 text-gray-300 ring-gray-500/30';

  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${classes}`}>
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

function SystemConstants() {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
        System Constants
      </h2>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
        {Object.entries(CONSTANTS).map(([key, value]) => (
          <div key={key}>
            <dt className="text-xs text-gray-500 font-mono">{key}</dt>
            <dd className="text-sm font-semibold text-gray-200 font-mono">{String(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export default async function DashboardPage() {
  const sessions = await fetchSessions();

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Workshop Dashboard</h1>
        <p className="mt-1 text-sm text-gray-400">
          Tell the orchestrator what to build, or view active and completed sessions below.
        </p>
      </div>

      {/* Chat interface */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          Start a Workshop
        </h2>
        <ChatBox />
      </section>

      {/* System constants panel */}
      <SystemConstants />

      {/* Sessions table */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-300">Sessions</h2>
          <span className="text-xs text-gray-500">{sessions.length} total</span>
        </div>

        {sessions.length === 0 ? (
          <div className="px-5 py-12 text-center text-gray-500 text-sm">
            No sessions found. Start one via{' '}
            <code className="font-mono text-xs bg-gray-800 px-1.5 py-0.5 rounded">
              POST /sessions
            </code>{' '}
            on the orchestrator.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
                  <th className="px-5 py-3 text-left font-medium">Session ID</th>
                  <th className="px-5 py-3 text-left font-medium">Workshop</th>
                  <th className="px-5 py-3 text-left font-medium">Status</th>
                  <th className="px-5 py-3 text-left font-medium">Created</th>
                  <th className="px-5 py-3 text-left font-medium">Updated</th>
                  <th className="px-5 py-3 text-left font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/50">
                {sessions.map((session) => (
                  <tr
                    key={session.id}
                    className="hover:bg-gray-800/40 transition-colors"
                  >
                    <td className="px-5 py-3 font-mono text-xs text-gray-400">
                      {session.id.slice(0, 8)}&hellip;
                    </td>
                    <td className="px-5 py-3 font-medium">{session.workshop_name}</td>
                    <td className="px-5 py-3">
                      <StatusBadge status={session.status} />
                    </td>
                    <td className="px-5 py-3 text-gray-400 text-xs">
                      {new Date(session.created_at).toLocaleString()}
                    </td>
                    <td className="px-5 py-3 text-gray-400 text-xs">
                      {new Date(session.updated_at).toLocaleString()}
                    </td>
                    <td className="px-5 py-3">
                      <a
                        href={`/sessions/${session.id}`}
                        className="text-xs text-brand-500 hover:text-brand-400 transition-colors"
                      >
                        View &rarr;
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
