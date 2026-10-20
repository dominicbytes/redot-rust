# Redot Rust Compatibility smoke add-on

This target archive contains the `redot-rust-compat` smoke GDExtension for the exact official Redot 26.2 and 26.3 profiles in its v2 manifest. It validates the reusable Rust binding path; it is not a replacement for the `godot-rust` crate.

Install by extracting the archive at a Redot project root, preserving `addons/redot-rust-compat/`. The package includes debug and release native libraries for the target named in `package-manifest.json`. Redot selects the correct library from `redot_rust_smoke.gdextension`.

For scripted installation with traversal, collision, inventory, size, and SHA-256 checks, use `tools/install_package.py` from the source repository.

After installation, restart Redot. The add-on registers `RedotRustSmoke`, `RedotRustSettings`, and `RedotRustTool`. A minimal real call is:

```gdscript
var smoke := RedotRustSmoke.new()
add_child(smoke)
assert(smoke.add_and_emit(20, 22) == 42)
```

The same debug/release libraries use the unchanged 26.2 binding ABI (Godot API 4.5.2, single precision). Struct, 26.3-only enum values and 26.3-only classes are unsupported. The v2 manifest records `binding_abi`, `certified_engines` and the coverage restriction. Runtime certification never expands the generated surface.

This archive supports only its manifest target and exact runtime profiles. The official Windows 26.3 editor initialization exception remains visible in source-tooling reports; installed and exported runtime diagnostics remain strict. Platform qualification limits are described beside the lock. See `docs/compatibility.md` and `docs/limitations.md` inside the add-on.

See `NOTICE.md`, `SOURCE.json`, and `package-manifest.json` for the public source repository and corresponding source revision. `dependency-inventory.json` distinguishes linked dependencies from build-only packages; `third-party/` contains the linked crates' license and notice texts.
