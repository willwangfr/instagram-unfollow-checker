"""Where your exports live.

Every path that identifies a person lives in a config file, never in code.
The repo is public; a hardcoded export path leaks the account name, the
export's ID and the machine's disk layout.
"""

import json
from pathlib import Path

DEFAULT_CONFIG = "snapshots.json"


class Config:
    """Resolved locations of the Instagram exports to analyse.

    latest_zip   the newest export, used for the current graph
    latest_dir   the same export unzipped (only needed for messages)
    snapshots    [(date, zip)] oldest first; the follow timeline needs 2+
    me           your handles, past and present, so your own DM messages
                 can be told apart from everyone else's
    work_dir     where generated reports and intermediates are written
    """

    def __init__(self, data, config_path):
        base = Path(config_path).resolve().parent

        def resolve(p):
            p = Path(p).expanduser()
            return p if p.is_absolute() else (base / p)

        self.latest_zip = resolve(data["latest_zip"])
        self.latest_dir = resolve(data["latest_dir"]) if data.get("latest_dir") else None
        self.snapshots = [(s["date"], resolve(s["zip"]))
                          for s in sorted(data.get("snapshots", []),
                                          key=lambda s: s["date"])]
        self.me = [h.lower() for h in data.get("me", [])]
        self.work_dir = resolve(data.get("work_dir", "."))

    @property
    def connections_dir(self):
        if not self.latest_dir:
            raise SystemExit(
                "This step needs the unzipped export: set 'latest_dir' in your config.")
        return self.latest_dir / "connections" / "followers_and_following"

    @property
    def activity_dir(self):
        if not self.latest_dir:
            raise SystemExit(
                "This step needs the unzipped export: set 'latest_dir' in your config.")
        return self.latest_dir / "your_instagram_activity"


def load(path=None):
    path = Path(path or DEFAULT_CONFIG)
    if not path.exists():
        raise SystemExit(
            f"No config at {path}. Copy snapshots.example.json to {DEFAULT_CONFIG} "
            "and point it at your own exports.")
    return Config(json.loads(path.read_text()), path)


def add_config_arg(parser):
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help=f"JSON describing your exports (default: {DEFAULT_CONFIG})")
    return parser
