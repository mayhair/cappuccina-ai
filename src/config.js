// Frontend feature flags. Flip a value here, or set the matching VITE_ env var
// at build time (e.g. VITE_SHOW_CONNECTIONS_TESTING_NOTE=false).

// Banner at the top of Settings → Connections telling users that Gmail and
// Calendar need the creator's manual approval while the app is in testing.
export const SHOW_CONNECTIONS_TESTING_NOTE =
  import.meta.env.VITE_SHOW_CONNECTIONS_TESTING_NOTE !== 'false'
