from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlayerRecord:
    source: str
    source_url: str
    game_id: str | None
    game_date: str | None
    team: str
    player: str
    player_id: str | None = None
    jersey: str | None = None
    position: str | None = None
    minutes: str | None = None
    participation: str = "unknown"
    stats: dict = field(default_factory=dict)
    validation: list[str] = field(default_factory=list)
    provenance: str = "local_unverified"
    extracted_at: str = field(default_factory=utc_now)

    def to_dict(self):
        return asdict(self)
