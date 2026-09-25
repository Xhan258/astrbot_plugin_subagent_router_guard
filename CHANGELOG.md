# 更新日志

## 0.1.3

- 按 AstrBot 插件市场审核规范，插件日志记录器只从 `astrbot.api` 导入。

## 0.1.2

- 修复 AstrBot 在不同 asyncio Task 中读取 Handoff 执行结果时，
  `ContextVar` 复位失败的问题。
- 面向中文用户将插件展示名改为“子代理任务分流器”。
- 用更容易理解的中文重写 README 和内部委派提示。

## 0.1.1

- 修复 AstrBot v4.27.5 的插件导入：`Context` 和 `Star` 改从
  `astrbot.api.star` 导入，`AstrBotConfig` 仍从 `astrbot.api` 导入。
- 将运行时日志记录器导入方式对齐 AstrBot v4.27.5。
- 排除 Ruff 开发缓存，避免进入源码仓库和发布 ZIP。

## 0.1.0

- 首次发布主 Agent 工具预算分流功能。
- 支持识别 AstrBot 原生 `HandoffTool`，并默认豁免子 Agent。
- 支持配置不计预算工具与立即委派工具规则。
- 按每次 Agent Run 原子记录预算，并支持卸载时恢复执行入口包装。
