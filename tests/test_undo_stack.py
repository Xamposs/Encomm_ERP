"""Tests for core/undo_stack.py."""
import pytest

from core.undo_stack import ActionHistory


def test_push_and_undo():
    history = ActionHistory()
    log = []
    history.push("Δράση Α", lambda: log.append("undo-A"), lambda: log.append("redo-A"))
    assert history.can_undo is True
    desc = history.undo()
    assert desc == "Δράση Α"
    assert log == ["undo-A"]
    assert history.can_undo is False
    assert history.can_redo is True


def test_redo_after_undo():
    history = ActionHistory()
    log = []
    history.push("A", lambda: log.append("u"), lambda: log.append("r"))
    history.undo()
    assert history.redo() == "A"
    assert log == ["u", "r"]
    assert history.can_redo is False


def test_push_clears_redo_stack():
    history = ActionHistory()
    history.push("A", lambda: None, lambda: None)
    history.undo()
    assert history.can_redo is True
    # New action invalidates redo history.
    history.push("B", lambda: None, lambda: None)
    assert history.can_redo is False


def test_undo_empty_returns_none():
    history = ActionHistory()
    assert history.undo() is None
    assert history.redo() is None


def test_max_stack_eviction():
    history = ActionHistory()
    for i in range(ActionHistory.MAX_STACK + 3):
        history.push(f"A{i}", lambda: None, lambda: None)
    # Undoing MAX_STACK times should drain it; the oldest 3 are gone.
    undos = 0
    while history.can_undo:
        history.undo()
        undos += 1
    assert undos == ActionHistory.MAX_STACK


def test_failed_undo_keeps_stack_consistent():
    """If undo_fn raises, the entry must NOT be moved to the redo stack —
    otherwise we'd allow redoing an action whose undo failed partway."""
    history = ActionHistory()

    def boom():
        raise RuntimeError("boom")

    history.push("A", boom, lambda: None)
    with pytest.raises(RuntimeError):
        history.undo()
    # The entry should remain on the undo stack (re-pushed on failure).
    assert history.can_undo is True
    assert history.can_redo is False
