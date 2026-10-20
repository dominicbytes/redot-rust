# Upstream lock: Redot-Rust compatibility layer

| Field | Value |
|---|---|
| Project | Redot-Rust compatibility layer |
| Project repository | https://github.com/dominicbytes/redot-rust |
| Upstream repository | https://github.com/godot-rust/gdext |
| Selected branch | master |
| Pinned upstream commit | `637cef73172bba23850131acd8583b0c72ebf0c7` |
| Pinned crate version | `godot` 0.5.5 with `api-custom-json` |
| Candidate baseline | This repository, version `0.2.0` |
| License | New code MPL-2.0; godot-rust upstream MPL-2.0 |
| Rust toolchain | Rust `1.94.0`; rustc commit `4a4ef493e3a1488c6e321570238084b38948f6db`; Windows MSVC, Linux GNU, and Apple arm64/x86-64 targets |
| Binding ABI | Unchanged Redot 26.2 / Godot API 4.5.2 / single precision |
| Runtime targets | Exact official Redot 26.2 and 26.3 profiles in schema v2 |
| Coverage restriction | No Struct, engine-only enum values or engine-only classes |
| Redot engine commit | `4f5b14abade2239104847d03d8f9056e4467cfcd` |
| Actual editor SHA-256 | `10DFBEFC273536F0C65903D6429E531C30133820BF0FA842F2D6E6FB81DB1D42` |
| Console launcher SHA-256 | `5633D02A28A73514084DF6A60FFE01FABDBBB9AC5E28FDFD590ED47277F51989` |
| `extension_api.json` SHA-256 | `177E7796166929B2193C9CCE2FD32F59601A0147D0D1E7FE904B94E8F69F6577` |
| `gdextension_interface.h` SHA-256 | `4CD695E86B92E2BF4E60BBE19CE137FAF41205DA1CF94F29E069AFEC0F7BF320` |
| Interface JSON | Not exposed by the installed Redot 26.2 build; pinned gdext bundled interface is the supported path |
| Quick-start generated artifacts | `build/api/` (not distributed) |
| Identity lock | `redot-api.lock.json` |
| Patch queue | Empty; JSON mode starts with unmodified gdext |
| Rapier consumer | Godot Rapier Physics `0.8.40`, commit `de3458c23426fe332e491013c4412fe1fb84e778`, unchanged source |
| macOS builder | `ghcr.io/rust-cross/cargo-zigbuild@sha256:b8364c2c60cdcc9b95c402d17654bff517410926a35678bd89dd924b8158d6ae` |
| Downstream branch | `main` |

## Official Redot inputs

All six editor archives were downloaded from their official release URLs and hashed. Exact executable members and their measured hashes are in `certified_engines.*.assets`. Template rows below are retained 26.2 provenance, not new candidate export qualification.

| Artifact | SHA-256 |
|---|---|
| `Redot_v26.2-stable_windows_win64.zip` | `4644C7591BBE8019B861DEB0CCDB64FD4F59A88514ABF7C788CF176C259855AF` |
| `Redot_v26.2-stable_linux_x64.zip` | `F474D890806C41AF15513CF5A8600243E241882E11B68DBB95660E3465B5B1E4` |
| `Redot_v26.2-stable_macos_universal.zip` | `128CB7BCA69B95A3998A84E72B0990A302CE5B1919DFB56CFDC709C2C87925AE` |
| `Redot_v26.3-stable_windows_win64.zip` | `2C6C7B4E96A88CA44EB413D9A5D43B8468B60A346C2448854C1A0C6BC9BAAB76` |
| `Redot_v26.3-stable_linux_x64.zip` | `7D5155B80F4262F0A394C5040E539BB330CB63DD147BEDD9172D83EC2EFDBD91` |
| `Redot_v26.3-stable_macos_universal.zip` | `BF781AE53DFAA4BBC6AADD21EBF2C69DA3681ED85894D3AB2A816F0B03755A34` |
| `Redot_v26.2-stable_export_templates.tpz` | `FA8C648BD9A9D911DAFFBCC4E1786D0248794571A7E4ED954444197C809079EA` |
| Extracted `macos.zip` template | `E41458D601C7129D984192FF025CB2D13B54D25835A78ADCEFFE2FD6B43E5ED4` |

The 26.3 raw API hash is `4C709D5920855DFE7294D01B9899D21AEC4E94D3DF9FC46F7AA90AD8FFF5A38E`; its C interface hash is `558C1F54F5E37AFDF259FEC29D6EB72DA83781AFDCE48E51A1783942F9CF9895`. Its exact engine commit is `85e5203945af15755dd2f66cb6fdf7545fd88c0b`. These are audit provenance; the binding hashes above remain unchanged.

## Historical 0.1 smoke-extension binaries

The following artifacts predate 0.2 and do not qualify the current candidate.

| Target/profile | SHA-256 | Size (bytes) |
|---|---|---:|
| Windows x86-64 debug | `953A206255432FA93284FFB5EEA38BAC86D60B35D934E601977DA7EB7CA8E18C` | 3,796,992 |
| Windows x86-64 release | `DD193645005EA4449C1C5EF7C18F35D5677AE072820080BCB1BC78318865A0D9` | 4,012,544 |
| Linux x86-64 debug | `BB18D994A6EC81E6B1BBFED1467CBA7FB0F9EBC0AD166D96F077EC0E8494C26F` | 93,497,608 |
| Linux x86-64 release | `131154F6DE8CB3516AA547A1FECBB644803E162007F259F6991F8F38C5C4BF91` | 4,902,776 |
| macOS universal debug | `C808771F63BF46F7FFA34330DF7349BE0A680AD70F4FDA338D1F0F866227667E` | 113,955,416 |
| macOS universal release | `080CF09FC027795D16BCFE89060BCEDD250C06C13777D5691EE5AB1760A89C08` | 34,397,952 |

The macOS builder was also rerun from a fresh output/cache directory and produced independently audited universal binaries. Native build products are not promised to be byte-for-byte reproducible; the deterministic release archive contract applies to a fixed pair of input binaries.

This lock defines the build inputs for local and CI reproduction. Retained evidence and the CI coverage boundary are documented in `docs/compatibility.md` and `docs/limitations.md`.
