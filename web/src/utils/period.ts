// Snapshot metadata dates, independent of workforce planning state or generated API types.
export interface Period {
  start_date: string;
  end_date: string;
}
export function validDate(value: string): boolean {
  const parsed = new Date(`${value}T12:00:00Z`);
  return (
    /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value) &&
    !Number.isNaN(parsed.valueOf()) &&
    parsed.toISOString().slice(0, 10) === value
  );
}
export function validSnapshotPeriod(period: Period) {
  if (!validDate(period.start_date) || !validDate(period.end_date)) return false;
  const days =
    (Date.parse(`${period.end_date}T00:00:00Z`) - Date.parse(`${period.start_date}T00:00:00Z`)) /
      86400000 +
    1;
  return days >= 1 && days <= 366;
}
