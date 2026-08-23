import { useCallback, useEffect, useState } from "react";
import {
  Button, Input, Space, Spin, Tag, Typography, message, Row, Col,
  InputNumber, Statistic, Card, Divider, Drawer, Modal,
} from "antd";
import {
  ArrowDownOutlined, ArrowUpOutlined, FilePdfOutlined,
  LockOutlined, ReloadOutlined, RobotOutlined, SafetyCertificateOutlined,
  UnlockOutlined, SendOutlined, PlusOutlined,
} from "@ant-design/icons";

import MarkdownMath from "./MarkdownMath";
import {
  downloadPaperPdf,
  openPaperPdf,
  type PaperPdfVersion,
} from "../utils/paperPdf";

const API = "/api/v1";
const CONVERSATION_ID_STORAGE_KEY = "teacher-agent.conversation-id";

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
type TeacherAgentResponse = {
  status: "completed" | "needs_clarification" | "waiting_confirmation" | "failed";
  message: string;
  clarification_questions?: string[];
  paper?: { ok: boolean; paper_id?: string | null; version_id?: string | null } | null;
  replacement?: { new_version_id?: string | null } | null;
  adjustment?: { new_version_id?: string | null } | null;
  version_operation?: { current_version_id?: string | null } | null;
  warnings?: string[];
  blocking_errors?: string[];
  teaching_planning_draft?: {
    problem_analysis: string;
    learning_objectives: string[];
    knowledge_focus: string[];
    teaching_strategy: string[];
    assessment_strategy: string[];
  } | null;
  generation_preview?: {
    ok: boolean; title?: string | null; total_questions?: number | null; total_score?: number | null;
    pending_version?: number | null;
    blocking_errors?: string[];
    clarification_questions?: string[];
    sections: { question_type: string; count: number; score_each?: number | null; total_score?: number | null }[];
  } | null;
};

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
  | { role: "agent"; type: "generation_plan"; title: string; sections: { question_type: string; count: number; score_each?: number | null; total_score?: number | null }[]; total_questions: number; total_score: number; pending_version: number; disabled?: boolean }
  | { role: "agent"; type: "status"; text: string }
  | { role: "agent"; type: "teaching_planning_draft"; draft: NonNullable<TeacherAgentResponse["teaching_planning_draft"]> }
  | { role: "agent"; type: "reply"; text: string }
  | { role: "agent"; type: "paper_ready"; paperId: string; version: number; preview: Preview; validationReport: ValidationReport }
  | { role: "agent"; type: "error"; text: string };

type GenerationSection = { question_type: string; count: number; score_each?: number | null; total_score?: number | null };
type GenerationPlanPatch = { question_type: string; count?: number; score_each?: number };
type TeacherAgentSession = {
  conversation_id: string;
  messages: { role: string; content: string; created_at?: string | null }[];
  generated_papers?: { paper_id: string; created_at: string }[];
  workspace?: { active_type?: string | null; current_paper_id?: string | null; current_version_id?: string | null } | null;
  active_teaching_design?: { title: string; version: number; status: string } | null;
  pending_generation?: {
    request: {
      scope_names?: string[] | null;
      question_count?: number | null;
      total_score?: number | null;
      question_type_requirements?: GenerationSection[] | null;
    };
    pending_version: number;
  } | null;
};

function loadConversationId(): string {
  try {
    const saved = globalThis.localStorage?.getItem(CONVERSATION_ID_STORAGE_KEY);
    if (saved) return saved;
  } catch {
    // Browser privacy settings may disable storage; the active tab still works.
  }
  const created = globalThis.crypto?.randomUUID?.() ?? `agent-${Date.now()}`;
  try {
    globalThis.localStorage?.setItem(CONVERSATION_ID_STORAGE_KEY, created);
  } catch {
    // Keep the generated id in React state when localStorage is unavailable.
  }
  return created;
}

export function clearStoredConversationId(): void {
  try {
    globalThis.localStorage?.removeItem(CONVERSATION_ID_STORAGE_KEY);
  } catch {
    // The new tab will generate an in-memory id when storage is unavailable.
  }
}

function GenerationPlanCard({
  title, initialSections, loading, disabled, onUpdate, onConfirm,
}: {
  title: string; initialSections: GenerationSection[]; loading: boolean; disabled?: boolean;
  onUpdate: (patches: GenerationPlanPatch[]) => void; onConfirm: () => void;
}) {
  const [sections, setSections] = useState(initialSections);
  const changed = JSON.stringify(sections) !== JSON.stringify(initialSections);
  const totalQuestions = sections.reduce((sum, item) => sum + item.count, 0);
  const totalScore = sections.every((item) => item.score_each != null)
    ? sections.reduce((sum, item) => sum + item.count * Number(item.score_each), 0)
    : null;
  const update = () => {
    const patches = sections.flatMap((item, index) => {
      const original = initialSections[index];
      const patch: GenerationPlanPatch = { question_type: item.question_type };
      if (item.count !== original.count) patch.count = item.count;
      if (item.score_each !== original.score_each && item.score_each != null) patch.score_each = item.score_each;
      return Object.keys(patch).length > 1 ? [patch] : [];
    });
    onUpdate(patches);
  };
  return (
    <Card size="small" title={<span>📋 待确认组卷方案 — {title}</span>} style={{ maxWidth: 560, background: "#fafafa" }}>
      <Typography.Paragraph>共 {totalQuestions} 题{totalScore != null ? `，${totalScore} 分` : ""}</Typography.Paragraph>
      {sections.map((section, index) => (
        <Row key={section.question_type} gutter={8} align="middle" style={{ marginBottom: 8 }}>
          <Col flex="100px"><Tag>{section.question_type}</Tag></Col>
          <Col><InputNumber min={1} max={100} value={section.count} addonAfter="题" onChange={(value) => setSections((items) => items.map((item, i) => i === index ? { ...item, count: value ?? 1 } : item))} /></Col>
          <Col><InputNumber min={0.5} max={300} step={0.5} value={section.score_each ?? undefined} placeholder="每题分值" addonAfter="分/题" onChange={(value) => setSections((items) => items.map((item, i) => i === index ? { ...item, score_each: value } : item))} /></Col>
        </Row>
      ))}
      <Divider style={{ margin: "12px 0" }} />
      <Space>
        <Button size="small" disabled={disabled || !changed} loading={loading} onClick={update}>更新方案</Button>
        <Button type="primary" size="small" loading={loading} disabled={disabled || changed} onClick={onConfirm}>确认并组卷</Button>
      </Space>
      {changed && <Typography.Text type="warning" style={{ display: "block", marginTop: 8 }}>方案已修改，请先更新方案并重新校验。</Typography.Text>}
    </Card>
  );
}

export default function AgentWorkspace() {
  // ── chat state ──
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState(loadConversationId);
  const [restoringSession, setRestoringSession] = useState(true);

  // ── blueprint/paper state (kept for compatibility) ──
  const [blueprintId, setBlueprintId] = useState<string | null>(null);
  const [currentPaperId, setCurrentPaperId] = useState<string | null>(null);
  const [supplyCheck, setSupplyCheck] = useState<SupplyCheck | null>(null);
  const [candidatePreview, setCandidatePreview] = useState<Preview | null>(null);
  const [candidateBlueprintId, setCandidateBlueprintId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const restoreSession = async () => {
      try {
        const response = await fetch(
          `${API}/teacher-agent/session?conversation_id=${encodeURIComponent(conversationId)}`,
        );
        if (!response.ok) throw new Error("会话恢复失败");
        const restored: TeacherAgentSession = await response.json();
        if (cancelled) return;
        const restoredMessages: ChatMessage[] = restored.messages.map((item) => (
          item.role === "user"
            ? { role: "user", text: item.content }
            : { role: "agent", type: "reply", text: item.content }
        ));
        const pending = restored.pending_generation;
        const sections = pending?.request.question_type_requirements ?? [];
        if (pending && sections.length) {
          restoredMessages.push({
            role: "agent",
            type: "generation_plan",
            title: `${pending.request.scope_names?.[0] ?? "高等数学"}测试卷`,
            sections,
            total_questions: pending.request.question_count ?? sections.reduce((sum, item) => sum + item.count, 0),
            total_score: pending.request.total_score ?? sections.reduce((sum, item) => sum + (item.total_score ?? 0), 0),
            pending_version: pending.pending_version,
          });
        }
        const restoredPaperId = restored.workspace?.current_version_id
          ?? restored.workspace?.current_paper_id
          ?? null;
        const generatedPapers = restored.generated_papers ?? [];
        let insertedPaperCount = 0;
        for (const generated of generatedPapers) {
          try {
            const paperResponse = await fetch(`${API}/papers/${encodeURIComponent(generated.paper_id)}`);
            if (!paperResponse.ok) continue;
            const savedPaper: SavedPaper = await paperResponse.json();
            // Generation records are timestamped independently from messages.
            // Insert the card after the last message that existed at generation time,
            // instead of appending every restored card to the end.
            let insertAt = restored.messages.filter((item) =>
              item.created_at && item.created_at <= generated.created_at,
            ).length;
            insertAt = Math.min(insertAt + insertedPaperCount, restoredMessages.length);
            restoredMessages.splice(insertAt, 0, {
              role: "agent", type: "paper_ready", paperId: savedPaper.paper_id,
              version: savedPaper.version, preview: savedPaper.preview,
              validationReport: savedPaper.validation_report,
            });
            insertedPaperCount += 1;
          } catch {
            // 历史文字仍可恢复；试卷读取失败不阻断会话恢复。
          }
        }
        setMessages(restoredMessages);
        setCurrentPaperId(restoredPaperId);
      } catch (e) {
        if (!cancelled) message.error(String(e));
      } finally {
        if (!cancelled) setRestoringSession(false);
      }
    };
    void restoreSession();
    return () => { cancelled = true; };
  }, [conversationId]);

  // 通知布局侧栏刷新选中状态及新产生的会话标题。
  useEffect(() => {
    window.dispatchEvent(new Event("teacher-agent:conversation-change"));
  }, [conversationId]);

  const handleSelectConversation = useCallback((id: string) => {
    if (id === conversationId) return;
    setRestoringSession(true);
    setMessages([]);
    setCurrentPaperId(null);
    try {
      globalThis.localStorage?.setItem(CONVERSATION_ID_STORAGE_KEY, id);
    } catch {
      // Keep the selected conversation in React state when storage is unavailable.
    }
    setConversationId(id);
  }, [conversationId]);

  // ── start a brand-new conversation (no Conversation table; just switch id) ──
  const handleNewConversation = useCallback(() => {
    const newId = globalThis.crypto?.randomUUID?.() ?? `agent-${Date.now()}`;
    try {
      globalThis.localStorage?.setItem(CONVERSATION_ID_STORAGE_KEY, newId);
    } catch {
      // Storage may be disabled; the in-memory state still switches.
    }
    setConversationId(newId);   // re-runs the restore effect for the new id (empty)
    setMessages([]);            // clear current messages
    setSupplyCheck(null);
    setCandidatePreview(null);
    setCandidateBlueprintId(null);
    setBlueprintId(null);
    setCurrentPaperId(null);
  }, []);

  // 侧栏会话列表位于 App 布局中，通过浏览器事件同步当前会话。
  useEffect(() => {
    const syncConversation = () => {
      const id = loadConversationId();
      if (id !== conversationId) handleSelectConversation(id);
    };
    window.addEventListener("teacher-agent:conversation-change", syncConversation);
    return () => window.removeEventListener("teacher-agent:conversation-change", syncConversation);
  }, [conversationId, handleSelectConversation]);

  const call = async (path: string, body: unknown) => {
    const r = await fetch(`${API}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) {
      const payload = await r.json().catch(() => ({}));
      throw new Error(formatApiDetail(payload.detail));
    }
    return r;
  };

  // ── send requirement → parse blueprint → show card ──
  const handleSend = async (overrideText?: string) => {
    if (restoringSession) return;
    const text = (overrideText ?? input).trim();
    if (!text) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text }]);
    setLoading(true);

    try {
      // Natural-language chat uses the Phase 2B Agent. The manual Blueprint
      // workspace continues to use /blueprints/parse independently.
      const r = await call("/teacher-agent/run", {
        message: text,
        conversation_id: conversationId,
        paper_id: currentPaperId,
        version_id: currentPaperId,
      });
      const agent: TeacherAgentResponse = await r.json();
      const planningDraft = agent.teaching_planning_draft;
      if (planningDraft) {
        setMessages((prev) => [...prev, {
          role: "agent", type: "teaching_planning_draft", draft: planningDraft,
        }]);
        return;
      }
      if (agent.generation_preview) {
        setMessages((prev) => prev.map((item) =>
          item.role === "agent" && item.type === "generation_plan"
            ? { ...item, disabled: true }
            : item
        ));
      }
      if (agent.status === "waiting_confirmation" && agent.generation_preview?.ok) {
        const plan = agent.generation_preview;
        setMessages((prev) => [...prev,
          { role: "agent", type: "reply", text: agent.message },
          {
            role: "agent", type: "generation_plan",
            title: plan.title ?? "组卷方案",
            sections: plan.sections,
            total_questions: plan.total_questions ?? plan.sections.reduce((sum, item) => sum + item.count, 0),
            total_score: plan.total_score ?? plan.sections.reduce((sum, item) => sum + (item.total_score ?? 0), 0),
            pending_version: plan.pending_version ?? 1,
          },
        ]);
        return;
      }
      if (agent.status === "completed" && (agent.paper?.paper_id || agent.replacement?.new_version_id || agent.adjustment?.new_version_id || agent.version_operation?.current_version_id)) {
        const paperId = agent.replacement?.new_version_id ?? agent.adjustment?.new_version_id ?? agent.version_operation?.current_version_id ?? agent.paper?.paper_id;
        const savedR = await fetch(`${API}/papers/${paperId}`);
        if (!savedR.ok) throw new Error("试卷已生成，但读取草稿失败");
        const saved: SavedPaper = await savedR.json();
        setCurrentPaperId(saved.paper_id);
        setMessages((prev) => [...prev,
          { role: "agent", type: "reply", text: agent.message },
          { role: "agent", type: "paper_ready", paperId: saved.paper_id, version: saved.version, preview: saved.preview, validationReport: saved.validation_report },
        ]);
        return;
      }
      setMessages((prev) => [...prev, {
        role: "agent",
        type: agent.status === "failed" ? "error" : "reply",
        text: agent.message + (agent.clarification_questions?.length ? ` ${agent.clarification_questions.join(" ")}` : ""),
      }]);
      return;

      const parsed: BlueprintRecord = await r.json();
      const bp = parsed.blueprint;
      if (parsed.paper_result) {
        const saved = parsed.paper_result as SavedPaper;
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
      setMessages((prev) => [...prev, { role: "agent", type: "error", text: `操作失败：${e}` }]);
    } finally {
      setLoading(false);
    }
  };

  const handlePlanUpdate = async (patches: GenerationPlanPatch[], expectedVersion: number) => {
    if (!patches.length) return;
    setLoading(true);
    try {
      const r = await call("/teacher-agent/pending-generation/update", {
        conversation_id: conversationId,
        expected_version: expectedVersion,
        question_type_patches: patches,
      });
      const response: { status: string; generation_preview: NonNullable<TeacherAgentResponse["generation_preview"]> } = await r.json();
      const plan = response.generation_preview;
      const pendingVersion = plan.pending_version;
      if (response.status !== "waiting_confirmation" || !plan.ok || !pendingVersion) {
        const detail = [
          ...(plan.blocking_errors ?? []),
          ...(plan.clarification_questions ?? []),
        ].join("；") || "方案未能更新。";
        setMessages((prev) => [...prev, { role: "agent", type: "error", text: detail }]);
        return;
      }
      setMessages((prev) => [
        ...prev.map((item) => item.role === "agent" && item.type === "generation_plan" ? { ...item, disabled: true } : item),
        {
          role: "agent",
          type: "generation_plan",
          title: plan.title ?? "组卷方案",
          sections: plan.sections,
          total_questions: plan.total_questions ?? plan.sections.reduce((sum, item) => sum + item.count, 0),
          total_score: plan.total_score ?? plan.sections.reduce((sum, item) => sum + (item.total_score ?? 0), 0),
          pending_version: pendingVersion,
        },
      ]);
    } catch (e: unknown) {
      setMessages((prev) => [...prev, { role: "agent", type: "error", text: `方案更新失败：${e}` }]);
    } finally {
      setLoading(false);
    }
  };

  const handlePlanConfirm = async (expectedVersion: number) => {
    setLoading(true);
    try {
      const r = await call("/teacher-agent/pending-generation/confirm", {
        conversation_id: conversationId,
        expected_version: expectedVersion,
      });
      const response: { status: string; paper: TeacherAgentResponse["paper"] } = await r.json();
      if (response.status !== "completed" || !response.paper?.ok || !response.paper.paper_id) {
        setMessages((prev) => [...prev, { role: "agent", type: "error", text: "组卷未完成，请检查约束校验结果。" }]);
        return;
      }
      const savedR = await fetch(`${API}/papers/${response.paper.paper_id}`);
      if (!savedR.ok) throw new Error("试卷已生成，但读取草稿失败");
      const saved: SavedPaper = await savedR.json();
      setCurrentPaperId(saved.paper_id);
      setMessages((prev) => [
        ...prev.map((item) => item.role === "agent" && item.type === "generation_plan" ? { ...item, disabled: true } : item),
        { role: "agent", type: "reply", text: "已按已确认的待生成方案创建试卷。" },
        { role: "agent", type: "paper_ready", paperId: saved.paper_id, version: saved.version, preview: saved.preview, validationReport: saved.validation_report },
      ]);
    } catch (e: unknown) {
      setMessages((prev) => [...prev, { role: "agent", type: "error", text: `组卷失败：${e}` }]);
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

    if (msg.type === "generation_plan") {
      const latestPlanIndex = messages.map((item) => item.role === "agent" && item.type === "generation_plan").lastIndexOf(true);
      return (
        <div key={idx} style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <RobotOutlined style={{ color: "#1677ff" }} />
            <Typography.Text strong>MathPaper Agent</Typography.Text>
          </div>
          <GenerationPlanCard
            title={msg.title}
            initialSections={msg.sections}
            loading={loading || restoringSession}
            disabled={msg.disabled || idx !== latestPlanIndex}
            onUpdate={(patches) => void handlePlanUpdate(patches, msg.pending_version)}
            onConfirm={() => void handlePlanConfirm(msg.pending_version)}
          />
        </div>
      );
    }

    if (msg.type === "teaching_planning_draft") {
      const sections = [
        ["学习问题分析", [msg.draft.problem_analysis]],
        ["学习目标", msg.draft.learning_objectives],
        ["知识重点", msg.draft.knowledge_focus],
        ["教学策略", msg.draft.teaching_strategy],
        ["评估策略", msg.draft.assessment_strategy],
      ] as const;
      return (
        <div key={idx} style={{ marginBottom: 16 }}>
          <Card size="small" title="教学规划草稿" style={{ maxWidth: 620, background: "#f6ffed" }}>
            {sections.map(([title, values]) => (
              <div key={title} style={{ marginBottom: 10 }}>
                <Typography.Text strong>{title}</Typography.Text>
                <ul style={{ margin: "4px 0 0", paddingLeft: 20 }}>
                  {values.map((value) => <li key={value}><MarkdownMath content={value} /></li>)}
                </ul>
              </div>
            ))}
            <Typography.Text type="secondary">请继续补充教材章节范围；之后可形成可确认的教学设计。</Typography.Text>
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
            <MarkdownMath content={msg.text} />
          </div>
        </div>
      );
    }

    if (msg.type === "paper_ready") {
      const validationPassed = msg.validationReport.passed;
      return (
        <div key={idx} style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <RobotOutlined style={{ color: "#1677ff" }} />
            <Typography.Text strong>MathPaper Agent</Typography.Text>
          </div>
          <Card size="small" style={{
            maxWidth: 520,
            background: validationPassed ? "#f6ffed" : "#fff2f0",
            border: `1px solid ${validationPassed ? "#b7eb8f" : "#ffccc7"}`,
          }}>
            <Tag icon={<SafetyCertificateOutlined />} color={validationPassed ? "success" : "error"}>
              {validationPassed ? "全部硬约束通过" : "审核未通过"}
            </Tag>
            <Typography.Paragraph style={{ marginTop: 8 }}>
              {validationPassed
                ? `组卷并审核通过，共 ${msg.preview.items.length} 题。可以下载使用。`
                : `草稿已生成，共 ${msg.preview.items.length} 题，但审核未通过，暂不可下载。`}
            </Typography.Paragraph>
            {!validationPassed && (
              <div style={{ marginBottom: 12 }}>
                {msg.validationReport.violations.length ? msg.validationReport.violations.map((violation) => (
                  <Typography.Paragraph type="danger" style={{ marginBottom: 4 }} key={`${violation.code}-${violation.field}`}>
                    {violation.message}：实际 {String(violation.actual)} / 要求 {String(violation.required)}
                  </Typography.Paragraph>
                )) : (
                  <Typography.Text type="danger">审核未通过，但系统未返回具体违规项。</Typography.Text>
                )}
              </div>
            )}
            <Space>
              <Button type="primary" size="small" onClick={() => void viewPaperPdf(msg.paperId, "student")}>
                查看试卷
              </Button>
              <Button disabled={!validationPassed} size="small" icon={<FilePdfOutlined />} onClick={() => downloadPdf(msg.paperId, "student", msg.preview.title)}>
                下载试卷 PDF
              </Button>
              <Button disabled={!validationPassed} size="small" icon={<FilePdfOutlined />} onClick={() => downloadPdf(msg.paperId, "teacher", msg.preview.title)}>
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
            <div style={{ color: "#ff4d4f" }}><MarkdownMath content={msg.text} /></div>
          </div>
        </div>
      );
    }

    return null;
  };

  const viewPaperPdf = async (
    paperId: string,
    version: PaperPdfVersion,
  ) => {
    try {
      await openPaperPdf(paperId, version);
    } catch (error) {
      message.error(String(error));
    }
  };

  const downloadPdf = async (
    paperId: string,
    version: PaperPdfVersion,
    title: string,
  ) => {
    try {
      await downloadPaperPdf(paperId, version, title);
    } catch (error) {
      message.error(String(error));
    }
  };

  // ── render ──
  return (
    <div className="agent-workspace">
      <div className="agent-chat-shell">
      {/* messages area */}
      <div className="agent-messages">
        {!restoringSession && messages.length === 0 && (
          <div className="agent-empty-state">
            <div className="agent-empty-mark"><RobotOutlined /></div>
            <Typography.Title level={3}>从一个教学目标开始</Typography.Title>
            <Typography.Paragraph type="secondary">
              告诉我章节、题量或难度，我来整理成可编辑的组卷方案。
            </Typography.Paragraph>
            <Space wrap className="agent-template-list">
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
      <div className="agent-composer">
        <div className="agent-composer-actions">
          <Button size="small" type="text" icon={<PlusOutlined />} onClick={handleNewConversation}>新建对话</Button>
        </div>
        <Space wrap className="agent-composer-templates">
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
          disabled={loading || restoringSession}
        />
        <div className="agent-send-row">
          <Typography.Text type="secondary">Enter 发送 · Shift + Enter 换行</Typography.Text>
          <Button type="primary" shape="circle" aria-label="发送" icon={<SendOutlined />} loading={loading} disabled={restoringSession || !input.trim()} onClick={() => void handleSend()} />
        </div>
      </div>
      </div>
    </div>
  );
}
