# Sample Review Policy

## API Contract

API handlers must return predictable response bodies. Error responses should include
`code` and `message` fields, and success responses should avoid leaking internal
database fields.

## Test Policy

Every behavior change should include a unit test or an integration test. Pull
requests that change public API behavior should update or add API-level tests.

## Security Policy

Changes touching authentication, authorization, tokens, secrets, or permission
checks require a high-risk review. Secrets must never be logged or sent back in
review comments.

## Style Policy

Prefer small functions with explicit names. Avoid broad exception handling unless
the error is re-raised or converted into a user-facing domain error.

