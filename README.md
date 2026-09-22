# astrbot_plugin_subagent_router_guard

一个通用的 AstrBot 基础设施插件：为**主 Agent**增加轻量、确定性的 Tool Budget（工具预算），并在预算耗尽或命中规则时，强制它转向 AstrBot 原生 SubAgent Handoff（子代理委派）。

它不替模型做任务规划，不决定委派给哪个子代理，也不关心你的 Persona、业务工具或用户场景。

## 要解决什么

在复杂任务中，主 Agent 很容易连续直接调用低层工具：

```text
LLM → tool → LLM → tool → LLM → tool → ...
```

这会让主 Agent 同时承担对话、规划和大量具体执行，既拉长上下文，也使本该由专职 SubAgent 完成的多步骤工作继续堆在主链路上。

AstrBot 已经提供原生 SubAgent Handoff；但仅靠提示词“请主动委派”不是确定性约束。模型仍可能继续直接调用工具。

## 为什么要解决

- 主 Agent 应保留少量直接操作能力，而不是无限承担执行工作流。
- 已经配置了 SubAgent 的实例，应能在任务开始变复杂时稳定回到原生编排机制。
- 不能为此引入第二个 LLM、复杂度分类器或自定义路由框架，否则会增加 token 成本和新的不确定性。
- 未配置 SubAgent 的普通 AstrBot 实例不能被插件锁死。

## 怎么解决

每个用户消息触发的主 Agent Run 都有独立普通工具预算，默认是 2：

```text
第 1 次普通工具 → 允许
第 2 次普通工具 → 允许
第 3 次普通工具 → 不执行原工具，返回 DELEGATE_REQUIRED
任意 Handoff 工具 → 始终允许，不计入预算
```

`DELEGATE_REQUIRED` 是返回给 LLM 的正常 Tool Result，不会直接发送给用户。它要求主 Agent 从**当前实际可用**的 Handoff 工具中自行选择合适的子代理，并携带原始目标、已获得的信息与剩余工作。

插件不会指定 `transfer_to_xxx`，不会写死任何 Agent 名称，也不会替 LLM 决定委派对象。

## 关键设计

### 真正阻止执行

`on_using_llm_tool` 只用于观察，不能被当成可靠 veto。插件包装 AstrBot 的 `FunctionToolExecutor.execute()`：拒绝分支只生成合法的 `CallToolResult`，因此不会进入原始 handler、MCP 调用或本地 `run/call`。

### 识别 Handoff 与子代理

优先使用 AstrBot 官方 `HandoffTool` 类型识别。为兼容经过 schema 转换的工具集，保留可配置的前缀兜底（默认 `transfer_to_`）。

当前 AstrBot 的公开 `AstrAgentContext` 没有 `main/subagent` 标记。插件以官方 `HandoffTool` 的实际执行动态作用域标记其下游 Agent Run 为子代理；不依赖 Persona、Prompt、固定 Agent 名称或 Provider。子代理默认完全豁免预算。

### 正确的回合和并发边界

预算状态以 AstrBot `ContextWrapper` 实例为键，即一次实际 Agent Run；不使用“30 秒后重置”之类的时间猜测。每个 Run 的预算预约由异步锁保护，因此同一个模型响应内出现多个工具调用时，剩余 1 次也只会放行 1 个。

### 无子代理时不锁死

当当前实际工具集合中没有任何 Handoff，默认行为是放行普通工具。WebUI 可切换为拒绝。

## WebUI 配置

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| 启用工具预算 | 开 | 总开关 |
| 主 Agent 每回合普通工具预算 | `2` | 非 Handoff 工具允许次数 |
| 自动识别原生 SubAgent Handoff | 开 | 优先官方类型识别 |
| Handoff 工具匹配前缀 | `transfer_to_` | 兼容性兜底规则 |
| 不计预算工具规则 | 空 | 轻量工具可不消耗预算 |
| 立即要求委派工具规则 | 空 | 首次调用即拦截 |
| 无可用子代理时 | 放行 | 避免未配置 SubAgent 时失能 |
| DELEGATE_REQUIRED 内部提示文本 | 内置文本 | 返回给 LLM 的提示 |
| 调试日志 | 关 | 不记录参数或完整工具结果 |

规则可逐条选择 `exact`、`prefix`、`contains` 或 `regex`。

## 安装

1. 从 [Releases](../../releases) 下载 ZIP，或克隆本仓库。
2. 将插件目录放入 `AstrBot/data/plugins/`。
3. 在 AstrBot WebUI 的“插件”页面重载插件。
4. 在“SubAgent”页面启用并配置至少一个原生子代理。
5. 保持默认预算 `2`，再按实际工具粒度调整规则。

## 验证清单

- 主 Agent 前两次普通工具调用正常执行；第三次收到 `DELEGATE_REQUIRED`，且原工具没有执行。
- 预算耗尽后，任意原生 Handoff 仍可调用。
- 多个 Handoff 存在时，插件不指定目标。
- 子 Agent 连续调用工具不受预算限制。
- 新用户消息的新 Agent Run 从 0 开始计数；不同会话互不影响。
- 并行工具调用不会穿透剩余预算。
- “立即要求委派”规则首次命中即拦截；“不计预算”规则不消耗预算。
- 没有 Handoff 时默认仍可调用普通工具。
- 重载/卸载插件后，原执行器包装被恢复。

独立策略测试：

```powershell
python -m unittest tests/test_guard.py -v
```

## 边界与兼容性

本插件只做主 Agent 工具预算与原生 Handoff 强制转向；不做权限验证、工具安全鉴权、Prompt Injection 防护、子代理规划、记忆系统或自定义多代理框架。

依赖 AstrBot 内置 Agent Runner 的当前执行链路，声明支持 `>=4.23.1,<5`。升级 AstrBot 后，应先按上方清单验证一次真实 Handoff 与插件重载路径。

## 开发与发布

```powershell
python -m unittest tests/test_guard.py -v
python -m py_compile __init__.py main.py guard.py runtime.py
```

发布 ZIP 必须以 `astrbot_plugin_subagent_router_guard/metadata.yaml` 为根路径，且归档路径使用 `/`。项目的 GitHub Releases 附带已校验的安装包。
