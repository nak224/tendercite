import { expect, test } from 'vitest';
import { actionDateLabel, localCalendarDate } from './actionDate';

test('device calendar date uses local year/month/day, including a year boundary', () => {
  expect(localCalendarDate(new Date(2026, 11, 31, 23, 59))).toBe('2026-12-31');
  expect(localCalendarDate(new Date(2027, 0, 1, 0, 1))).toBe('2027-01-01');
});
test.each([
  ['2026-10-29', 'Overdue action date'],
  ['2026-10-30', 'Action date today'],
  ['2026-10-31', 'Upcoming action date'],
  [null, 'No action date'],
])('date-only action label for %s', (due, expected) => {
  expect(actionDateLabel(due, 'PREPARING', '2026-10-30')).toBe(expected);
});
test.each(['WON', 'LOST', 'ARCHIVED'] as const)(
  'closed %s stage does not imply unfinished work',
  (stage) => {
    expect(actionDateLabel('2026-10-01', stage, '2026-10-30')).toBe('Recorded action date');
  },
);
