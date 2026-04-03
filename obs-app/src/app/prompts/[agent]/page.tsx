'use client';

/**
 * obs-app/src/app/prompts/[agent]/page.tsx
 *
 * Prompt editor for a single agent.
 * Client component — full textarea editor with save + reset.
 */

import { useParams, useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

const AGENT_LABELS: Record<string, string> = {
  coder:            'Coder',
  reviewer:         'Reviewer',
  qa:               'QA',
  'sme-data':       'SME · Data',
  'sme-api':        'SME · API',
  'sme-ux':         'SME · UX',
  'sme-business':   'SME · Business',
  'sme-networking': 'SME · Networking',
  'sme-devops':     'SME · DevOps',
};

export default function PromptEditorPage() {
  const params = useParams<{ agent: string }>();
  const router = useRouter();
  const agent = params.agent;

  const [original, setOriginal] = useState('');
  const [content, setContent] = useState('');
  const [updatedAt, setUpdatedAt] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/prompts/${agent}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setContent(data.content);
      setOriginal(data.content);
      setUpdatedAt(data.updated_at);
    } catch (e) {
      setBanner({ type: 'error', msg: `Failed to load prompt: ${e}` });
    } finally {
      setLoading(false);
    }
  }, [agent]);

  useEffect(() => { load(); }, [load]);

  async function save() {
    setSaving(true);
    setBanner(null);
    try {
      const res = await fetch(`/api/prompts/${agent}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setOriginal(content);
      setUpdatedAt(new Date().toISOString());
      setBanner({ type: 'success', msg: 'Saved — takes effect on the next agent request.' });
    } catch (e) {
      setBanner({ type: 'error', msg: `Save failed: ${e}` });
    } finally {
      setSaving(false);
    }
  }

  const isDirty = content !== original;

  return (
    <div className="space-y-6">
      {/* Back nav */}
      <a
        href="/prompts"
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-300 transition-colors"
      >
        ← Prompts
      </a>

      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-bold tracking-tight">
            {AGENT_LABELS[agent] ?? agent}
          </h1>
          <p className="mt-1 text-xs font-mono text-gray-500">
            {agent}
            {updatedAt && (
              <span className="ml-3 text-gray-600">
                last saved {new Date(updatedAt).toLocaleString()}
              </span>
            )}
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => { setContent(original); setBanner(null); }}
            disabled={!isDirty || saving}
            className="px-4 py-2 rounded-lg border border-gray-700 text-sm text-gray-400 hover:text-white hover:border-gray-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Reset
          </button>
          <button
            onClick={save}
            disabled={!isDirty || saving}
            className="px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
          >
            {saving ? 'Saving…' : 'Save prompt'}
          </button>
        </div>
      </div>

      {/* Banner */}
      {banner && (
        <div
          className={`rounded-lg px-4 py-3 text-sm ${
            banner.type === 'success'
              ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20'
              : 'bg-red-500/10 text-red-300 border border-red-500/20'
          }`}
        >
          {banner.msg}
        </div>
      )}

      {/* Editor */}
      {loading ? (
        <div className="h-96 rounded-xl border border-gray-800 bg-gray-900 animate-pulse" />
      ) : (
        <div className="rounded-xl border border-gray-800 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-800">
            <span className="text-xs text-gray-500 font-mono">system prompt</span>
            {isDirty && (
              <span className="text-xs text-yellow-400">unsaved changes</span>
            )}
          </div>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            spellCheck={false}
            className="w-full bg-gray-950 text-gray-200 font-mono text-sm p-4 resize-none focus:outline-none"
            style={{ minHeight: '540px' }}
          />
        </div>
      )}
    </div>
  );
}
