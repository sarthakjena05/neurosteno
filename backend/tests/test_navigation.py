"""Navigation + full phrase accessibility."""

import pytest

from neurosteno.context import rank, shortcuts
from neurosteno.headless import isolated_store
from neurosteno.navigation import BACK_KEY, Frame, Navigator, make_slots
from neurosteno.phrases.catalog import build_catalog
from neurosteno.routing import URGENT_ROUTES, lengths, selections_to

CAT = build_catalog()
CATEGORIES = ["Pain", "Breathing assistance", "Food and water", "Comfort", "Call caregiver"]


def test_catalog_size_and_quality():
    st = CAT.stats()
    assert st["phrases"] >= 1000
    assert st["unique_sentences"] == st["phrases"]  # no duplicate outputs
    for leaf in CAT.leaves():
        assert leaf.phrase and leaf.phrase[0].isupper() and leaf.phrase.rstrip()[-1] in ".!"
        assert "{" not in leaf.phrase  # no unfilled templates


def test_example_phrase_from_spec():
    leaf = CAT.get("pain/legs-feet/left-leg/reposition/skip")
    assert [n.label for n in leaf.path()[1:4]] == ["Pain", "Legs and feet", "Left leg"]
    assert leaf.phrase == "My left leg hurts. Please help me reposition."


def test_no_numbered_groups():
    for node in CAT.by_id.values():
        assert not any(ch.isdigit() for ch in node.group), node.id


@pytest.mark.parametrize("n", [2, 3, 5])
def test_home_shows_five_categories(n):
    nav = Navigator(CAT, shortcuts=["comfort/position/turn-left"], urgent=URGENT_ROUTES)
    assert nav.prompt() == "What do you need?"
    labels = [o.label for s in nav.slots(n) for o in s.options]
    assert labels == CATEGORIES  # never displaced or reordered by context
    if n == 5:
        assert [s.label for s in nav.slots(n)] == CATEGORIES


@pytest.mark.parametrize("n", [2, 3, 5])
def test_every_phrase_reachable_static(n):
    L = lengths(CAT, n, (), ())  # raises if any phrase is unreachable or routing loops
    assert len(L) == len(CAT.leaves())
    assert max(L.values()) < 40


@pytest.mark.parametrize("profile", ["new-patient", "mobility", "respiratory"])
@pytest.mark.parametrize("n", [2, 3, 5])
def test_every_phrase_reachable_with_context(profile, n):
    store, _ = isolated_store()
    sc = tuple(s.node_id for s in shortcuts(rank(store.load(profile), CAT), CAT))
    L = lengths(CAT, n, sc, tuple(URGENT_ROUTES))
    assert len(L) == len(CAT.leaves())


@pytest.mark.parametrize("n", [2, 3, 5])
def test_slots_never_exceed_n_and_always_progress(n):
    """Every screen reachable from Home has <= n targets and no chunk re-offers the whole screen."""
    seen, todo = set(), [Navigator(CAT, ["pain/legs-feet/left-leg/reposition"], URGENT_ROUTES)]
    while todo and len(seen) < 3000:
        nav = todo.pop()
        key = tuple((f.node_id, f.subset) for f in nav.stack)
        if key in seen:
            continue
        seen.add(key)
        opts = nav.options()
        slots = nav.slots(n)
        assert 1 <= len(slots) <= n
        assert sorted(o.key for s in slots for o in s.options) == sorted(o.key for o in opts)
        for s in slots:
            if s.kind == "group":
                assert len(s.options) < len(opts)
                assert not s.label[0].isdigit()
            if s.kind == "group" or (s.options[0].kind == "node"):
                child = Navigator(CAT, nav.shortcuts, nav.urgent)
                child.stack = [Frame(f.node_id, f.subset, f.subset_label) for f in nav.stack]
                child.track_undo = False
                child.select(s)
                todo.append(child)


def test_back_home_undo():
    nav = Navigator(CAT)
    s = nav.slots(5)
    nav.select(s[0])  # Pain
    assert nav.frame.node_id == "pain"
    nav.select(nav.slots(5)[1])
    depth = len(nav.stack)
    assert nav.undo()
    assert len(nav.stack) == depth - 1
    back = [x for x in nav.slots(5) if x.options[0].key == BACK_KEY][0]
    nav.select(back)
    assert nav.frame.node_id == "home"
    nav.select(nav.slots(5)[2])
    nav.home()
    assert nav.frame.node_id == "home" and nav.crumbs == []


def test_go_back_is_last_slot_for_3_and_5():
    nav = Navigator(CAT)
    nav.select(nav.slots(5)[0])
    for n in (3, 5):
        assert nav.slots(n)[-1].options[0].key == BACK_KEY


def test_context_shortens_relevant_requests_and_is_measured():
    store, _ = isolated_store()
    sc = tuple(s.node_id for s in shortcuts(rank(store.load("mobility"), CAT), CAT))
    target = "comfort/position/turn-left"
    assert selections_to(CAT, target, 2, sc, tuple(URGENT_ROUTES)) < selections_to(CAT, target, 2)


def test_make_slots_semantic_labels():
    nav = Navigator(CAT)
    labels = [s.label for s in make_slots(nav.options(), 2)]
    assert labels == ["Medical", "Daily needs · People"]
