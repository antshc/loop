from dataclasses import dataclass


@dataclass
class IssueComment:
    id: str
    body: str
    created_at: str
