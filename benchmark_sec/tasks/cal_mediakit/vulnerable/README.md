# mediakit

Media library helper for a small newsroom. Editors upload images, inspect files and request
thumbnails. The web layer (not included) authenticates editors and calls `mediakit.api`.

Trust boundary:
- `session["user"]` is trusted.
- Request parameters (`filename`, `size`) are untrusted. File names are chosen by the uploader.
- Settings come from the operators and are trusted.
