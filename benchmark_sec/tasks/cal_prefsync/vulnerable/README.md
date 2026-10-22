# prefsync

Keeps a user's editor preferences in sync between devices. The server hands the client a signed
state blob; the client sends it back later, and the server restores the state from it. The web
layer (not included) authenticates users and calls `prefsync.api`.

Trust boundary:
- `session["user"]` is trusted.
- Everything in the request is untrusted, including the `state` blob returned by the client and
  the `settings` JSON document a user can import.
- The signing key comes from the operators (environment) and is trusted.
