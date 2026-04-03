/**
 * obs-app/src/app/api/chat/route.ts
 *
 * Proxies POST /api/chat → orchestrator POST /chat (server-side, Docker-internal URL).
 */

import { NextRequest, NextResponse } from 'next/server';

const ORCHESTRATOR_INTERNAL =
  process.env.ORCHESTRATOR_INTERNAL_URL ?? 'http://orchestrator:8000';

export async function POST(req: NextRequest) {
  const body = await req.json();

  const res = await fetch(`${ORCHESTRATOR_INTERNAL}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
