"""The one predicate that decides whether an account may read a message row.

Every query against `messages` that answers a person must carry this. It returns
a SQL condition rather than filtering a fetched list on purpose: dropping rows
in the serializer still selects the text out of the database and still puts it
on the wire, where anyone can read it in the browser's network panel. Filtering
in the frontend is the same mistake one layer further out.

One function rather than a repeated `or_(...)` so that the rule has a single
definition. When private messages grow a second kind of reader — a thread the
assistant shares with two people, say — this is the only place that has to learn
about it, and every call site inherits the change instead of being audited again.
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from src.database.models import Message


def visible_to(user_id: str) -> ColumnElement[bool]:
    """Build the condition restricting `messages` to what ``user_id`` may read.

    Args:
        user_id: The account the rows are being read for. Pass the authenticated
            caller, never a value the client supplied.

    Returns:
        A condition for ``.where(...)``: the message is public, or it is private
        and addressed to this account.
    """
    return or_(
        Message.visibility == "public",
        Message.visible_to_user_id == user_id,
    )


def public_only() -> ColumnElement[bool]:
    """Build the condition for readers that are not one specific person.

    Used where the consumer is a process rather than an account — profile
    inference, and the translation agent's context window, which is built once
    and then delivered to every member of a conversation. A private message must
    never reach either: whatever it produced would be shown to people the
    message was deliberately kept from.
    """
    return Message.visibility == "public"
