"""Transparent, deterministic patient-context ranking (no learned model).

Profile sources (all SYNTHETIC in this prototype):
  * long-term preferences        (caregiver-entered, weighted)
  * caregiver communication info (notes + pinned requests)
  * prior confirmed messages     (seed history + messages confirmed in this app)
  * recent requests              (last 24 h)
  * optional session context     (tags such as "night", "after-meal", "on-niv")

Each rule adds points to a catalog node and records a human-readable reason. The top
nodes become "Suggested for you" shortcuts inside their category, and categories whose
subtree contains a suggestion get a badge on Home. Context never reorders the five Home
categories, never changes what the EEG decoder predicts, and never creates or confirms a
message by itself.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .phrases.catalog import Catalog

SESSION_RULES: dict[str, list[tuple[str, float, str]]] = {
    "night": [
        ("comfort/room/lights-off", 1.5, "Session context: night time"),
        ("comfort/feelings/cant-sleep", 1.0, "Session context: night time"),
    ],
    "after-meal": [
        ("food-water/mouth/clean-mouth", 1.5, "Session context: just after a meal"),
        ("food-water/mouth/lips", 1.0, "Session context: just after a meal"),
    ],
    "on-niv": [
        ("breathing/adjust-mask", 1.5, "Session context: using non-invasive ventilation"),
        ("breathing/mask-leak", 1.0, "Session context: using non-invasive ventilation"),
    ],
    "visitors-expected": [
        ("call-caregiver/family/visit", 1.0, "Session context: visitors expected today"),
    ],
}

PIN_POINTS = 5.0
PREF_POINTS = 2.0
CONFIRMED_POINTS = 1.0
RECENT_POINTS = 1.5
SHORTCUT_MIN_SCORE = 2.0
MAX_SHORTCUTS = 4


def _ts(v, now: float | None = None) -> float:
    if isinstance(v, dict):  # {"time": ...} or {"hours_ago": ...}
        if "hours_ago" in v:
            return (now or time.time()) - float(v["hours_ago"]) * 3600
        v = v["time"]
    if isinstance(v, (int, float)):
        return float(v)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()


@dataclass
class Profile:
    id: str
    display_name: str
    summary: str = ""
    synthetic: bool = True
    preferences: list[dict] = field(default_factory=list)
    caregiver_info: dict = field(default_factory=dict)
    prior_confirmed_messages: list[dict] = field(default_factory=list)
    recent_requests: list[dict] = field(default_factory=list)
    session_context: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Profile":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class ProfileStore:
    """Seed profiles live in data/profiles; runtime history in data/runtime (git-ignored)."""

    def __init__(self, data_dir: Path):
        self.profile_dir = Path(data_dir) / "profiles"
        self.runtime_dir = Path(data_dir) / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.profile_dir.glob("*.json")):
            pr = Profile.load(p)
            out.append({"id": pr.id, "display_name": pr.display_name, "summary": pr.summary, "synthetic": pr.synthetic})
        return out

    def load(self, profile_id: str, origin: str | None = None) -> Profile:
        """Seed profile plus messages confirmed in the app. History is kept per data origin
        (real / simulated / replay) so synthetic sessions never shape real-EEG context."""
        pr = Profile.load(self.profile_dir / f"{profile_id}.json")
        if origin:
            pr.prior_confirmed_messages = pr.prior_confirmed_messages + self.history(profile_id, origin)
        return pr

    def _hist_path(self, profile_id: str, origin: str) -> Path:
        return self.runtime_dir / f"history_{profile_id}_{origin}.json"

    def history(self, profile_id: str, origin: str) -> list[dict]:
        p = self._hist_path(profile_id, origin)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    def append_confirmed(self, profile_id: str, node_id: str, text: str, origin: str) -> None:
        hist = self.history(profile_id, origin)
        hist.append({"node": node_id, "text": text, "time": time.time(), "source": f"confirmed in app ({origin})"})
        self._hist_path(profile_id, origin).write_text(json.dumps(hist[-500:], indent=1), encoding="utf-8")

    def append_metric(self, record: dict) -> None:
        with open(self.runtime_dir / "metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def metrics(self) -> list[dict]:
        p = self.runtime_dir / "metrics.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def runtime_path(self, name: str) -> Path:
        return self.runtime_dir / name


@dataclass
class Suggestion:
    node_id: str
    score: float
    reasons: list[str]

    def to_dict(self, catalog: Catalog) -> dict:
        n = catalog.get(self.node_id)
        return {"node_id": self.node_id, "title": n.title, "phrase": n.phrase, "score": round(self.score, 2),
                "reasons": self.reasons, "category": n.path()[1].label}


def rank(profile: Profile, catalog: Catalog, now: float | None = None) -> list[Suggestion]:
    now = now or time.time()
    scores: dict[str, float] = {}
    reasons: dict[str, list[str]] = {}

    def add(node_id: str, pts: float, why: str) -> None:
        if node_id not in catalog.by_id or node_id == "home":
            return
        scores[node_id] = scores.get(node_id, 0.0) + pts
        reasons.setdefault(node_id, [])
        if why not in reasons[node_id]:
            reasons[node_id].append(why)

    for pin in profile.caregiver_info.get("pinned", []):
        add(pin["node"], PIN_POINTS, f"Caregiver pinned: {pin.get('reason', '')}".rstrip(": "))
    for pref in profile.preferences:
        add(pref["node"], PREF_POINTS * float(pref.get("weight", 1.0)), f"Preference: {pref.get('reason', '')}".rstrip(": "))

    counts: dict[str, float] = {}
    for m in profile.prior_confirmed_messages:
        age_days = max(0.0, (now - _ts(m, now)) / 86400)
        counts[m["node"]] = counts.get(m["node"], 0.0) + 0.5 ** (age_days / 7.0)  # one-week half-life
    for nid, c in counts.items():
        k = sum(1 for m in profile.prior_confirmed_messages if m["node"] == nid)
        add(nid, CONFIRMED_POINTS * c, f"Confirmed {k} time{'s' if k != 1 else ''} before")

    for r in profile.recent_requests:
        hours = (now - _ts(r, now)) / 3600
        if 0 <= hours <= 24:
            add(r["node"], RECENT_POINTS, f"Requested {hours:.0f} h ago")

    for tag in profile.session_context.get("tags", []):
        for nid, pts, why in SESSION_RULES.get(tag, []):
            add(nid, pts, why)

    out = [Suggestion(n, s, reasons[n]) for n, s in scores.items()]
    out.sort(key=lambda x: (-x.score, x.node_id))  # deterministic
    return out


def shortcuts(suggestions: list[Suggestion], catalog: Catalog) -> list[Suggestion]:
    picked: list[Suggestion] = []
    for s in suggestions:
        if s.score < SHORTCUT_MIN_SCORE:
            break
        # Skip a node already covered by a picked ancestor/descendant to avoid near-duplicates.
        ids = {p.node_id for p in picked}
        path_ids = {n.id for n in catalog.get(s.node_id).path()}
        if ids & path_ids or any(s.node_id in {n.id for n in catalog.get(p).path()} for p in ids):
            continue
        picked.append(s)
        if len(picked) >= MAX_SHORTCUTS:
            break
    return picked
