export function formatPercent(fraction: number, digits = 3): string {
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function formatUsd(value: number): string {
  const abs = Math.abs(value);
  const digits = abs >= 100 ? 0 : 2;
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function formatPrice(value: number): string {
  return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 8 });
}

export function formatAge(seconds: number): string {
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  return `${(seconds / 60).toFixed(1)}m`;
}
