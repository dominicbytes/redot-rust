extends Node

const RESULT_PATH := "res://.results/fixture-result.json"
const SETTINGS_PATH := "user://redot_rust_settings.tres"
const STAGE_SCENE := 1 << 0
const STAGE_MAIN_LOOP := 1 << 2

var _frames: int = 0
var _signal_value: int = -1
var _worker_received: bool = false
var _worker_value: int = -1
var _smoke: RedotRustSmoke
var _assertions: Dictionary = {}
var _failures: Array[String] = []


func _ready() -> void:
	_run_tests()


func _process(_delta: float) -> void:
	_frames += 1


func _run_tests() -> void:
	_record("class_visible", ClassDB.class_exists("RedotRustSmoke"))
	_record("resource_class_visible", ClassDB.class_exists("RedotRustSettings"))
	_record("tool_class_visible", ClassDB.class_exists("RedotRustTool"))

	_smoke = RedotRustSmoke.new()
	_smoke.computed.connect(_on_computed)
	_smoke.worker_completed.connect(_on_worker_completed)
	add_child(_smoke)
	await get_tree().process_frame

	var value: int = _smoke.add_and_emit(20, 22)
	_record("method", value == 42)
	_record("property", _smoke.value == 42)
	_record("signal", _signal_value == 42)
	_record("lifecycle_ready", _smoke.ready_was_seen())
	_record(
		"identity",
		_smoke.compatibility_identity()
		== "Redot 26.2 / Godot API 4.5.2 / single"
	)
	var expected_profile := str(
		ProjectSettings.get_setting("compatibility/expected_library_profile", "debug")
	)
	_record("build_profile", _smoke.build_profile() == expected_profile)

	var stage_mask: int = _smoke.init_stage_mask()
	_record(
		"init_stages",
		(stage_mask & STAGE_SCENE) != 0 and (stage_mask & STAGE_MAIN_LOOP) != 0
	)
	_record("enum", _smoke.echo_mode(2) == 2)

	var input_array: Array[int] = [1, 2, 3]
	var output_array: Array[int] = _smoke.double_array(input_array)
	_record("array", output_array == [2, 4, 6])

	var decorated: Dictionary = _smoke.decorate_dictionary({"name": "redot"})
	_record(
		"dictionary",
		decorated.get("rust") == true
		and decorated.get("count") == 1
		and decorated.get("input", {}).get("name") == "redot"
	)

	var variant_input: Variant = Vector2(3.0, 4.0)
	var variant_output: Variant = _smoke.round_trip_variant(variant_input)
	_record("variant", variant_output == variant_input)

	var callable_result: Variant = _smoke.invoke_callable(
		Callable(self, "_gdscript_double"), 21
	)
	_record("rust_to_gdscript_callable", callable_result == 42)

	var settings := RedotRustSettings.new()
	settings.label = "fixture"
	settings.multiplier = 3
	settings.mode = 2
	_record("resource_apply", _smoke.apply_settings(settings, 14) == 42)
	_record("resource_inspector", _has_editor_property(settings, "multiplier"))

	var save_error := ResourceSaver.save(settings, SETTINGS_PATH)
	var loaded := ResourceLoader.load(
		SETTINGS_PATH,
		"RedotRustSettings",
		ResourceLoader.CACHE_MODE_IGNORE
	) as RedotRustSettings
	_record(
		"resource_round_trip",
		save_error == OK
		and loaded != null
		and loaded.label == "fixture"
		and loaded.multiplier == 3
		and loaded.mode == 2
	)

	var tool := RedotRustTool.new()
	tool.tool_value = 42
	_record(
		"editor_tool",
		tool.tool_identity() == "RedotRustTool/tool" and tool.tool_value == 42
	)
	tool.free()

	var worker_started: bool = _smoke.start_worker(19, 23)
	var worker_wait_frames := 0
	while not _worker_received and worker_wait_frames < 120:
		await get_tree().process_frame
		worker_wait_frames += 1
	_record("worker_started", worker_started)
	_record("worker_handoff", _worker_received and _worker_value == 42)

	while _frames < 2:
		await get_tree().process_frame
	_finish()


func _gdscript_double(value: int) -> int:
	return value * 2


func _has_editor_property(instance: Object, expected_name: String) -> bool:
	for property: Dictionary in instance.get_property_list():
		if str(property.get("name", "")) != expected_name:
			continue
		var usage := int(property.get("usage", 0))
		return (
			(usage & PROPERTY_USAGE_EDITOR) != 0
			and (usage & PROPERTY_USAGE_STORAGE) != 0
		)
	return false


func _record(assertion_name: String, passed: bool) -> void:
	_assertions[assertion_name] = passed
	if not passed:
		_failures.append(assertion_name)


func _on_computed(value: int) -> void:
	_signal_value = value


func _on_worker_completed(value: int) -> void:
	_worker_received = true
	_worker_value = value


func _finish() -> void:
	var passed := _failures.is_empty()
	var payload := {
		"status": "PASS" if passed else "FAIL",
		"frames": _frames,
		"assertions": _assertions,
		"failures": _failures,
		"redot_product": "26.2",
		"godot_api": "4.5.2",
		"library_profile": str(
			ProjectSettings.get_setting(
				"compatibility/expected_library_profile",
				"debug"
			)
		),
	}
	var result_path := RESULT_PATH
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with("--redot-rust-result="):
			result_path = argument.trim_prefix("--redot-rust-result=")
	var file := FileAccess.open(result_path, FileAccess.WRITE)
	if file == null:
		push_error("REDOT_RUST_RESULT cannot write %s" % result_path)
		get_tree().quit(1)
		return
	file.store_string(JSON.stringify(payload, "\t"))
	file.close()
	print("REDOT_RUST_RESULT status=%s frames=%d" % [payload.status, _frames])
	get_tree().quit(0 if passed else 1)
