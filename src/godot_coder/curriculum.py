from __future__ import annotations
"""Generate the controlled v0.3 synthetic curriculum (192 lessons across 8 topics)."""

import argparse
import json
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

CURRICULUM_VERSION = 1
DEFAULT_ROOT = Path("data/raw/curriculum_v03")


@dataclass(frozen=True)
class Topic:
    slug: str
    label: str
    render: Callable[[int], str]


def _header(topic: str, index: int, task: str) -> str:
    return (
        f"# curriculum: godot-coder-v0.3\n"
        f"# topic: {topic}\n"
        f"# lesson: {index:03d}\n"
        f"# task: {task}\n"
    )


def _basics(index: int) -> str:
    limit = 10 + index
    return _header("basics", index, "Clamp an integer and report whether it reached the limit.") + f'''extends Node

@export var maximum_value: int = {limit}
var current_value: int = 0

func set_value(next_value: int) -> void:
    current_value = clampi(next_value, 0, maximum_value)

func add_value(amount: int) -> int:
    set_value(current_value + amount)
    return current_value

func is_full() -> bool:
    return current_value >= maximum_value
'''


def _functions(index: int) -> str:
    cost = float((index % 7) + 1)
    return _header("functions", index, "Spend energy with guard clauses and return success.") + f'''extends Node

@export var maximum_energy: float = {80.0 + index:.1f}
var energy: float = maximum_energy

func can_spend(amount: float) -> bool:
    return amount > 0.0 and energy >= amount

func spend(amount: float) -> bool:
    if not can_spend(amount):
        return false
    energy -= amount
    return true

func restore_default_cost() -> void:
    energy = minf(energy + {cost:.1f}, maximum_energy)
'''


def _collections(index: int) -> str:
    capacity = 3 + (index % 8)
    return _header("collections", index, "Manage a typed string inventory without duplicates.") + f'''extends Node

@export var capacity: int = {capacity}
var items: Array[String] = []

func add_item(item_id: String) -> bool:
    if item_id.is_empty() or items.has(item_id):
        return false
    if items.size() >= capacity:
        return false
    items.append(item_id)
    return true

func remove_item(item_id: String) -> bool:
    if not items.has(item_id):
        return false
    items.erase(item_id)
    return true

func item_count() -> int:
    return items.size()
'''


def _signals(index: int) -> str:
    start = 50.0 + index
    return _header("signals", index, "Emit typed signals when health changes or reaches zero.") + f'''extends Node

signal health_changed(current: float, maximum: float)
signal died

@export var maximum_health: float = {start:.1f}
var health: float = maximum_health

func take_damage(amount: float) -> void:
    if amount <= 0.0 or health <= 0.0:
        return
    health = maxf(health - amount, 0.0)
    health_changed.emit(health, maximum_health)
    if health == 0.0:
        died.emit()

func heal(amount: float) -> void:
    if amount <= 0.0 or health <= 0.0:
        return
    health = minf(health + amount, maximum_health)
    health_changed.emit(health, maximum_health)
'''


def _nodes(index: int) -> str:
    speed = 2.0 + (index % 10) * 0.35
    return _header("nodes", index, "Move a Node2D toward a target without overshooting.") + f'''extends Node2D

@export var speed: float = {speed:.2f}
var target_position: Vector2 = Vector2.ZERO
var moving: bool = false

func move_to(next_target: Vector2) -> void:
    target_position = next_target
    moving = true

func _process(delta: float) -> void:
    if not moving:
        return
    position = position.move_toward(target_position, speed * delta)
    if position.is_equal_approx(target_position):
        moving = false
'''


def _gameplay(index: int) -> str:
    cooldown = 0.25 + (index % 9) * 0.1
    return _header("gameplay", index, "Implement a reusable cooldown that can be queried and triggered.") + f'''extends Node

@export var cooldown_duration: float = {cooldown:.2f}
var cooldown_remaining: float = 0.0

func _process(delta: float) -> void:
    cooldown_remaining = maxf(cooldown_remaining - delta, 0.0)

func is_ready() -> bool:
    return cooldown_remaining <= 0.0

func trigger() -> bool:
    if not is_ready():
        return false
    cooldown_remaining = cooldown_duration
    return true

func reset() -> void:
    cooldown_remaining = 0.0
'''


def _state(index: int) -> str:
    return _header("state", index, "Represent a small state machine with guarded transitions.") + f'''extends Node

enum State {{ IDLE, ACTIVE, DISABLED }}

var state: State = State.IDLE
var transition_count: int = {index % 3}

func set_state(next_state: State) -> bool:
    if state == next_state:
        return false
    if state == State.DISABLED and next_state == State.ACTIVE:
        return false
    state = next_state
    transition_count += 1
    return true

func disable() -> void:
    set_state(State.DISABLED)

func is_active() -> bool:
    return state == State.ACTIVE
'''


def _architecture(index: int) -> str:
    multiplier = 1.0 + (index % 5) * 0.25
    return _header("architecture", index, "Create a small reusable stat component with a modifier.") + f'''extends Node

signal value_changed(value: float)

@export var base_value: float = {10.0 + index:.1f}
@export var multiplier: float = {multiplier:.2f}
var bonus: float = 0.0

func get_value() -> float:
    return maxf((base_value + bonus) * multiplier, 0.0)

func set_bonus(next_bonus: float) -> void:
    if is_equal_approx(bonus, next_bonus):
        return
    bonus = next_bonus
    value_changed.emit(get_value())

func reset_bonus() -> void:
    set_bonus(0.0)
'''


TOPICS = (
    Topic("01_basics", "Basics", _basics),
    Topic("02_functions", "Functions", _functions),
    Topic("03_collections", "Collections", _collections),
    Topic("04_signals", "Signals", _signals),
    Topic("05_nodes", "Nodes", _nodes),
    Topic("06_gameplay", "Gameplay", _gameplay),
    Topic("07_state", "State Machines", _state),
    Topic("08_architecture", "Architecture", _architecture),
)


def _split_for(index: int) -> str:
    # Per topic: 18 train, 3 validation, 3 test lessons.
    if index <= 18:
        return "train"
    if index <= 21:
        return "val"
    return "test"


def build_curriculum(output_dir: str | Path = DEFAULT_ROOT, *, overwrite: bool = False) -> dict[str, object]:
    root = Path(output_dir).resolve()
    if root.exists() and any(root.iterdir()):
        if not overwrite:
            raise FileExistsError(f"curriculum directory is not empty: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    (root / "project.godot").write_text(
        '[application]\nconfig/name="Godot Coder Curriculum v0.3"\n\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n',
        encoding="utf-8",
        newline="\n",
    )

    lessons: list[dict[str, object]] = []
    counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    for topic in TOPICS:
        for index in range(1, 25):
            split = _split_for(index)
            destination = root / split / topic.slug / f"lesson_{index:03d}.gd"
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = topic.render(index)
            destination.write_text(content, encoding="utf-8", newline="\n")
            relative = destination.relative_to(root).as_posix()
            lessons.append(
                {
                    "path": relative,
                    "split": split,
                    "topic": topic.slug,
                    "topic_label": topic.label,
                    "lesson": index,
                    "bytes": len(content.encode("utf-8")),
                }
            )
            counts[split] += 1
            topic_counts[topic.slug] += 1

    manifest: dict[str, object] = {
        "format": "godot-coder-curriculum",
        "format_version": CURRICULUM_VERSION,
        "name": "Controlled Godot Curriculum v0.3",
        "root": str(root),
        "total_lessons": len(lessons),
        "split_counts": dict(counts),
        "topic_counts": dict(topic_counts),
        "topics": [{"slug": topic.slug, "label": topic.label} for topic in TOPICS],
        "lessons": lessons,
    }
    (root / "curriculum_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the controlled Godot curriculum used by Studio v0.3.")
    parser.add_argument("--output", default=str(DEFAULT_ROOT))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_curriculum(args.output, overwrite=args.overwrite)
    print(json.dumps({key: manifest[key] for key in ("name", "total_lessons", "split_counts", "topic_counts")}, indent=2))


if __name__ == "__main__":
    main()
