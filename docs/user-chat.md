# User chat and authentication

The browser uses the chat service's origin for chat and `/admin/*`. Login and ontology APIs are separate Heroku Eco services called by the chat gateway. The browser never receives a service credential or Mistral key.

One predefined email/password account opens both interfaces. The migration seeds a salted scrypt hash from private operator configuration. Login returns a random opaque session; PostgreSQL stores its SHA-256 fingerprint, expiry, CSRF token, and account identity. Logout revokes it. The chat gateway sets a host-scoped HttpOnly, Secure, SameSite=Strict cookie on HTTPS and validates Origin and CSRF on state changes. Service-to-service requests need a separate bearer credential; ontology also checks the user's session through login.

The sidebar controls Base/Enhanced mode, A/B comparisons, and administrator Review mode. Tool activity appears before the response; Review mode exposes tool/model request and response JSON. SQL and query results are available with the response. Traces exclude authorization headers, cookies, service tokens, password inputs, and the Mistral API key. Reviews belong to the job's authenticated owner and require administrator access.

Token enforcement remains off until explicitly enabled in COA administration. Usage, active reservations, and the parked 100,000-token/hour policy persist across dyno restarts. Model requests reserve a conservative allowance; reported usage settles it. Ambiguous interrupted requests retain a conservative charge. No model request is automatically replayed after a restart. A running job interrupted by a chat restart expires and the user can choose to retry.

Eco services sleep independently. A first request can take longer or need retry while a dependency wakes. No keep-alive resource is provisioned. Browser chat history is stored on its origin; history from the AWS URL does not automatically move to the Heroku URL.

Ranking responses use a deterministic evidence presenter after SQL execution. Both Base and Enhanced use the same presenter; their difference is the context supplied to SQL generation. It displays only returned rows, calculates comparisons from compatible event/rank endpoints, preserves gaps, and labels elapsed event days separately from UTC dates. Review mode records this formatting step above the response. Other metadata questions retain model synthesis. This does not validate every generated SQL query automatically.
