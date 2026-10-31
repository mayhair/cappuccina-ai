// Frontend feature flags. Flip a value here, or set the matching VITE_ env var
// at build time (e.g. VITE_SHOW_CONNECTIONS_TESTING_NOTE=false).

// Banner at the top of Settings → Connections telling users that Gmail and
// Calendar need the creator's manual approval while the app is in testing.
export const SHOW_CONNECTIONS_TESTING_NOTE =
  import.meta.env.VITE_SHOW_CONNECTIONS_TESTING_NOTE !== 'false'

// Which sign-in methods the landing page shows. The frontend doesn't ask the
// server, so match these to the providers the backend enables, then rebuild and
// redeploy. Set the matching VITE_ env var at build time (e.g. VITE_AUTH_PASSWORD=false).
export const AUTH_OPTIONS = {
  google: import.meta.env.VITE_AUTH_GOOGLE !== 'false',
  password: import.meta.env.VITE_AUTH_PASSWORD !== 'false',
  // Public Turnstile site key. Sign-up fails without it when the backend has
  // TURNSTILE_SECRET_KEY set, because the form then shows no captcha.
  turnstileSiteKey: import.meta.env.VITE_TURNSTILE_SITE_KEY ?? '',
}
