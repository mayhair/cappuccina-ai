import { isAdultBirthday } from '../src/lib/adultMode.js'

const cases = [
  ['exactly 18 today', '2008-05-10', new Date(2026, 4, 10), true],
  ['turns 18 tomorrow', '2008-05-10', new Date(2026, 4, 9), false],
  ['16-year-old', '2010-01-01', new Date(2026, 0, 1), false],
  ['empty', '', new Date(2026, 0, 1), false],
  ['future date', '2030-01-01', new Date(2026, 0, 1), false],
  ['invalid month', '2008-13-01', new Date(2026, 0, 1), false],
  ['leap day, Feb 28 (strict)', '2008-02-29', new Date(2026, 1, 28), false],
  ['leap day, Mar 1', '2008-02-29', new Date(2026, 2, 1), true],
]

let failed = 0
for (const [name, value, now, expected] of cases) {
  const got = isAdultBirthday(value, now)
  if (got !== expected) failed++
  console.log(`${got === expected ? 'PASS' : 'FAIL'} ${name}: ${got} (expected ${expected})`)
}
if (failed) process.exit(1)
console.log('adult-mode-check: all passed')
