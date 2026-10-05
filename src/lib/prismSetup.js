// Prism's core auto-highlights every `code[class*=language-]` element on
// DOMContentLoaded unless this flag is set before the core loads. Our rendered
// output contains `language-css`/`language-javascript` spans for inlined
// <script>/<style>, so the automatic pass would re-process code we already
// highlighted. ESM evaluates imports in source order, so this module must stay
// the first import of src/lib/highlightCode.js.
if (typeof window !== 'undefined') {
  window.Prism = { ...(window.Prism ?? {}), manual: true }
}
