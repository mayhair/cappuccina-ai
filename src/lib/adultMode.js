// True when `value` (an <input type="date"> "YYYY-MM-DD") is a real date and
// the person is at least 18 on `now`. Empty, malformed, impossible, future or
// under-18 dates are false. The birthday is never sent anywhere.
export function isAdultBirthday(value, now = new Date()) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '')
  if (!m) return false
  const year = Number(m[1])
  const month = Number(m[2])
  const day = Number(m[3])
  const dob = new Date(year, month - 1, day)
  const real =
    dob.getFullYear() === year && dob.getMonth() === month - 1 && dob.getDate() === day
  if (!real) return false
  let age = now.getFullYear() - year
  const monthNow = now.getMonth() + 1
  const dayNow = now.getDate()
  if (monthNow < month || (monthNow === month && dayNow < day)) age -= 1
  return age >= 18
}
