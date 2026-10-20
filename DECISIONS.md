# Architecture decisions

## ADR-0006: Pin the Redot–Rust compatibility inputs

- Date: 2026-08-09
- Status: accepted
- Context: A reproducible Redot 26.2 binding contract requires pinned upstream source, dependencies, API dumps, tests, and legal notices.
- Decision: Create a repeatable Rust GDExtension path for Redot 26.2.
- Alternatives considered: source-only copy without upstream history; unpinned current-main port; unchecked API substitution.
- Consequences: The upstream source snapshot stays unmodified. Compatibility work begins only after the exact Redot API and toolchain lock is recorded.
- Evidence: `UPSTREAM_LOCK.md` and `redot-api.lock.json`.

## ADR-0007: License new code under MPL-2.0

- Date: 2026-08-10
- Status: accepted
- Context: The pinned gdext source uses MPL-2.0, and new compatibility code needs an explicit license.
- Decision: License new code under MPL-2.0, matching gdext.
- Alternatives considered: leave the license undecided; use a permissive license different from upstream.
- Consequences: New compatibility code and the pinned upstream share the same license; distribution must preserve the required license and notices.
- Evidence: `LICENSE`, `NOTICE.md`, and `UPSTREAM_LOCK.md`.

## ADR-0008: Pin gdext 0.5.5 and Rust 1.94.0 for JSON mode

- Date: 2026-08-10
- Status: accepted
- Context: gdext commit `637cef73172bba23850131acd8583b0c72ebf0c7` declares Rust 1.94 and exposes `api-custom-json` through the `godot` crate.
- Decision: Pin that full revision, Rust `1.94.0-x86_64-pc-windows-msvc`, and the `api-custom-json` feature. Start with no gdext fork or source patch.
- Alternatives considered: unpinned master; crates.io default API; direct-binary `api-custom`; an immediate fork.
- Consequences: Cargo generation uses Godot API 4.5.2 from Redot's standard header while Redot 26.2 remains separate product metadata.
- Evidence: the pinned checkout, `rust-toolchain.toml`, and `redot-api.lock.json`.

## ADR-0009: Resolve the actual Windows editor for API dumps

- Date: 2026-08-10
- Status: accepted
- Context: the configured `.console.exe` launcher crashes on both dump commands while the sibling editor succeeds when run headlessly.
- Decision: Dump tooling must resolve, hash, and invoke the sibling editor binary. It must reject launcher misuse before starting a dump.
- Alternatives considered: invoke the launcher; ignore the crash; redistribute a checked-in API dump.
- Consequences: the launcher failure becomes a negative regression case and generated artifacts remain outside the repository.
- Evidence: `redot-api.lock.json`, `tools/dump_redot_api.py`, and `tests/test_dump_resolution.py`.

## ADR-0010: Use restart-based editor loading and a 120-frame import window

- Date: 2026-08-10
- Status: accepted
- Context: Redot 26.2 can terminate a clean first editor import with signal 11 when forced to quit after five frames. The same import completes cleanly at 120 frames. Reloadable GDExtensions also make editor teardown unstable in this build.
- Decision: Set `reloadable = false`, require an editor restart after replacing the DLL, and bound automated editor initialization at 120 frames plus a wall-clock timeout.
- Alternatives considered: hot reload; a five-frame import; treating the first-import crash as an extension failure.
- Consequences: Debug iteration requires a restart, but clean debug and release imports are deterministic. The harness hard-gates all init stages and a reflective editor-tool invocation.
- Evidence: `fixtures/redot-project/bin/redot_rust_smoke.gdextension` and `reports/qa/ms029-semantics/`.

## ADR-0011: Waive direct-binary mode without weakening Redot identity

- Date: 2026-08-10
- Status: accepted
- Context: gdext direct `api-custom` derives its API identity from the executable version. Redot reports product major 26, while the compatible GDExtension API is Godot 4.5.2.
- Decision: Support `api-custom-json` only. Retain the reproduced direct-mode rejection as a required negative test and do not patch gdext's version checks.
- Alternatives considered: accept API major 26; fork gdext's executable parser; hide Redot's product identity.
- Consequences: builds require the locked API JSON, but Redot and Godot identities remain truthful and fail closed.
- Evidence: `reports/qa/direct-mode/` and `tools/probe_direct_mode.py`.

## ADR-0012: Use Rapier 0.8.40 as the unchanged consumer baseline

- Date: 2026-08-10
- Status: accepted
- Context: Rapier 0.8.40 provides a consumer baseline for this Redot 26.2 / Godot 4.5 lock.
- Decision: Pin Rapier 0.8.40 at `de3458c23426fe332e491013c4412fe1fb84e778`, unify its gdext dependency at the workspace level, and make no source edits.
- Alternatives considered: port a newer Rapier release in this compatibility kit; copy compatibility fixes into Rapier; weaken the API lock.
- Consequences: The baseline proves unchanged-consumer reuse; porting newer Rapier APIs remains separate work.
- Evidence: `reports/gauntlet/g02/final/consumer-report.json`.

## ADR-0013: Cross-build macOS locally and require native macOS CI execution

- Date: 2026-08-10
- Status: accepted
- Context: Universal Mach-O libraries and unsigned macOS exports can be produced and audited without macOS, but native runtime validation requires macOS.
- Decision: Pin the cargo-zigbuild container by digest for local universal builds, audit both Mach-O slices, export with the exact Redot template, and require the native `macos-14` CI job to execute debug, release, and clean-install fixtures.
- Alternatives considered: claim cross-build success as runtime proof; omit macOS.
- Consequences: Native source and clean-installed project execution require a macOS CI run. No CI job exports or executes exported applications; native macOS exported-app execution remains unverified even if CI passes.
- Evidence: `tools/build_macos_universal.py`, `.github/workflows/ci.yml`, and `reports/gauntlet/g03/macos-universal/`.

## ADR-0014: Ship target-specific deterministic archives

- Date: 2026-08-10
- Status: accepted
- Context: one mixed-platform archive would be unnecessarily large and makes target selection and restricted-file review harder.
- Decision: Create one archive per target, include both debug and release libraries for that target, generate the `.gdextension` mapping, hash every member, reject unsafe paths and private material, and refuse installation over existing files.
- Alternatives considered: one all-platform archive; raw binary drops; installers that overwrite existing add-ons.
- Consequences: target packages are independently auditable and safely installable, while consumers still compile their own application-specific Rust source.
- Evidence: `tools/package_release.py`, `tools/install_package.py`, and `tests/test_packaging.py`.

## ADR-0015: Separate binding ABI from certified runtime engines

- Date: 2026-10-20
- Status: accepted for candidate 0.2.0; native Linux/macOS and hosted gates pending.
- Context: The unchanged 26.2 snapshot initializes on official 26.3, while raw 26.3 generation fails in pinned gdext. Struct generation and the engine's missing Struct Variant converters are separate blockers. One identity cannot truthfully describe both generation and runtime provenance.
- Decision: Require schema v2 with `binding_abi` and exact `certified_engines` profiles. Keep the 26.2 API/header hashes, gdext pin and toolchain unchanged. Reject legacy v1 and mixed shapes explicitly because v1 cannot express independent runtime selection. Build/package validation selects binding input; dump/inspect/run validation selects an engine profile. Embed compile-time binding identity in the smoke extension and compare actual runtime version dictionaries with the selected profile. Use v2 manifests and assertion schema 2, retaining completion exit 42.
- Coverage: No Struct builtin, 26.3-only typed enum values or 26.3-only classes. New physics enum members do not become supported merely because existing physics signatures match.
- Official-engine exception: Only the locked official Windows `redot-26.3` binary/version may accept `shutdown-leaks` (exit zero with exactly the seven measured shutdown lines) or `shutdown-abnormal-exit` (Windows access violation `3221225477` / `-1073741819`, hexadecimal `0xC0000005`). Both require every stage/tool marker, completed filesystem scan/editor layout and extension/class caches deleted before the run and recreated with the exact expected contents. Qualified fixtures have no global GDScript classes, so their class cache must be `list=[]`. Abnormal output may contain only the seven shutdown diagnostics and the exact crash-handler grammar in `compat/redot-26.3-import-allowlist.json`: the fixed signal-11 banner, exact engine version/commit, fixed backtrace announcement, sequential frames 1 through at most 13 with the fixed no-debug-info text, end marker and 64-equals separator. A handler truncated at a complete line boundary, including immediately after its opening separator, is supported only after all required work is complete. Bare abnormal exits fail. Preserve the original exit/diagnostics and record `engine_import_exception.form` and truncation visibly. No Linux/macOS signal exception is evidenced or enabled. Unknown diagnostics, other exit codes, engines/platforms, runtime, installed and exported runs remain strict. A clean-installed runtime consumes a separate import proof only after identity/marker/cache-hash checks. Remove both forms when an official engine release containing the shutdown fix is pinned.
- Alternatives considered: replace the binding snapshot with raw 26.3; filter raw dumps; duplicate single-engine locks; allow broad diagnostic substrings; certify patched local engines under official profiles. These lose provenance or fail generator/engine initialization gates.
- Future route: Raw-26.3 bindings require a pinned Struct-capable binding revision and an engine with working GDExtension Struct converters, followed by complete native, value/lifetime, install and export qualification. That route is outside 0.2.0.
- Consequences: The same generated ABI can be qualified against multiple exact engines without expanding coverage. Six native CI lanes derive official assets from the lock. Supporting multiple profiles here does not change a downstream plugin's one-engine-per-release policy or certify its physics behavior. Engine/API artifacts remain undistributed.
- Contract: `redot-api.lock.json`, `docs/lock-schema.md`, `compat/redot-26.3-import-allowlist.json` and `.github/workflows/ci.yml`.
