'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';

interface ContinueSessionProps {
  sessionId: string;
  status: string;
}

export function ContinueSession({ sessionId, status }: ContinueSessionProps) {
  const [instructions, setInstructions] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const isActive = status === 'running' || status === 'pending';

  async function submit() {
    if (!instructions.trim() || loading) return;
    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`/api/sessions/${sessionId}/continue`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instructions: instructions.trim() }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail ?? `HTTP ${res.status}`);
      }

      setInstructions('');
      // Refresh the page so the status badge updates
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  if (isActive) {
    return (
      <div className="rounded-xl border border-gray-800 bg-gray-900 px-5 py-4 text-sm text-gray-500">
        Session is currently running — wait for it to finish before continuing.
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-5 space-y-3">
      <p className="text-xs text-gray-500">
        The agents will pick up from where they left off, with all previous iteration outputs as context.
      </p>
      <textarea
        value={instructions}
        onChange={(e) => setInstructions(e.target.value)}
        placeholder="e.g. Add authentication with JWT. Fix the database connection pooling issue the reviewer flagged."
        rows={3}
        className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-brand-500 resize-none"
      />
      {error && (
        <p className="text-xs text-red-400">{error}</p>
      )}
      <button
        onClick={submit}
        disabled={!instructions.trim() || loading}
        className="px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
      >
        {loading ? 'Starting…' : 'Continue session →'}
      </button>
    </div>
  );
}
