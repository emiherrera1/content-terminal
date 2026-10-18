"""Small helpers for model replies."""


def reply_text(msg) -> str:
    """The text of a reply, skipping thinking and other non-text blocks."""
    return "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "text") == "text")


def warn_if_cut(msg, what: str) -> None:
    """Flag a reply that hit max_tokens (the model can spend tokens thinking before it answers)."""
    if getattr(msg, "stop_reason", None) == "max_tokens":
        print(f"::warning title=reply cut off::{what}: the model reply hit max_tokens; raise it", flush=True)
