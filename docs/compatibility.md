# Compatibility matrix

This matrix describes evidence for candidate `0.1.0+redot.26.2.1`. A platform is supported only for the exact Redot/API/gdext/toolchain lock in `redot-api.lock.json`.

| Target | Build | Editor/direct runtime | Exported runtime | Binary audit | Clean archive | Release claim |
|---|---|---|---|---|---|---|
| Windows x86-64 debug | Pass | Pass, 21/21 assertions | Pass, 21/21 assertions | Pass, PE32+, `gdext_rust_init`, no debug CRT | Pass | Supported locally |
| Windows x86-64 release | Pass | Pass, 21/21 assertions | Pass, 21/21 assertions | Pass, PE32+, `gdext_rust_init`, no debug CRT | Pass | Supported locally |
| Linux x86-64 debug | Pass | Pass in Docker with official Redot template, 21/21 | Pass in Docker, 21/21 | Pass, ELF64, required export and system dependencies only | Pass | Supported locally |
| Linux x86-64 release | Pass | Pass in Docker with official Redot template, 21/21 | Pass in Docker, 21/21 | Pass, ELF64, required export and system dependencies only | Pass | Supported locally |
| macOS universal debug | Pass, pinned cargo-zigbuild reproduction | Source-project execution configured in native CI; no retained native pass | Pass export + embedded-library audit; native execution unverified, no CI gate | Pass, universal Mach-O, arm64 + x86-64, `gdext_rust_init`, only `libSystem` dependency | Pass install/inventory | Pending native CI and exported-app execution evidence |
| macOS universal release | Pass, pinned cargo-zigbuild reproduction | Source-project execution configured in native CI; no retained native pass | Pass export + embedded-library audit; native execution unverified, no CI gate | Pass, universal Mach-O, arm64 + x86-64, `gdext_rust_init`, only `libSystem` dependency | Pass install/inventory | Pending native CI and exported-app execution evidence |

## API contract

- Redot product identity: `26.2.0 stable official`.
- Compatible Godot API identity: `4.5.2 stable redot.official`.
- Precision: single.
- GDExtension minimum compatibility: Godot API 4.5.
- Loading policy: restart after replacing a native library; `reloadable = false`.
- Supported Rust binding mode: gdext `api-custom-json`.
- Direct executable mode: deliberately waived after the expected product-version rejection was reproduced.

## Consumer evidence

Godot Rapier Physics `0.8.40` at commit `de3458c23426fe332e491013c4412fe1fb84e778` builds as an unchanged read-only consumer against the same gdext revision and API JSON. Its debug and release 2D runtime fixtures create a Rapier space/body, simulate 90 fixed frames, collide, settle, and report nine passing assertions. Exactly one `rapier_2d_init` entry symbol is exported.

## Automation boundary

The pass results above describe retained evidence under `reports/gauntlet/g03/`, not a verified remote CI run. `.github/workflows/ci.yml` is configured to reproduce the locked API and run source-project editor/runtime plus clean-installed debug/release project fixtures on Windows, Linux, and macOS. Native macOS runtime validation is configured only through CI; the retained evidence does not establish a native macOS pass.

No current CI job obtains export templates, invokes `tools/export_fixture.py`, audits exported products, or launches exported applications. The retained Windows/Linux exported-runtime passes are separate from CI coverage. Cross-building or exporting a macOS `.app` is not native runtime evidence; native macOS exported-app execution remains unverified and is not established by a green workflow.
