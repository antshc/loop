from dataclasses import dataclass

from .comment import Comment


@dataclass
class ReviewThread:
    thread_id: str
    path: str
    lines: str
    is_resolved: bool
    comments: list[Comment]

    @property
    def actionable_comment(self) -> str:
        labeled = next((c for c in reversed(self.comments) if c.get_label() is not None), None)
        return labeled.body if labeled else ""

    @property
    def is_actionable(self) -> bool:
        if self.is_resolved:
            return False
        if not self.comments:
            return False
        last = self.comments[-1]
        if last.is_excluded():
            return False
        label = last.get_label()
        if label is None:
            return False
        return label.is_actionable()
