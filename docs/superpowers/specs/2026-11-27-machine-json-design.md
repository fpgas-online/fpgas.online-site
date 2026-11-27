# /fleet/<serial>/machine.json: design

Date: 2026-11-27. Status: approved by the user ("Expand the MCP so it can surface all the details
from /fleet and switches"). The fpgas.online MCP (fpgas-online/fpgas.online-mcp) needs one Pi's
full details as data; the device page is for people and may change layout.

## The contract: `GET /fleet/<serial>/machine.json`

Everything the device page `/fleet/<serial>/` shows, for programs (the page is for people and may
change its layout; this is the contract, as `/fleet/registry.json` is for the list). Same rules as
registry.json: times ISO 8601 with offset (UTC); a field the site does not know is `null`, never
guessed; keys are stable words, not the page's titles; marks (✓ ✗ ⟳) never appear, only result
words. 404 `{"error": "no host with this serial has registered"}` for an unknown serial.

```
{
  "site": "<site>", "generated_at": "<iso>", "serial": "<serial>",
  "page": "<absolute url of /fleet/<serial>/>",
  "machine": {
    "hostname", "label", "model", "model_full", "memory",            # as the page's head
    "port", "port_note",                                               # the port the gateway saw it on
    "condition": "operational" | "attention" | "missing" | "checking",
    "offered": bool, "offered_reason": null | "<why not offered>",
    "board_page": null | "<url of its /fpgas/ page when it has one>",
    "first_seen", "last_seen", "checked", "uptime_s" | null,
    "boot_id", "setup_port": bool, "cameras": [..] | null
  },
  "boards": [ {                                                        # this boot's boards
    "type", "variant", "id" | null, "label", "result", "reason",
    "tests": {"<name>": {"result": "<word>", "reason": "<text>"}},     # every check column
    "docs": null | "<url>" } ],
  "boot_checks": [ {                                                   # newest first, as the page
    "boot_id", "this_boot": bool, "at", "running": bool, "result", "reason",
    "boards": [ {"type", "result", "reason",
                 "tests": {"<name>": {"result", "reason"}}} ] } ],
  "labels": {
    "missing": [ {"label", "field", "sent_by", "sent_by_title"} ],
    "notes": ["<text>"], "refusals": [ {"kind", "text"} ],
    "images": [ {"title", "png", "pdf"} ], "label_input": "<url>" },
  "network": null | {
    "state", "administrative_state", "interface", "mac", "changed", "recorded",
    "last_ack", "last_ack_source", "expires", "boot_id", "current_boot",
    "addresses": [ {"family", "address", "scope", "valid_lifetime_s", "preferred_lifetime_s"} ],
    "default_routes": [ {..as the record has them..} ],
    "lease": null | { ..the DHCP lease fields the page shows.. } },
  "network_error": null | "<why the section cannot be shown>",
  "switch_port": null | { ..that port's row exactly as /management/switches.json has it.. },
  "registrations": [ {"fingerprint", "current": bool, "first_seen", "last_confirmed",
                      "document": { ..the registration document as sent.. }} ],
  "events": [ {"ts", "kind", "stage", "test", "result", "reason", "detail": { ..as sent.. } | null} ],
  "console": null | {"since", "receiving_since", "receiver_late": bool,
                     "lines": [ {"at", "text"} ]},
  "console_error": null | "<text>", "console_not_set_up": null | "<text>"
}
```

Query parameters (optional, for size): `events=<n>` keeps the newest n events, `console=<n>` the
newest n console lines (default: all, as the page). Unknown parameters are ignored.

The page and the JSON must come from the same code path (one function builds the data, the page
renders it, the JSON returns it), so they cannot drift.

## As built (2026-11-27)

The view's docstring (`management.views.machine.machine_json`) is the contract; it adds to the shape above
only keys the page shows that the sketch left out, and changes none:

- `machine`: `registered_model` (the model string the registration sent: the page's Model hover), `port_seen_at`
  (when the gateway leased it an address on that port: the Port hover), `condition_reason` (the State hover),
  `check` (this boot's check result word: the Boot check row), `online` (the Online badge). `model` is the short
  form ("Pi 3B+"), `model_full` the label's ("Pi 3 Model B+").
- `network.addresses[]`: `prefixlen` (the page shows address/prefixlen). `changed` is when the record arrived (as
  registry.json's `changed`), `recorded` the record's own `at`. A host known only from the gateway's ack has the
  record's fields null, empty lists and `lease` null.
- `switch_port_error`: null, or why there is no switch row (the switches cannot be read, or the host is on no port
  they read): the page says it, and `switch_port` alone would be a bare null.
- `console`: `address`, `count`, `partial` (the page's sentence above the lines).
- A test's `result` is the reported result as sent, else `not_run` or `not_applicable`; its `reason` the page's
  words for the cell. `switch_port` is the switches.json row verbatim, so it carries that row's own display
  fields (its `net` and `link` marks among them): it is the switch dashboard's contract, not this one's.
- `events=` / `console=` that are not whole numbers are a 400 `{"error"}`.
