from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    type: str
    start: int
    end: int
    value: str
