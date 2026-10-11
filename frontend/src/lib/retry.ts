/** Delay before the nth stream refresh: the CDN rejects bursts (403/503), so retrying at once fails again. */
export function refreshDelayMs(attempt: number): number {
  return Math.min(1000 * 2 ** (attempt - 1), 8000)
}
