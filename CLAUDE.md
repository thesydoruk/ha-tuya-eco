# ha-tuya-eco — rules for AI agents

## Workflow rules

- **Use cheaper models for cheap work.** Always delegate tasks that do not need the
  expensive model to subagents running on a cheaper model (`model: "haiku"` or
  `model: "sonnet"` in the Agent tool): web research, reading/summarising sources,
  grepping and locating code, running tests/linters and reporting results, writing
  boilerplate (translations, YAML profiles, docstrings), mechanical refactors.
  Keep the expensive model for architecture, protocol/crypto code, concurrency and
  reconnect logic, and anything that needs cross-file judgement.
- Run long commands (pip install, pytest of the whole suite) in the background.
- Never commit secrets: local keys, tokens, client secrets, QR tokens. Test fixtures
  must use obviously fake values.

## Project layout

- `custom_components/tuya_eco/` — the integration (domain `tuya_eco`).
  - `providers/` — one module per ecosystem (cloud login + device list with local keys).
    Register new providers in `providers/registry.py` only.
  - `local/` — LAN protocol engine (discovery, link, device state machine).
  - `mapping/` — DP schema → entity description rules.
  - `profiles/` — optional YAML profiles keyed by product id.
  - platform files (`switch.py`, `light.py`, …) — thin, no protocol logic.
- `docs/ARCHITECTURE.md` — read this first before changing core modules.
- `tests/` — pytest with `pytest-homeassistant-custom-component`.

## Conventions

- Python 3.12+, type hints everywhere, `from __future__ import annotations`.
- All blocking I/O (tinytuya) goes through executor jobs; never block the event loop.
- Devices are keyed by Tuya device id, never by IP.
- Providers must not import platform code; platforms must not import providers.
- Lint with `ruff check` and `ruff format` before committing.

## Commands

```bash
.venv/Scripts/python -m pytest -q
```

```bash
.venv/Scripts/python -m ruff check custom_components tests
```
