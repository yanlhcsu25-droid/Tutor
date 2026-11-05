# 六例冒烟后的根因检查（未修复）

本轮无真实模型重跑，无业务代码/Prompt/评分修改。基于首次报告、调用链阅读、3项确定性复现。

## 1. WF-001：80分丢失

`runtime/coordinator.py` → `ToolExecutor.prepare` → `_apply_explicit_opt_in_guards` → GenerationWorkflow.prepare → GenerationService.preview。

`runtime/request_guards.py`的分值白名单仅接受总分/满分前缀、分数后接试卷等名词、整句独立分数。原句“总共8题，80分，先让我看方案”不匹配。

确定性复现：给Guard传入`{"question_count": 8, "total_score": 80}`，原用户输入不变，返回只有`{"question_count": 8}`。

结论：即使模型正确解析80，Python也会把它删除；随后默认模板产生50分。首轮Trace记录的是Guard后的参数，模型Span只保存调用数和工具名，无法证明该轮模型原始输出是否含80。不能武断归因于模型遗漏，但Guard误删已确定。

修复方向：停止把“正则没命中”当成“教师未指定”的证据。语义来源识别与确定性数值/一致性校验分开；不加一个只覆盖“逗号80分”的补丁，也不能直接无条件相信模型分值。需要明确来源/不确定性的处理契约，并补Guard前后参数观测。

## 2. WF-033：总分100，分项130

`teaching_design/schemas.py:AssessmentPlan`只验证数值范围和ability_weights合计100，没有验证题型count合计或`count * score_each`合计。

直接构造与报告相同AssessmentPlan（3×10、4×15、2×10、1×20）可成功通过Pydantic验证，total_score=100，实际合计130。
Tool create路径使用CreateTeachingDesignInput校验，之后进入TeachingDesignWorkflow或Service持久化；报告中已返回保存产物并等待确认。

结论：模型提出了算术不一致计划，Schema缺少跨字段不变量，且当前候选grader漏检。这不是需要自然语言关键词规则的问题。

修复方向：在共享计划校验入口验证完整显式配额的数量/分值一致性；分值不全时不擅自补齐，也不默改教师总分。错误返回结构化诊断，由模型纠正。覆盖create/revise及不完整方案兼容性。

## 3. WF-051：语言澄清却completed

Coordinator对Route.clarification_needed只限制Tool暴露并添加Prompt指示。随后初始化turn_status=completed、clarification_questions=[]；无Tool的最终文本分支只取content并退出。
FinalizationInput没有路由澄清字段，FinalizationPolicy不会读取自然语言来推断是否在追问。

确定性复现：将“请问希望复习什么内容？”和初始completed交给FinalizationPolicy，结果仍为completed，问题列表为空。
首轮该例无Tool，模型Span记录n_definitions=0，符合路由澄清分支；但报告缺少原始TaskRoute快照，不能据此还原全部语义路由输出。

修复方向：把已存在的结构化Route澄清决定接入最终结果契约，而不是用问号或关键词扫描回复。需验证强状态优先级，不把已有业务产物错误改成澄清。

## 4. WF-043：短缺已处理，结构化问题为空

GenerationService.confirm返回ok=false、needs_clarification=false、clarification_questions=[]，同时含type_supply_shortage诊断和ask_user恢复选项。
GenerationWorkflow.confirm据needs_clarification=false暂映射failed，FinalizationPolicy又根据insufficient_candidates改成needs_clarification，却未填充问题。

确定性复现：failed + insufficient_candidates经FinalizationPolicy变needs_clarification，问题列表仍空。

结论：短缺拒绝和原约束保留有效；结构化恢复信息存在，但向API澄清字段投影不完整。当前grader把“数组非空”等同于澄清有效也过窄，不能凭最终回复带问句就自动认定内容质量合格。

修复方向：基于Tool的diagnosis/recovery_action投影澄清契约，不通过字符串识别问题句；状态和问题字段保持一致，同时保留自然语言输出及原始诊断。

## 建议修复次序

1. 算术不变量：最确定、无需新增语义推断。
2. 澄清契约接线：复用TaskRoute与Tool恢复信息。
3. 分值来源Guard：先设计保留显式需求且防止模型杜撰的通用契约，再改代码；不要扩单句regex。

原始失败保留，修复结果单独记录。涉及Finalization/Domain校验时应明确属于本轮可靠性修复，而不是之前Semantic Routing最小接线范围内的隐式变更。
