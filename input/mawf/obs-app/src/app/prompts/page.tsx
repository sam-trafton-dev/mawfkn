/**
 * obs-app/src/app/prompts/page.tsx
 *
 * Lists all agent system prompts with last-updated time and a link to the editor.
 * Server component — fetches from orchestrator at request time.
 */

const ORCHESTRATOR = process.env.ORCHESTRATOR_INTERNAL_URL ?? 'http://orchestrator:8000';

interface PromptRow {
  agent_role: string;
  content: string;
  updated_at: string;
}

const AGENT_LABELS: Record<string, string> = {
  coder:           'Coder',
  reviewer:        'Reviewer',
  qa:              'QA',
  'sme-data':      'SME · Data',
  'sme-api':       'SME · API',
  'sme-ux':        'SME · UX',
  'sme-business':  'SME · Business',
  'sme-networking':'SME · Networking',
  'sme-devops':    'SME · DevOps',
};

async function fetchPrompts(): Promise<PromptRow[]> {
  try {
    const res = await fetch(`${ORCHESTRATOR}/prompts`, { next: { revalidate: 0 } });
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

export default async function PromptsPage() {
  const prompts = await fetchPrompts();

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Agent System Prompts</h1>
        <p className="mt-1 text-sm text-gray-400">
          View and edit each agent&apos;s system prompt. Changes take effect on the next agent request.
        </p>
      </div>

      {prompts.length === 0 ? (
        <div className="rounded-xl border border-gray-800 bg-gray-900 px-5 py-12 text-center text-sm text-gray-500">
          No prompts found. Agents seed their defaults on startup — make sure all services are running.
        </div>
      ) : (
        <div className="rounded-xl border border-gray-800 bg-gray-900 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-xs text-gray-500 uppercase tracking-wider">
                <th className="px-5 py-3 text-left font-medium">Agent</th>
                <th className="px-5 py-3 text-left font-medium">Prompt preview</th>
                <th className="px-5 py-3 text-left font-medium">Last updated</th>
                <th className="px-5 py-3 text-left font-medium"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/50">
              {prompts.map((row) => (
                <tr key={row.agent_role} className="hover:bg-gray-800/40 transition-colors">
                  <td className="px-5 py-3 font-medium text-gray-200">
                    {AGENT_LABELS[row.agent_role] ?? row.agent_role}
                    <span className="ml-2 font-mono text-xs text-gray-600">{row.agent_role}</span>
                  </td>
                  <td className="px-5 py-3 text-gray-500 text-xs max-w-[380px] truncate font-mono">
                    {row.content.slice(0, 120).replace(/\n/g, ' ')}…
                  </td>
                  <td className="px-5 py-3 text-gray-500 text-xs tabular-nums">
                    {new Date(row.updated_at).toLocaleString()}
                  </td>
                  <td className="px-5 py-3">
                    <a
                      href={`/prompts/${row.agent_role}`}
                      className="text-xs text-brand-500 hover:text-brand-400 transition-colors"
                    >
                      Edit →
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
