# ssohub

Login portal for a company's internal tools. After a successful login the user is sent back to
the page they originally asked for (`next`). The web layer (not included) turns the returned
`Location` into an HTTP redirect.

Trust boundary:
- Request parameters (`username`, `password`, `next`) are untrusted; links containing `next`
  can be crafted by anyone and sent to employees.
- The user database and settings are managed by operators and are trusted.
