/**
 * PassRateBar — visual pass-rate indicator with colour-coded fill.
 * Pure presentational component; no data fetching.
 */

import { CONSTANTS } from '@/lib/constants';

interface PassRateBarProps {
  rate: number | null;
  showLabel?: boolean;
}

export function PassRateBar({ rate, showLabel = true }: PassRateBarProps) {
  if (rate === null || rate === undefined) {
    return <span className="text-xs text-gray-500">—</span>;
  }

  const pct = Math.round(rate * 100);

  const barColor =
    rate >= CONSTANTS.PASS_RATE_THRESHOLD
      ? 'bg-emerald-500'
      : rate >= CONSTANTS.MIN_PASS_RATE_EARLY
      ? 'bg-yellow-500'
      : 'bg-red-500';

  const textColor =
    rate >= CONSTANTS.PASS_RATE_THRESHOLD
      ? 'text-emerald-400'
      : rate >= CONSTANTS.MIN_PASS_RATE_EARLY
      ? 'text-yellow-400'
      : 'text-red-400';

  return (
    <div className="flex items-center gap-2 min-w-[120px]">
      <div className="flex-1 h-1.5 rounded-full bg-gray-800 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {showLabel && (
        <span className={`text-xs font-mono font-medium tabular-nums ${textColor}`}>
          {pct}%
        </span>
      )}
    </div>
  );
}
