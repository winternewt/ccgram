# ccgram/1 wire contract

- [OpenAPI 3.1](openapi.yaml): HTTP queries, actions, pairing, devices, and uploads.
- [AsyncAPI 3](asyncapi.yaml): WebSocket handshake, subscriptions, and server events.
- `schemas/*.json`: shared JSON Schema 2020-12 models.
- `fixtures/*.json`: golden payloads for server/client conformance. `_manifest.json`
  maps filenames to schema names. Decode each payload directly. There is no fixture
  envelope.

The `.yaml` documents use JSON syntax, which is also valid YAML. Shared schemas
allow additional fields for forward compatibility. Part and action kinds are
explicit variants. Clients ignore unknown fields and unknown server event types.

Version `1` is the major version in `/v1` and the stream envelope. An incompatible
wire change needs a new major version. Change schemas and affected fixtures together.

[Client guide](../../docs/protocol/client-guide.md) owns merge, retry, and recovery
rules. [Operations](../../docs/protocol/operations.md) owns setup and credentials.
[Implementation](../../docs/protocol/implementation.md) owns module boundaries and
verification.
