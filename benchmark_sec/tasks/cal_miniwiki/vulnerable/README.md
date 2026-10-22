# miniwiki

A tiny team wiki. Anyone in the company can create and edit pages; pages are rendered to HTML and
viewed by everyone else. The web layer (not included) calls `miniwiki.api` and returns the HTML.

Trust boundary:
- Page titles, bodies and tags are written by users and are untrusted, both when saved and when
  rendered later.
- The page slug is derived by the application.
