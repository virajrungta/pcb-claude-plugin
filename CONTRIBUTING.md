# Contributing

## Development setup

```bash
git clone https://github.com/virajrungta/pcb-claude-plugin
```

```bash
cd pcb-claude-plugin && claude --plugin-dir .
```

`--plugin-dir` loads your working copy for one session, so edits to skills or
`kipcb` take effect in the next session without reinstalling.

## Repository layout

| Path | Contents |
|---|---|
| `.claude-plugin/` | Plugin manifest and marketplace entry |
| `skills/` | `design`, `review`, `fab` skills; `design/references/` holds the engineering references Claude reads |
| `agents/` | `circuit-reviewer` subagent |
| `bin/kipcb` | Wrapper that runs `kipcb` under KiCad's Python |
| `scripts/kipcb/` | The engine: spec validation, schematic/board generation, placement, routing, checks, fab, learning |
| `examples/` | Example design specs |
| `tests/` | Unit tests for the pure-Python core |
| `docs/` | User documentation |

## Tests

```bash
PYTHONPATH=scripts python3 -m unittest discover -s tests -v
```

The unit tests need no KiCad and run in CI on every push. For an end-to-end
check with KiCad installed:

```bash
bin/kipcb build examples/c3_sensor.json && bin/kipcb route examples/c3_sensor && bin/kipcb fab examples/c3_sensor
```

Code in `scripts/kipcb` must stay compatible with Python 3.9, the version
bundled with KiCad on macOS.

## Weekly releases

Versions are `V<major>.<minor>`: V1.0, then V1.1, V1.2… each week.

1. As you work, add bullet points under `## Unreleased` in `CHANGELOG.md`.
2. Release:

   ```bash
   scripts/release.sh 1.1
   ```

   This moves the notes under `V1.1`, bumps the version in the plugin manifest
   and `kipcb`, runs the tests, commits `V1.1`, tags `v1.1` and pushes. GitHub
   Actions then publishes the release page from the changelog.

Users get the update with `claude plugin update pcb`.
