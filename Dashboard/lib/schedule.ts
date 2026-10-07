// Read-only context, verified against the official schedule on 2026-09-30.
// A matching date is not evidence that a Kinexon session is an official game.
export const scheduleSource = 'https://ramblinwreck.com/sports/m-baskbl/schedule/season/2025-26';
export const scheduleStart = '2025-11-03';
export const scheduleEnd = '2026-03-07';
export const schedule = [
  { date: '2025-11-03', opponent: 'Maryland Eastern Shore', venue: 'home' },
  { date: '2025-11-07', opponent: 'Bryant', venue: 'home' },
  { date: '2025-11-10', opponent: 'Southeastern Louisiana', venue: 'home' },
  { date: '2025-11-14', opponent: 'Georgia', venue: 'away' },
  { date: '2025-11-18', opponent: 'Georgia Southern', venue: 'home' },
  { date: '2025-11-23', opponent: 'West Georgia', venue: 'home' },
  { date: '2025-11-28', opponent: 'DePaul', venue: 'neutral' },
  { date: '2025-11-29', opponent: 'Drake', venue: 'neutral' },
  { date: '2025-12-03', opponent: 'Mississippi State', venue: 'home' },
  { date: '2025-12-06', opponent: 'Monmouth', venue: 'home' },
  { date: '2025-12-16', opponent: 'Marist', venue: 'home' },
  { date: '2025-12-20', opponent: 'Lafayette', venue: 'home' },
  { date: '2025-12-28', opponent: 'Florida A&M', venue: 'home' },
  { date: '2025-12-31', opponent: 'Duke', venue: 'away' },
  { date: '2026-01-03', opponent: 'Boston College', venue: 'home' },
  { date: '2026-01-06', opponent: 'Syracuse', venue: 'home' },
  { date: '2026-01-10', opponent: 'Miami', venue: 'away' },
  { date: '2026-01-14', opponent: 'Pittsburgh', venue: 'home' },
  { date: '2026-01-17', opponent: 'NC State', venue: 'away' },
  { date: '2026-01-24', opponent: 'Clemson', venue: 'home' },
  { date: '2026-01-27', opponent: 'Virginia Tech', venue: 'away' },
  { date: '2026-01-31', opponent: 'North Carolina', venue: 'home' },
  { date: '2026-02-04', opponent: 'California', venue: 'away' },
  { date: '2026-02-07', opponent: 'Stanford', venue: 'away' },
  { date: '2026-02-11', opponent: 'Wake Forest', venue: 'home' },
  { date: '2026-02-14', opponent: 'Notre Dame', venue: 'away' },
  { date: '2026-02-18', opponent: 'Virginia', venue: 'home' },
  { date: '2026-02-21', opponent: 'Louisville', venue: 'away' },
  { date: '2026-02-28', opponent: 'Florida State', venue: 'home' },
  { date: '2026-03-04', opponent: 'California', venue: 'home' },
  { date: '2026-03-07', opponent: 'Clemson', venue: 'away' },
] as const;
