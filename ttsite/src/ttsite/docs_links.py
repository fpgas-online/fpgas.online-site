"""Curated Tiny Tapeout documentation index shown at /docs/ and linked from board pages."""

SECTIONS = [
    {"title": "Getting started", "links": [
        {"label": "Tiny Tapeout", "url": "https://tinytapeout.com/", "note": "the project"},
        {"label": "Demo board guide", "url": "https://tinytapeout.com/guides/get-started-demoboard/", "note": "the board you are driving"},
        {"label": "Commander app (upstream)", "url": "https://github.com/TinyTapeout/tt-commander-app", "note": "what this page embeds"},
        {"label": "Commander app (fpgas.online fork)", "url": "https://github.com/fpgas-online/tt-commander-app", "note": "web-transport fork"},
    ]},
    {"title": "Specs", "links": [
        {"label": "Tech specs", "url": "https://tinytapeout.com/specs/", "note": ""},
        {"label": "Pinouts & PMODs", "url": "https://tinytapeout.com/specs/pinouts/", "note": ""},
        {"label": "MicroPython SDK / firmware", "url": "https://github.com/TinyTapeout/tt-micropython-firmware", "note": "the REPL you see in the terminal tab"},
        {"label": "Demo board PCB", "url": "https://github.com/TinyTapeout/tt-demo-pcb", "note": ""},
    ]},
    {"title": "FPGA emulation", "links": [
        {"label": "FPGA breakout guide", "url": "https://tinytapeout.com/guides/fpga-breakout/", "note": "iCE40UP5K ASIC simulator"},
        {"label": "tt-support-tools", "url": "https://github.com/TinyTapeout/tt-support-tools", "note": "tt_fpga.py harden / configure"},
        {"label": "Breakout PCB", "url": "https://github.com/TinyTapeout/breakout-pcb", "note": ""},
    ]},
    {"title": "Chips", "links": [
        {"label": f"TT{n:02d}", "url": f"https://tinytapeout.com/chips/tt{n:02d}/", "note": ""} for n in range(1, 11)
    ]},
    {"title": "KianV", "links": [
        {"label": "KianV uLinux SoC (TT06)", "url": "https://tinytapeout.com/chips/tt06/tt_um_kianV_rv32ima_uLinux_SoC/", "note": ""},
        {"label": "kianRiscV", "url": "https://github.com/splinedrive/kianRiscV", "note": "source + Linux images"},
        {"label": "QSPI Pmod", "url": "https://github.com/mole99/qspi-pmod", "note": "flash + PSRAM the SoC needs"},
    ]},
    {"title": "This instance", "links": [
        {"label": "fpgas.online", "url": "https://fpgas.online/", "note": "the general FPGA boards"},
        {"label": "Design spec", "url": "https://github.com/fpgas-online/fpgas.online-infra/blob/main/docs/superpowers/specs/2026-08-22-tinytapeout-fpgas-online-design.md", "note": ""},
        {"label": "Source: fpgas.online-site", "url": "https://github.com/fpgas-online/fpgas.online-site", "note": ""},
    ]},
]
