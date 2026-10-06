from dataclasses import dataclass, field


@dataclass
class ExecutionRecord:
    task: str
    count: int
    last_run: str
    last_items: list[str | int] = field(default_factory=list)
