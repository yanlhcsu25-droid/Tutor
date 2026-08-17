import { useState, useEffect, useRef } from "react";
import { Drawer, Table, Tag, Typography, Input, Select, Space, Empty, Button, Spin, Alert, Modal, Card, message, Popconfirm, InputNumber, Collapse, Radio } from "antd";
import { SearchOutlined, ArrowLeftOutlined, TagsOutlined, EditOutlined, DeleteOutlined, EyeOutlined } from "@ant-design/icons";
import PreviewPane from "./PreviewPane";
import { extractPlainQuestionPreview } from "../utils/questionMarkdown";
import { wb } from "../api";
import type { WbChapter } from "../api";
import "./QuestionBankDrawer.css";

  /** 正式题库列表项 —— 来自 GET /api/v1/questions/search（approved + 仅用户自己 OCR 导入）。 */
  interface QuestionRow {
  id: string;
  question_text: string;
  question_type: string;
  knowledge: string[];
  original_number: string | null;
  source_name: string | null;
  source_page: number | null;
  chapter: string | null;
  chapter_id: string | null;
  chapter_status: "ok" | "missing" | "unresolvable" | null;
  difficulty: number | null;
  knowledge_match_status: string;
  publish_source: "manual" | "ai_auto";
  quality_sample_required: boolean;
}

/** 正式题详情 —— 来自 GET /api/v1/questions/{id}。 */
interface QuestionDetail extends QuestionRow {
  solution_content: string | null;
  final_answer: string | null;
  chapter: string | null;
  knowledge_node_ids: string[];
  is_active: boolean;
  ai_review?: { reason?: string } | null;
}

interface EditDraft {
  id: string;
  question_text: string;
  solution_content: string;
  question_type: string;
  chapter: string;
  knowledge_node_ids: string[];
  original_number: string;
  source_page: number | null;
  difficulty: number | null;
}

interface KnowledgeSuggestionItem {
  question_id: string;
  question_text: string;
  question_type: string;
  suggestions: { knowledge_node_id: string; name: string; score: number; evidence: string[] }[];
}

interface QuestionProfileItem {
  profile_id: string; question_id: string; question_text: string; question_type: string;
  knowledge: string[]; difficulty: number; estimated_time_min: number;
  reasoning_depth: number; calculation_load: number; knowledge_depth: number;
  comprehensive_level: number; confidence: number; profile_status: string;
  profile_source: string; reason: string;
}

const { Option } = Select;

/** 合法题型（与后端 canonical 契约对齐；后端是唯一权威）。
 * canonical 契约固定为 5 个值：选择题 / 填空题 / 计算题 / 证明题 / unknown。
 * 多选题作为选择题的别名已在契约层收敛，不在 UI 入口单独暴露；
 * unknown 表示「未知 / 未分类」，纳入选择器便于人工重新归类。 */
const QUESTION_TYPES = ["选择题", "填空题", "计算题", "证明题", "unknown"];
const QUESTION_TYPE_LABEL: Record<string, string> = {
  "选择题": "选择题",
  "填空题": "填空题",
  "计算题": "计算题",
  "证明题": "证明题",
  "unknown": "未知（未分类）",
};

const EMPTY = "—";
const KNOWLEDGE_EMPTY = "暂未标注";

function formatSource(name: string | null, page: number | null): string {
  if (!name) return EMPTY;
  return page != null ? `${name} · 第${page}页` : name;
}

interface Props {
  open: boolean;
  onClose: () => void;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 20 }}>
      <Typography.Text strong>{title}</Typography.Text>
      <div style={{ borderBottom: "1px solid #f0f0f0", margin: "6px 0 10px" }} />
      <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.7 }}>{children}</div>
    </div>
  );
}

export default function QuestionBankDrawer({ open, onClose }: Props) {
  const [data, setData] = useState<QuestionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [publishSourceFilter, setPublishSourceFilter] = useState<string>("all");
  const [chapterFilter, setChapterFilter] = useState<string>("all");
  const [chapters, setChapters] = useState<WbChapter[]>([]);
  const [editDraft, setEditDraft] = useState<EditDraft | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const editOriginalRef = useRef<QuestionDetail | null>(null);
  const [knowledgeOptions, setKnowledgeOptions] = useState<{ value: string; label: string }[]>([]);

  const [detail, setDetail] = useState<QuestionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [knowledgeItems, setKnowledgeItems] = useState<KnowledgeSuggestionItem[]>([]);
  const [knowledgeSelection, setKnowledgeSelection] = useState<Record<string, string[]>>({});
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profiles, setProfiles] = useState<QuestionProfileItem[]>([]);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: "50" });
      // 「我的题库」默认只展示用户自己导入并发布的题（source_name=ocr_import），
      // built-in-demo / test_source 等示例数据默认隐藏、不删除。
      params.set("source_name", "ocr_import,ocr_doc");
      if (search.trim()) params.set("query", search.trim());
      if (typeFilter !== "all") params.set("question_type", typeFilter);
      if (publishSourceFilter !== "all") params.set("publish_source", publishSourceFilter);
      if (chapterFilter !== "all") params.set("chapter_id", chapterFilter);
      const response = await fetch(`/api/v1/questions/search?${params.toString()}`);
      if (!response.ok) {
        const body = await response.text();
        throw new Error(`HTTP ${response.status}: ${body.slice(0, 500)}`);
      }
      setData((await response.json()) as QuestionRow[]);
    } catch (error) {
      console.error("[QuestionBank] list loading failed", error);
      setError("题库加载失败，请稍后重试");
      setData([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(load, 250);
    return () => clearTimeout(timer);
  }, [open, search, typeFilter, publishSourceFilter, chapterFilter]);

  // 加载当前激活教材的一级章节（大章节）供筛选
  useEffect(() => {
    if (!open) return;
    wb.listChapters()
      .then((result) => setChapters(result.items))
      .catch(() => setChapters([]));
  }, [open]);

  useEffect(() => {
    if (!open) setDetail(null);
  }, [open]);

  const openDetail = async (id: string) => {
    setDetailLoading(true);
    try {
      const response = await fetch(`/api/v1/questions/${id}`);
      if (!response.ok) {
        const body = await response.text();
        throw new Error(`HTTP ${response.status}: ${body.slice(0, 500)}`);
      }
      setDetail((await response.json()) as QuestionDetail);
    } catch (error) {
      console.error("[QuestionBank] detail loading failed", error);
      setError("题目详情加载失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const loadProfiles = async () => {
    setProfileLoading(true);
    try {
      const batch = await fetch("/api/v1/question-profiles/batch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_name: "ocr_import", force: false }),
      });
      if (!batch.ok) throw new Error("题目画像预标失败");
      const response = await fetch("/api/v1/question-profiles?source_name=ocr_import");
      if (!response.ok) throw new Error("题目画像加载失败");
      setProfiles(await response.json());
      setProfileOpen(true);
    } catch (e) { message.error(String(e)); }
    finally { setProfileLoading(false); }
  };

  const setProfileValue = (profileId: string, field: keyof QuestionProfileItem, value: number) => {
    setProfiles((items) => items.map((item) => item.profile_id === profileId ? { ...item, [field]: value } : item));
  };

  const approveProfile = async (item: QuestionProfileItem) => {
    setProfileLoading(true);
    try {
      const response = await fetch(`/api/v1/question-profiles/${item.profile_id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          difficulty: item.difficulty, estimated_time_min: item.estimated_time_min,
          reasoning_depth: item.reasoning_depth, calculation_load: item.calculation_load,
          knowledge_depth: item.knowledge_depth, comprehensive_level: item.comprehensive_level,
          confidence: item.confidence, reason: item.reason, approve: true,
        }),
      });
      if (!response.ok) throw new Error("画像审核保存失败");
      const approved = await response.json();
      setProfiles((items) => items.map((value) => value.question_id === approved.question_id ? approved : value));
      message.success("题目画像已批准");
    } catch (e) { message.error(String(e)); }
    finally { setProfileLoading(false); }
  };

  const openEdit = async (id: string) => {
    setEditSaving(true);
    try {
      const [detailResponse, optionsResponse] = await Promise.all([
        fetch(`/api/v1/questions/${id}`),
        fetch("/api/v1/questions/edit-options"),
      ]);
      if (!detailResponse.ok || !optionsResponse.ok) throw new Error("load failed");
      const item = await detailResponse.json() as QuestionDetail;
      const options = await optionsResponse.json() as { knowledge: { id: string; name: string }[] };
      setKnowledgeOptions(options.knowledge.map((node) => ({ value: node.id, label: node.name })));
      editOriginalRef.current = item;
      setEditDraft({
        id: item.id,
        question_text: item.question_text,
        solution_content: item.solution_content ?? "",
        question_type: item.question_type,
        chapter: item.chapter ?? "",
        knowledge_node_ids: item.knowledge_node_ids,
        original_number: item.original_number ?? "",
        source_page: item.source_page,
        difficulty: item.difficulty,
      });
    } catch {
      message.error("题目编辑信息加载失败");
    } finally {
      setEditSaving(false);
    }
  };

  const patchQuestionType = async (questionId: string, questionType: string): Promise<QuestionDetail> => {
    const response = await fetch(`/api/v1/questions/${questionId}/question-type`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_type: questionType }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { detail?: string } | null;
      throw new Error(body?.detail ?? `HTTP ${response.status}`);
    }
    return (await response.json()) as QuestionDetail;
  };

  const saveEdit = async () => {
    if (!editDraft || !editDraft.question_text.trim() || !editDraft.question_type.trim()) {
      message.warning("题目内容和题型不能为空");
      return;
    }
    setEditSaving(true);
    try {
      // 仅修改题型：走专用轻量端点，避免整题重校验知识点 / 重置审核态 / 标记知识点待重匹配。
      const orig = editOriginalRef.current;
      const onlyTypeChanged =
        orig != null &&
        editDraft.question_type !== orig.question_type &&
        editDraft.question_text.trim() === (orig.question_text ?? "").trim() &&
        (editDraft.solution_content ?? "") === (orig.solution_content ?? "") &&
        (editDraft.difficulty ?? null) === (orig.difficulty ?? null) &&
        (editDraft.original_number ?? "") === (orig.original_number ?? "") &&
        editDraft.source_page === orig.source_page &&
        JSON.stringify(editDraft.knowledge_node_ids) === JSON.stringify(orig.knowledge_node_ids);
      if (onlyTypeChanged) {
        const updated = await patchQuestionType(editDraft.id, editDraft.question_type);
        setEditDraft(null);
        editOriginalRef.current = null;
        setDetail((current) => (current?.id === updated.id ? updated : current));
        message.success("题型已更新（审核状态与知识点保持不变）");
        await load();
        return;
      }
      const response = await fetch(`/api/v1/questions/${editDraft.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(editDraft),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(body?.detail ?? `HTTP ${response.status}`);
      }
      const updated = await response.json() as QuestionDetail;
      setEditDraft(null);
      setDetail((current) => current?.id === updated.id ? updated : current);
      message.success(updated.knowledge_match_status === "stale"
        ? "题目已保存；原知识点已标记为待重新匹配"
        : "题目已保存");
      await load();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "题目保存失败");
    } finally {
      setEditSaving(false);
    }
  };

  const retireQuestion = async (id: string) => {
    try {
      const response = await fetch(`/api/v1/questions/${id}`, { method: "DELETE" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (detail?.id === id) setDetail(null);
      message.success("题目已从题库停用，历史引用和 ID 均已保留");
      await load();
    } catch {
      message.error("题目删除失败");
    }
  };

  const loadKnowledgeSuggestions = async () => {
    setKnowledgeOpen(true);
    setKnowledgeLoading(true);
    try {
      const response = await fetch("/api/v1/knowledge/classification/suggestions?limit=50", { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json() as { items: KnowledgeSuggestionItem[] };
      setKnowledgeItems(result.items);
      setKnowledgeSelection(Object.fromEntries(result.items.map((item) => [
        item.question_id,
        item.suggestions.length > 0 ? [item.suggestions[0].knowledge_node_id] : [],
      ])));
    } catch {
      message.error("知识点候选生成失败");
    } finally {
      setKnowledgeLoading(false);
    }
  };

  const confirmKnowledge = async (questionId: string) => {
    const nodeIds = knowledgeSelection[questionId] ?? [];
    if (nodeIds.length === 0) { message.warning("请至少选择一个知识点"); return; }
    setKnowledgeLoading(true);
    try {
      const response = await fetch(`/api/v1/questions/${questionId}/knowledge/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ knowledge_node_ids: nodeIds }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setKnowledgeItems((items) => items.filter((item) => item.question_id !== questionId));
      message.success("知识点已确认");
      await load();
    } catch {
      message.error("知识点保存失败");
    } finally {
      setKnowledgeLoading(false);
    }
  };

  const renderActions = (record: QuestionRow) => (
        <Space size={4} onClick={(event) => event.stopPropagation()}>
          <Button size="small" icon={<EyeOutlined />} onClick={() => void openDetail(record.id)}>查看</Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => void openEdit(record.id)}>修改</Button>
          <Popconfirm
            title="从题库停用这道题？"
            description="题目 ID、知识点关联和历史试卷引用都会保留。"
            okText="停用"
            cancelText="取消"
            onConfirm={() => void retireQuestion(record.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
  );

  const renderQuestionCard = (record: QuestionRow) => (
    <Card className="question-bank-card" key={record.id} onClick={() => void openDetail(record.id)}>
      <div className="question-bank-card-header">
        <Space wrap>
          <Typography.Text strong>{record.original_number ?? "未编号"}</Typography.Text>
          <Tag color="blue">{record.question_type}</Tag>
          {record.difficulty ? <Tag color="gold">难度 {record.difficulty}</Tag> : null}
          {record.publish_source === "ai_auto" && <Tag color="green">AI 自动发布</Tag>}
          {record.quality_sample_required && <Tag color="purple">随机抽检</Tag>}
        </Space>
        {renderActions(record)}
      </div>
      <div className="question-bank-card-preview">
        <PreviewPane markdown={record.question_text} />
      </div>
      <div className="question-bank-card-meta">
        <div><Typography.Text type="secondary">知识点：</Typography.Text>{record.knowledge.length ? record.knowledge.map((name) => <Tag key={name}>{name}</Tag>) : <Typography.Text type="secondary">{KNOWLEDGE_EMPTY}</Typography.Text>}</div>
        <div>
          <Typography.Text type="secondary">章节：</Typography.Text>
          {record.chapter_status === "unresolvable" ? (
            <Typography.Text type="danger">无法确定（知识点无法追溯章节）</Typography.Text>
          ) : record.chapter ? (
            record.chapter
          ) : (
            <Typography.Text type="secondary">未确定章节</Typography.Text>
          )}
        </div>
        <div><Typography.Text type="secondary">来源：</Typography.Text>{formatSource(record.source_name, record.source_page)}</div>
      </div>
    </Card>
  );

  const renderDetail = (item: QuestionDetail) => (
    <div>
      <Space style={{ marginBottom: 4 }}>
        <Button icon={<ArrowLeftOutlined />} size="small" onClick={() => setDetail(null)}>
          返回列表
        </Button>
        <Tag>原题号 {item.original_number ?? EMPTY}</Tag>
        <Tag color="blue">{item.question_type}</Tag>
        {item.publish_source === "ai_auto" && <Tag color="green">AI 自动发布</Tag>}
        {item.quality_sample_required && <Tag color="purple">随机抽检</Tag>}
      </Space>
      <Space style={{ marginBottom: 8 }}>
        <Typography.Text strong>快速改题型：</Typography.Text>
        <Select
          style={{ width: 160 }}
          value={item.question_type}
          onChange={(value: string) => {
            void (async () => {
              try {
                const updated = await patchQuestionType(item.id, value);
                setDetail(updated);
                message.success("题型已更新（审核状态与知识点保持不变）");
                await load();
              } catch (e) {
                message.error(String(e));
              }
            })();
          }}
          options={[
            ...QUESTION_TYPES.map((t) => ({ value: t, label: QUESTION_TYPE_LABEL[t] ?? t })),
            ...(QUESTION_TYPES.includes(item.question_type)
              ? []
              : [{ value: item.question_type, label: `${item.question_type}（原始）` }]),
          ]}
        />
      </Space>

      <Section title="题目内容">
        {item.question_text ? <PreviewPane markdown={item.question_text} /> : EMPTY}
      </Section>
      <Section title="参考解答">
        <Collapse items={[{ key: "solution", label: "展开参考解答", children: item.solution_content ? <PreviewPane markdown={item.solution_content} /> : EMPTY }]} />
      </Section>
      <Section title="知识点">
        {item.knowledge_match_status === "stale" && (
          <Alert type="warning" showIcon message="题目内容已修改，知识点需要重新确认" style={{ marginBottom: 8 }} />
        )}
        {item.knowledge.length > 0 ? (
          <Space size={[0, 4]} wrap>
            {item.knowledge.map((name) => (
              <Tag key={name}>{name}</Tag>
            ))}
          </Space>
        ) : (
          <Typography.Text type="secondary">{KNOWLEDGE_EMPTY}</Typography.Text>
        )}
      </Section>
      <Section title="章节与难度">
        <Space wrap>
          {item.chapter_status === "unresolvable" ? (
            <Tag color="red">章节无法确定</Tag>
          ) : item.chapter ? (
            <Tag color="blue">{item.chapter}</Tag>
          ) : (
            <Tag>未确定章节</Tag>
          )}
          <Tag color="gold">{item.difficulty ? `难度 ${item.difficulty}` : "难度未设置"}</Tag>
        </Space>
      </Section>
      <Section title="来源">{formatSource(item.source_name, item.source_page)}</Section>
      {item.publish_source === "ai_auto" && item.ai_review?.reason && (
        <Section title="AI 审核依据">{item.ai_review.reason}</Section>
      )}
      <Section title="技术信息"><Typography.Text type="secondary">question_id：{item.id}</Typography.Text></Section>
    </div>
  );

  return (
    <Drawer open={open} onClose={onClose} width="70%" title="题库">
      {error && (
        <Alert type="error" message={error} showIcon closable style={{ marginBottom: 12 }} onClose={() => setError(null)} />
      )}
      {detailLoading ? (
        <Spin />
      ) : detail ? (
        renderDetail(detail)
      ) : (
        <Space style={{ width: "100%" }} direction="vertical">
          <Space>
            <Input
              prefix={<SearchOutlined />}
              placeholder="搜索题干或题目 ID..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 240 }}
              allowClear
            />
            <Select value={typeFilter} onChange={setTypeFilter} style={{ width: 130 }}>
              <Option value="all">全部题型</Option>
              <Option value="选择题">选择题</Option>
              <Option value="填空题">填空题</Option>
              <Option value="计算题">计算题</Option>
            </Select>
            <Select value={publishSourceFilter} onChange={setPublishSourceFilter} style={{ width: 150 }}>
              <Option value="all">全部发布来源</Option>
              <Option value="ai_auto">AI 自动发布</Option>
              <Option value="manual">人工发布</Option>
            </Select>
            <Select value={chapterFilter} onChange={setChapterFilter} style={{ width: 220 }}>
              <Option value="all">全部章节</Option>
              {chapters.map((chapter) => (
                <Option key={chapter.id} value={chapter.id}>{chapter.name}</Option>
              ))}
            </Select>
            <Button icon={<TagsOutlined />} onClick={() => void loadKnowledgeSuggestions()}>
              知识点审核
            </Button>
            <Button loading={profileLoading} onClick={() => void loadProfiles()}>
              题目画像审核
            </Button>
          </Space>
          {loading || data.length > 0 ? (
            <Spin spinning={loading}>
              <div className="question-bank-card-list">{data.map(renderQuestionCard)}</div>
            </Spin>
          ) : (
            <Empty description="暂无已发布题目" />
          )}
        </Space>
      )}
      <Modal
        open={editDraft !== null}
        title={editDraft ? `修改正式题 · ${editDraft.id.slice(0, 12)}` : "修改正式题"}
        width={900}
        okText="保存修改"
        cancelText="取消"
        confirmLoading={editSaving}
        onOk={() => void saveEdit()}
        onCancel={() => setEditDraft(null)}
      >
        {editDraft && (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Typography.Text type="secondary">正式题 ID 保持不变：{editDraft.id}</Typography.Text>
            <Typography.Text strong>题目内容</Typography.Text>
            <Input.TextArea rows={6} value={editDraft.question_text}
              onChange={(e) => setEditDraft({ ...editDraft, question_text: e.target.value })} />
            <Typography.Text type="secondary">题目预览</Typography.Text>
            <div className="question-edit-preview"><PreviewPane markdown={editDraft.question_text} /></div>
            <Typography.Text strong>参考解答 / 解析</Typography.Text>
            <Input.TextArea rows={7} value={editDraft.solution_content}
              onChange={(e) => setEditDraft({ ...editDraft, solution_content: e.target.value })} />
            <Typography.Text type="secondary">参考解答 / 解析预览</Typography.Text>
            <div className="question-edit-preview"><PreviewPane markdown={editDraft.solution_content} /></div>
            <Space wrap style={{ width: "100%" }}>
              <div><Typography.Text strong>题型</Typography.Text><br />
                <Select style={{ width: 180 }} value={editDraft.question_type}
                  onChange={(value: string) => setEditDraft({ ...editDraft, question_type: value })}
                  options={[
                    ...QUESTION_TYPES.map((t) => ({ value: t, label: QUESTION_TYPE_LABEL[t] ?? t })),
                    ...(QUESTION_TYPES.includes(editDraft.question_type)
                      ? []
                      : [{ value: editDraft.question_type, label: `${editDraft.question_type}（原始）` }]),
                  ]} /></div>
              <div><Typography.Text strong>章节</Typography.Text><br />
                <Typography.Text type="secondary">{editDraft.chapter || "根据知识点自动派生"}</Typography.Text></div>
              <div><Typography.Text strong>难度</Typography.Text><br />
                <Radio.Group value={editDraft.difficulty} onChange={(e) => setEditDraft({ ...editDraft, difficulty: e.target.value })}>
                  {[1, 2, 3, 4, 5].map((value) => <Radio.Button key={value} value={value}>{value}</Radio.Button>)}
                </Radio.Group></div>
              <div><Typography.Text strong>原始题号</Typography.Text><br />
                <Input style={{ width: 130 }} value={editDraft.original_number}
                  onChange={(e) => setEditDraft({ ...editDraft, original_number: e.target.value })} /></div>
              <div><Typography.Text strong>来源页码</Typography.Text><br />
                <InputNumber min={1} value={editDraft.source_page}
                  onChange={(value) => setEditDraft({ ...editDraft, source_page: value })} /></div>
            </Space>
            <Typography.Text strong>知识点（最多 3 个，全部平级）</Typography.Text>
            <Select mode="multiple" maxCount={3} style={{ width: "100%" }}
              options={knowledgeOptions} value={editDraft.knowledge_node_ids}
              onChange={(value) => setEditDraft({ ...editDraft, knowledge_node_ids: value })} />
            <Alert type="info" showIcon message="章节会根据最终知识点自动派生；题目或解析修改后，未重新确认的知识点会标记为待重新匹配。手动选择的知识点不会被自动推荐覆盖。" />
          </Space>
        )}
      </Modal>
      <Modal
        open={knowledgeOpen}
        title="知识点候选审核"
        width={860}
        footer={<Button onClick={() => setKnowledgeOpen(false)}>关闭</Button>}
        onCancel={() => setKnowledgeOpen(false)}
      >
        <Spin spinning={knowledgeLoading}>
          {knowledgeItems.length === 0 ? <Empty description="没有待标注的正式 OCR 题目" /> : (
            <Space direction="vertical" size={12} style={{ width: "100%", maxHeight: "65vh", overflowY: "auto" }}>
              {knowledgeItems.map((item) => (
                <Card key={item.question_id} size="small">
                  <Typography.Paragraph ellipsis={{ rows: 3 }}>
                    {extractPlainQuestionPreview(item.question_text)}
                  </Typography.Paragraph>
                  {item.suggestions.length === 0 ? (
                    <Alert type="warning" showIcon message="没有可靠候选，请先完善知识点词表或人工搜索" />
                  ) : (
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Select
                        mode="multiple"
                        style={{ width: "100%" }}
                        value={knowledgeSelection[item.question_id] ?? []}
                        onChange={(value) => setKnowledgeSelection((current) => ({ ...current, [item.question_id]: value }))}
                        options={item.suggestions.map((suggestion) => ({
                          value: suggestion.knowledge_node_id,
                          label: `${suggestion.name} · ${Math.round(suggestion.score * 100)}%`,
                        }))}
                      />
                      <Space wrap>
                        {item.suggestions.map((suggestion) => (
                          <Tag key={suggestion.knowledge_node_id} color={suggestion.score >= 0.8 ? "green" : "gold"}>
                            {suggestion.name}：{suggestion.evidence.join("、")}
                          </Tag>
                        ))}
                      </Space>
                      <Button type="primary" onClick={() => void confirmKnowledge(item.question_id)}>
                        确认当前题
                      </Button>
                    </Space>
                  )}
                </Card>
              ))}
            </Space>
          )}
        </Spin>
      </Modal>
      <Modal
        open={profileOpen}
        title="题目画像审核"
        width="94vw"
        footer={<Button onClick={() => setProfileOpen(false)}>关闭</Button>}
        onCancel={() => setProfileOpen(false)}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="难度与计算量独立评分；修改后点击批准会创建新的画像版本，不覆盖自动预标记录。"
        />
        <Table
          rowKey="profile_id"
          loading={profileLoading}
          dataSource={profiles}
          size="small"
          scroll={{ x: 1450, y: "58vh" }}
          pagination={{ pageSize: 15 }}
          columns={[
            {
              title: "题目 ID",
              width: 170,
              fixed: "left" as const,
              render: (_, item) => (
                <Typography.Text copyable={{ text: item.question_id }} code>
                  {item.question_id.slice(0, 12)}
                </Typography.Text>
              ),
            },
            {
              title: "题目内容",
              width: 360,
              render: (_, item) => (
                <Typography.Paragraph
                  ellipsis={{ rows: 4, expandable: true, symbol: "展开" }}
                  style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}
                  title={extractPlainQuestionPreview(item.question_text)}
                >
                  {extractPlainQuestionPreview(item.question_text)}
                </Typography.Paragraph>
              ),
            },
            { title: "题型", dataIndex: "question_type", width: 85 },
            { title: "难度", width: 92, render: (_, item) => <InputNumber min={1} max={5} value={item.difficulty} onChange={(v) => v && setProfileValue(item.profile_id, "difficulty", v)} /> },
            { title: "时间(分)", width: 100, render: (_, item) => <InputNumber min={1} max={180} value={item.estimated_time_min} onChange={(v) => v && setProfileValue(item.profile_id, "estimated_time_min", v)} /> },
            { title: "推理", width: 92, render: (_, item) => <InputNumber min={1} max={5} value={item.reasoning_depth} onChange={(v) => v && setProfileValue(item.profile_id, "reasoning_depth", v)} /> },
            { title: "计算量", width: 92, render: (_, item) => <InputNumber min={1} max={5} value={item.calculation_load} onChange={(v) => v && setProfileValue(item.profile_id, "calculation_load", v)} /> },
            { title: "知识深度", width: 100, render: (_, item) => <InputNumber min={1} max={5} value={item.knowledge_depth} onChange={(v) => v && setProfileValue(item.profile_id, "knowledge_depth", v)} /> },
            { title: "综合度", width: 92, render: (_, item) => <InputNumber min={1} max={5} value={item.comprehensive_level} onChange={(v) => v && setProfileValue(item.profile_id, "comprehensive_level", v)} /> },
            { title: "置信度", width: 85, render: (_, item) => `${Math.round(item.confidence * 100)}%` },
            { title: "状态", width: 95, render: (_, item) => <Tag color={item.profile_status === "approved" ? "green" : item.profile_status === "needs_review" ? "red" : "gold"}>{item.profile_status}</Tag> },
            { title: "依据", dataIndex: "reason", width: 280 },
            {
              title: "操作",
              fixed: "right",
              width: 190,
              render: (_, item) => (
                <Space size={4}>
                  <Button size="small" icon={<EyeOutlined />} onClick={() => { setProfileOpen(false); void openDetail(item.question_id); }}>
                    查看
                  </Button>
                  <Button size="small" icon={<EditOutlined />} onClick={() => { setProfileOpen(false); void openEdit(item.question_id); }}>
                    修改
                  </Button>
                  <Button type="primary" size="small" disabled={item.profile_status === "approved"} onClick={() => void approveProfile(item)}>
                    批准
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Modal>
    </Drawer>
  );
}
