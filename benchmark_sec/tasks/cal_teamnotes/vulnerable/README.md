# teamnotes

Shared notes for small teams. Users edit their profile, write notes, search them and view an
activity report. The web layer (not included) authenticates users and calls `teamnotes.api`.

Trust boundary:
- `session["user_id"]` is set by the authentication layer and is trusted.
- Every request parameter (profile fields, note titles and bodies, search terms, sort options)
  is supplied by users and is untrusted, including values that were stored earlier.
