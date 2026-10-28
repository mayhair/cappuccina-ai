// How a file's size reads: "812 KB". Empty for a missing or zero size.
export function formatBytes(bytes) {
  const size = Number(bytes) || 0
  if (size <= 0) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}
