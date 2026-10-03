# Golden Smoke v1 — 人工审核清单

- YAML file SHA256: `b093f1748e66c8c4381a444777a0e34bfad7f4cf8e80a3a06386edf6112f03af`
- Manifest dataset SHA256: `f0545f54b4c9703dc42533b38faf1fe04c3c3a03ead45569ef4228413d1d1c34`
- 状态：`frozen`；未经版本升级不得修改 Dataset 或 Contract。
- Code Git SHA：`4ca727e098e6db01ae37b4474610c45a9b11558c`（targeted regressions passed）
- Provider / Model：`siliconflow` / `Qwen/Qwen3.5-35B-A3B`
- 分布：Easy 6 / Medium 9 / Hard 5。

## 逐条审核

### 1. SMOKE-GEN-01 — 第一章5题50分预览

- 来源：`EVAL-002`；类别：`generation/preview_generation`；难度：`easy`
- Turns：“第一章出5题，总分50分，先给方案。”
- Required tools：`prepare_generation_plan`
- Forbidden tools：`confirm_generation, confirm_paper_changes, confirm_teaching_design`
- Side effect：`{"paper_unchanged": true, "forbidden_tools": ["confirm_generation", "confirm_paper_changes", "confirm_teaching_design"]}`
- Expected：
```yaml
status: waiting_confirmation
pending_generation:
  scope_names:
  - 第一章 函数与极限
  question_count: 5
  total_score: 50
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 2. SMOKE-GEN-02 — 显式题型分布预览

- 来源：`EVAL-011`；类别：`generation/preview_generation`；难度：`medium`
- Turns：“第一章出7题，总分70分，选择2道、填空2道、计算3道，先给方案。”
- Required tools：`prepare_generation_plan`
- Forbidden tools：`confirm_generation`
- Side effect：`{"paper_unchanged": true, "forbidden_tools": ["confirm_generation"]}`
- Expected：
```yaml
status: waiting_confirmation
pending_generation:
  scope_names:
  - 第一章 函数与极限
  question_count: 7
  total_score: 70
  question_type_requirements:
  - question_type: 选择题
    count: 2
  - question_type: 填空题
    count: 2
  - question_type: 计算题
    count: 3
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 3. SMOKE-GEN-03 — 两种题型严格预览

- 来源：`EVAL-015`；类别：`generation/preview_generation`；难度：`medium`
- Turns：“第一章出5题，总分50分，选择2道、计算3道，先给方案。”
- Required tools：`prepare_generation_plan`
- Forbidden tools：`confirm_generation`
- Side effect：`{"paper_unchanged": true, "forbidden_tools": ["confirm_generation"]}`
- Expected：
```yaml
status: waiting_confirmation
pending_generation:
  scope_names:
  - 第一章 函数与极限
  question_count: 5
  total_score: 50
  question_type_requirements:
  - question_type: 选择题
    count: 2
  - question_type: 计算题
    count: 3
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 4. SMOKE-GEN-04 — 计算题供给不足不得放宽

- 来源：`EVAL-046`；类别：`generation/shortage_or_conflict`；难度：`hard`
- Turns：“第一章组卷：只要6道计算题，总分60分，题型不能改。” → “确认。”
- Required tools：`prepare_generation_plan, confirm_generation`
- Forbidden tools：`none`
- Side effect：`{"paper_unchanged": true}`
- Expected：
```yaml
status: needs_clarification
pending_generation:
  scope_names:
  - 第一章 函数与极限
  question_count: 6
  total_score: 60
  question_type_requirements:
  - question_type: 计算题
    count: 6
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 5. SMOKE-PENDING-01 — 只改题数并保留总分范围

- 来源：`EVAL-072`；类别：`pending/partial_patch`；难度：`easy`
- Turns：“题数改成7题，其他条件不变。”
- Required tools：`prepare_generation_plan`
- Forbidden tools：`confirm_generation`
- Side effect：`{"paper_unchanged": true, "forbidden_tools": ["confirm_generation"]}`
- Expected：
```yaml
status: waiting_confirmation
pending_generation:
  scope_names:
  - 第一章 函数与极限
  question_count: 7
  total_score: 100
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 6. SMOKE-PENDING-02 — 取消必须清除Pending

- 来源：`EVAL-095`；类别：`pending/cancel`；难度：`easy`
- Turns：“取消这次组卷计划。”
- Required tools：`discard_pending_plan`
- Forbidden tools：`confirm_generation`
- Side effect：`{"paper_unchanged": true, "forbidden_tools": ["confirm_generation"]}`
- Expected：
```yaml
status: completed
pending_generation: null
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 7. SMOKE-PENDING-03 — 修改总分但不确认

- 来源：`EVAL-082`；类别：`pending/patch_no_confirm`；难度：`medium`
- Turns：“先降到90分，暂时不要确认。”
- Required tools：`prepare_generation_plan`
- Forbidden tools：`confirm_generation`
- Side effect：`{"paper_unchanged": true, "forbidden_tools": ["confirm_generation"]}`
- Expected：
```yaml
status: waiting_confirmation
pending_generation:
  scope_names:
  - 第一章 函数与极限
  question_count: 10
  total_score: 90
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 8. SMOKE-PENDING-04 — 确认后真实生成Paper

- 来源：`EVAL-113`；类别：`pending/explicit_confirm`；难度：`hard`
- Turns：“确认，就按当前方案生成。”
- Required tools：`confirm_generation`
- Forbidden tools：`confirm_paper_changes, confirm_teaching_design`
- Side effect：`{}`
- Expected：
```yaml
status: completed
pending_generation: null
paper_db:
  persisted: true
  question_count: 10
  total_score: 100
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 9. SMOKE-READ-01 — 读取题数并与数据库一致

- 来源：`EVAL-126`；类别：`paper/read_count`；难度：`easy`
- Turns：“当前这套卷总共有几道题？”
- Required tools：`read_paper`
- Forbidden tools：`preview_paper_changes, confirm_paper_changes`
- Side effect：`{"business_state_unchanged": true}`
- Expected：
```yaml
status: completed
paper_db:
  persisted: true
  question_count: 10
  total_score: 100
response_facts:
  question_count: 10
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 10. SMOKE-READ-02 — 读取总分并与数据库一致

- 来源：`EVAL-117`；类别：`paper/read_score`；难度：`easy`
- Turns：“这套卷满分多少？不要修改。”
- Required tools：`read_paper`
- Forbidden tools：`preview_paper_changes, confirm_paper_changes`
- Side effect：`{"business_state_unchanged": true}`
- Expected：
```yaml
status: completed
paper_db:
  persisted: true
  question_count: 10
  total_score: 100
response_facts:
  total_score: 100
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 11. SMOKE-READ-03 — 读取题型分布并与数据库一致

- 来源：`EVAL-118`；类别：`paper/read_type_distribution`；难度：`medium`
- Turns：“当前卷各种题型分别有多少题？”
- Required tools：`read_paper`
- Forbidden tools：`preview_paper_changes, confirm_paper_changes`
- Side effect：`{"business_state_unchanged": true}`
- Expected：
```yaml
status: completed
paper_db:
  persisted: true
  question_count: 10
  total_score: 100
  question_type_counts:
    选择题: 4
    填空题: 2
    计算题: 3
    证明题: 1
response_facts:
  question_type_counts:
    选择题: 4
    填空题: 2
    计算题: 3
    证明题: 1
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 12. SMOKE-MODIFY-01 — 删除全卷第5题只创建预览

- 来源：`EVAL-141`；类别：`paper/delete_preview`；难度：`medium`
- Turns：“删除全卷第5题，先给我看修改预览。”
- Required tools：`read_paper, preview_paper_changes`
- Forbidden tools：`confirm_paper_changes`
- Side effect：`{"paper_unchanged": true, "forbidden_tools": ["confirm_paper_changes"]}`
- Expected：
```yaml
status: waiting_confirmation
pending_paper_change:
  exists: true
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 13. SMOKE-MODIFY-02 — 替换全卷第2题只创建预览

- 来源：`EVAL-142`；类别：`paper/replace_preview`；难度：`medium`
- Turns：“把全卷第2题换成更简单的题，其他不变，先预览。”
- Required tools：`read_paper, preview_paper_changes`
- Forbidden tools：`confirm_paper_changes`
- Side effect：`{"paper_unchanged": true, "forbidden_tools": ["confirm_paper_changes"]}`
- Expected：
```yaml
status: waiting_confirmation
pending_paper_change:
  exists: true
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 14. SMOKE-MODIFY-03 — 新增计算题只创建预览

- 来源：`EVAL-144`；类别：`paper/add_preview`；难度：`medium`
- Turns：“给当前卷增加一道计算题，先出修改方案。”
- Required tools：`read_paper, preview_paper_changes`
- Forbidden tools：`confirm_paper_changes`
- Side effect：`{"paper_unchanged": true, "forbidden_tools": ["confirm_paper_changes"]}`
- Expected：
```yaml
status: waiting_confirmation
pending_paper_change:
  exists: true
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 15. SMOKE-TEACH-01 — 两课时教学设计真实持久化

- 来源：`EVAL-208`；类别：`teaching/design_create`；难度：`medium`
- Turns：“给第一章设计一份两课时复习方案，先让我审核。”
- Required tools：`create_teaching_design`
- Forbidden tools：`confirm_teaching_design, prepare_generation_plan`
- Side effect：`{"paper_unchanged": true, "forbidden_tools": ["confirm_teaching_design"]}`
- Expected：
```yaml
status: waiting_confirmation
active_teaching_design:
  persisted: true
  status: awaiting_confirmation
  scope_names:
  - 第一章 函数与极限
  lesson_count: 2
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 16. SMOKE-TEACH-02 — 三课时教学设计真实持久化

- 来源：`EVAL-197`；类别：`teaching/design_create`；难度：`medium`
- Turns：“针对函数与极限安排3课时复习，不要直接确认。”
- Required tools：`create_teaching_design`
- Forbidden tools：`confirm_teaching_design, prepare_generation_plan`
- Side effect：`{"paper_unchanged": true, "forbidden_tools": ["confirm_teaching_design"]}`
- Expected：
```yaml
status: waiting_confirmation
active_teaching_design:
  persisted: true
  status: awaiting_confirmation
  scope_names:
  - 第一章 函数与极限
  lesson_count: 3
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 17. SMOKE-CONSULT-01 — 教学咨询不得写业务状态

- 来源：`EVAL-219`；类别：`teaching/consultation`；难度：`easy`
- Turns：“暂时不要建教学设计，先讲讲高阶导数的教学重点。”
- Required tools：`none`
- Forbidden tools：`create_teaching_design, prepare_generation_plan, preview_paper_changes, confirm_generation, confirm_teaching_design`
- Side effect：`{"business_state_unchanged": true}`
- Expected：
```yaml
status: completed
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 18. SMOKE-CLARIFY-01 — 未限定第N题必须澄清

- 来源：`EVAL-151`；类别：`ambiguity/ambiguous_address`；难度：`hard`
- Turns：“删掉第二题。”
- Required tools：`none`
- Forbidden tools：`preview_paper_changes, confirm_paper_changes`
- Side effect：`{"business_state_unchanged": true, "forbidden_tools": ["preview_paper_changes", "confirm_paper_changes"]}`
- Expected：
```yaml
status: needs_clarification
pending_paper_change: null
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 19. SMOKE-MULTI-01 — 多轮修改题数并保留总分范围

- 来源：`EVAL-291`；类别：`multi_turn/pending_patch`；难度：`hard`
- Turns：“第一章出8题80分，先给方案。” → “题数改成6题，总分不变。”
- Required tools：`prepare_generation_plan`
- Forbidden tools：`confirm_generation`
- Side effect：`{"paper_unchanged": true, "forbidden_tools": ["confirm_generation"]}`
- Expected：
```yaml
status: waiting_confirmation
pending_generation:
  scope_names:
  - 第一章 函数与极限
  question_count: 6
  total_score: 80
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

### 20. SMOKE-MULTI-02 — 多轮预览后确认生成真实Paper

- 来源：`EVAL-279`；类别：`multi_turn/generate_then_confirm`；难度：`hard`
- Turns：“第一章出6题60分。” → “确认。”
- Required tools：`prepare_generation_plan, confirm_generation`
- Forbidden tools：`confirm_paper_changes, confirm_teaching_design`
- Side effect：`{}`
- Expected：
```yaml
status: completed
pending_generation: null
paper_db:
  persisted: true
  question_count: 6
  total_score: 60
```
- [ ] 业务标签合理  - [ ] Fixture 可满足  - [ ] Required Tool 合理  - [ ] 副作用契约合理

