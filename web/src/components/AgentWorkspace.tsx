import { useEffect, useState } from "react";
import {
  Button, Input, Space, Spin, Tag, Typography, message, Row, Col,
  InputNumber, Statistic, Card, Divider, Drawer, Modal,
} from "antd";
import {
  ArrowDownOutlined, ArrowUpOutlined, FilePdfOutlined,
  LockOutlined, ReloadOutlined, RobotOutlined, SafetyCertificateOutlined,
  UnlockOutlined, SendOutlined,
} from "@ant-design/icons";

const API = "/api/v1";

// ── types ──
type Blueprint = {
  title: string; total_questions: number; total_score: number;
  question_type_counts: Record<string, number>;
  sections: { question_type: string; count: number; score_per_question: number; total_score: number }[];
  knowledge_quotas: { name: string; count: number }[]; soft_knowledge_preferences: string[];
  locked_question_ids: string[];
  manual_question_ids: string[]; excluded_question_ids: string[]; question_order: string[];
  score_overrides: Record<string, number>; seed: number;
};
type BlueprintRecord = { blueprint_id: string; status: "draft" | "confirmed" | "used"; blueprint: Blueprint; cached: boolean; agent_message?: string | null; needs_clarification?: boolean; paper_result?: SavedPaper | null };
type Preview = { title: string; total_score: number; feasible: boolean; warnings: string[]; constraints: { name: string; required: string | number; actual: string | number; satisfied: boolean }[]; items: { item_id: string; question_id: string; question_text: string; question_type: string; score: number; knowledge: string[]; locked: boolean }[] };
type Violation = { code: string; field: string; required: unknown; actual: unknown; question_ids: string[]; repairable: boolean; message: string };
type ValidationReport = { passed: boolean; violations: Violation[] };
type SavedPaper = { paper_id: string; version: number; preview: Preview; validation_report: ValidationReport };
type SupplyCheck = { feasible: boolean; violations: Violation[]; suggestions?: string[] };

const formatApiDetail = (detail: unknown): string => {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const value = detail as { message?: unknown; violations?: unknown };
    const messageText = typeof value.message === "string" ? value.message : "请求失败";
    if (Array.isArray(value.violations)) {
      const violations = value.violations
        .map((item) => {
          if (!item || typeof item !== "object") return "";
          const violation = item as Partial<Violation>;
          return `${violation.message ?? violation.field ?? "约束未满足"}`
            + `（实际 ${String(violation.actual ?? "-")} / 要求 ${String(violation.required ?? "-")}）`;
        })
        .filter(Boolean);
      return violations.length ? `${messageText}：${violations.join("；")}` : messageText;
    }
    return messageText;
  }
  return "请求失败";
};

// ── templates ──
const TEMPLATES = [
  {
    label: "章节练习",
    prompt: `帮我生成一套【函数与极限】章节练习。

面向学生：大一
题目总数：10 题
题目总分：100 分

重点覆盖：
- 函数极限
- 极限运算法则
- 无穷小

题型与分值：
选择题 2 道 × 5 分 = 10 分
填空题 1 道 × 5 分 = 5 分
计算题 5 道 × 13 分 = 65 分
证明题 2 道 × 10 分 = 20 分

希望题目从基础逐渐过渡到需要一定思考的题，避免大量重复套路题。`,
  },
  {
    label: "专项训练",
    prompt: `帮我生成一套导数运算专项训练。

面向大一学生，共8题。

重点覆盖：
- 基本初等函数求导
- 复合函数求导
- 隐函数求导

全部为计算题，难度逐步递增。`,
  },
  {
    label: "期中复习",
    prompt: `帮我生成一套期中复习试卷。

面向大一学生，共15题。

覆盖：
- 函数与极限
- 导数与微分
- 微分中值定理

题型：选择题5道、填空题3道、计算题7道。`,
  },
  {
    label: "模拟考试",
    prompt: `帮我生成一套高等数学上册模拟考试卷。

面向大一学生，共20题，满分100分。

题型分布：
- 选择题5道（每题3分）
- 填空题5道（每题3分）
- 计算题6道（每题5分）
- 证明题2道（每题10分）
- 综合题2道（每题10分）

覆盖全部上册核心知识点。`,
  },
];

// ── chat message type ──
type ChatMessage =
  | { role: "user"; text: string }
  | { role: "agent"; type: "requirement_card"; title: string; sections: Blueprint["sections"]; total_questions: number; total_score: number; suggestions?: string[]; blueprintId: string }
  | { role: "agent"; type: "status"; text: string }
  | { role: "agent"; type: "reply"; text: string }
  | { role: "agent"; type: "paper_ready"; paperId: string; version: number; preview: Preview; validationReport: ValidationReport }
  | { role: "agent"; type: "error"; text: string };

interface Props {
  onOpenPaperDrawer: (paperId: string, preview: Preview, validation: ValidationReport, version: number) => void;
  activePaperId?: string | null;
}

export default function AgentWorkspace({ onOpenPaperDrawer, activePaperId }: Props) {
  // ── chat state ──
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  // ── blueprint/paper state (kept for compatibility) ──
  const [blueprintId, setBlueprintId] = useState<string | null>(null);
  const [currentPaperId, setCurrentPaperId] = useState<string | null>(null);
  useEffect(() => {
    if (activePaperId) setCurrentPaperId(activePaperId);
  }, [activePaperId]);
  const [supplyCheck, setSupplyCheck] = useState<SupplyCheck | null>(null);
  const [candidatePreview, setCandidatePreview] = useState<Preview | null>(null);
  const [candidateBlueprintId, setCandidateBlueprintId] = useState<string | null>(null);

  const call = async (path: string, body: unknown) => {
    const r = await fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) {
      const payload = await r.json().catch(() => ({}));
      throw new Error(formatApiDetail(payload.detail));
    }
    return r;
  };

  // ── send requirement → parse blueprint → show card ──
  const handleSend = async () => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text }]);
    setLoading(true);

    try {
      // 调用现有 Blueprint 解析 API
      const r = await call("/blueprints/parse", {
        requirement: text,
        base_blueprint_id: blueprintId,
        current_paper_id: currentPaperId,
        conversation_history: messages.slice(-10).flatMap((msg) => {
          if (msg.role === "user") return [{ role: "user", content: msg.text }];
          if (msg.type === "reply" || msg.type === "error") {
            return [{ role: "assistant", content: msg.text }];
          }
          if (msg.type === "requirement_card") {
            return [{
              role: "assistant",
              content: `当前方案：${msg.sections.map((s) => `${s.question_type}${s.count}题，每题${s.score_per_question}分`).join("；")}`,
            }];
          }
          return [];
        }),
      });
      const parsed: BlueprintRecord = await r.json();
      const bp = parsed.blueprint;
      if (parsed.paper_result) {
        const saved = parsed.paper_result;
        setCurrentPaperId(saved.paper_id);
        setMessages((prev) => [...prev,
          { role: "agent", type: "reply", text: parsed.agent_message || "已恢复试卷历史状态。" },
          {
            role: "agent", type: "paper_ready", paperId: saved.paper_id,
            version: saved.version, preview: saved.preview,
            validationReport: saved.validation_report,
          },
        ]);
        return;
      }
      if (parsed.needs_clarification) {
        setMessages((prev) => [...prev, {
          role: "agent",
          type: "reply",
          text: parsed.agent_message || "请补充说明希望修改的部分。",
        }]);
        return;
      }
      setBlueprintId(parsed.blueprint_id);
      // The previous paper is useful context for this turn, but after the blueprint
      // changes it no longer represents the newly displayed plan.
      setCurrentPaperId(null);

      // 检查供给
      const supplyR = await fetch(`${API}/blueprints/${parsed.blueprint_id}/supply-check`);
      const supply: SupplyCheck = await supplyR.ok ? await supplyR.json() : { feasible: true, violations: [] };
      setSupplyCheck(supply);

      // 添加确认卡消息
      setMessages((prev) => [
        ...prev,
        {
          role: "agent",
          type: "requirement_card",
          title: bp.title,
          sections: bp.sections,
          total_questions: bp.total_questions,
          total_score: bp.total_score,
          suggestions: supply.suggestions ?? [],
          blueprintId: parsed.blueprint_id,
        },
        ...(parsed.agent_message ? [{
          role: "agent" as const,
          type: "reply" as const,
          text: parsed.agent_message,
        }] : []),
      ]);
    } catch (e: unknown) {
      setMessages((prev) => [...prev, { role: "agent", type: "error", text: `需求解析失败：${e}` }]);
    } finally {
      setLoading(false);
    }
  };

  // ── confirm and compose ──
  const handleStartPaper = async (bpId: string) => {
    setLoading(true);
    setMessages((prev) => [...prev, { role: "agent", type: "status", text: "正在检索符合要求的题目……" }]);
    try {
      const supplyR = await fetch(`${API}/blueprints/${bpId}/supply-check`);
      const supply: SupplyCheck = supplyR.ok
        ? await supplyR.json()
        : { feasible: true, violations: [] };
      if (!supply.feasible) {
        throw new Error(formatApiDetail({
          message: "题库无法满足组卷约束",
          violations: supply.violations,
        }));
      }
      // confirm blueprint
      await call(`/blueprints/${bpId}/confirm`, {});
      setMessages((prev) => [...prev, { role: "agent", type: "status", text: "正在生成试卷……" }]);

      // create paper
      const paperR = await call("/papers", { blueprint_id: bpId });
      const saved: SavedPaper = await paperR.json();
      setCurrentPaperId(saved.paper_id);

      setMessages((prev) => [...prev, {
        role: "agent",
        type: "paper_ready",
        paperId: saved.paper_id,
        version: saved.version,
        preview: saved.preview,
        validationReport: saved.validation_report,
      }]);
    } catch (e: unknown) {
      setMessages((prev) => [...prev, { role: "agent", type: "error", text: `组卷失败：${e}` }]);
    } finally {
      setLoading(false);
    }
  };

  const previewCandidates = async (bpId: string) => {
    try {
      const response = await fetch(`${API}/blueprints/${bpId}/candidate-preview`);
      if (!response.ok) throw new Error("候选题加载失败");
      setCandidatePreview(await response.json());
      setCandidateBlueprintId(bpId);
    } catch (e: unknown) { message.error(String(e)); }
  };

  // ── render message ──
  const renderMessage = (msg: ChatMessage, idx: number) => {
    if (msg.role === "user") {
      return (
        <div key={idx} style={{ display: "flex", justifyContent: "flex-end", marginBottom: 16 }}>
          <div style={{ maxWidth: "70%", background: "#e6f4ff", borderRadius: 12, padding: "10px 16px" }}>
            <Typography.Text>{msg.text}</Typography.Text>
          </div>
        </div>
      );
    }

    if (msg.type === "requirement_card") {
      return (
        <div key={idx} style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <RobotOutlined style={{ color: "#1677ff" }} />
            <Typography.Text strong>MathPaper Agent</Typography.Text>
          </div>
          <Card
            size="small"
            title={<span>📋 组卷方案 — {msg.title}</span>}
            style={{ maxWidth: 520, background: "#fafafa" }}
          >
            <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
              共 {msg.total_questions} 题，{msg.total_score} 分
            </Typography.Paragraph>
            {msg.suggestions?.length ? (
              <Typography.Paragraph type="warning" style={{ marginBottom: 12 }}>
                题库供给不足：{msg.suggestions.join(" ")}
              </Typography.Paragraph>
            ) : null}
            {msg.sections.map((s) => (
              <div key={s.question_type} style={{ marginBottom: 4 }}>
                <Tag>{s.question_type}</Tag>
                {s.count} 题 × {Number(s.score_per_question.toFixed(2))} 分 = {Number(s.total_score.toFixed(2))} 分
              </div>
            ))}
            <Divider style={{ margin: "12px 0" }} />
            <Space>
              <Button size="small" onClick={() => { setInput(msg.title ? `修改要求：${msg.title} 的方案中...` : "修改要求："); }}>
                修改要求
              </Button>
              <Button size="small" onClick={() => void previewCandidates(msg.blueprintId)}>
                查看候选题
              </Button>
              <Button type="primary" size="small" loading={loading} onClick={() => handleStartPaper(msg.blueprintId)}>
                开始组卷
              </Button>
            </Space>
          </Card>
        </div>
      );
    }

    if (msg.type === "status") {
      return (
        <div key={idx} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, color: "#888" }}>
          <Spin size="small" />
          <Typography.Text type="secondary">{msg.text}</Typography.Text>
        </div>
      );
    }

    if (msg.type === "reply") {
      return (
        <div key={idx} style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 16 }}>
          <RobotOutlined style={{ color: "#1677ff", marginTop: 4 }} />
          <div style={{ maxWidth: 620, background: "#fafafa", borderRadius: 10, padding: "9px 13px" }}>
            <Typography.Text>{msg.text}</Typography.Text>
          </div>
        </div>
      );
    }

    if (msg.type === "paper_ready") {
      return (
        <div key={idx} style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <RobotOutlined style={{ color: "#1677ff" }} />
            <Typography.Text strong>MathPaper Agent</Typography.Text>
          </div>
          <Card size="small" style={{ maxWidth: 520, background: "#f6ffed", border: "1px solid #b7eb8f" }}>
            <Tag icon={<SafetyCertificateOutlined />} color="success">
              {msg.validationReport.passed ? "全部硬约束通过" : "存在违规"}
            </Tag>
            <Typography.Paragraph style={{ marginTop: 8 }}>
              组卷完毕，共 {msg.preview.items.length} 题。请分别点击右侧按钮下载文件。
            </Typography.Paragraph>
            <Space>
              <Button type="primary" size="small" onClick={() => onOpenPaperDrawer(msg.paperId, msg.preview, msg.validationReport, msg.version)}>
                查看试卷
              </Button>
              <Button size="small" icon={<FilePdfOutlined />} onClick={() => downloadPdf(msg.paperId, "student")}>
                下载试卷 PDF
              </Button>
              <Button size="small" icon={<FilePdfOutlined />} onClick={() => downloadPdf(msg.paperId, "teacher")}>
                下载题目与答案解析 PDF
              </Button>
            </Space>
          </Card>
        </div>
      );
    }

    if (msg.type === "error") {
      return (
        <div key={idx} style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <RobotOutlined style={{ color: "#ff4d4f" }} />
            <Typography.Text type="danger">{msg.text}</Typography.Text>
          </div>
        </div>
      );
    }

    return null;
  };

  const downloadPdf = async (paperId: string, version: "student" | "teacher") => {
    try {
      const r = await fetch(`${API}/papers/${paperId}/exports/${version}.pdf`);
      if (!r.ok) throw new Error("导出失败");
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = version === "student" ? "试卷.pdf" : "题目与答案解析.pdf";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) { message.error(String(e)); }
  };

  // ── render ──
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", maxWidth: 800, margin: "0 auto", padding: "0 24px" }}>
      {/* messages area */}
      <div style={{ flex: 1, overflow: "auto", padding: "16px 0" }}>
        {messages.length === 0 && (
          <div style={{ textAlign: "center", padding: "60px 0" }}>
            <RobotOutlined style={{ fontSize: 48, color: "#d9d9d9" }} />
            <Typography.Title level={4} type="secondary" style={{ marginTop: 16 }}>
              MathPaper Agent
            </Typography.Title>
            <Typography.Paragraph type="secondary">
              我可以根据你的要求生成高等数学试卷。
            </Typography.Paragraph>
            <Space wrap style={{ justifyContent: "center" }}>
              {TEMPLATES.map((t) => (
                <Button key={t.label} size="small" onClick={() => setInput(t.prompt)}>
                  {t.label}
                </Button>
              ))}
            </Space>
          </div>
        )}
        {messages.map(renderMessage)}
        {loading && messages[messages.length - 1]?.role !== "agent" && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#888" }}>
            <Spin size="small" />
            <Typography.Text type="secondary">正在解析组卷要求……</Typography.Text>
          </div>
        )}
        <div style={{ height: 80 }} />
      </div>
      <Modal
        open={candidatePreview !== null}
        title="组卷候选题确认"
        width={720}
        onCancel={() => setCandidatePreview(null)}
        footer={candidatePreview ? <Button type="primary" disabled={!candidatePreview.feasible || !candidateBlueprintId} onClick={() => { const id = candidateBlueprintId; setCandidatePreview(null); if (id) void handleStartPaper(id); }}>确认候选题，开始组卷</Button> : null}
      >
        {candidatePreview && (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Typography.Text type="secondary">
              共 {candidatePreview.items.length} 题，预计 {candidatePreview.total_score} 分。确认后点击原方案中的“开始组卷”正式生成试卷。
            </Typography.Text>
            {candidatePreview.items.map((item, index) => (
              <Card key={item.question_id} size="small">
                <Typography.Text strong>{index + 1}. {item.question_text}</Typography.Text>
                <div><Tag>{item.question_type}</Tag><Tag>{item.score} 分</Tag></div>
              </Card>
            ))}
          </Space>
        )}
      </Modal>

      {/* input area */}
      <div style={{ borderTop: "1px solid #f0f0f0", padding: "12px 0", background: "#fff" }}>
        <Space wrap style={{ marginBottom: 8 }}>
          {TEMPLATES.map((t) => (
            <Button key={t.label} size="small" type="dashed" onClick={() => setInput(t.prompt)}>
              {t.label}
            </Button>
          ))}
        </Space>
        <Input.TextArea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPressEnter={(e) => {
            if (!e.shiftKey) { e.preventDefault(); handleSend(); }
          }}
          placeholder="输入你的组卷要求……（Enter 发送，Shift+Enter 换行）"
          autoSize={{ minRows: 2, maxRows: 6 }}
          disabled={loading}
        />
        <div style={{ textAlign: "right", marginTop: 4 }}>
          <Button type="primary" icon={<SendOutlined />} loading={loading} disabled={!input.trim()} onClick={handleSend}>
            发送
          </Button>
        </div>
      </div>
    </div>
  );
}
