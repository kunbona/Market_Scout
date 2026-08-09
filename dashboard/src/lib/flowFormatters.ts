export function formatFlowYi(value?: number | null): string {
  return value != null ? `${(value / 1e8).toFixed(2)}亿` : '--';
}
