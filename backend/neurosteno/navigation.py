"""Deterministic phrase navigation with N-target (2/3/5) screens.

A *screen* shows at most N SSVEP targets ("slots"). If a node has more options than N,
options are packed into contiguous, semantically labelled chunks using the catalog's
``group`` labels (e.g. "Upper body", "Left side"), never numbers. Selecting a chunk opens it
as a sub-screen. Every option therefore stays reachable in every mode; tests verify that all
phrases are reachable for N = 2, 3 and 5.

Navigation is a stack of frames; Back pops a frame, Home clears it, and Undo restores the
snapshot taken before the last accepted selection.

Spatial consistency: option order follows the catalog; personalised shortcuts occupy a
Home always shows exactly the five categories; personalised shortcuts appear first inside
their own category ("Suggested for you"); "Go back" is always the last slot.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .phrases.catalog import Catalog, Node

BACK_KEY = "__back__"
SUGGESTED = "Suggested for you"
URGENT = "Urgent"
LEAD_GROUPS = (URGENT, SUGGESTED)


@dataclass(frozen=True)
class Option:
    key: str  # node id, or BACK_KEY
    label: str
    group: str
    kind: str  # "node" | "leaf" | "back"
    node_id: str | None = None
    icon: str = ""
    urgent: bool = False


@dataclass
class Slot:
    options: list[Option]
    label: str
    kind: str  # "option" (single) | "group" (chunk opening a sub-screen)

    @property
    def key(self) -> str:
        if self.kind == "option":
            return self.options[0].key
        return "group:" + "|".join(o.key for o in self.options)

    def contains(self, key: str) -> bool:
        return any(o.key == key for o in self.options)


@dataclass
class Frame:
    node_id: str  # catalog node id
    subset: tuple[str, ...] | None = None  # option keys when inside a chunk
    subset_label: str = ""


@dataclass
class Crumb:
    label: str
    provisional: bool = False


# -------------------------------------------------------------------------------------
# packing
# -------------------------------------------------------------------------------------
def _runs(options: list[Option]) -> list[list[Option]]:
    runs: list[list[Option]] = []
    for o in options:
        if runs and runs[-1][0].group == o.group:
            runs[-1].append(o)
        else:
            runs.append([o])
    return runs


def _split_even(items: list, n: int) -> list[list]:
    n = min(n, len(items))
    q, r = divmod(len(items), n)
    out, i = [], 0
    for k in range(n):
        size = q + (1 if k < r else 0)
        out.append(items[i : i + size])
        i += size
    return out


def _pack_runs(runs: list[list[Option]], n: int) -> list[list[Option]]:
    """Contiguous partition of runs into n chunks minimising the largest chunk (DP)."""
    sizes = [len(r) for r in runs]
    m = len(runs)
    pref = [0]
    for s in sizes:
        pref.append(pref[-1] + s)
    INF = float("inf")
    best = [[INF] * (n + 1) for _ in range(m + 1)]
    cut = [[0] * (n + 1) for _ in range(m + 1)]
    best[0][0] = 0
    for i in range(1, m + 1):
        for k in range(1, n + 1):
            for j in range(k - 1, i):
                v = max(best[j][k - 1], pref[i] - pref[j])
                if v < best[i][k]:
                    best[i][k], cut[i][k] = v, j
    chunks, i, k = [], m, n
    while k > 0:
        j = cut[i][k]
        chunks.append([o for r in runs[j:i] for o in r])
        i, k = j, k - 1
    return chunks[::-1]


def pack(options: list[Option], n: int) -> list[list[Option]]:
    if len(options) <= n:
        return [[o] for o in options]
    runs = _runs(options)
    if len(runs) == 1:
        return _split_even(options, n)
    if len(runs) > n:
        return _pack_runs(runs, n)
    chunks = [list(r) for r in runs]
    # Spare slots: expand whole groups into individual options (largest that fits entirely),
    # so semantic groups are never cut in arbitrary places.
    while len(chunks) < n:
        spare = n - len(chunks)
        fits = [c for c in chunks if 1 < len(c) <= spare + 1]
        if not fits:
            break
        big = max(fits, key=len)
        i = chunks.index(big)
        chunks[i : i + 1] = [[o] for o in big]
    return chunks


def chunk_label(chunk: list[Option], all_options: list[Option]) -> str:
    groups = []
    for o in chunk:
        if o.group not in groups:
            groups.append(o.group)
    whole = all(sum(1 for a in all_options if a.group == g) == sum(1 for c in chunk if c.group == g) for g in groups)
    if whole and all(groups):
        return " · ".join(groups)
    names = [o.label for o in chunk]
    if len(names) <= 3:
        return ", ".join(names)
    return f"{names[0]}, {names[1]} and {len(names) - 2} more"


def make_slots(options: list[Option], n: int) -> list[Slot]:
    """Arrange options into at most n slots. 'Go back' is always the last slot when n >= 3."""
    back = [o for o in options if o.kind == "back"]
    content = [o for o in options if o.kind != "back"]
    lead_groups = [[o for o in content if o.group == g] for g in LEAD_GROUPS]
    lead_groups = [g for g in lead_groups if g]
    lead: list[list[Option]] = []
    has_other = any(o.group not in LEAD_GROUPS for o in content)
    if lead_groups and has_other and len(options) > n:
        # Urgent routes and context suggestions get their own target(s) so they are one
        # selection away. With few targets they share one target.
        rest = [o for o in content if o.group not in LEAD_GROUPS]
        k = len(lead_groups) if n - len(lead_groups) >= 2 else 1
        lead = lead_groups if k > 1 else [[o for g in lead_groups for o in g]]
        content, n = rest, n - len(lead)
    if back and n >= 3 and len(content) + 1 > n:
        body = pack(content, n - 1)
        if len(content) > 1 and any(len(c) == len(content) for c in body):
            body = _split_even(content, n - 1)  # never re-offer the whole screen as one chunk
        chunks = lead + body + [back]
    else:
        body = pack(content + back, n)
        if len(content) > 1 and any(set(o.key for o in content) <= set(o.key for o in c) for c in body):
            # Larger chunks last, so the first content option stands alone and progress is guaranteed.
            body = [list(reversed(c)) for c in reversed(_split_even(list(reversed(content + back)), n))]
        chunks = lead + body
    slots = []
    for c in chunks:
        if len(c) == 1:
            slots.append(Slot(c, c[0].label, "option"))
        else:
            slots.append(Slot(c, chunk_label(c, options), "group"))
    return slots


# -------------------------------------------------------------------------------------
# navigator
# -------------------------------------------------------------------------------------
def node_option(n: Node, group: str | None = None) -> Option:
    return Option(key=n.id, label=n.label, group=n.group if group is None else group,
                  kind="leaf" if n.is_leaf else "node", node_id=n.id, icon=n.icon, urgent=n.urgent)


class Navigator:
    def __init__(self, catalog: Catalog, shortcuts: list[str] | None = None, urgent: list[str] | None = None):
        self.catalog = catalog
        self.shortcuts: list[str] = [s for s in (shortcuts or []) if s in catalog.by_id]
        self.urgent: list[str] = [s for s in (urgent or []) if s in catalog.by_id]
        self.stack: list[Frame] = [Frame("home")]
        self.crumbs: list[Crumb] = []
        self._undo: list[tuple[list[Frame], list[Crumb]]] = []

    # ---- options --------------------------------------------------------------------
    def shortcuts_for(self, category_id: str) -> list[str]:
        return [nid for nid in self.shortcuts if self.catalog.get(nid).path()[1].id == category_id]

    def urgent_for(self, category_id: str) -> list[str]:
        return [nid for nid in self.urgent if self.catalog.get(nid).path()[1].id == category_id]

    def base_options(self, frame: Frame) -> list[Option]:
        """Home is always exactly the five categories (fixed positions for every profile).
        Personalised shortcuts are placed first inside their own category, as a
        'Suggested for you' group, so context reorders nothing and hides nothing."""
        node = self.catalog.get(frame.node_id)
        opts = [node_option(c) for c in node.children]
        if node.parent is not None and node.parent.id == "home":
            lead = []
            urgent = self.urgent_for(node.id)
            for prefix, group, ids in (("ur", URGENT, urgent),
                                       ("sc", SUGGESTED, [i for i in self.shortcuts_for(node.id) if i not in urgent])):
                for nid in ids:
                    target = self.catalog.get(nid)
                    label = " › ".join(n.label for n in target.path()[2:])
                    lead.append(Option(key=f"{prefix}:{nid}", label=label, group=group,
                                       kind="leaf" if target.is_leaf else "node", node_id=nid, urgent=target.urgent))
            opts = lead + opts
        return opts

    def options(self, frame: Frame | None = None) -> list[Option]:
        frame = frame or self.stack[-1]
        opts = self.base_options(frame)
        if frame.subset is not None:
            keep = set(frame.subset)
            opts = [o for o in opts if o.key in keep]
        if len(self.stack) > 1 or frame is not self.stack[0]:
            opts.append(Option(key=BACK_KEY, label="Go back", group="Go back", kind="back"))
        return opts

    def slots(self, n: int) -> list[Slot]:
        return make_slots(self.options(), n)

    @property
    def frame(self) -> Frame:
        return self.stack[-1]

    def prompt(self) -> str:
        node = self.catalog.get(self.frame.node_id)
        return node.prompt or node.label

    def location(self) -> list[str]:
        out = []
        for f in self.stack[1:]:
            if f.subset is not None:
                out.append(f.subset_label)
            else:
                out.append(self.catalog.get(f.node_id).label)
        return out

    # ---- actions --------------------------------------------------------------------
    track_undo = True

    def snapshot(self) -> None:
        if not self.track_undo:
            return
        self._undo.append((copy.deepcopy(self.stack), copy.deepcopy(self.crumbs)))
        del self._undo[:-50]

    def can_undo(self) -> bool:
        return bool(self._undo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self.stack, self.crumbs = self._undo.pop()
        return True

    def back(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop()
            if self.crumbs:
                self.crumbs.pop()

    def home(self) -> None:
        self.stack = [Frame("home")]
        self.crumbs = []

    def select(self, slot: Slot, provisional: bool = False) -> Node | None:
        """Apply an accepted selection. Returns the leaf Node if a phrase was chosen."""
        self.snapshot()
        if slot.kind == "group":
            self.stack.append(Frame(self.frame.node_id, tuple(o.key for o in slot.options), slot.label))
            self.crumbs.append(Crumb(slot.label, provisional))
            return None
        opt = slot.options[0]
        if opt.kind == "back":
            if self.track_undo:
                self._undo.pop()  # back is its own undo
            self.back()
            return None
        node = self.catalog.get(opt.node_id)
        if node.is_leaf:
            self.crumbs.append(Crumb(node.label, provisional))
            return node
        self.stack.append(Frame(node.id))
        self.crumbs.append(Crumb(node.label, provisional))
        return None

    def leaf_cancelled(self) -> None:
        """Message review was rejected: return to the screen the leaf was chosen on."""
        if self.crumbs:
            self.crumbs.pop()
        if self._undo:
            self._undo.pop()

    # ---- guidance (used by the simulated patient and tests) ---------------------------
    def slot_toward(self, slots: list[Slot], target_id: str) -> int:
        """Index of the slot that leads toward target leaf `target_id` from the current screen."""
        target = self.catalog.get(target_id)
        depth = {n.id: d for d, n in enumerate(target.path())}
        # Prefer the route whose option sits deepest on the target's path (shortcut / urgent
        # routes jump ahead); ties go to the regular route.
        best, best_depth = None, -1
        for i, s in enumerate(slots):
            for o in s.options:
                if o.node_id in depth:
                    d = depth[o.node_id] + (0.5 if o.key == o.node_id else 0.0)
                    if d > best_depth:
                        best, best_depth = i, d
        if best is not None:
            return best
        for i, s in enumerate(slots):
            if s.contains(BACK_KEY):
                return i
        raise LookupError(f"no route toward {target_id}")
