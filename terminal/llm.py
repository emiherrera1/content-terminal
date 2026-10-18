"""Small helpers for model replies."""


def reply_text(msg) -> str:
    """The text of a reply, skipping thinking and other non-text blocks."""
    return "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "text") == "text")
