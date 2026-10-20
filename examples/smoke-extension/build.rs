fn main() {
    println!("cargo:rerun-if-changed=build.rs");

    // The macOS linker records the output path as the dylib's install name
    // and the absolute paths of the linked objects in its debug map. Both
    // leak the build machine's directories and the install name is not
    // relocatable, so use a loader-relative name and omit the debug map.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rustc-cdylib-link-arg=-Wl,-install_name,@rpath/libredot_rust_smoke.dylib");
        println!("cargo:rustc-cdylib-link-arg=-Wl,-S");
    }
}
