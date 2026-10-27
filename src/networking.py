"""Coffee chat / networking tracker — log conversations, takeaways, and action items.

Backed by src/user_store.py (durable JSON), not SQLite — see that module for why.
"""

from datetime import datetime

from . import user_store


def log_chat(person_name, person_role="", person_company="", chat_date=None,
             linkedin_url="", how_connected="", summary="", key_takeaways="",
             action_items="", follow_up_date=""):
    """Log a coffee chat. Takeaways/action_items can be newline-separated strings."""
    if not chat_date:
        chat_date = datetime.now().strftime("%Y-%m-%d")
    return user_store.add("coffee_chats", {
        "chat_date": chat_date,
        "person_name": person_name,
        "person_role": person_role,
        "person_company": person_company,
        "linkedin_url": linkedin_url,
        "how_connected": how_connected,
        "summary": summary,
        "key_takeaways": key_takeaways,
        "action_items": action_items,
        "follow_up_date": follow_up_date,
    }, message=f"Log coffee chat with {person_name}")


def get_all_chats():
    return sorted(
        user_store.get("coffee_chats"),
        key=lambda c: c.get("chat_date") or "",
        reverse=True,
    )


def delete_chat(chat_id):
    return user_store.delete("coffee_chats", chat_id)


def get_all_action_items():
    """Aggregate all action items across chats."""
    items = []
    for chat in get_all_chats():
        if chat.get("action_items"):
            for line in str(chat["action_items"]).split("\n"):
                line = line.strip().lstrip("-•* ")
                if line:
                    items.append({
                        "item": line,
                        "from": chat.get("person_name", ""),
                        "date": chat.get("chat_date", ""),
                    })
    return items
