import { useEffect, useState } from 'react'

export function useMediaQuery(query) {
  const [matches, setMatches] = useState(
    () => window.matchMedia?.(query).matches ?? false,
  )

  useEffect(() => {
    const mq = window.matchMedia?.(query)
    if (!mq) return
    const onChange = () => setMatches(mq.matches)
    onChange()
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [query])

  return Boolean(matches)
}
