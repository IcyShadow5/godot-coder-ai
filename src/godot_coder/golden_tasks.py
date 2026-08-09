"""Golden tasks: 30 hand-written GDScript challenges that a useful model
should handle. Each has a scaffold (what the model sees) and a reference
completion (what a correct answer looks like). The benchmark runs every
task through the model, checks the output with Godot's parser, and
compares token-prefix accuracy against the reference.

I use these to measure whether the model actually gets *better* between
runs instead of guessing from validation loss alone. A task that parses
is worth 1 point. Exact match against the reference is a bonus.
"""

from __future__ import annotations

from typing import Any

# Each task: id, topic, difficulty, scaffold (prefix the model sees),
# reference_suffix (what the model should produce), description (what
# the task is asking for — used only in reports, never in the prompt).
GOLDEN_TASKS: tuple[dict[str, Any], ...] = (

    # ---- functions (5) --------------------------------------------------

    {
        "id": "func_clamp_value",
        "topic": "functions",
        "difficulty": "easy",
        "description": "Clamp a value between min and max and return it.",
        "scaffold": "extends Node\n\nfunc clamp_value(value: float, minimum: float, maximum: float) -> float:\n\t",
        "reference_suffix": "return clampf(value, minimum, maximum)\n",
    },
    {
        "id": "func_is_even",
        "topic": "functions",
        "difficulty": "easy",
        "description": "Return true when the given integer is even.",
        "scaffold": "extends Node\n\nfunc is_even(number: int) -> bool:\n\t",
        "reference_suffix": "return number % 2 == 0\n",
    },
    {
        "id": "func_lerp_position",
        "topic": "functions",
        "difficulty": "easy",
        "description": "Linearly interpolate between two Vector2 positions by a factor t (0-1).",
        "scaffold": "extends Node\n\nfunc lerp_position(from_pos: Vector2, to_pos: Vector2, t: float) -> Vector2:\n\t",
        "reference_suffix": "return from_pos.lerp(to_pos, t)\n",
    },
    {
        "id": "func_circle_area",
        "topic": "functions",
        "difficulty": "easy",
        "description": "Calculate the area of a circle given its radius.",
        "scaffold": "extends Node\n\nfunc circle_area(radius: float) -> float:\n\t",
        "reference_suffix": "return PI * radius * radius\n",
    },
    {
        "id": "func_random_in_range",
        "topic": "functions",
        "difficulty": "medium",
        "description": "Return a random integer between min_value and max_value inclusive.",
        "scaffold": "extends Node\n\nfunc random_in_range(min_value: int, max_value: int) -> int:\n\t",
        "reference_suffix": "return randi_range(min_value, max_value)\n",
    },

    # ---- signals (4) ----------------------------------------------------

    {
        "id": "signal_health_changed",
        "topic": "signals",
        "difficulty": "medium",
        "description": "Emit a health_changed signal with the current and maximum health values.",
        "scaffold": "extends Node\n\nsignal health_changed(current: float, maximum: float)\n\n@export var maximum_health: float = 100.0\nvar health: float = maximum_health\n\nfunc take_damage(amount: float) -> void:\n\thealth = maxf(health - amount, 0.0)\n\t",
        "reference_suffix": "health_changed.emit(health, maximum_health)\n",
    },
    {
        "id": "signal_died",
        "topic": "signals",
        "difficulty": "medium",
        "description": "Check if health is zero and emit a died signal if so.",
        "scaffold": "extends Node\n\nsignal died\n\nvar health: float = 100.0\n\nfunc check_death() -> void:\n\tif health <= 0.0:\n\t\t",
        "reference_suffix": "died.emit()\n",
    },
    {
        "id": "signal_score_changed",
        "topic": "signals",
        "difficulty": "easy",
        "description": "Add points to the score and emit a signal with the new score.",
        "scaffold": "extends Node\n\nsignal score_changed(new_score: int)\n\nvar score: int = 0\n\nfunc add_points(points: int) -> void:\n\tscore += points\n\t",
        "reference_suffix": "score_changed.emit(score)\n",
    },
    {
        "id": "signal_cooldown_ready",
        "topic": "signals",
        "difficulty": "medium",
        "description": "Decrease a cooldown timer each frame and emit a signal when it reaches zero.",
        "scaffold": "extends Node\n\nsignal cooldown_ready\n\nvar cooldown: float = 0.0\n\nfunc _process(delta: float) -> void:\n\tif cooldown > 0.0:\n\t\tcooldown = maxf(cooldown - delta, 0.0)\n\t\tif cooldown == 0.0:\n\t\t\t",
        "reference_suffix": "cooldown_ready.emit()\n",
    },

    # ---- collections (4) ------------------------------------------------

    {
        "id": "coll_array_has_item",
        "topic": "collections",
        "difficulty": "easy",
        "description": "Return true when the array contains the given item.",
        "scaffold": "extends Node\n\nfunc has_item(items: Array, target: String) -> bool:\n\t",
        "reference_suffix": "return items.has(target)\n",
    },
    {
        "id": "coll_array_sum",
        "topic": "collections",
        "difficulty": "easy",
        "description": "Sum all integers in an array and return the total.",
        "scaffold": "extends Node\n\nfunc array_sum(values: Array[int]) -> int:\n\tvar total := 0\n\tfor value in values:\n\t\t",
        "reference_suffix": "total += value\n\treturn total\n",
    },
    {
        "id": "coll_dict_get_default",
        "topic": "collections",
        "difficulty": "easy",
        "description": "Get a value from a dictionary, returning a default if the key is missing.",
        "scaffold": "extends Node\n\nfunc get_or_default(data: Dictionary, key: String, default_value: Variant) -> Variant:\n\t",
        "reference_suffix": "return data.get(key, default_value)\n",
    },
    {
        "id": "coll_filter_active",
        "topic": "collections",
        "difficulty": "medium",
        "description": "Filter an array of dictionaries, keeping only entries where 'active' is true.",
        "scaffold": "extends Node\n\nfunc filter_active(entries: Array[Dictionary]) -> Array[Dictionary]:\n\tvar result: Array[Dictionary] = []\n\tfor entry in entries:\n\t\t",
        "reference_suffix": "if entry.get(\"active\", false):\n\t\t\tresult.append(entry)\n\treturn result\n",
    },

    # ---- gameplay (4) ---------------------------------------------------

    {
        "id": "game_can_afford",
        "topic": "gameplay",
        "difficulty": "easy",
        "description": "Check if the player has enough resources to spend the given amount.",
        "scaffold": "extends Node\n\nvar gold: int = 0\n\nfunc can_afford(cost: int) -> bool:\n\t",
        "reference_suffix": "return gold >= cost\n",
    },
    {
        "id": "game_spend_resource",
        "topic": "gameplay",
        "difficulty": "medium",
        "description": "Spend energy if enough is available, returning true on success.",
        "scaffold": "extends Node\n\n@export var maximum_energy: float = 100.0\nvar energy: float = maximum_energy\n\nfunc spend(amount: float) -> bool:\n\tif amount <= 0.0 or energy < amount:\n\t\treturn false\n\t",
        "reference_suffix": "energy -= amount\n\treturn true\n",
    },
    {
        "id": "game_add_inventory_item",
        "topic": "gameplay",
        "difficulty": "medium",
        "description": "Add an item to the inventory if there is room, returning true on success.",
        "scaffold": "extends Node\n\n@export var capacity: int = 10\nvar items: Array[String] = []\n\nfunc add_item(item_id: String) -> bool:\n\tif items.size() >= capacity:\n\t\treturn false\n\t",
        "reference_suffix": "items.append(item_id)\n\treturn true\n",
    },
    {
        "id": "game_level_up",
        "topic": "gameplay",
        "difficulty": "medium",
        "description": "Increase the player's level and recalculate max health based on it.",
        "scaffold": "extends Node\n\nvar level: int = 1\nvar maximum_health: float = 100.0\n\nfunc level_up() -> void:\n\tlevel += 1\n\t",
        "reference_suffix": "maximum_health = 100.0 + float(level - 1) * 20.0\n",
    },

    # ---- nodes (3) ------------------------------------------------------

    {
        "id": "node_move_toward",
        "topic": "nodes",
        "difficulty": "medium",
        "description": "Move a Node2D toward a target position at a fixed speed each frame.",
        "scaffold": "extends Node2D\n\n@export var speed: float = 200.0\nvar target_position: Vector2 = Vector2.ZERO\nvar moving: bool = false\n\nfunc move_to(target: Vector2) -> void:\n\ttarget_position = target\n\tmoving = true\n\nfunc _process(delta: float) -> void:\n\tif not moving:\n\t\treturn\n\t",
        "reference_suffix": "var direction := target_position - position\n\tvar distance := direction.length()\n\tif distance < speed * delta:\n\t\tposition = target_position\n\t\tmoving = false\n\telse:\n\t\tposition += direction.normalized() * speed * delta\n",
    },
    {
        "id": "node_set_modulate",
        "topic": "nodes",
        "difficulty": "easy",
        "description": "Set the modulate color of a sprite with an alpha value.",
        "scaffold": "extends Sprite2D\n\nfunc set_alpha(alpha: float) -> void:\n\t",
        "reference_suffix": "modulate.a = alpha\n",
    },
    {
        "id": "node_get_children_count",
        "topic": "nodes",
        "difficulty": "easy",
        "description": "Return the number of direct children this node has.",
        "scaffold": "extends Node\n\nfunc child_count() -> int:\n\t",
        "reference_suffix": "return get_child_count()\n",
    },

    # ---- state (3) ------------------------------------------------------

    {
        "id": "state_machine_transition",
        "topic": "state",
        "difficulty": "medium",
        "description": "Implement a simple state machine: only allow valid transitions and count them.",
        "scaffold": "extends Node\n\nenum State { IDLE, RUNNING, JUMPING, DEAD }\n\nvar state: State = State.IDLE\nvar transition_count: int = 0\n\nfunc set_state(next_state: State) -> bool:\n\tif state == State.DEAD and next_state != State.IDLE:\n\t\treturn false\n\t",
        "reference_suffix": "state = next_state\n\ttransition_count += 1\n\treturn true\n",
    },
    {
        "id": "state_is_invulnerable",
        "topic": "state",
        "difficulty": "easy",
        "description": "A player is invulnerable for a short time after taking damage.",
        "scaffold": "extends Node\n\nvar invulnerability_timer: float = 0.0\n\nfunc take_damage(amount: float) -> void:\n\tif invulnerability_timer > 0.0:\n\t\treturn\n\tinvulnerability_timer = 1.5\n\nfunc is_invulnerable() -> bool:\n\t",
        "reference_suffix": "return invulnerability_timer > 0.0\n",
    },
    {
        "id": "state_toggle_pause",
        "topic": "state",
        "difficulty": "easy",
        "description": "Toggle between paused and unpaused state.",
        "scaffold": "extends Node\n\nvar paused: bool = false\n\nfunc toggle_pause() -> void:\n\t",
        "reference_suffix": "paused = not paused\n",
    },

    # ---- basics (4) -----------------------------------------------------

    {
        "id": "basic_distance_2d",
        "topic": "basics",
        "difficulty": "easy",
        "description": "Calculate the Euclidean distance between two 2D points.",
        "scaffold": "extends Node\n\nfunc distance_2d(a: Vector2, b: Vector2) -> float:\n\t",
        "reference_suffix": "return a.distance_to(b)\n",
    },
    {
        "id": "basic_normalize_vector",
        "topic": "basics",
        "difficulty": "easy",
        "description": "Return the normalized direction vector from one point to another.",
        "scaffold": "extends Node\n\nfunc direction(from_pos: Vector2, to_pos: Vector2) -> Vector2:\n\t",
        "reference_suffix": "return from_pos.direction_to(to_pos)\n",
    },
    {
        "id": "basic_format_time",
        "topic": "basics",
        "difficulty": "easy",
        "description": "Format a time in seconds as MM:SS string.",
        "scaffold": "extends Node\n\nfunc format_time(total_seconds: float) -> String:\n\tvar minutes := int(total_seconds) / 60\n\tvar seconds := int(total_seconds) % 60\n\t",
        "reference_suffix": "return \"%02d:%02d\" % [minutes, seconds]\n",
    },
    {
        "id": "basic_percentage",
        "topic": "basics",
        "difficulty": "easy",
        "description": "Calculate what percentage 'value' is of 'maximum' (0-100).",
        "scaffold": "extends Node\n\nfunc percentage(value: float, maximum: float) -> float:\n\tif maximum <= 0.0:\n\t\treturn 0.0\n\t",
        "reference_suffix": "return clampf(value / maximum * 100.0, 0.0, 100.0)\n",
    },

    # ---- architecture (3) -----------------------------------------------

    {
        "id": "arch_stat_with_bonus",
        "topic": "architecture",
        "difficulty": "medium",
        "description": "Calculate a stat value with a bonus multiplier applied.",
        "scaffold": "extends Node\n\n@export var base_value: float = 10.0\n@export var multiplier: float = 1.0\nvar bonus: float = 0.0\n\nfunc get_value() -> float:\n\t",
        "reference_suffix": "return (base_value + bonus) * multiplier\n",
    },
    {
        "id": "arch_component_reset",
        "topic": "architecture",
        "difficulty": "easy",
        "description": "Reset a component to its default values.",
        "scaffold": "extends Node\n\n@export var default_speed: float = 100.0\n@export var default_jump: float = 300.0\nvar speed: float\nvar jump_force: float\n\nfunc _ready() -> void:\n\treset()\n\nfunc reset() -> void:\n\t",
        "reference_suffix": "speed = default_speed\n\tjump_force = default_jump\n",
    },
    {
        "id": "arch_on_hit_chain",
        "topic": "architecture",
        "difficulty": "medium",
        "description": "Call a virtual _on_hit method and emit a signal; subclasses override _on_hit.",
        "scaffold": "extends Node\n\nsignal was_hit(damage: float)\n\nfunc hit(damage: float) -> void:\n\t",
        "reference_suffix": "_on_hit(damage)\n\twas_hit.emit(damage)\n\nfunc _on_hit(_damage: float) -> void:\n\tpass\n",
    },

)


def task_count() -> int:
    return len(GOLDEN_TASKS)


def tasks_by_topic() -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for task in GOLDEN_TASKS:
        topic = str(task["topic"])
        result.setdefault(topic, []).append(task)
    return result
