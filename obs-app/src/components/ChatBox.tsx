'use client';

import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

interface Message {
  role: 'user' | 'assistant';
  text: string;
  sessionId?: string;
  workshopName?: string;
}

type ImprovementMode = 'refactor' | 'bugfix' | 'feature' | '';

export function ChatBox() {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'assistant',
      text: "Hey! Tell me what you'd like to build and I'll spin up the workshop agents to build it for you.",
    },
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);

  // Improvement mode panel state
  const [showImprove, setShowImprove] = useState(false);
  const [inputFolder, setInputFolder] = useState('');
  const [improvementMode, setImprovementMode] = useState<ImprovementMode>('');

  const router = useRouter();
  const bottomRef = useRef<HTMLDivElement>(null);

  async function send() {
    let text = input.trim();
    if (!text || loading) return;

    // Append input/mode context if the improve panel is open and folder is set
    if (showImprove && inputFolder.trim()) {
      const folder = inputFolder.trim();
      const mode = improvementMode || 'refactor';
      text = `[input: ./input/${folder}, mode: ${mode}] ${text}`;
    }

    setInput('');
    setMessages((prev) => [...prev, { role: 'user', text }]);
    setLoading(true);

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      const data = await res.json();
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: data.reply,
          sessionId: data.session_id,
          workshopName: data.workshop_name,
        },
      ]);
      // Scroll to bottom
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', text: 'Something went wrong talking to the orchestrator.' },
      ]);
    } finally {
      setLoading(false);
    }
  }

  const modeButtons: { value: ImprovementMode; label: string }[] = [
    { value: 'refactor', label: 'Refactor' },
    { value: 'bugfix',   label: 'Bug fixes' },
    { value: 'feature',  label: 'Feature' },
  ];

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 flex flex-col" style={{ minHeight: '420px' }}>
      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3" style={{ maxHeight: '320px' }}>
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[80%] rounded-xl px-4 py-2.5 text-sm leading-relaxed ${
                msg.role === 'user'
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-800 text-gray-200'
              }`}
            >
              <p>{msg.text}</p>
              {msg.sessionId && (
                <button
                  onClick={() => router.push(`/sessions/${msg.sessionId}`)}
                  className="mt-2 text-xs text-brand-300 hover:text-brand-200 underline underline-offset-2"
                >
                  Watch {msg.workshopName} build →
                </button>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-800 rounded-xl px-4 py-2.5 text-sm text-gray-500 animate-pulse">
              Thinking…
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Improve existing project panel */}
      <div className="border-t border-gray-800 px-3 pt-2 pb-1">
        <button
          type="button"
          onClick={() => setShowImprove((v) => !v)}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
        >
          Improve existing project {showImprove ? '↑' : '↓'}
        </button>

        {showImprove && (
          <div className="mt-2 space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500 whitespace-nowrap">Input folder:</span>
              <input
                type="text"
                value={inputFolder}
                onChange={(e) => setInputFolder(e.target.value)}
                placeholder="e.g. mawf"
                className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-brand-500"
              />
              {inputFolder.trim() && (
                <span className="text-xs text-gray-600 font-mono whitespace-nowrap">
                  ./input/{inputFolder.trim()}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500">Mode:</span>
              <div className="flex gap-1">
                {modeButtons.map((btn) => (
                  <button
                    key={btn.value}
                    type="button"
                    onClick={() => setImprovementMode((prev) => prev === btn.value ? '' : btn.value)}
                    className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
                      improvementMode === btn.value
                        ? 'bg-brand-600 text-white'
                        : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-gray-200'
                    }`}
                  >
                    {btn.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-gray-800 p-3 flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && send()}
          placeholder="Build me a REST API for a task manager with SQLite…"
          disabled={loading}
          className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:opacity-50"
        />
        <button
          onClick={send}
          disabled={loading || !input.trim()}
          className="px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
        >
          Send
        </button>
      </div>
    </div>
  );
}
