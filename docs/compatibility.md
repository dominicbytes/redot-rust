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
| Windows x86-64 | `redot-26.3` | Debug editor + smoke and clean install pass with the visible import exception; release editor shutdown fails, while separate strict smoke 22/22 and installed-fixture 8/8 runtime checks pass |
| Linux x86-64 | Both | Native CI configured; candidate execution pending |
| macOS universal | Both | Both architectures built/audited and native host execution configured in CI; candidate execution pending |

The official Windows 26.3 editor's seven shutdown-leak diagnostics reproduce
without an extension. An exit-zero editor initialization can pass only with
the exact locked engine, all extension/tool markers, completed scan/layout and
fresh caches. Its report records `engine_import_exception`; it is not a clean
log. Extra diagnostics, incomplete initialization, abnormal exits, other engines
and other platforms fail. Installed and exported runtime gates never use it.
The official release editor also reproduces an abnormal shutdown after completed initialization, without a preceding scan-thread warning. That form remains rejected; this candidate has no complete 26.3 release qualification. The engine issue also reproduces on an empty project.

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
