// Frontend feature flags. Flip a value here, or set the matching VITE_ env var
// at build time (e.g. VITE_SHOW_CONNECTIONS_TESTING_NOTE=false).

// Banner at the top of Settings → Connections telling users that Gmail and
// Calendar need the creator's manual approval while the app is in testing.
export const SHOW_CONNECTIONS_TESTING_NOTE =
  import.meta.env.VITE_SHOW_CONNECTIONS_TESTING_NOTE !== 'false'

// Which sign-in buttons the landing page shows before the server has answered.
// Match these to the providers the backend enables, so no button appears or
// disappears when /auth/options returns. The server's answer still wins once it
// arrives. Set the matching VITE_ env var at build time (e.g. VITE_AUTH_PASSWORD=false).
export const AUTH_DEFAULTS = {
  google: import.meta.env.VITE_AUTH_GOOGLE !== 'false',
  password: import.meta.env.VITE_AUTH_PASSWORD !== 'false',
  // Public Turnstile site key. With it set, the sign-up captcha is ready even
  // if the form opens before the server answers.
  turnstileSiteKey: import.meta.env.VITE_TURNSTILE_SITE_KEY ?? '',
}
