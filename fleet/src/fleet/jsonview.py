"""A registration or event shown as JSON for a person (#93): laid out, keys sorted, each kind of value in its own
class for the stylesheet to colour, and a string longer than LONG cut with "…" and its whole text in the title
(an ssh host key is one line, not a screen). Everything is escaped: the documents come from the Pis, and the
broker is open on the site LAN."""

import json
import re

from django.utils.html import escape
from django.utils.safestring import mark_safe

LONG = 72  # characters of a string shown before it is cut
TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"(?P<key>\s*:)?|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null')


def _span(css, text, title=""):
    attr = f' title="{escape(title)}"' if title else ""
    return f'<span class="j-{css}"{attr}>{escape(text)}</span>'


def _token(match):
    text = match.group(0)
    if text[0] == '"':
        if match.group("key"):
            return _span("key", text[: -len(match.group("key"))]) + escape(match.group("key"))
        value = json.loads(text)
        if len(value) > LONG:
            return _span("str", json.dumps(value[:LONG], ensure_ascii=False)[:-1] + '…"', value)
        return _span("str", text)
    if text in ("true", "false"):
        return _span("bool", text)
    if text == "null":
        return _span("null", text)
    return _span("num", text)


def render(value):
    """`value` as highlighted, indented JSON (safe HTML for a <pre>)."""
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    out, end = [], 0
    for match in TOKEN.finditer(text):
        out.append(escape(text[end:match.start()]))
        out.append(_token(match))
        end = match.end()
    out.append(escape(text[end:]))
    return mark_safe("".join(out))


def compact(value):
    """`value` as one line of JSON, for a table cell (keys sorted)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(", ", ": "), default=str)
