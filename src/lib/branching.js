// Chat branching: messages form a tree instead of a flat list.
//
// Each node carries a stable `id`, a `parentId` (null at the root) and a
// `branchIndex` (creation order among its siblings). A chat stores every node
// in `chat.messages` plus `chat.activePath` — the ids of the visible
// root -> leaf chain. Editing a user message or regenerating a reply adds a
// new sibling; the branch navigator moves along siblings.

export function makeMessageId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  return `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

// Live (non-failed) children of a parent, in creation order. Failed stream
// placeholders are transient: they never take part in the branch structure.
export function childrenOf(messages, parentId) {
  const parent = parentId ?? null
  return messages
    .filter((m) => (m.parentId ?? null) === parent && !m.failed)
    .sort((a, b) => (a.branchIndex ?? 0) - (b.branchIndex ?? 0))
}

export function siblingsOf(messages, node) {
  return childrenOf(messages, node?.parentId ?? null)
}

// 1-based position and total, for the "x/y" navigator.
export function branchPosition(messages, node) {
  const siblings = siblingsOf(messages, node)
  const index = siblings.findIndex((s) => s.id === node.id)
  return { index, total: siblings.length }
}

// Next sibling slot, matching the server's max+1 rule.
export function nextBranchIndex(messages, parentId) {
  return childrenOf(messages, parentId).reduce(
    (max, s) => Math.max(max, (s.branchIndex ?? 0) + 1),
    0,
  )
}

// The tail shown below a node: follow the newest child at each level.
export function newestDescendantChain(messages, node) {
  const chain = [node]
  let current = node
  for (;;) {
    const kids = childrenOf(messages, current.id)
    if (!kids.length) break
    current = kids[kids.length - 1]
    chain.push(current)
  }
  return chain
}

// Default visible chain: newest root branch. Roots are siblings (parent null),
// so editing the very first message adds a second root rather than a child.
export function defaultPath(messages) {
  const roots = childrenOf(messages, null)
  if (!roots.length) return []
  const root = roots[roots.length - 1]
  return newestDescendantChain(messages, root).map((m) => m.id)
}

// The message objects currently on screen, resolved from `activePath`.
export function visibleMessages(chat) {
  const messages = chat?.messages ?? []
  const byId = new Map(messages.map((m) => [m.id, m]))
  const path = Array.isArray(chat?.activePath) ? chat.activePath : []
  const resolved = path.map((id) => byId.get(id)).filter(Boolean)
  if (resolved.length) return resolved
  const fallback = defaultPath(messages)
  return fallback.map((id) => byId.get(id)).filter(Boolean)
}

// Move `delta` siblings from `nodeId`, keeping the ancestors and showing the
// chosen sibling's newest descendant chain.
export function navigate(messages, activePath, nodeId, delta) {
  const node = messages.find((m) => m.id === nodeId)
  const at = (activePath ?? []).indexOf(nodeId)
  if (!node || at === -1) return activePath ?? []
  const siblings = siblingsOf(messages, node)
  const pos = siblings.findIndex((s) => s.id === node.id)
  const next = siblings[pos + delta]
  if (!next) return activePath
  const tail = newestDescendantChain(messages, next).map((m) => m.id)
  return [...activePath.slice(0, at), ...tail]
}
