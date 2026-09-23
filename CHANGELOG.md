# Changelog

## 0.1.1

- Fix AstrBot v4.27.5 plugin import: import `Context` and `Star` from
  `astrbot.api.star`, while retaining `AstrBotConfig` from `astrbot.api`.
- Align the runtime logger import with AstrBot v4.27.5.
- Exclude Ruff's development cache from source control and release ZIPs.

## 0.1.0

- Initial release of deterministic main-agent tool budgeting.
- Native AstrBot HandoffTool detection and SubAgent exemption.
- Configurable exempt and immediate-delegate matching rules.
- Atomic per-Agent-Run budget reservation and reversible executor wrapping.
