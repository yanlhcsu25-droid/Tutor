# 设计说明

## 设计目标

本 Skill 是语义行为契约，不是新的业务执行层，也不是子 Agent。

推荐调用链：

User
→ Python 确定性加载 SKILL.md
→ LLM 在 Skill + Tool Schema + Domain State 下做语义决策
→ 现有 Tool Layer
→ Python validation / execution
→ Database

## 禁止的架构

不要实现：

User
→ Skill Agent
→ 主 Agent
→ Tool

也不要让 SKILL.md 直接实现 CRUD 或数据库写入逻辑。

## Progressive Disclosure

`SKILL.md` 应保持为运行时核心规则。

`references/` 用于：
- Eval 数据设计
- 边界案例
- 开发调试
- 人工审查

不建议默认把全部 references 注入每次模型调用。
