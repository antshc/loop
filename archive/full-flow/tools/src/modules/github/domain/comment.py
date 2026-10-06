import re
from dataclasses import dataclass
from typing import ClassVar

from .thread_label import ThreadLabel


@dataclass
class Comment:
    author: str
    body: str

    _LABEL_PATTERNS: ClassVar[list[tuple[ThreadLabel, re.Pattern]]] = [
        (ThreadLabel.FIX, re.compile(r"fix!:")),
        (ThreadLabel.SUGGEST_BANG, re.compile(r"suggest!:")),
        (ThreadLabel.SUGGEST, re.compile(r"suggest:")),
        (ThreadLabel.NIT, re.compile(r"nit:")),
        (ThreadLabel.GOOD, re.compile(r"good:")),
        (ThreadLabel.QUESTION, re.compile(r"question!:")),
        (ThreadLabel.FIXED, re.compile(r"fixed\.", re.IGNORECASE)),
    ]

    def is_excluded(self) -> bool:
        return self.get_label() in (ThreadLabel.QUESTION, ThreadLabel.FIXED)

    def get_label(self) -> ThreadLabel | None:
        for label, pattern in self._LABEL_PATTERNS:
            if pattern.search(self.body):
                return label
        return None
