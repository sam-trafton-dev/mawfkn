import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'MAWF — Multi-Agent Workshop Observer',
  description: 'Real-time observability dashboard for the MAWF multi-agent workshop system',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full bg-gray-950 text-gray-100">
      <body className="h-full font-sans antialiased">
        <div className="min-h-full flex flex-col">
          {/* Top nav */}
          <header className="border-b border-gray-800 bg-gray-900 px-6 py-4">
            <div className="mx-auto max-w-7xl flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="h-8 w-8 rounded-lg bg-brand-600 flex items-center justify-center">
                  <span className="text-white text-sm font-bold">M</span>
                </div>
                <span className="text-lg font-semibold tracking-tight">
                  MAWF Observer
                </span>
              </div>
              <nav className="flex items-center gap-6 text-sm text-gray-400">
                <a href="/" className="hover:text-white transition-colors">
                  Dashboard
                </a>
                <a href="/prompts" className="hover:text-white transition-colors">
                  Prompts
                </a>
                <a
                  href="/api/orchestrator/health"
                  target="_blank"
                  rel="noreferrer"
                  className="hover:text-white transition-colors"
                >
                  Health
                </a>
              </nav>
            </div>
          </header>

          {/* Main content */}
          <main className="flex-1 px-6 py-8">
            <div className="mx-auto max-w-7xl">
              {children}
            </div>
          </main>

          {/* Footer */}
          <footer className="border-t border-gray-800 px-6 py-4 text-center text-xs text-gray-600">
            MAWF Multi-Agent Workshop System
          </footer>
        </div>
      </body>
    </html>
  );
}
