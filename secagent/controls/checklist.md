Security review checklist:
1. List every entry point that receives untrusted input (request parameters, uploaded data, stored user content, cookies, configuration that users can change).
2. List every sensitive operation: file system access, database queries, shell or process execution, deserialization, HTML generation, redirects, dynamic code evaluation, and access to other users' data.
3. For each sensitive operation, trace backwards to see whether untrusted input can reach it, including through helper functions, other modules and values stored earlier.
4. When validation exists, check that it is complete for the context where the value is used, not only that it is present.
5. Check that authorization is enforced on every path to protected data, not only on most of them.
6. Report a weakness only when you can name the entry point, the path and the sensitive operation. A risky-looking call that only receives trusted or validated data is not a finding.
