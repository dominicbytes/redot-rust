# Compatibility matrix

This matrix describes evidence for candidate `0.1.0+redot.26.2.1`. A platform is supported only for the exact Redot/API/gdext/toolchain lock in `redot-api.lock.json`.

| Target | Build | Editor/direct runtime | Exported runtime | Binary audit | Clean archive | Release claim |
|---|---|---|---|---|---|---|
| Windows x86-64 debug | Pass | Pass, 21/21 assertions | Pass, 21/21 assertions | Pass, PE32+, `gdext_rust_init`, no debug CRT | Pass | Supported locally |
| Windows x86-64 release | Pass | Pass, 21/21 assertions | Pass, 21/21 assertions | Pass, PE32+, `gdext_rust_init`, no debug CRT | Pass | Supported locally |
| Linux x86-64 debug | Pass | Pass in Docker with official Redot template, 21/21 | Pass in Docker, 21/21 | Pass, ELF64, required export and system dependencies only | Pass | Supported locally |
| Linux x86-64 release | Pass | Pass in Docker with official Redot template, 21/21 | Pass in Docker, 21/21 | Pass, ELF64, required export and system dependencies only | Pass | Supported locally |
| macOS universal debug | Pass, pinned cargo-zigbuild reproduction | Native CI gate implemented; not executable on this Windows host | Pass export + embedded-library audit; native execution requires macOS | Pass, universal Mach-O, arm64 + x86-64, `gdext_rust_init`, only `libSystem` dependency | Pass install/inventory | Pending native macOS CI run |
| macOS universal release | Pass, pinned cargo-zigbuild reproduction | Native CI gate implemented; not executable on this Windows host | Pass export + embedded-library audit; native execution requires macOS | Pass, universal Mach-O, arm64 + x86-64, `gdext_rust_init`, only `libSystem` dependency | Pass install/inventory | Pending native macOS CI run |

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

`.github/workflows/ci.yml` reproduces the locked API and runs source plus clean-installed debug/release fixtures on Windows, Linux, and macOS. The workflow is implemented locally but cannot be executed until a downstream remote is authorized. Cross-building or exporting a macOS `.app` is not treated as native runtime evidence.
