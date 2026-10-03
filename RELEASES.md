# Releases

Every version of the PCB Design plugin, newest first. Full notes for each are in
[CHANGELOG.md](CHANGELOG.md) and on the [GitHub releases page](https://github.com/virajrungta/pcb-claude-plugin/releases).

To update: `claude plugin marketplace update pcb-claude-plugin`, then
`claude plugin update pcb@pcb-claude-plugin`.

| Version | Date | What's new |
|---|---|---|
| [V2.1](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v2.1) | 2026-10-02 | Better placement: layout rules from application notes checked before routing, a layout score against real boards, smarter placement of decoupling, ESD, sensors and regulator parts; builds 3-5x faster; knowledge base grown to 666 open-source projects. |
| [V2.0](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v2.0) | 2026-10-02 | Windows 10/11 support: same plugin and commands as macOS, only the KiCad and Java install differs. |
| [V1.11](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v1.11) | 2026-10-01 | Status bar progress built in: an install option sets up Claude Code's status bar automatically. |
| [V1.10](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v1.10) | 2026-10-01 | Readable progress: Claude reports a progress bar in its own messages after each step. |
| [V1.9](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v1.9) | 2026-10-01 | Cleaner progress display: a short progress bar, shown only when progress changes; optional status bar. |
| [V1.8](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v1.8) | 2026-10-01 | The progress checklist appears as soon as the requirements questions are answered. |
| [V1.7](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v1.7) | 2026-10-01 | Progress checklist shown by the plugin itself, so it works on every Claude Code version. |
| [V1.6](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v1.6) | 2026-10-01 | Progress checklist with step times, roomier placement on large boards, more preflight checks. |
| [V1.5](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v1.5) | 2026-10-01 | Preflight: checks in under a second whether a placed board can route, before spending minutes routing. |
| [V1.4](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v1.4) | 2026-09-30 | Prebuilt circuit blocks: verified sub-circuits (USB-C, regulators, ESP32, chargers, LEDs) in one line each. |
| [V1.3](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v1.3) | 2026-09-30 | One command for the whole pipeline (`kipcb run`) with a final report: ready or not, where, and what to check. |
| [V1.2](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v1.2) | 2026-09-30 | Setup dialog at install: manufacturer, assembly, layer count and learning become your defaults. |
| [V1.1](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v1.1) | 2026-09-30 | Faster failures and no more stranded parts when spacing is tight. |
| [V1.0](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v1.0) | 2026-09-30 | First release: a plain-English idea to a checked, routed KiCad 9 board and manufacturing files. |
