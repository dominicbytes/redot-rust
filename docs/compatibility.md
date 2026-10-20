# Compatibility matrix

Candidate `0.2.0` uses one unchanged binding ABI and two exact official runtime
targets. The full archive, executable, raw API and interface identities are in
`redot-api.lock.json`; [schema v2](lock-schema.md) defines their separate roles.

| Identity | Value |
|---|---|
| Binding snapshot | Redot 26.2, `4f5b14abade2239104847d03d8f9056e4467cfcd` |
| Compatible Godot API | 4.5.2, single precision |
| Binding mode | gdext 0.5.5 at `637cef73172bba23850131acd8583b0c72ebf0c7`, `api-custom-json`, pinned bundled interface |
| Runtime `redot-26.2` | `26.2.stable.official.4f5b14aba` |
| Runtime `redot-26.3` | `26.3.stable.official.85e520394` |
| Coverage | Snapshot surface only; no Struct, engine-only enum values or engine-only classes |

| Target | Engine profiles | Candidate qualification |
|---|---|---|
| Windows x86-64 | `redot-26.2` | Debug/release editor + smoke 22/22, clean install 8/8, binary audit and v2 package validation pass |
| Windows x86-64 | `redot-26.3` | Debug/release editor + smoke 22/22, clean install 8/8, binary audit and v2 package validation pass; completed imports record exact shutdown exceptions |
| Linux x86-64 | Both | Native CI configured; candidate execution pending |
| macOS universal | Both | Both architectures built/audited and native host execution configured in CI; candidate execution pending |

The official 26.3 editor's shutdown defect reproduces without an extension;
the leak form is measured on Windows, Linux and macOS, the abnormal exit on
Windows. Completed editor initialization may record `engine_import_exception`
as `shutdown-leaks` (exit zero, exactly seven known lines) or
`shutdown-abnormal-exit` (Windows `0xC0000005`, with only the allowlisted crash
handler and any known shutdown lines). The handler may truncate immediately
after its 64-equals separator or at a later complete line. Both forms require
the locked binary/version, every extension/tool marker, completed scan/layout
and freshly recreated matching extension/class caches. Reports retain the
original exit, diagnostics and truncation status; an exception is not a clean
shutdown. Unknown diagnostics, incomplete work, other exits/engines/platforms
and bare abnormal exits fail. No Linux/macOS signal exception is enabled.
Runtime, installed and exported gates never use either form.

A clean install uses a separate successful import report and verifies its cache checksums
before strict runtime execution. Pinning an official shutdown-fixed engine is
the removal condition.

## Qualification boundary

The six CI lanes derive profiles, download URLs, checksums and executable names
from the lock, and reproduce binding and runtime dumps separately. Each lane
requires Python checks, Rust format/Clippy/tests, debug/release builds, binary
audits, editor/runtime smoke, packages and clean installs. Hosted CI has not been
run as part of this candidate's local Windows qualification.

No CI lane downloads export templates, exports applications, audits exported
products or executes exported applications. No candidate export-runtime pass is
claimed. Retained `reports/gauntlet/g03/` evidence belongs to 0.1/26.2 and does not
certify 0.2 or 26.3. Cross-building a macOS library is not native execution.

## Consumer boundary

The historical unchanged-source Rapier baseline remains 0.8.40 at
`de3458c23426fe332e491013c4412fe1fb84e778`. Its old nine-assertion evidence is
historical; updated consumer harnesses use assertion schema 2 and also check the
actual runtime identity. It does not qualify the owner's 0.35.2-based physics
fork. That fork must adopt this contract and rerun its own backend, physics,
export and packaging checks against its selected engine profile.
