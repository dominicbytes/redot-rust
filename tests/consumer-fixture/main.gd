# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

extends Node2D

const RESULT_PATH := "res://.results/consumer-result.json"
const EXPECTED_ENGINE := "Rapier2D"

var assertions: Dictionary[String, bool] = {}
var falling_body: RigidBody2D
var frames := 0
var peak_active_objects := 0


func _ready() -> void:
	print("REDOT_RAPIER_CONSUMER lifecycle=ready")
	_setup()


func _setup() -> void:
	assertions["project_selects_rapier"] = (
		ProjectSettings.get_setting("physics/2d/physics_engine", "") == EXPECTED_ENGINE
	)
	assertions["server_class_registered"] = ClassDB.class_exists("RapierPhysicsServer2D")
	assertions["factory_class_registered"] = ClassDB.class_exists("RapierPhysicsServerFactory2D")
	assertions["active_server_is_rapier"] = PhysicsServer2D.is_class("RapierPhysicsServer2D")

	var floor_body := StaticBody2D.new()
	floor_body.position = Vector2(0.0, 120.0)
	var floor_collision := CollisionShape2D.new()
	var floor_shape := RectangleShape2D.new()
	floor_shape.size = Vector2(240.0, 20.0)
	floor_collision.shape = floor_shape
	floor_body.add_child(floor_collision)
	add_child(floor_body)

	falling_body = RigidBody2D.new()
	falling_body.position = Vector2.ZERO
	var body_collision := CollisionShape2D.new()
	var body_shape := RectangleShape2D.new()
	body_shape.size = Vector2(20.0, 20.0)
	body_collision.shape = body_shape
	falling_body.add_child(body_collision)
	add_child(falling_body)


func _physics_process(_delta: float) -> void:
	peak_active_objects = maxi(
		peak_active_objects,
		PhysicsServer2D.get_process_info(PhysicsServer2D.INFO_ACTIVE_OBJECTS)
	)
	frames += 1
	if frames < 90:
		return
	set_physics_process(false)
	_finish()


func _finish() -> void:
	var body_rid := falling_body.get_rid()
	var space_rid := PhysicsServer2D.body_get_space(body_rid)
	var active_objects := PhysicsServer2D.get_process_info(
		PhysicsServer2D.INFO_ACTIVE_OBJECTS
	)
	assertions["body_rid_valid"] = body_rid.is_valid()
	assertions["body_uses_active_space"] = (
		space_rid.is_valid() and PhysicsServer2D.space_is_active(space_rid)
	)
	assertions["server_reported_active_objects"] = peak_active_objects >= 1
	assertions["rigid_body_fell"] = falling_body.position.y > 1.0
	assertions["rigid_body_resting_on_floor"] = falling_body.position.y < 111.0

	var passed := true
	for value: bool in assertions.values():
		passed = passed and value

	var result := {
		"schema_version": 1,
		"status": "PASS" if passed else "FAIL",
		"frames": frames,
		"assertions": assertions,
		"active_server_class": PhysicsServer2D.get_class(),
		"active_objects": active_objects,
		"peak_active_objects": peak_active_objects,
		"body_position_y": falling_body.position.y,
	}
	DirAccess.make_dir_recursive_absolute(
		ProjectSettings.globalize_path("res://.results")
	)
	var result_file := FileAccess.open(RESULT_PATH, FileAccess.WRITE)
	if result_file == null:
		push_error("REDOT_RAPIER_CONSUMER result_file=FAIL")
		get_tree().quit(2)
		return
	result_file.store_string(JSON.stringify(result, "  ") + "\n")
	result_file.close()
	print(
		"REDOT_RAPIER_CONSUMER status=%s assertions=%d/%d server=%s body_y=%.3f"
		% [
			result.status,
			assertions.values().count(true),
			assertions.size(),
			result.active_server_class,
			result.body_position_y,
		]
	)
	get_tree().quit(0 if passed else 2)
