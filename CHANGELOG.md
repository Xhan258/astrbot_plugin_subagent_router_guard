# Changelog

## 0.1.3

- Import the plugin logger only from `astrbot.api`, as required by AstrBot
  marketplace review rules.

## 0.1.2

- Fix the Handoff ContextVar reset failure when AstrBot consumes executor output
  in different asyncio Tasks.
- Rename the plugin for Chinese users: **子代理任务分流器**.
- Rewrite the README and default internal delegation prompt in plain Chinese.

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
