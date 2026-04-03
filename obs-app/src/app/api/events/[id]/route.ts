/**
 * obs-app/src/app/api/events/[id]/route.ts
 *
 * SSE proxy — pipes the orchestrator's event stream to the browser.
 *
 * Why not next.config.js rewrites:  Next.js rewrites buffer the full response.
 * Why not global fetch:             Next.js wraps fetch with caching which
 *                                   prevents true streaming for SSE.
 *
 * Solution: use Node's http.get() directly to get an un-buffered stream,
 * then pipe it into a ReadableStream returned to the browser.
 */

import http from 'http';
import { NextRequest } from 'next/server';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

const ORCHESTRATOR_INTERNAL =
  process.env.ORCHESTRATOR_INTERNAL_URL ?? 'http://orchestrator:8000';

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const url = new URL(
    `/sessions/${id}/events`,
    ORCHESTRATOR_INTERNAL,
  );

  const stream = new ReadableStream({
    start(controller) {
      const request = http.get(
        url.toString(),
        { headers: { Accept: 'text/event-stream', 'Cache-Control': 'no-cache' } },
        (res) => {
          if (res.statusCode !== 200) {
            controller.enqueue(
              new TextEncoder().encode(
                `data: {"error":"upstream ${res.statusCode}"}\n\n`,
              ),
            );
            controller.close();
            return;
          }

          res.on('data', (chunk: Buffer) => {
            try {
              controller.enqueue(new Uint8Array(chunk));
            } catch {
              // controller already closed (client disconnected)
            }
          });

          res.on('end', () => {
            try { controller.close(); } catch { /* already closed */ }
          });

          res.on('error', (err) => {
            try { controller.error(err); } catch { /* already closed */ }
          });
        },
      );

      request.on('error', (err) => {
        try { controller.error(err); } catch { /* already closed */ }
      });

      // When the browser disconnects, abort the upstream request
      req.signal.addEventListener('abort', () => {
        request.destroy();
        try { controller.close(); } catch { /* already closed */ }
      });
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type':      'text/event-stream',
      'Cache-Control':     'no-cache',
      'Connection':        'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
}
