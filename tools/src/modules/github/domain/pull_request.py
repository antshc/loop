from dataclasses import dataclass


@dataclass
class PullRequest:
    owner: str
    repo: str
    number: int
    url: str
    title: str
