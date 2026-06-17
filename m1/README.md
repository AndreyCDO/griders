# M1 C++ Service

The service is compiled as a single translation unit from `m1_server.cpp`.
The implementation is split into include fragments to keep Codex and editor
navigation focused on the part being changed.

- `m1_server.cpp` keeps includes, shared structs, constants, and `main()`.
- `m1_util.inc` contains string, URL, base64, HMAC, and time helpers.
- `m1_storage.inc` contains data directory paths and line-based persistence.
- `m1_mexc.inc` contains curl wrappers, MEXC API helpers, and JSON extraction.
- `m1_bot_engine.inc` contains bot parsing, planning, execution, and protection sync.
- `m1_web.inc` contains auth, page rendering, responses, routing, and socket handling.

Run `make` from this directory on Linux to build `m1_server`.
