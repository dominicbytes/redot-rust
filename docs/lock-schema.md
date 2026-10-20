# Lock and package schema v2

`redot-api.lock.json` separates generation inputs from runtime qualification.
Only schema v2 is accepted. Migrate v1 explicitly; it cannot express which
runtime identity a binding snapshot certifies. Duplicate JSON keys, mixed
v1/v2 fields and incomplete identities are rejected before use.

| Field | Contract |
|---|---|
| `binding_abi` | Immutable generation snapshot: profile, originating engine, compatible Godot API, precision, raw API/header SHA-256, interface mode, gdext revision/feature and Rust toolchain. |
| `binding_abi.engine_profile` | Certified profile used to reproduce the generation snapshot. |
| `binding_abi.coverage` | Unsupported Variant types, engine-only classes and engine-only enum values. Runtime qualification does not expand the generated surface. |
| `certified_engines` | Named exact runtime targets, each referencing the binding profile and carrying its own complete product/API identities and raw dump hashes. |
| `certified_engines.*.assets` | Official release archive URL/SHA-256, exact executable member and extracted binary SHA-256 for every supported platform. |
| `default_engine_profile` | Explicit default for callers migrating from the single-engine interface. Use `--engine-profile` for qualification. |
| `certified_engines.*.import_exception` | Optional repository-relative diagnostic policy for that engine. No other profile inherits it. |

`configure_cargo.py`, `build_macos_universal.py` and packaging select
`binding_abi`. `inspect_redot_api.py` inspects binding input by default;
`--engine-profile` instead validates the selected engine's raw dump.
`dump_redot_api.py --binding` reproduces the binding-origin engine dump;
`--engine-profile` audits a runtime engine without changing Cargo input.
`fetch_engine.py` verifies both archive and extracted executable hashes.
Windows dump/run helpers resolve and verify the sibling editor rather than
using its console launcher for dump commands.

Runtime harnesses verify the actual binary and `--version`, pass the selected
profile to the fixture, and compare `Engine.get_version_info()` plus
`Engine.get_godot_compatible_version_info()` with the lock. Reports retain both
`binding_abi` and `runtime_engine`. The smoke extension embeds the normalized
binding identity at compile time through `configure_cargo.py`; filesystem
path hints are omitted from that identity. Runtime assertions use schema 2:
22 smoke, 8 clean-install and 10 historical-consumer checks. Successful fixture
completion still requires exit 42, full assertion coverage and all markers.

Package manifests also use schema 2. They carry `binding_abi`,
`certified_engines`, `coverage`, source revision, target, debug/release binary
inventory and every member's hash/size. The installer rejects legacy or mixed
manifest shapes before extraction. A profile in the lock is a qualification
target, not a substitute for a completed platform gate; see compatibility.md.

The seven-line official Windows editor exception is visible in a separate
editor-initialization report as `engine_import_exception`. It requires exact
binary/version identity, every extension/tool marker, completed filesystem
scan/editor layout, fresh extension/class caches and exit zero. Unknown lines,
missing proof, abnormal exits and other platforms fail. Installed and exported
runtime gates never consume the exception. An installed run may consume a
separate successful editor import report only when its identity, required
markers and cache checksums still match. Remove the policy when an official
engine containing the shutdown fix is pinned.
