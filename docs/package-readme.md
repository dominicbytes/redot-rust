# Redot Rust Compatibility smoke add-on

This target archive contains the `redot-rust-compat` smoke GDExtension for Redot 26.2. It validates the reusable Rust binding path; it is not a replacement for the `godot-rust` crate.

Install by extracting the archive at a Redot project root, preserving `addons/redot-rust-compat/`. The package includes debug and release native libraries for the target named in `package-manifest.json`. Redot selects the correct library from `redot_rust_smoke.gdextension`.

For scripted installation with traversal, collision, inventory, size, and SHA-256 checks, use `tools/install_package.py` from the source repository.

After installation, restart Redot. The add-on registers `RedotRustSmoke`, `RedotRustSettings`, and `RedotRustTool`. A minimal real call is:

```gdscript
var smoke := RedotRustSmoke.new()
add_child(smoke)
assert(smoke.add_and_emit(20, 22) == 42)
```

This archive supports only its manifest target and the exact Redot/API lock included beside it. See `docs/compatibility.md` and `docs/limitations.md` inside the add-on.

See `NOTICE.md`, `SOURCE.json`, and `package-manifest.json` for the public source repository and corresponding source revision. `dependency-inventory.json` distinguishes linked dependencies from build-only packages; `third-party/` contains the linked crates' license and notice texts.
