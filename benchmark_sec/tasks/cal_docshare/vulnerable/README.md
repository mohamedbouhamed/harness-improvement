# docshare

A small internal document library. Staff browse folders, download documents and upload new files.
The HTTP layer (not included) authenticates the user and calls the functions in `docshare.api`
with the session and the raw request parameters.

Trust boundary:
- `session["user"]` is set by the authentication layer and is trusted.
- Every request parameter (`name`, `folder`, `filename`, and uploaded content) comes from the
  client and is untrusted.
- Configuration comes from environment variables set by the operators and is trusted.
