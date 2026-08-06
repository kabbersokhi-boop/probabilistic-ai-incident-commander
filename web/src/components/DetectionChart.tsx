import type { RecordValue } from '../bundle/schema';
import { number, time } from '../bundle/formatters';

export function DetectionChart({ points }: { points: RecordValue[] }) {
  const values = points.map((point) => point.observed_value).filter((value): value is number => typeof value === 'number');
  if (!values.length) return null;
  const low = Math.min(...values), span = Math.max(...values) - low || 1;
  return <figure className="chart"><figcaption>Exact detector values in UTC; focus a point for its source value.</figcaption><svg viewBox="0 0 720 180" role="img" aria-label={`${points.length} detector observations`}>{points.map((point, index) => { const value = point.observed_value; if (typeof value !== 'number') return null; const x = 30 + (660 * (points.length === 1 ? .5 : index / (points.length - 1))), y = 150 - ((value - low) / span) * 115; return <circle key={String(point.observation_id ?? index)} cx={x} cy={y} r="6" tabIndex={0}><title>{`${time(point.period_start)}: ${number(value)}`}</title></circle>; })}</svg><p>{points.map((point) => `${time(point.period_start)}: ${number(point.observed_value)}`).join('; ')}</p></figure>;
}
