# Limitations and operating rules

## Exact-version support

The compatibility claim is intentionally narrow: Redot `26.2.stable.official.4f5b14aba`, Godot API `4.5.2`, single precision, gdext commit `637cef73172bba23850131acd8583b0c72ebf0c7`, and Rust 1.94.0. A different Redot build, API dump, precision, architecture, gdext revision, or Rust lock is a new candidate and must pass the gates again.

## Restart-based native iteration

Redot 26.2 is unstable when this GDExtension is marked reloadable, and a forced five-frame first editor import can terminate during shutdown. The supported loop uses `reloadable = false`, restarts the editor after native-library replacement, and allows up to 120 frames for a clean first import.

## JSON mode only

The supported path is gdext `api-custom-json`. Direct `api-custom` parses `redot --version` as major 26 and correctly refuses to treat that as a Godot API major. The direct-mode failure is kept as negative evidence; no assertion was weakened and no gdext fork was introduced.

Redot 26.2 does not expose a GDExtension interface JSON dump. The pinned gdext bundled interface is therefore required. The C interface header remains locked and audited separately.

## Platform scope

Windows x86-64 and Linux x86-64 debug/release are locally built and run. macOS universal debug/release binaries are locally cross-built, statically audited, exported, checked inside the app bundles, packaged, and clean-installed. Native editor, exported-app, and clean-install execution still require the implemented macOS CI job; the candidate is not advertised as a completed three-platform public release until that job passes. Android, iOS, Web, headless/server-specific packages, double precision, and non-x86-64 Linux/Windows are out of scope for this candidate.

No code signing or notarization is performed. Those are distribution-owner steps after unsigned functional validation.

## Package scope

The binary archive is a smoke-test add-on, not a runtime that replaces gdext. A consuming Rust extension still compiles its own source against the pinned dependency and exact API JSON. Archives deliberately exclude Redot binaries/templates, complete API dumps, generated bindings, Cargo source caches, debug symbols, credentials, and developer-specific absolute paths.

## Rapier boundary

The consumer proof is not a Rapier port release. It proves that the shared adapter supports the most recent compatible Rapier baseline found (`0.8.40`) without modifying its source or copying binding fixes. Later Rapier versions that use newer Godot APIs remain Priority 7 work.
