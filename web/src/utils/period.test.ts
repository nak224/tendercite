import { expect, test } from 'vitest';
import { validDate, validSnapshotPeriod } from './period';

test('snapshot metadata keeps the original calendar validation without planning state', () => {
  expect(validDate('2028-02-29')).toBe(true);
  for (const value of ['2027-02-29', '2027-13-01', 'not-a-date'])
    expect(validDate(value)).toBe(false);
  expect(validSnapshotPeriod({ start_date: '2028-01-01', end_date: '2028-12-31' })).toBe(true);
  expect(validSnapshotPeriod({ start_date: '2027-01-01', end_date: '2028-01-02' })).toBe(false);
  expect(validSnapshotPeriod({ start_date: '2027-02-07', end_date: '2027-02-01' })).toBe(false);
});
