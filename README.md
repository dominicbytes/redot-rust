# Redot Rust Compatibility

`redot-rust-compat` is the pinned build, validation, packaging, and test path for Rust GDExtensions on Redot 26.2. It keeps Redot's product version (`26.2`) separate from the compatible Godot API identity (`4.5.2`) and uses `godot-rust/gdext` JSON mode without a fork.

The repository includes:

- a fail-closed Redot API identity and hash lock;
- a portable Cargo launcher for `api-custom-json`;
- a Rust smoke extension covering registration, all init stages, methods, properties, typed signals, enums, arrays, dictionaries, callables, `Variant`, custom resources, editor tooling, lifecycle, bidirectional calls, and worker-thread handoff;
- deterministic Redot fixtures for editor, runtime, export, and clean-package tests;
- PE, ELF, and universal Mach-O architecture/dependency/export auditing;
- deterministic target archives with safe installation and manifest verification;
- a read-only Rapier 0.8.40 consumer proof with no source edits or copied binding patches.

## Locked compatibility

| Component | Locked value |
|---|---|
| Redot | `26.2.stable.official.4f5b14aba` |
| Redot engine commit | `4f5b14abade2239104847d03d8f9056e4467cfcd` |
| Compatible Godot API | `4.5.2`, single precision |
| `extension_api.json` SHA-256 | `177E7796166929B2193C9CCE2FD32F59601A0147D0D1E7FE904B94E8F69F6577` |
| gdext | `0.5.5` at `637cef73172bba23850131acd8583b0c72ebf0c7` |
| Rust | `1.94.0` |
| Candidate version | `0.1.0+redot.26.2.1` |

Windows x86-64 and Linux x86-64 debug/release builds, editor loads, runtime fixtures, exports, binary audits, and clean-install flows are locally proven. macOS universal debug/release builds, unsigned exports, bundle inventories, static binary audits, target archives, and clean installations are also locally proven; native execution is implemented in CI and must pass on macOS before a three-platform public release is claimed. See [compatibility](docs/compatibility.md) and [limitations](docs/limitations.md).

## Prerequisites

- the exact Redot 26.2 editor and export templates;
- Rust/Cargo 1.94.0;
- Python 3.11 or newer;
- Docker for Linux runtime checks from a Windows host;
- a generated Redot `extension_api.json` matching `redot-api.lock.json`.

The generated API and engine binaries are intentionally not distributed.

## Quick start

Set the editor and Cargo paths for the current shell:

```powershell
$env:REDOT_BIN = "<path-to-redot-editor>"
$Cargo = "<path-to-rust-1.94-cargo>"
```

Reproduce and validate the locked API artifacts:

```powershell
python tools\dump_redot_api.py `
  --redot-bin $env:REDOT_BIN `
  --output-dir ..\build\redot-rust-compat\api `
  --lock redot-api.lock.json

python tools\inspect_redot_api.py --lock redot-api.lock.json
```

Build through the adapter so Cargo receives only the validated JSON path:

```powershell
python tools\configure_cargo.py --lock redot-api.lock.json -- `
  $Cargo build --workspace

python tools\configure_cargo.py --lock redot-api.lock.json -- `
  $Cargo build --workspace --release
```

Stage and run the Windows fixture:

```powershell
python tools\stage_extension.py `
  --target-dir target `
  --project-dir fixtures\redot-project

python tools\run_fixture.py fixtures\redot-project `
  --redot-bin $env:REDOT_BIN `
  --report-dir reports\local-smoke
```

Run the source checks:

```powershell
python -m unittest discover -s tests -p "test_*.py"
python tools\configure_cargo.py --lock redot-api.lock.json -- `
  $Cargo fmt --all -- --check
python tools\configure_cargo.py --lock redot-api.lock.json -- `
  $Cargo clippy --workspace --all-targets --all-features -- -D warnings
python tools\configure_cargo.py --lock redot-api.lock.json -- `
  $Cargo test --workspace --all-features
```

Reproduce both universal macOS libraries from Windows or Linux with the pinned container:

```powershell
python tools\build_macos_universal.py `
  --api <validated-extension-api.json> `
  --output-dir ..\build\redot-rust-compat\macos-universal
```

This cross-build performs a structural Mach-O audit. It does not replace the native macOS runtime job in `.github/workflows/ci.yml`.

## Use from another Rust GDExtension

Pin the same gdext revision and enable JSON mode:

```toml
[dependencies]
godot = { git = "https://github.com/godot-rust/gdext", rev = "637cef73172bba23850131acd8583b0c72ebf0c7", features = ["api-custom-json"] }
```

Invoke that consumer's Cargo command through `tools/configure_cargo.py`, or reproduce its validated `GDRUST_GODOT_API_JSON` environment contract. Do not set `GDRUST_GODOT_INTERFACE_JSON` for this lock: Redot 26.2 exposes the C interface header but not an interface JSON dump, and the pinned gdext bundled interface is the supported path.

The Rapier proof applies a workspace-level `[patch.crates-io]` to this exact gdext revision and leaves the pinned Rapier source checkout byte-for-byte unchanged. Its report is under `reports/gauntlet/g02/final/`.

## Package and install

Create a target archive after both profile binaries exist:

```powershell
python tools\package_release.py `
  --target windows-x86_64 `
  --debug-binary <debug-dll> `
  --release-binary <release-dll> `
  --output-dir dist
```

Install only after validating every archive member and hash:

```powershell
python tools\install_package.py `
  dist\<archive>.zip `
  <clean-redot-project> `
  --target windows-x86_64
```

The archive installs a test add-on under `addons/redot-rust-compat/`. It is evidence that the build contract works; application-specific Rust source remains in the consuming project.

## Direct-binary mode

`api-custom` is intentionally not used. Its executable-version parser sees Redot product major `26` and rejects it because gdext correctly expects Godot API major `4`. The reproduced negative test is retained, while JSON mode passes without weakening version checks or patching gdext.

## Maintenance

`tools/sync_gdext.py` performs a read-only pin-update rehearsal and reports API, dependency, license, patch, and consumer impact. It never changes the lock automatically. Any future revision update must rerun the smoke, Rapier, desktop export, binary audit, and package gates.

New code is MPL-2.0. See [NOTICE.md](NOTICE.md), [UPSTREAM_LOCK.md](UPSTREAM_LOCK.md), and the generated dependency report under `reports/release/`.

## Notes

I vibe coded this in GPT Sol 5.6. Use at your own risk. Actual programmers are welcome to submit PR's and feedback. I made this on a lark. Might make the Rust Cultists happy.

## About Dominic Bytes

Greetings! I am Dominic Bytes, the synth walker. I hail from the distant future. Where brains occupy robot bodies, time travel is a trip to the corner store, and the neon glow of our attire is powered by the light of our souls. Join me on a 1.21 gigawatt powered journey of chill vibes with gaming, anime, movies, and more!

- [Website](https://dominicbytes.carrd.co/)
- [X](https://x.com/DominicBytes)
- [Twitch](https://www.twitch.tv/dominicbytes)
- [YouTube](http://www.youtube.com/@DominicBytes)
- [Kick](https://kick.com/dominicbytes)
