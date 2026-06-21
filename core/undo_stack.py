"""Undo/Redo action-history stack for ENCOMM ERP.

Stores up to 5 undo entries as (description, undo_fn, redo_fn) tuples.
Oldest entry is evicted when the stack exceeds MAX_STACK.
Pushing a new action automatically clears any redo history.
"""

from typing import Callable, List, Tuple, Optional

ActionEntry = Tuple[str, Callable, Callable]  # (description_greek, undo_fn, redo_fn)


class ActionHistory:
    MAX_STACK = 5

    def __init__(self):
        self._undo_stack: List[ActionEntry] = []
        self._redo_stack: List[ActionEntry] = []

    # ── properties ──────────────────────────────────────────────────

    @property
    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    @property
    def undo_description(self) -> Optional[str]:
        return self._undo_stack[-1][0] if self._undo_stack else None

    @property
    def redo_description(self) -> Optional[str]:
        return self._redo_stack[-1][0] if self._redo_stack else None

    # ── public API ──────────────────────────────────────────────────

    def push(self, description: str, undo_fn: Callable, redo_fn: Callable) -> None:
        """Record a new undoable action.

        Automatically clears the redo stack (new action invalidates redo history).
        Evicts the oldest entry if the undo stack exceeds MAX_STACK.
        """
        self._redo_stack.clear()
        if len(self._undo_stack) >= self.MAX_STACK:
            self._undo_stack.pop(0)
        self._undo_stack.append((description, undo_fn, redo_fn))

    def undo(self) -> Optional[str]:
        """Execute the most recent undo action.

        Moves the undone entry onto the redo stack so it can be redone later.
        Returns the action description, or None if the undo stack is empty.
        """
        if not self._undo_stack:
            return None
        desc, undo_fn, redo_fn = self._undo_stack.pop()
        undo_fn()
        self._redo_stack.append((desc, undo_fn, redo_fn))
        if len(self._redo_stack) > self.MAX_STACK:
            self._redo_stack.pop(0)
        return desc

    def redo(self) -> Optional[str]:
        """Re-execute the most recently undone action.

        Moves the redone entry back onto the undo stack.
        Returns the action description, or None if the redo stack is empty.
        """
        if not self._redo_stack:
            return None
        desc, undo_fn, redo_fn = self._redo_stack.pop()
        redo_fn()
        self._undo_stack.append((desc, undo_fn, redo_fn))
        if len(self._undo_stack) > self.MAX_STACK:
            self._undo_stack.pop(0)
        return desc
