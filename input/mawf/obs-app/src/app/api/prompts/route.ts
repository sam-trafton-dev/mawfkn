import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const ORCHESTRATOR = process.env.ORCHESTRATOR_INTERNAL_URL ?? 'http://orchestrator:8000';

export async function GET() {
  const res = await fetch(`${ORCHESTRATOR}/prompts`, { next: { revalidate: 0 } });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
