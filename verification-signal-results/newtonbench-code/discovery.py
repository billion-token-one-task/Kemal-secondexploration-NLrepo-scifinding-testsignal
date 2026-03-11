from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass
from pathlib import Path


DIFFICULTIES = ["easy", "medium", "hard"]
SYSTEMS = ["vanilla_equation", "simple_system", "complex_system"]


@dataclass
class NewtonBenchTask:
    module: str
    difficulty: str
    system: str
    law_version: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def discover_modules(benchmark_root: str | Path) -> list[str]:
    root = Path(benchmark_root) / "modules"
    return sorted(path.name for path in root.iterdir() if path.is_dir() and path.name.startswith("m"))


def discover_tasks(benchmark_root: str | Path) -> list[NewtonBenchTask]:
    benchmark_root = Path(benchmark_root)
    if str(benchmark_root) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(benchmark_root))
    tasks: list[NewtonBenchTask] = []
    for module_name in discover_modules(benchmark_root):
        module = importlib.import_module(f"modules.{module_name}")
        for difficulty in DIFFICULTIES:
            versions = module.get_available_law_versions(difficulty)
            for law_version in versions:
                for system in SYSTEMS:
                    tasks.append(
                        NewtonBenchTask(
                            module=module_name,
                            difficulty=difficulty,
                            system=system,
                            law_version=law_version,
                        )
                    )
    return tasks
