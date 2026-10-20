# Notices

Copyright 2026 DominicBytes.

Original source code in this repository is licensed under the Mozilla Public License 2.0. See `LICENSE`.

This project depends on `godot-rust/gdext`, licensed under MPL-2.0, at the exact revision recorded in `UPSTREAM_LOCK.md`. No gdext source file is copied or modified by this implementation. Each archive includes `dependency-inventory.json` with the full locked inventory and the target's linked dependency closure. License and notice texts for linked third-party crates are copied unchanged from the exact local Cargo registry or git cache into `third-party/`; build-only and procedural macro dependencies are inventoried separately from linked code.

The read-only consumer proof uses Godot Rapier Physics 0.8.40, licensed under the MIT License, at commit `de3458c23426fe332e491013c4412fe1fb84e778`. Rapier source and binaries are not included in the compatibility add-on archives.

Redot Engine is a separate project. Redot executables, export templates, generated API/interface artifacts, and generated Rust bindings are fingerprinted or consumed locally but are not redistributed in this repository's add-on archives.

The optional local macOS cross-build uses the `cargo-zigbuild` container pinned by digest in `UPSTREAM_LOCK.md`. The container image and Apple SDK content are build inputs only and are not redistributed in target archives.

Corresponding source is available at https://github.com/dominicbytes/redot-rust. Each archive records the exact source revision in `SOURCE.json` and `package-manifest.json`.

Target archives include only this project's smoke GDExtension binaries, configuration, lock metadata, documentation, dependency inventory, third-party license texts, MPL-2.0 license, and notices. They exclude debug symbols, private paths, credentials, source caches, and restricted engine artifacts.
