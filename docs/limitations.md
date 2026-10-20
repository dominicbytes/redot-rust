# Limitations and operating rules

## Exact identities and restricted surface

Only the exact official engines in `certified_engines` are qualification
targets. Cargo always uses the separate, unchanged `binding_abi`: Redot 26.2's
Godot API 4.5.2, single precision, gdext revision
`637cef73172bba23850131acd8583b0c72ebf0c7` and Rust 1.94.0. Different engine
builds, raw snapshots, precision or binding/toolchain pins require new gates.
Custom patched engines are rejected by the official profiles.

The generated API contains no **Struct** builtin, 26.3-only enum values or
26.3-only classes. Do not pass Struct Variants across this binding surface.
Shared physics signatures do not make the entire raw APIs identical. Raw 26.3
binding generation fails with the pinned generator, and official 26.3 lacks
the required GDExtension Struct converters. Future raw-26.3 support needs both
an immutable Struct-capable binding revision and a complete converter-fixed
engine, followed by value/lifetime and native-platform qualification. That route
is outside this release.

## Editor shutdown and iteration

Use `reloadable = false` and restart after replacing a native library. Editor
initialization allows 120 frames and a wall-clock timeout. Official 26.3 has
two temporary editor-initialization exceptions, both reproduced without an
extension. `shutdown-leaks` (Windows, Linux and macOS) requires exit zero and
exactly seven known shutdown diagnostics. `shutdown-abnormal-exit` requires only the Windows
access violation `3221225477` / `-1073741819` (`0xC0000005`), with the exact
crash-handler banner/backtrace grammar in the allowlist. The handler can be
truncated at a complete line, including after its opening 64-equals separator;
a bare abnormal exit remains a failure. No Linux/macOS signal form is enabled.

Both forms require the locked official profile's binary hash and version,
every stage/tool marker, completed filesystem scan/editor layout and fresh
extension/class caches. Caches are deleted before each import; the extension
list must match the project's descriptors, and these fixtures' global script
class cache must be `list=[]`. Reports retain diagnostics and original exit
codes, with `engine_import_exception.form` and `crash_handler_truncated` visible.
Any extra diagnostic, incomplete work, other exit code or unofficial binary
fails. Remove both forms when an official engine release containing the
shutdown fix is pinned.

Runtime, installed and exported execution remain strict. Installed fixtures
can reuse only a separate successful import proof whose identities, markers
and cache checksums match. Neither a frame-cap exit nor an empty/partial
assertion map can satisfy fixture completion: success requires exit 42 and the
complete schema-2 assertion set.

## JSON mode and platform scope

Use `api-custom-json` and the pinned gdext bundled interface. The C header and
raw runtime dumps are separately audited; they do not replace the binding
input. Custom interface JSON requires an explicit hash-locked interface mode.
Direct `api-custom` rejects Redot's product major 26 and remains unsupported.

Windows checks cover editor initialization, runtime smoke and clean installs for both official profiles and build profiles. The recorded 26.3 shutdown exceptions qualify completed imports only; they do not claim clean engine shutdown.
Linux x86-64 and macOS universal execution require the configured native CI
lanes, which remain pending.
No CI job obtains export templates or runs exported applications; candidate
export qualification and native macOS exported-app execution remain pending.
Older retained platform reports do not qualify the new candidate. Android,
iOS, Web, double precision and other Linux/Windows architectures are outside
this release. Signing and notarization remain distribution-owner steps.

## Package and consumer scope

The target archive is a smoke-test add-on, not a replacement for gdext.
Consumers compile their own source against the pinned snapshot. Packages omit
engine binaries/templates, raw API dumps, generated bindings, source caches,
debug symbols, credentials and private paths. V1 locks/manifests require
explicit migration; mixed identity shapes are rejected.

The historical Rapier 0.8.40 proof is not qualification of the newer physics
fork. Adopting the shared lock and tooling does not fix or certify downstream
physics, unsafe callbacks, deterministic replay, performance or export flows.
