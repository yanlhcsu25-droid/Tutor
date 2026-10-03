# Tutor 工作流候选评测 v0

## 定位与边界

`tests/evals/cases/teacher_workflow_candidate_v0.yaml` 包含60个开发者可见的候选场景，
不是独立 Blind Holdout，也尚未获得教师审核或真实模型通过率。不得将离线 fixture
测试通过表述为60个Agent任务通过。现有100条Intent集仍属于Contract/Regression。

场景：新建组卷12、Pending调整/取消10、试卷读取/修改10、教学设计/咨询10、
供给不足4、目标缺失4、歧义5、否定5。当前不是版本/幂等全覆盖；该部分保留既有回归和故障注入。

每个场景定义输入、初始状态、容许状态、禁止动作及状态断言。v0.2取消固定required_tools，所有场景含pending人工审核项；不以Tool名或自动检查通过替代任务成功。
这些预期是待审核业务契约，不按模型结果修改标签来刷通过率。
组卷成功指符合该任务的预期：预览应等待确认，短缺应明确阻塞，不能统一要求completed。

## 离线预检

```bash
uv run pytest -q tests/evals/test_workflow_candidate.py
```

检查类别、唯一性、grader一致性，逐例构造隔离数据库与fixture；不调用LLM。

## 真实执行（付费）

先人工审核标签，然后选少量case进行执行器冒烟：

```bash
RUN_LIVE_LLM=1 RUN_WORKFLOW_CANDIDATE=1 WORKFLOW_CASE_IDS=WF-001,WF-023,WF-051 \
  uv run pytest -q tests/evals/test_workflow_candidate.py -k live
```

正式三轮：

```bash
RUN_LIVE_LLM=1 RUN_WORKFLOW_CANDIDATE=1 WORKFLOW_REPEATS=3 \
  uv run pytest -q tests/evals/test_workflow_candidate.py -k live
```

每次创建新的`tests/evals/reports/workflow-candidate-*.jsonl`，逐例flush，不覆盖旧报告。
记录dataset SHA、Git SHA/dirty、轮次、耗时、Runner原始结果和异常。失败不删除，不自动重试。
Runner负责独立会话/数据库、真实模型、既有grader与Trace；异常按失败保留。
v0.2新增workflow_safety：逐轮比较隔离库全部Paper/PaperItem及当前Paper版本指针，检查禁止动作和结构化澄清问题非空。不检测同轮内写入后撤回的瞬时变化。
Pending、题型等state断言仍主要在最后一轮，未完成全部中间业务产物审计。
报告task_success固定为null，另列automated_checks_passed；人工审核前不得汇总为任务成功率。容许澄清也不自动视为正确澄清。
供给不足但提前澄清是否必须保存Pending的契约仍待确认；现有Pending字段断言可能拒绝未保存方案的提前澄清，这不是已解决的标签问题。

## 正式盲测交接

由不了解实现的教师/助教独立编写另一份数据（不能把本文件更名为blind）。
审核业务标签、fixture供给和容许结果；冻结题库/模型/数据/代码版本后运行，首轮结果封存。
已查看并用于修复的案例转Regression。三轮相同case不能当作180个独立业务样本，
区间估计应以case分组抽样。不能以增加依赖或100%回归通过替代这一步。

## 效率实验

使用`tests/evals/cases/preparation_efficiency_tasks_v0.csv`的10个任务作为实验任务草案。
人工题库操作和Tutor两种方式均须达到同一质量门槛，记录参与者、顺序、耗时、纠错时间和完成情况。
交叉平衡顺序，避免先做人工后做Tutor的学习效应；不要求同一个人机械重复同一道题3次。
真实教师/助教尚未参加，因此当前无效率提升数据。另行收集，不能由模型填写计时结果。

知识检索holdout需要针对冻结Curriculum节点逐条人工标注相关项；本批不编造100条gold节点标签。
