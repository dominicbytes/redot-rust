// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.

use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::mpsc::{self, Receiver, TryRecvError};

use godot::prelude::*;

const STAGE_SCENE: u32 = 1 << 0;
const STAGE_EDITOR: u32 = 1 << 1;
const STAGE_MAIN_LOOP: u32 = 1 << 2;

static INIT_STAGE_MASK: AtomicU32 = AtomicU32::new(0);

#[derive(Default, GodotConvert, Var, Export, Clone, Copy, Eq, PartialEq, Debug)]
#[godot(via = i64)]
#[repr(i64)]
enum SmokeMode {
    #[default]
    Idle = 0,
    Active = 1,
    Complete = 2,
}

#[derive(GodotClass)]
#[class(init, base = Resource)]
struct RedotRustSettings {
    #[export]
    label: GString,

    #[export]
    multiplier: i64,

    #[export]
    mode: SmokeMode,
}

#[derive(GodotClass)]
#[class(tool, init, base = Node)]
struct RedotRustTool {
    #[export]
    tool_value: i64,
}

#[godot_api]
impl RedotRustTool {
    #[func]
    fn tool_identity(&self) -> GString {
        "RedotRustTool/tool".into()
    }
}

#[derive(GodotClass)]
#[class(base = Node)]
struct RedotRustSmoke {
    base: Base<Node>,

    #[var]
    value: i64,

    ready_seen: bool,
    worker_receiver: Option<Receiver<i64>>,
}

#[godot_api]
impl INode for RedotRustSmoke {
    fn init(base: Base<Node>) -> Self {
        Self {
            base,
            value: 0,
            ready_seen: false,
            worker_receiver: None,
        }
    }

    fn ready(&mut self) {
        self.ready_seen = true;
        godot_print!("REDOT_RUST_SMOKE lifecycle=ready");
    }

    fn process(&mut self, _delta: f64) {
        let worker_result = self
            .worker_receiver
            .as_ref()
            .map(|receiver| receiver.try_recv());

        match worker_result {
            Some(Ok(value)) => {
                self.worker_receiver = None;
                self.base_mut()
                    .emit_signal("worker_completed", &[value.to_variant()]);
            }
            Some(Err(TryRecvError::Disconnected)) => {
                self.worker_receiver = None;
                self.base_mut()
                    .emit_signal("worker_completed", &[(-1_i64).to_variant()]);
            }
            Some(Err(TryRecvError::Empty)) | None => {}
        }
    }
}

#[godot_api]
impl RedotRustSmoke {
    #[signal]
    fn computed(value: i64);

    #[signal]
    fn worker_completed(value: i64);

    #[func]
    fn add_and_emit(&mut self, left: i64, right: i64) -> i64 {
        let value = left + right;
        self.value = value;
        self.base_mut()
            .emit_signal("computed", &[value.to_variant()]);
        value
    }

    #[func]
    fn echo_mode(&self, mode: SmokeMode) -> SmokeMode {
        mode
    }

    #[func]
    fn double_array(&self, values: Array<i64>) -> Array<i64> {
        Array::from_iter(values.iter_shared().map(|value| value * 2))
    }

    #[func]
    fn decorate_dictionary(&self, values: VarDictionary) -> VarDictionary {
        vdict! {
            "count" => values.len() as i64,
            "input" => &values,
            "rust" => true,
        }
    }

    #[func]
    fn round_trip_variant(&self, value: Variant) -> Variant {
        value
    }

    #[func]
    fn invoke_callable(&self, callback: Callable, value: i64) -> Variant {
        callback.callv(&varray![value])
    }

    #[func]
    fn apply_settings(&self, settings: Gd<RedotRustSettings>, value: i64) -> i64 {
        let multiplier = settings.bind().multiplier;
        value * multiplier
    }

    #[func]
    fn ready_was_seen(&self) -> bool {
        self.ready_seen
    }

    #[func]
    fn init_stage_mask(&self) -> i64 {
        INIT_STAGE_MASK.load(Ordering::SeqCst) as i64
    }

    #[func]
    fn start_worker(&mut self, left: i64, right: i64) -> bool {
        if self.worker_receiver.is_some() {
            return false;
        }

        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let _ = sender.send(left + right);
        });
        self.worker_receiver = Some(receiver);
        self.base_mut().set_process(true);
        true
    }

    #[func]
    fn binding_abi(&self) -> GString {
        env!("REDOT_RUST_BINDING_ABI").into()
    }

    #[func]
    fn build_profile(&self) -> GString {
        if cfg!(debug_assertions) {
            "debug".into()
        } else {
            "release".into()
        }
    }
}

struct RedotRustCompat;

#[gdextension]
unsafe impl ExtensionLibrary for RedotRustCompat {
    fn on_stage_init(stage: InitStage) {
        let stage_bit = match stage {
            InitStage::Scene => STAGE_SCENE,
            InitStage::Editor => STAGE_EDITOR,
            InitStage::MainLoop => STAGE_MAIN_LOOP,
            _ => 0,
        };
        INIT_STAGE_MASK.fetch_or(stage_bit, Ordering::SeqCst);
        godot_print!("REDOT_RUST_INIT stage={stage:?}");

        if stage == InitStage::Editor {
            let mut tool = RedotRustTool::new_alloc();
            let identity = tool.call("tool_identity", &[]).to::<GString>();
            let status = if identity == "RedotRustTool/tool" {
                "PASS"
            } else {
                "FAIL"
            };
            godot_print!("REDOT_RUST_EDITOR_TOOL status={status}");
            tool.free();
        }
    }

    fn on_stage_deinit(stage: InitStage) {
        godot_print!("REDOT_RUST_DEINIT stage={stage:?}");
    }
}
