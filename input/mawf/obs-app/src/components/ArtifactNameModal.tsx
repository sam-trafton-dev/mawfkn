'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

interface ArtifactNameModalProps {
  sessionId: string;
  /** Pass true to auto-show the modal (session done, no name set yet). */
  show: boolean;
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
}

export function ArtifactNameModal({ sessionId, show: initialShow }: ArtifactNameModalProps) {
  const [open, setOpen] = useState(initialShow);
  const [value, setValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  // Focus input when modal opens
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const slug = slugify(value);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!slug || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/sessions/${sessionId}/artifact-name`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: slug }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail ?? `HTTP ${res.status}`);
      }
      setOpen(false);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="text-xs text-gray-500 hover:text-gray-300 underline underline-offset-2 transition-colors"
      >
        Name output folder
      </button>
    );
  }

  return (
    /* Backdrop */
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-gray-900 border border-gray-700 rounded-2xl p-6 w-full max-w-md shadow-2xl space-y-4">
        <div>
          <h2 className="text-base font-semibold">Name your output folder</h2>
          <p className="mt-1 text-xs text-gray-500">
            Artifacts are saved to <code className="text-gray-400">./artifacts/&lt;name&gt;/src/</code> on
            your machine. Choose something descriptive.
          </p>
        </div>

        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1">
            <input
              ref={inputRef}
              type="text"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="e.g. my-todo-api"
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
            {value && (
              <p className="text-[11px] text-gray-500 font-mono px-1">
                → ./artifacts/<span className="text-gray-300">{slug || '…'}</span>/src/
              </p>
            )}
          </div>

          {error && <p className="text-xs text-red-400">{error}</p>}

          <div className="flex gap-2 justify-end">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="px-4 py-2 rounded-lg text-sm text-gray-400 hover:text-gray-200 transition-colors"
            >
              Skip for now
            </button>
            <button
              type="submit"
              disabled={!slug || loading}
              className="px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
            >
              {loading ? 'Saving…' : 'Save name →'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
