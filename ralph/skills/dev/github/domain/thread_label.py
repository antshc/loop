from enum import Enum


class ThreadLabel(Enum):
    FIX = "fix!"
    SUGGEST_BANG = "suggest!"
    SUGGEST = "suggest"
    NIT = "nit"
    GOOD = "good"
    QUESTION = "question"
    FIXED = "fixed"

    def is_actionable(self) -> bool:
        return self in (ThreadLabel.FIX, ThreadLabel.SUGGEST_BANG)
