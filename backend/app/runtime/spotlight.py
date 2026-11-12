"""`Spotlighted`: typed prompt-injection defence.

Per SPEC §10.1 + the step-3 plan change: document-derived text
is wrapped in a per-run random tag with `&`, `<`, `>` escaped BEFORE
wrapping. Prompt builders accept only `Spotlighted` for user text —
type-level enforcement; no linting required.

Tag: a random hex suffix picked at run start (`new_run_tag()`) so a
malicious document can't guess the closing marker. Even if it did,
the escaping would turn its `</tag>` into `&lt;/tag&gt;`.

Escape order: `&` first, THEN `<` and `>` — if `<` was escaped before
`&`, the resulting `&amp;` would be re-escaped to `&amp;amp;`.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass


def new_run_tag() -> str:
    """Per-run random tag suffix. Called once at run start; the
    tag is threaded through the run context and passed to
    `spotlight()` for each piece of document text."""

    return f"document_{secrets.token_hex(4)}"


def _escape(text: str) -> str:
    # `&` MUST be replaced first so the escaped forms of `<` and `>`
    # don't get their own `&` escaped a second time.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass(frozen=True)
class Spotlighted:
    """Wrapped, escaped document text ready to drop into a prompt.

    Prompt builders accept only this type — passing a raw `str` is
    a TypeError at the call site, so we can't accidentally bypass
    the escaping. The `tag` is the per-run marker; `text` is the
    already-escaped body.
    """

    tag: str
    text: str

    def as_prompt_block(self) -> str:
        """Render as `<tag>…</tag>`. The tag is a safe identifier
        (lowercase + digits + underscores) so it needs no further
        escaping itself."""

        return f"<{self.tag}>\n{self.text}\n</{self.tag}>"


def spotlight(text: str, *, tag: str) -> Spotlighted:
    """Escape `text` and wrap it under the per-run `tag`.

    `tag` should come from `new_run_tag()` at run start — passing a
    static or caller-controlled tag defeats the un-guessable-close
    property.
    """

    return Spotlighted(tag=tag, text=_escape(text))
