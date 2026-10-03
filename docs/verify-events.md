# What fpgas-verify sends to the site

As of 2026-10-03.

On every boot, `fpgas-verify` on each Pi sends seven kinds of boot event to
the site over MQTT. Each carries a flat `detail` of string key/value pairs:
the check starting, each board it found (`board` + `variant`), who each board
is, each test, and a final `pass` / `fail` / `missing` / `changed` / `error`.

The Pis on welland run `fpgas-online-verify 0.0.post771`. Main has moved on
since (the `fpga-identity/1` schema landed on 2026-10-02), so where the two
differ, this page says which one it describes.

## At a glance

```
fpgas-verify ──> fleet-event ──> fleet_consumer ──> BootEvent table
(every Pi boot)  (MQTT, QoS 1)   (on tweed)         (every event kept)
                                                          │
             ┌──────────────────────────┬─────────────────┴──────────┐
             v                          v                            v
        Port gate and              hwid labels                /fleet/<serial>/
        board names                (fpga-identity/1           (current boot,
        (pibfpgas pages)           events only)               shown raw)
```

## Boot events

The events below come in send order. The last column counts each one in
welland's database on 2026-10-03, which holds events since 2026-05-18.

| Stage | When it is sent | Detail keys | On welland |
| --- | --- | --- | --- |
| `fpga-verifying` | First, as the check starts | `started_at` (ISO time, UTC) | 129 |
| `fpga-board-found` | Once per board found, before any test | `board`, `variant`, `where` (PCI slot, USB path or JTAG IDCODE) | 35 |
| `fpga-no-board` | Instead of the above, when nothing is found | `reason` | 7 |
| `fpga-board-identified` | Once per board, after it is probed | who the board is: see below | 18 (Acorns only) |
| `fpga-test-started` | Before each test | `test` (main adds `board`) | 189 |
| `fpga-test-finished` | After each test | `test`, `result`, `reason` (main adds `board`) | 187 |
| `fpga-verified` | Last, always, even if the check crashed | `result`, `mode`, `reason`?, then per board `board<i>` = `"<board> <variant> <result>"`, `board<i>_tests`, `board<i>_reason`, `board<i>_bitstreams`, `board<i>_state_*`, `board<i>_identity_*`, and `changed<j>` | 209 |

Each event is one `fleet-event <stage> --detail k=v ...` call
(fpgas.online-setup-pi `fleet-scripts/fleet_event.py`). It publishes
`{"stage", "boot_id", "ts", "detail"}` with QoS 1 to
`fpgas/<site>/pi/<serial>/event`. The site's `fleet_consumer` stores it as a
`BootEvent` row. Values are always single-line strings; `-` means "read, and
none". A broker that does not answer within 15 s stops the progress events,
but `fpga-verified` is still tried.

`fpga-verified` results:

- `pass`: every board passed every test.
- `fail`: a test failed. `board<i>_reason` says which.
- `missing`: no installed board type was found (seen on the Orange Pi ports
  p18 to p24).
- `changed`: a board's identity or flash differs from the last recorded
  state.
- `error`: `fpgas-verify` itself failed.

An `fpga-verified` from pi-sw2-p46 on 2026-10-03, as stored (shortened):

```json
{"result": "pass", "mode": "auto",
 "board0": "acorn cle-215+ pass",
 "board0_tests": "pcie-link=pass pcie-bar0=pass jtag=pass flash=pass ddr=pass p2-uart=pass p2-serial=pass scratch=pass p2-gpio=pass",
 "board0_bitstreams": "vivado-bitstreams-acorn-pcie-20261001-ge568a408e7bd",
 "board0_identity_variant": "cle-215+", "board0_identity_dna": "0x54b48664b04854"}
```

## Boards found on welland

Every Pi with a board sends `fpga-board-found` each boot, so this is the one
event that names the board for every board type. Only the Acorn sends
`fpga-board-identified` in the deployed version. Each host's newest boot, on
2026-10-03:

| Hosts | `board` | `variant` | `where` | Newest result |
| --- | --- | --- | --- | --- |
| pi-sw2-p46, pi-sw2-p47 | `acorn` | `cle-215+` | `0001:01:00.0` (PCI) | pass |
| pi-sw1-p38, pi-sw2-p37 | `acorn` | `-` (variant not known) | `0001:01:00.0` (PCI) | fail |
| pi-sw2-p9, p10, p12, p15 | `arty` | `a7-35` | `1-1.4` (USB) | fail |
| pi-sw1-p10, p12, p14, p16, p18 | `netv2` | `a7-35` | `0x0362d093` (JTAG IDCODE) | fail |
| pi-sw2-p33, p35, p36 | `tt` | `tt-fpga` | `1-1.2` (USB) | fail |
| pi-sw1-p17 | `fomu` | `evt` | `1-1.1.3` (USB) | pass |
| pi-sw2-p18 to p24 | (sends `fpga-no-board`) | | | missing |

`fpgas-verify`'s own status script (`scripts/collect_verify_status.py`
`BOARD_TITLES`) titles the `board` key: acorn → Acorn, arty → Arty A7,
netv2 → NeTV2, tt → TT FPGA, fomu → Fomu EVT.

## fpga-board-identified

The deployed version sends this only from the Acorn, with no `schema` and no
`kind`. The site's label-input builder (`fleet/hwid.py`) requires both, so it
drops every one of these events today. Main sends it for every board type, in
rpi-hwid's field names.

| | Deployed (0.0.post771) | Main (since 2026-10-02) |
| --- | --- | --- |
| Boards that send it | Acorn only | Every board type |
| `schema` | absent | `fpga-identity/1` |
| `kind` | absent | rpi-hwid kind: `acorn`, `arty`, `netv2`, `tt`, `fomu`, `pcileech`, `unknown-fpga` |
| How a board is told apart | `board`, `variant`, `bdf`, `pci_ids`, `subsystem` | `board`, `variant`, `bdf` / `usb` / `serial` (TT: `usb_serial`) |
| Chip identity | `dna`, `idcode`, `identifier`, `build` | `dna`, `idcode` plus decoded `idcode_*`, `identifier`, `build`, `soc_model` |
| Flash | `flash_part`, `flash_jedec`, `flash_unique_id` | `flash`, `flash_jedec`, `flash_extended_id`, `flash_sfdp`, `flash_uid` (+ bits, state, note), `flash_size_bytes`, `flash_status`, `flash_config`, `flash_quad`, `flash_source` |
| Tiny Tapeout | n/a | `usb_serial`, `mcu`, `shuttle`, `chip`, `repo`, `commit`, `demoboard`, `demoboard_version`, `sdk` |
| A read that failed | absent | `<field>_error` with the reason |

On main, a key that is absent was not read, and `-` was read and is none.
`docs/identity.md` in fpgas.online-test-designs has each field's type.
pi-sw2-p46's event on 2026-10-03 (deployed version):

```json
{"board": "acorn", "variant": "cle-215+", "bdf": "0001:01:00.0",
 "pci_ids": "10ee:7021", "subsystem": "1e24:021f",
 "identifier": "fpgas-online Acorn PCIe SoC cle-215+ 2026-10-01 15:34:49", "build": "operational",
 "dna": "0x54b48664b04854", "idcode": "0x3636093",
 "flash_part": "S25FL256S", "flash_jedec": "0x010219", "flash_unique_id": "edcbeececb2b2a88b04f914d2e46af90"}
```

## What the site reads

The `/fpgas/` pages come from fleet registration alone: no table of Pis or
boards is kept. A Pi is listed when its registered hostname names a port
(`pi-sw<s>-p<p>`, or `pi<p>` at a flat site), it is the newest machine on
that hostname, it is online with a status beat in the last 3 minutes, and
its FPGA check passed in the boot it is running now.

| Site code | Reads | Uses it for |
| --- | --- | --- |
| `fleet/services.py` `machine_hosts()`, `checked_in()` | the registration's hostname; the status beat's `online` and arrival time | Which machine is on each port, and whether it is there now |
| `fleet/services.py` `fpga_states()`, `offered_hosts()` | `fpga-verifying`, `fpga-verified` `result`, current boot only | Which Pis `/fpgas/` lists and serves pages and uploads for |
| `fleet/services.py` `found_boards()` | `fpga-board-found` `board`, `variant`, `where`, current boot only | The board name on `/fpgas/` and each board page |
| `fleet/hwid.py` `fpga_boards()` | `fpga-board-identified` with `schema` `fpga-identity/1` | rpi-hwid label documents |
| `fleet/consumer.py` `_bridge()` | the stage name of every event | A `piview: <stage>` line in that Pi's status log (group `pistat_<hostname>`) |
| `/fleet/<serial>/` page | every event of the current boot | Shown raw, for debugging |

Not read anywhere yet: per-test results, `board<i>_tests` / `board<i>_reason`
in `fpga-verified`, and `fpga-no-board`'s reason.

The broker takes anonymous messages from any Pi on the site LAN, so the
listing is only as trustworthy as that (fpgas.online-infra#196). The
addresses the site reaches (upload, ping, ssh) derive from the port's
hostname, never from anything a message says.

## Sources

- fpgas.online-test-designs `7ee3a5e` (main, 2026-10-03):
  `verify/src/fpgas_online_verify/runner.py` (`EVENTS`, `details()`, `run()`),
  `identity.py`, `core.py` (`flatten()`, `publish()`),
  `scripts/collect_verify_status.py` (`BOARD_TITLES`), `docs/identity.md`.
- fpgas.online-setup-pi main: `fleet-scripts/fleet_event.py`.
- fpgas.online-site `e00e8dc` (main): `fleet/consumer.py`,
  `fleet/services.py`, `fleet/hwid.py`, `fleet/views.py`, `pibfpgas/views.py`.
- welland (tweed) on 2026-10-03: the `BootEvent` table, read through
  `manage.py shell`, and the NFS root's dpkg database.
