# invoicer

Invoicing for freelancers sharing one workspace. Each member sees and manages only their own
invoices; admins can see all of them. The web layer (not included) authenticates the user and calls
`invoicer.api` with the user record and the request parameters.

Trust boundary:
- The `user` record (`name`, `role`) is set by the authentication layer and is trusted.
- Request parameters (`id`, `status`, `format`, invoice fields) are untrusted.
