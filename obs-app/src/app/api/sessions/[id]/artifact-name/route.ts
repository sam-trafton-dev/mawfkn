import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const ORCHESTRATOR = process.env.ORCHESTRATOR_INTERNAL_URL ?? 'http://orchestrator:8000';

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.json();
  const res = await fetch(`${ORCHESTRATOR}/sessions/${id}/artifact-name`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
