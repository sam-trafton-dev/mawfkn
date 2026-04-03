import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const ORCHESTRATOR = process.env.ORCHESTRATOR_INTERNAL_URL ?? 'http://orchestrator:8000';

export async function GET(_req: NextRequest, { params }: { params: Promise<{ agent: string }> }) {
  const { agent } = await params;
  const res = await fetch(`${ORCHESTRATOR}/prompts/${agent}`, { next: { revalidate: 0 } });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}

export async function PUT(req: NextRequest, { params }: { params: Promise<{ agent: string }> }) {
  const { agent } = await params;
  const body = await req.json();
  const res = await fetch(`${ORCHESTRATOR}/prompts/${agent}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
