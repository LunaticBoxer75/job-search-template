"""To-Do tracker — manual items + auto-imported coffee chat action items.

Backed by src/user_store.py (durable JSON), not SQLite — see that module for why.
"""

from datetime import datetime

from . import profile, user_store


def _todo(item, category="general", source="manual", priority="medium"):
    return {"item": item, "category": category, "source": source,
            "priority": priority, "status": "open", "done_at": None}


def add_todo(item, category="general", source="manual", priority="medium"):
    # Dedup by exact item text, checked inside the write so it's race-free.
    # Re-adding something she deleted brings that item back rather than doing nothing.
    gone = next((t for t in user_store.get("todos")
                 if t.get("item") == item and t.get("status") == "deleted"), None)
    if gone:
        return user_store.update("todos", gone["id"], {"status": "open", "done_at": None},
                                 message="Restore to-do")
    return user_store.add_unique("todos", _todo(item, category, source, priority),
                                 unique_on="item", message="Add to-do")


def set_status(todo_id, status):
    user_store.update("todos", todo_id, {
        "status": status,
        "done_at": datetime.now().isoformat() if status == "done" else None,
    }, message="To-do marked done" if status == "done" else f"To-do marked {status}")


def get_todos(status=None):
    """Open and done items (deleted ones are hidden). Pass status to filter."""
    rows = [t for t in user_store.get("todos") if t.get("status") != "deleted"]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return sorted(
        rows,
        key=lambda r: (r.get("status") or "", r.get("created_at") or ""),
        reverse=True,
    )


def delete_todo(todo_id):
    """Hide a to-do. It's marked deleted, not erased: coffee-chat action items are
    re-imported by text on every load, so erasing one would bring it straight back."""
    return user_store.update("todos", todo_id, {"status": "deleted"}, message="Delete to-do")


def import_coffee_chat_actions():
    """Pull action items from coffee chats into todos (dedup by text)."""
    from .networking import get_all_action_items

    items = get_all_action_items()
    existing = {t.get("item") for t in user_store.get("todos")}
    new = [_todo(ai["item"], "from-coffee-chat", ai["from"])
           for ai in items if ai["item"] not in existing]
    # Runs on every render: one commit for all new items, zero if none are new
    if new:
        user_store.add_many("todos", new, "Import coffee-chat action items", unique_on="item")
    return len(new)


def seed_defaults():
    """Seed the standing to-dos listed in config/profile.json (seed.todos) if not present."""
    defaults = profile.load().get("seed", {}).get("todos", [])
    existing = {t.get("item") for t in user_store.get("todos")}
    new = [_todo(i, c, s, p) for i, c, s, p in defaults if i not in existing]
    if new:
        user_store.add_many("todos", new, "Seed standing to-dos", unique_on="item")
