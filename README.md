# MathPaper Agent

面向中文初中数学教师的可验证智能组卷系统。教师用自然语言描述要求，检查并确认结构化蓝图后，系统通过严格约束求解生成、持久化并审核试卷；预览、编辑和导出始终读取同一条 `Paper/PaperItem` 版本链。

> 当前项目由旧高数原型独立重构而来，Python 包名暂时保留为 `calculus_agent`，不影响运行；后续稳定数据库迁移后再统一改名。

## 核心闭环

```text
教师自然语言要求
→ 唯一需求解析器创建 Blueprint 草稿
→ 教师编辑 Sections、知识点、难度和随机种子
→ 确认 Blueprint
→ OR-Tools CP-SAT 严格约束组卷
→ 保存 Paper 与 PaperItem
→ 自动生成结构化 ValidationReport
→ 按 paper_id 预览、版本化编辑和导出 PDF/LaTeX
```

LLM 只负责把自然语言转换为蓝图，不直接决定最终选题。所有题型、题量、分值、知识点、难度和排除约束均由代码执行。相同题库快照、蓝图和随机种子会产生相同题目与顺序；题库不足时返回结构化缺口，不会自动放宽要求。

## 唯一正式组卷流程

教师主页面只保留“解析要求 → 编辑蓝图 → 确认并生成 → 编辑已保存试卷 → 导出”这一条业务主线。历史多 Agent 实验代码仍保留用于研究，但不进入正式教师组卷入口，也不能绕过已确认 Blueprint 创建正式 Paper。

## 错题备课任务

第一版不识别学生手写答案，也不推断学生为何出错。教师录入错题、标准答案、标准解析，以及由教师或 ChatGPT 确认的错误原因，并指定年级、知识点、题型和目标难度。系统保存任务后，从已审核题库中按以下规则匹配巩固题：

```text
年级硬过滤
→ 知识点必须重合
→ 同题型加权
→ 难度接近度排序
→ 返回题目、答案、解析和匹配依据
```

该结构后续可直接作为讲义和 PPT 的统一内容来源，并在真实匹配案例积累后加入向量检索与教材 RAG。

错题表单支持上传标准印刷题图片，以及可选的答案/解析图片。SiliconFlow 视觉模型会识别题干、选项、公式、题型、答案、解析和知识点，并回填到表单；教师检查修改后才会保存任务。配置：

```env
SILICONFLOW_API_KEY=你的API-Key
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_VL_MODEL=zai-org/GLM-4.5V
```

也可以使用百炼控制台提供的业务空间专属兼容端点替换 `BASE_URL`。API Key 只能保存在本地 `.env`，不要提交到 Git。

## 已实现

- CMM-Math JSONL 适配，可筛选初中年级、纯文本题以及带完整解析的记录；
- MM-Math 适配与字段审计保留为英文多模态数据实验入口；
- 可信公开数据集批量发布；以后 OCR/教师自有题目仍可走草稿审核流程；
- 唯一自然语言解析入口，生成可编辑、可确认的 Blueprint；
- 年级和难度显式表达的规则兜底；
- Sections 级题型数量、每题分值和部分总分建模；
- OR-Tools CP-SAT 严格约束求解，未知难度题默认排除；
- 结构化 ValidationReport 和不可行供给缺口；
- React + TypeScript + Ant Design 教师端；
- 错题、标准答案、标准解析和错误原因的结构化备课任务；
- 根据知识点、题型与难度匹配巩固题，并保留匹配依据；
- 阿里云百炼标准印刷题图片识别，并将结构化结果回填到教师审核表单；
- `Paper/PaperItem` 持久化以及 `root_paper_id/parent_version_id` 版本链；
- 换题、锁题、调序和改分均创建新 Paper 版本并自动重新审核；
- 预览、审核和 PDF/LaTeX 导出只读取当前 `paper_id`，导出过程不重新选题；
- 学生卷采用标准 A4 校内试卷版式，选择项横向排列，解答题独立成节并按分值预留答题区域；
- 嵌入中文字体的学生卷和教师解析卷 PDF；
- 可二次编辑的学生卷和教师解析卷 LaTeX 源文件；
- UGMathBench 旧适配器保留为可选外部评测入口。
- 主调度 Agent、知识库 Agent和试卷审核 Agent；
- 单 Agent与多 Agent两种运行模式；
- 工具白名单、步数预算、委托预算和重复调用防护；
- Agent运行和工具调用轨迹持久化；
- 工具选择、调用顺序、专业 Agent覆盖和禁用工具评测指标。

## 朋友本地试用

要求电脑已安装 Python 工具 `uv`、Node.js 18+ 和 `pnpm`。克隆项目后，一条命令
完成依赖安装、演示题库初始化和启动：

```bash
./scripts/quickstart.sh
```

打开 `http://127.0.0.1:5173`。安装脚本会自动安装前后端依赖、创建本地
SQLite 数据库并写入15道内置演示题；重复执行不会重复导入。

以后再次启动不需要重装依赖：

```bash
./scripts/start.sh
```

如果想让同一局域网的朋友访问，在你的电脑运行：

```bash
HOST=0.0.0.0 ./scripts/start.sh
```

朋友打开 `http://你的电脑IP:5173`。你的电脑需要保持开机，且防火墙需要允许
5173和8000端口。正式公网试用仍应部署到服务器，不要直接暴露开发服务。

基础组卷、锁题换题、版本保存和PDF导出不依赖模型 API。模型能力统一使用 SiliconFlow，
在 `.env` 中配置以下字段：

```bash
SILICONFLOW_API_KEY=你的API密钥
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_AGENT_MODEL=zai-org/GLM-4.5V
```

后端 API 文档位于 `http://127.0.0.1:8000/docs`。

Linux 环境需要安装兼容的中文 TrueType 字体，或显式提供字体路径：

```bash
export MATH_PAPER_FONT_PATH=/path/to/compatible-cjk-font.ttf
```

## 导入中文 CMM-Math

从官方数据集下载 `all_data.jsonl`，调用
`POST /api/v1/datasets/cmm-math/import`：

```json
{
  "path": "/absolute/path/to/all_data.jsonl",
  "levels": ["七年级", "八年级", "九年级"],
  "text_only": true,
  "require_analysis": true,
  "limit": 3000,
  "publish": false
}
```

首版建议保持 `text_only=true`，因为当前 PDF 渲染链路尚未支持题目多图。
`require_analysis=true` 会排除只有答案、没有解析的记录。CMM-Math 默认进入待审核区；`publish=true`
仅适用于已经抽样检查并确认公式、题型、答案和解析质量的数据；教师后续上传或 OCR
识别的内容不应跳过审核。完整审计见
[数据集审计](docs/dataset-audit.md)。

## 关键接口

- `POST /api/v1/blueprints/parse`：自然语言创建 Blueprint 草稿；
- `GET/PATCH /api/v1/blueprints/{blueprint_id}`：读取或编辑草稿；
- `POST /api/v1/blueprints/{blueprint_id}/confirm`：确认蓝图；
- `POST /api/v1/papers`：根据已确认蓝图生成、保存并审核试卷；
- `GET /api/v1/papers/{paper_id}`：读取同一份持久化试卷和审核报告；
- `POST /api/v1/papers/{paper_id}/items/{item_id}/replace`：换题并创建新版本；
- `PATCH /api/v1/papers/{paper_id}/items/{item_id}`：改分并创建新版本；
- `POST /api/v1/papers/{paper_id}/items/reorder`：调序并创建新版本；
- `POST /api/v1/papers/{paper_id}/items/{item_id}/lock`：锁题并创建新版本；
- `POST /api/v1/papers/{paper_id}/validate`：重新审核已保存试卷；
- `GET /api/v1/papers/{paper_id}/exports/{student|teacher}.{pdf|tex}`：导出当前版本；
- `POST /api/v1/datasets/cmm-math/import`：中文 K12 题库筛选导入；
- `POST /api/v1/datasets/mm-math/import`：英文多模态 MM-Math 实验导入；
- `POST /api/v1/agents/runs`：执行单 Agent或多 Agent组卷任务；
- `GET /api/v1/agents/runs/{run_id}`：读取持久化调度轨迹。

## Agent 路由评测

评测样例位于 `evaluations/cases.jsonl`，同一批任务可以分别运行：

```bash
uv run python -m calculus_agent.evaluations.runner \
  --mode single_agent --output output/evaluations/single-agent.json

uv run python -m calculus_agent.evaluations.runner \
  --mode multi_agent --output output/evaluations/multi-agent.json
```

当前指标包括工具选择精确率、召回率、调用顺序得分、要求的专业 Agent覆盖率和禁用工具调用次数。正式结论必须在真实题库导入后运行完整评测集得到。

## 验证

```bash
uv run pytest
uv run ruff check src tests
cd web && pnpm build
```

当前自动化测试：48 项全部通过；包含 Paper 版本化编辑、预览与导出同源、相同 seed 可复现、未知难度排除，以及 100/1,000/3,000 题题库性能验收。前端 TypeScript 检查和生产构建通过。GitHub Actions 会在 push 和 Pull Request 上自动执行 Ruff、Pytest 和前端生产构建。

## 下一阶段

- 用知识分类 Agent 将 CMM-Math 的粗粒度 `subject` 映射到教材目录知识节点；
- 对未知难度进行离线标注和抽样复核，未标注前不宣称可精确控难；
- 题目图片与 LaTeX 公式的高质量 PDF 排版；
- 增加拖拽排序、更多学校页眉模板和版本恢复操作；
- 建立组卷成功率、知识点覆盖率和难度偏差评测；
- 最后补 Docker 一键启动与公开 Demo；
- OCR 作为独立输入适配器接入，不改变组卷核心。
