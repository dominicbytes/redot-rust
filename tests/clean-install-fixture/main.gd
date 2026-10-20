extends Node

const DEFAULT_RESULT_PATH := "user://redot_rust_clean_install_result.json"

var _frames: int = 0
var _signal_value: int = -1
var _assertions: Dictionary = {}
var _failures: Array[String] = []


func _process(_delta: float) -> void:
	_frames += 1


func _ready() -> void:
	_record("class_visible", ClassDB.class_exists("RedotRustSmoke"))
	var smoke := ClassDB.instantiate("RedotRustSmoke") as Node
	if smoke == null:
		_record("instance", false)
		_finish()
		return
	_record("instance", true)
	smoke.connect("computed", _on_computed)
	add_child(smoke)
	await get_tree().process_frame
	_record("method", int(smoke.call("add_and_emit", 20, 22)) == 42)
	_record("signal", _signal_value == 42)
	_record(
		"identity",
		str(smoke.call("compatibility_identity"))
		== "Redot 26.2 / Godot API 4.5.2 / single"
	)
	_record("lifecycle", bool(smoke.call("ready_was_seen")))
	var expected_profile := str(
		ProjectSettings.get_setting("compatibility/expected_library_profile", "debug")
	)
	_record("build_profile", str(smoke.call("build_profile")) == expected_profile)
	while _frames < 2:
		await get_tree().process_frame
	_finish()


func _on_computed(value: int) -> void:
	_signal_value = value


func _record(assertion_name: String, passed: bool) -> void:
	_assertions[assertion_name] = passed
	if not passed:
		_failures.append(assertion_name)


func _finish() -> void:
	var result_path := DEFAULT_RESULT_PATH
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with("--redot-rust-result="):
			result_path = argument.trim_prefix("--redot-rust-result=")
	var passed := _failures.is_empty()
	var payload := {
		"assertion_schema_version": 1,
		"fixture_type": "clean-install",
		"status": "PASS" if passed else "FAIL",
		"frames": _frames,
		"assertions": _assertions,
		"failures": _failures,
	}
	var file := FileAccess.open(result_path, FileAccess.WRITE)
	if file == null:
		push_error("REDOT_RUST_CLEAN_RESULT cannot write %s" % result_path)
		get_tree().quit(1)
		return
	file.store_string(JSON.stringify(payload, "\t"))
	file.close()
	print("REDOT_RUST_CLEAN_RESULT status=%s frames=%d" % [payload.status, _frames])
	get_tree().quit(42 if passed else 1)
