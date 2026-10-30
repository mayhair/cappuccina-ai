-- Email + password sign-in, alongside Google. A sign-up waits here until its
-- confirmation link is opened; only then does it become a users row, so the
-- users table never holds an address nobody has proven they own. Tokens are
-- stored as SHA-256 hashes: a leaked table can't be replayed as links.

CREATE TABLE pending_registrations (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_pending_registrations_email ON pending_registrations(email);

CREATE TABLE password_resets (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_password_resets_user ON password_resets(user_id);
