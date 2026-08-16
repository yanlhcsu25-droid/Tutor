import { useState, useEffect, useCallback, useRef } from "react";
import { Drawer, Button, Space, Typography, Spin, Modal, message, Progress, Row, Col, Tag, Tabs, InputNumber, Select, Card, Input, Alert } from "antd";
import { ArrowRightOutlined, ArrowLeftOutlined } from "@ant-design/icons";
import PdfImportPanel from "./PdfImportPanel";
import PdfViewer from "./PdfViewer";
import QuestionNav from "./QuestionNav";
import QuestionEditor from "./QuestionEditor";
import PageMarkdownPanel from "./PageMarkdownPanel";
import { wb } from "../api";
import type { WbQuestion, WbSource, WbSubmitResult } from "../api";
import "./OcrReviewDrawer.css";

// OCR Markdown 只承载题目正文类内容（题目内容 / 参考解答 / 题型 / 来源页码 /
// 原始题号 / 审核备注）。章节 / 知识点 / 难度已由结构化字段（knowledge_points_json
// / difficulty_level / QuestionKnowledgeLink）提供，下方审核卡片单独展示，
// 因此加载与保存时从 Markdown 中剥离这些空 section，避免用户误以为未保存。
const OCR_META_SECTION_RE = /^##\s+(?:章节|知识点|难度)[\s\S]*?(?=\n##\s+|$)/gm;

function stripOcrMetaSections(markdown: string): string {
  return markdown
    .replace(OCR_META_SECTION_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/^\n+/, "")
    .trimEnd() + "\n";
}

function questionMarkdownForReview(question: WbQuestion): string {
  let markdown = stripOcrMetaSections(question.edited_markdown);
  // 历史版本可能在答案已匹配、题目甚至已发布后，仍在草稿 Markdown
  // 中保留系统生成的缺答案提示。已发布草稿保持不可变，但审核界面不再误报。
  if (question.match_status === "matched") {
    markdown = markdown.replace(
      /(^##\s+审核备注\s*\n)\s*answer_not_found（未找到与该题号对应的参考解答）\s*(?=^##\s+|$)/m,
      "$1\n",
    );
  }
  return markdown;
}

interface Props {
  open: boolean;
  onClose: () => void;
  initialSourceId?: string | null;
}

export default function OcrReviewDrawer({ open, onClose, initialSourceId }: Props) {
  // ── state ──
  const [step, setStep] = useState<"import" | "review">("import");
  const [source, setSource] = useState<WbSource | null>(null);
  const [questions, setQuestions] = useState<WbQuestion[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [question, setQuestion] = useState<WbQuestion | null>(null);
  const [markdown, setMarkdown] = useState("");
  const [page, setPage] = useState(1);
  const [pdfSection, setPdfSection] = useState<"questions" | "solutions">("questions");
  const [zoom, setZoom] = useState(1);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(false);
  const [rightTab, setRightTab] = useState("question");
  const [splitDebug, setSplitDebug] = useState<Awaited<ReturnType<typeof wb.debugSplit>> | null>(null);
  const [preferredQuestion, setPreferredQuestion] = useState<{ page: number; number: string } | null>(null);
  const [classification, setClassification] = useState<Awaited<ReturnType<typeof wb.classifyKnowledge>> | null>(null);
  const [humanPrimary, setHumanPrimary] = useState<string | null>(null);
  const [humanSecondary, setHumanSecondary] = useState<string[]>([]);
  const [modificationReason, setModificationReason] = useState("");
  const [difficulty, setDifficulty] = useState<number | null>(null);
  const [recommending, setRecommending] = useState(false);
  const [recommendationError, setRecommendationError] = useState(false);
  const reviewStackRef = useRef<HTMLDivElement>(null);

  const total = questions.length;
  const reviewed = questions.filter((q) => ["reviewed", "published"].includes(q.review_status)).length;
  const qualitySampleCount = questions.filter((item) => item.quality_sample_required).length;
  const aiFailedCount = questions.filter((item) => item.ai_review && !item.ai_review.passed).length;
  const q = question;
  const isPublished = q?.review_status === "published";
  const aiKnowledge = q?.knowledge_shadow?.ai;
  const knowledgeOptions = classification?.options ?? [];
  const knowledgeLabel = (knowledgeId: string | null | undefined) => {
    if (!knowledgeId) return "未设置";
    return knowledgeOptions.find((item) => item.knowledge_id === knowledgeId)?.name ?? "加载中…";
  };
  const questionPages = source?.layout?.question_pages ?? [];
  const solutionPages = source?.layout?.solution_pages ?? [];
  const hasSeparatePages = source?.layout?.solution_mode === "separate" && solutionPages.length > 0;
  const visiblePdfPages = hasSeparatePages
    ? (pdfSection === "questions" ? questionPages : solutionPages)
    : Array.from({ length: source?.page_count ?? 0 }, (_, index) => index + 1);

  useEffect(() => {
    reviewStackRef.current?.scrollTo({ top: 0 });
  }, [q?.question_id]);

  const hydrateKnowledgeState = useCallback((current: WbQuestion) => {
    const shadow = current.knowledge_shadow;
    const ai = shadow?.ai;
    setClassification(ai ? {
      question_id: current.question_id,
      knowledge_points: [],
      options: [],
      primary_knowledge_point: null,
      secondary_knowledge_points: [],
      ...ai,
    } : null);
    setRecommendationError(false);
    setHumanPrimary(
      shadow?.human?.primary_knowledge_point_id
      ?? ai?.primary_knowledge_point_id
      ?? current.knowledge_points?.[0]
      ?? null,
    );
    setHumanSecondary(
      shadow?.human?.secondary_knowledge_point_ids
      ?? ai?.secondary_knowledge_point_ids
      ?? current.knowledge_points?.slice(1)
      ?? [],
    );
    setModificationReason(shadow?.human?.modification_reason ?? "");
    setDifficulty(current.difficulty_level ?? null);
    if (current.knowledge_points?.length || ai) {
      wb.getKnowledgeOptions(current.question_id)
        .then((res) => {
          const options = res.options ?? [];
          const legalIds = new Set(options.map((item) => item.knowledge_id));
          setHumanPrimary((previous) => previous && legalIds.has(previous) ? previous : null);
          setHumanSecondary((previous) => previous.filter((item) => legalIds.has(item)));
          setClassification((previous) => previous
            ? { ...previous, options }
            : {
              question_id: res.question_id,
              knowledge_points: [],
              options,
              needs_review: false,
              reason: "",
              provenance: "rule_suggested",
            });
        })
        .catch(() => {});
    }
  }, []);

  // ── load question ──
  const loadQuestion = useCallback(async (index: number) => {
    if (index < 0 || index >= questions.length) return;
    setLoading(true);
    try {
      const data = await wb.getQuestion(questions[index].question_id);
      setQuestion(data.question);
      setQuestions((previous) => previous.map((item) =>
        item.question_id === data.question.question_id ? data.question : item,
      ));
      setMarkdown(questionMarkdownForReview(data.question));
      setSource(data.source);
      setPage(data.question.page_number);
      setPdfSection("questions");
      setCurrentIndex(index);
      setDirty(false);
      hydrateKnowledgeState(data.question);
    } catch (e: unknown) { message.error(String(e)); }
    finally { setLoading(false); }
  }, [questions, hydrateKnowledgeState]);

  const updateSection = (sourceMarkdown: string, title: string, value: string) => {
    const re = new RegExp(`(^##\\s+${title}\\s*\\n)([\\s\\S]*?)(?=^##\\s+|$)`, "m");
    if (re.test(sourceMarkdown)) return sourceMarkdown.replace(re, `$1${value.trim()}\n`);
    return `${sourceMarkdown.trim()}\n\n## ${title}\n${value.trim()}\n`;
  };
  const saveMetadata = async (points: string[], level: number | null) => {
    if (!q) return;
    // points 本身就是 knowledge_id 列表，直接提交；名称仅用于 UI 展示，不写入后端/Markdown。
    try {
      const updated = await wb.saveMetadata(q.question_id, {
        knowledge_points: points,
        difficulty_level: level,
        content_confirmed: q.content_confirmed,
      });
      setQuestion(updated.question);
      setDifficulty(level);
      message.success("知识点与难度已保存");
    } catch (e: unknown) {
      message.error(String(e));
    }
  };
  const classify = async (questionId = q?.question_id) => {
    if (!questionId || !q || q.review_status === "published") return;
    setRecommending(true);
    setRecommendationError(false);
    try {
      const result = await wb.classifyKnowledge(questionId);
      setClassification(result);
      if (result.knowledge_shadow) {
        setQuestion((previous) => previous ? { ...previous, knowledge_shadow: result.knowledge_shadow } : previous);
      }
      setHumanPrimary(result.primary_knowledge_point?.knowledge_id ?? null);
      setHumanSecondary(result.secondary_knowledge_points?.map((item) => item.knowledge_id) ?? []);
      if (result.difficulty_result?.provenance === "llm_suggested") {
        setDifficulty(result.difficulty_result.difficulty_level);
      }
      const bothSuggested = result.provenance === "llm_suggested"
        && result.difficulty_result?.provenance === "llm_suggested";
      message.success(bothSuggested
        ? "已生成 AI 知识点与难度推荐，请人工确认"
        : "部分 AI 推荐不可用，已保留可人工修改的审核项");
    } catch (e: unknown) {
      setRecommendationError(true);
      message.error("题目内容已确认，但知识点推荐失败");
    } finally {
      setRecommending(false);
    }
  };
  const saveHumanKnowledgeReview = async () => {
    if (!q) return;
    try {
      const updated = await wb.saveHumanKnowledgeReview(q.question_id, {
        primary_knowledge_point_id: humanPrimary,
        secondary_knowledge_point_ids: humanSecondary,
        modification_reason: modificationReason || null,
      });
      setQuestion(updated.question);
      setQuestions((previous) => previous.map((item) => item.question_id === updated.question.question_id ? updated.question : item));
      message.success("人工知识点已确认，AI 快照已保留用于准确率分析");
    } catch (e: unknown) { message.error(String(e)); }
  };
  const savePublishedAiProfileReview = async () => {
    if (!q || q.publish_source !== "ai_auto" || !humanPrimary || !difficulty) return;
    try {
      const updated = await wb.reviewPublishedAiProfile(q.question_id, {
        primary_knowledge_point_id: humanPrimary,
        secondary_knowledge_point_ids: humanSecondary,
        difficulty_level: difficulty,
        modification_reason: modificationReason || null,
      });
      setQuestion(updated.question);
      setQuestions((previous) => previous.map((item) =>
        item.question_id === updated.question.question_id ? updated.question : item,
      ));
      hydrateKnowledgeState(updated.question);
      message.success("AI 自动发布画像已由人工复核并同步到正式题库");
    } catch (e: unknown) { message.error(String(e)); }
  };
  const confirmContent = async () => {
    if (!q || q.review_status === "published") return;
    try {
      let current = q;
      if (dirty) {
        const saved = await wb.saveQuestion(q.question_id, markdown);
        current = saved.question;
        setQuestion(current);
        setQuestions((previous) => previous.map((item) =>
          item.question_id === current.question_id ? current : item,
        ));
        setDirty(false);
      }
      const updated = await wb.confirmContent(current.question_id);
      setQuestion(updated);
      setQuestions((prev) => prev.map((item) => item.question_id === updated.question_id ? updated : item));
      message.success("题目内容已确认，正在推荐知识点");
      // 确认状态已经独立提交成功；推荐失败只影响推荐状态，不回滚确认结果。
      await classify(updated.question_id);
    } catch (e: unknown) { message.error(String(e)); }
  };

  const loadSourceForReview = useCallback(async (sourceId: string, preferredQuestionId?: string) => {
    setLoading(true);
    try {
      const data = await wb.listQuestions(sourceId);
      setSource(data.source);
      setQuestions(data.items);
      if (!data.items.length) {
        setQuestion(null);
        setMarkdown("");
        setPage(1);
        setCurrentIndex(0);
        setRightTab("page");
        setDirty(false);
        message.warning("没有切出题目，已打开整页 Markdown，请校对题号后重新切题");
        return;
      }
      setRightTab("question");
      const firstIncomplete = data.items.findIndex(
        (item) => !["reviewed", "published"].includes(item.review_status),
      );
      const preferredIndex = preferredQuestionId
        ? data.items.findIndex((item) => item.question_id === preferredQuestionId)
        : -1;
      const index = preferredIndex >= 0 ? preferredIndex : firstIncomplete >= 0 ? firstIncomplete : 0;
      const detail = await wb.getQuestion(data.items[index].question_id);
      setQuestion(detail.question);
      setMarkdown(questionMarkdownForReview(detail.question));
      setSource(detail.source);
      setPage(detail.question.page_number);
      setPdfSection("questions");
      setCurrentIndex(index);
      setDirty(false);
      hydrateKnowledgeState(detail.question);
    } catch (error: unknown) {
      message.error(String(error));
    } finally {
      setLoading(false);
    }
  }, [hydrateKnowledgeState]);

  // ── on source selected ──
  const handleImportReady = useCallback(async (sourceId: string, _count: number) => {
    setStep("review");
    await loadSourceForReview(sourceId);
  }, [loadSourceForReview]);

  const handleSelectExisting = useCallback(async (sourceId: string) => {
    setStep("review");
    await loadSourceForReview(sourceId);
  }, [loadSourceForReview]);

  // 外层 PDF 导入窗口选择已有资料后，直接进入本 Drawer 的审核步骤，
  // 避免再次显示一层完全相同的“PDF 导入”页面。
  useEffect(() => {
    if (!open || !initialSourceId) return;
    setStep("review");
    setSource(null);
    setQuestions([]);
    setQuestion(null);
    setCurrentIndex(0);
    void loadSourceForReview(initialSourceId);
  }, [open, initialSourceId, loadSourceForReview]);

  // ── actions ──
  const saveAndNext = async () => {
    if (!q) return;
    setLoading(true);
    try {
      await wb.saveQuestion(q.question_id, markdown);
      setDirty(false);
      if (currentIndex < total - 1) await loadQuestion(currentIndex + 1);
    } catch (e: unknown) { message.error(String(e)); }
    finally { setLoading(false); }
  };

  const generateQuestions = async () => {
    if (!source) return;
    setLoading(true);
    try {
      const plan = await wb.generatePreview(source.source_file_id);
      Modal.confirm({
        title: "根据已保存 Markdown 重建未发布题目？",
        content: `将保留 ${plan.preserved_published.length} 道已发布题，生成 ${plan.created.length} 道题，并替换 ${plan.old_unpublished.length} 个未发布草稿。不会修改已发布题，也不会重新 OCR。`,
        onOk: async () => {
          setLoading(true);
          try {
            await wb.generateApply(source.source_file_id, plan.new_numbers);
            message.success("已根据最新 Markdown 重新生成题目");
            setRightTab("page");
            await loadSourceForReview(source.source_file_id);
          } catch (error: unknown) { message.error(String(error)); }
          finally { setLoading(false); }
        },
      });
    } catch (error: unknown) { message.error(String(error)); }
    finally { setLoading(false); }
  };

  const repairMissingAnswers = () => {
    if (!source) return;
    Modal.confirm({
      title: "重新匹配缺失答案？",
      content: "只补充未发布且参考解答为空的题目，不覆盖人工答案，也不修改已发布题。",
      onOk: async () => {
        setLoading(true);
        try {
          const result = await wb.repairMissingAnswers(source.source_file_id);
          if (result.repaired_count) {
            message.success(`已恢复 ${result.repaired_count} 道题的参考解答`);
          } else {
            message.info("没有发现可安全自动恢复的参考解答");
          }
          await loadSourceForReview(source.source_file_id);
        } catch (error: unknown) {
          message.error(String(error));
        } finally {
          setLoading(false);
        }
      },
    });
  };

  const runAiAutoPublish = () => {
    if (!source) return;
    Modal.confirm({
      title: "AI 审核并受控自动发布？",
      content: "只自动发布题目答案已匹配、确定性硬校验通过、AI 内容审核通过、知识点与必填画像完整的题目；其余题目保留给人工审核。",
      onOk: async () => {
        setLoading(true);
        try {
          if (q && dirty && q.review_status !== "published") {
            await wb.saveQuestion(q.question_id, markdown);
            setDirty(false);
          }
          const result = await wb.aiAutoPublish(source.source_file_id);
          message.success(
            `AI 自动发布 ${result.published_count} 题，转人工 ${result.manual_review_count} 题，抽检标记 ${result.quality_sample_count} 题`,
          );
          await loadSourceForReview(
            source.source_file_id,
            result.manual_review[0]?.question_id,
          );
        } catch (error: unknown) {
          message.error(String(error));
        } finally {
          setLoading(false);
        }
      },
    });
  };

  const openFirstQualitySample = () => {
    const index = questions.findIndex((item) => item.quality_sample_required);
    if (index >= 0) void loadQuestion(index);
  };

  const openAiFailure = (direction: "first" | "previous" | "next") => {
    const indexes = questions
      .map((item, index) => item.ai_review && !item.ai_review.passed ? index : -1)
      .filter((index) => index >= 0);
    if (!indexes.length) return;
    let target = indexes[0];
    if (direction === "next") {
      target = indexes.find((index) => index > currentIndex) ?? indexes[0];
    } else if (direction === "previous") {
      target = [...indexes].reverse().find((index) => index < currentIndex) ?? indexes.at(-1)!;
    }
    void loadQuestion(target);
  };

  const showFullAiReviewReason = () => {
    if (!q?.ai_review) return;
    Modal.info({
      title: "AI 未通过审核的完整理由",
      width: 720,
      content: <div className="ocr-review-modal-scroll">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
            {q.ai_review.reason}
          </Typography.Paragraph>
          <Typography.Text type="secondary">
            风险项：{(q.ai_review.risk_codes || []).join("、") || "未给出风险码"}
          </Typography.Text>
        </Space>
      </div>,
    });
  };

  const reviewPublishAndNext = async () => {
    if (!source || !q || q.review_status === "published") return;
    setLoading(true);
    try {
      if (dirty) {
        await wb.saveQuestion(q.question_id, markdown);
        setDirty(false);
      }
      const result = await wb.submitAll(source.source_file_id, [q.question_id]);
      if (result.success_count === 1) {
        // 提交接口的校验通过后，才允许调用正式发布接口；发布端仍会复核
        // 答案匹配状态等门禁，任一失败均停留当前题，不会自动跳转。
        const published = await wb.publish([q.question_id]);
        if (!published.published_count) {
          Modal.error({
            title: "审核通过，但发布失败",
            content: published.failures.map((item) => item.reason).join("；") || "未知发布错误",
          });
          const data = await wb.listQuestions(source.source_file_id);
          setQuestions(data.items);
          await loadQuestion(currentIndex);
          return;
        }
        const publishedQuestion = await wb.getQuestion(q.question_id);
        setQuestions((previous) => previous.map((item) =>
          item.question_id === publishedQuestion.question.question_id
            ? publishedQuestion.question
            : item,
        ));
        message.success(`第 ${q.original_number} 题已审核通过并发布`);
        if (currentIndex < total - 1) await loadQuestion(currentIndex + 1);
        else {
          setQuestion(publishedQuestion.question);
          setSource(publishedQuestion.source);
        }
      } else {
        const reasons = result.failures.flatMap((item) => item.reasons ?? []);
        Modal.error({
          title: "题目不完整，无法发布",
          content: reasons.length
            ? <ul style={{ marginBottom: 0 }}>{reasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}</ul>
            : "当前题目未通过完整性审核",
        });
      }
    } catch (e: unknown) { message.error(String(e)); }
    finally { setLoading(false); }
  };

  const debugCurrentPage = async () => {
    if (!source) return;
    setLoading(true);
    try {
      const firstPage = Math.max(1, page - 1);
      const lastPage = Math.min(source.page_count || page, page + 1);
      const pages = await Promise.all(
        Array.from({ length: lastPage - firstPage + 1 }, (_, offset) => {
          const pageNumber = firstPage + offset;
          if (pageNumber === page) return Promise.resolve({ page_number: pageNumber, markdown });
          return wb.getPageMarkdown(source.source_file_id, pageNumber).then((data) => ({
            page_number: pageNumber,
            markdown: data.edited_markdown || data.raw_markdown,
          }));
        }),
      );
      const result = await wb.debugSplit(pages);
      setSplitDebug(result);
    } catch (e: unknown) { message.error(String(e)); }
    finally { setLoading(false); }
  };

  // 重新切题后草稿被整体替换，必须重新拉取列表并重置游标
  const reloadAfterResplit = useCallback(async () => {
    if (!source) return;
    const currentTarget = q ? { page: q.page_number, number: q.original_number } : null;
    setLoading(true);
    try {
      const data = await wb.listQuestions(source.source_file_id);
      setRightTab("page");
      setQuestions(data.items);
      setPreferredQuestion(currentTarget);
      const targetIndex = currentTarget
        ? data.items.findIndex((item) => item.page_number === currentTarget.page && item.original_number === currentTarget.number)
        : -1;
      setCurrentIndex(targetIndex >= 0 ? targetIndex : 0);
      setQuestion(null);
      setMarkdown("");
      setDirty(false);
    } catch (e: unknown) { message.error(String(e)); }
    finally { setLoading(false); }
  }, [source, q]);

  // 列表被替换后重新载入第一题
  useEffect(() => {
    if (step === "review" && questions.length > 0 && question === null) {
      const targetIndex = preferredQuestion
        ? questions.findIndex((item) => item.page_number === preferredQuestion.page && item.original_number === preferredQuestion.number)
        : currentIndex;
      void loadQuestion(targetIndex >= 0 ? targetIndex : 0);
      setPreferredQuestion(null);
    }
  }, [step, questions, question, loadQuestion, preferredQuestion, currentIndex]);

  const prev = () => { if (currentIndex > 0) loadQuestion(currentIndex - 1); };
  const next = () => { if (currentIndex < total - 1) loadQuestion(currentIndex + 1); };

  const switchPdfSection = (section: "questions" | "solutions") => {
    setPdfSection(section);
    const pages = section === "questions" ? questionPages : solutionPages;
    if (pages.length && !pages.includes(page)) setPage(pages[0]);
  };

  const jumpLine = (line: number) => {
    if (!line) return;
    const lines = markdown.split("\n");
    const start = lines.slice(0, line - 1).join("\n").length + (line > 1 ? 1 : 0);
    // Switch to edit tab is handled by jumping directly
  };

  // ── reset on close ──
  const handleClose = () => {
    setStep("import");
    setSource(null);
    setQuestions([]);
    setCurrentIndex(0);
    setQuestion(null);
    setDirty(false);
    onClose();
  };

  return (
    <>
    <Drawer
      open={open}
      onClose={handleClose}
      width="100%"
      className="ocr-review-drawer"
      title={step === "import" ? "PDF 导入" : "题目审核"}
      extra={step === "review" && source ? (
        <Space>
          <Typography.Text type="secondary">{source.original_name}</Typography.Text>
          <Button onClick={() => setStep("import")}>返回选择</Button>
          <Button onClick={() => void debugCurrentPage()}>切题诊断</Button>
          <Button onClick={repairMissingAnswers}>重新匹配缺失答案</Button>
          <Button onClick={() => setRightTab("page")}>返回 Markdown 校对</Button>
          <Button onClick={() => void generateQuestions()}>{questions.length ? "重新生成题目" : "生成题目"}</Button>
          <Button type="primary" onClick={runAiAutoPublish}>AI 审核并自动发布</Button>
          {aiFailedCount > 0 && (
            <Button danger onClick={() => openAiFailure("first")}>查看 AI 未通过（{aiFailedCount}）</Button>
          )}
          {qualitySampleCount > 0 && (
            <Button onClick={openFirstQualitySample}>查看 AI 抽检（{qualitySampleCount}）</Button>
          )}
        </Space>
      ) : undefined}
      styles={{ body: { padding: 0, overflow: "hidden", display: "flex", minHeight: 0 } }}
    >
      {step === "import" && (
        <PdfImportPanel
          open={step === "import"}
          onReady={handleImportReady}
          onSelectExisting={handleSelectExisting}
        />
      )}

      {step === "review" && (
        <Spin spinning={loading} wrapperClassName="ocr-review-spinner">
          <div className="ocr-review-layout">
          {/* progress */}
          <div className="ocr-review-progress">
            <Row align="middle" gutter={16}>
              <Col flex="auto">
                <Typography.Text>
                  {total > 0 ? `已审核 ${reviewed} 题，剩余 ${total - reviewed} 题` : "未切出题目，请校对整页 Markdown"}
                </Typography.Text>
                <Progress percent={total ? Math.round((reviewed / total) * 100) : 0} size="small" style={{ marginBottom: 0 }} />
              </Col>
              {total > 0 && <Col>
                <QuestionNav
                  currentIndex={currentIndex}
                  total={total}
                  status={q?.review_status ?? "pending"}
                  originalNumber={q?.original_number ?? ""}
                  pageNumber={q?.page_number ?? 1}
                  onPrev={prev}
                  onNext={next}
                />
              </Col>}
            </Row>
          </div>

          {/* workspace */}
          <div className="ocr-review-workspace">
            <section className="ocr-review-pane ocr-review-pdf-pane">
              {source && (
                <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
                  {hasSeparatePages && (
                    <Tabs
                      activeKey={pdfSection}
                      onChange={(key) => switchPdfSection(key as "questions" | "solutions")}
                      items={[
                        { key: "questions", label: `题目（${questionPages[0]}–${questionPages.at(-1)} 页）` },
                        { key: "solutions", label: `答案（${solutionPages[0]}–${solutionPages.at(-1)} 页）` },
                      ]}
                      style={{ marginBottom: 4 }}
                    />
                  )}
                  <div style={{ flex: 1, minHeight: 0 }}>
                    <PdfViewer
                      sourceId={source.source_file_id}
                      pageCount={source.page_count}
                      pages={visiblePdfPages}
                      page={page}
                      zoom={zoom}
                      bbox={pdfSection === "questions" ? q?.source_bbox ?? null : null}
                      onPageChange={setPage}
                      onZoomChange={setZoom}
                    />
                  </div>
                </div>
              )}
            </section>
            <section className="ocr-review-pane ocr-review-editor-pane">
              <Tabs
                className="ocr-review-tabs"
                activeKey={rightTab}
                onChange={setRightTab}
                items={[
                  {
                    key: "question",
                    label: "题目审核",
                    children: q ? (
                      <div ref={reviewStackRef} className="question-review-stack">
                        {q.publish_source === "ai_auto" && <Alert
                          type={q.quality_sample_required ? "info" : "success"}
                          showIcon
                          message={q.quality_sample_required ? "AI 自动发布 · 随机抽检题" : "AI 受控自动发布"}
                          description={q.ai_review?.reason || "确定性硬校验、AI 内容审核、知识点与画像校验均已通过。"}
                        />}
                        {q.ai_review && !q.ai_review.passed && <Alert
                          type="warning"
                          showIcon
                          message="AI 审核未通过，需人工校验"
                          description={`${q.ai_review.reason.slice(0, 180)}${q.ai_review.reason.length > 180 ? "…" : ""}（风险：${(q.ai_review.risk_codes || []).join("、") || "未给出风险码"}）`}
                          action={<Space direction="vertical" size={4}>
                            <Button size="small" onClick={showFullAiReviewReason}>查看完整理由</Button>
                            <Button size="small" onClick={() => openAiFailure("previous")}>上一道未通过</Button>
                            <Button size="small" type="primary" onClick={() => openAiFailure("next")}>下一道未通过</Button>
                          </Space>}
                        />}
                        {q.match_status !== "matched" && <Alert
                          type="warning"
                          showIcon
                          message="参考解答尚未可靠匹配"
                          description={`${q.review_note || q.match_status}。请切换左侧答案页核对；若答案确实存在，可在下方 Markdown 源码的“## 参考解答”中补充后保存。`}
                        />}
                        <QuestionEditor key={q.question_id} questionId={q.question_id} markdown={markdown} validation={q.validation} onChange={(v) => { setMarkdown(v); setDirty(true); }} onJumpLine={jumpLine} readOnly={isPublished} />
                        {isPublished && q.publish_source === "ai_auto" && <Card
                          size="small"
                          title={q.quality_sample_required ? "AI 自动发布抽检（可修改）" : "AI 自动发布画像复核（可修改）"}
                        >
                          <Space direction="vertical" style={{ width: "100%" }}>
                            <Card size="small" type="inner" title="AI 原始推荐（只读快照）">
                              <Space direction="vertical" size={4}>
                                <Typography.Text>主知识点：{knowledgeLabel(aiKnowledge?.primary_knowledge_point_id)}</Typography.Text>
                                <Typography.Text>
                                  辅助知识点：{aiKnowledge?.secondary_knowledge_point_ids?.length
                                    ? aiKnowledge.secondary_knowledge_point_ids.map(knowledgeLabel).join(" / ")
                                    : "无"}
                                </Typography.Text>
                                <Typography.Text>难度：{q.ai_review?.difficulty_level ?? q.difficulty_level ?? "未设置"}</Typography.Text>
                                <Typography.Text type="secondary">
                                  难度依据：{q.ai_review?.difficulty_result?.reason
                                    ?? "历史自动发布记录：该难度由旧版 Python 规则估算"}
                                </Typography.Text>
                                {q.ai_review?.difficulty_result && <Typography.Text type="secondary">
                                  参考人工样例：{q.ai_review.difficulty_result.example_count ?? 0} 道；
                                  AI 把握度：{q.ai_review.difficulty_result.confidence.toFixed(2)}
                                </Typography.Text>}
                                <Typography.Text type="secondary">推荐理由：{aiKnowledge?.reason ?? "未记录"}</Typography.Text>
                              </Space>
                            </Card>
                            <Typography.Text strong>人工复核结果</Typography.Text>
                            <Select
                              value={humanPrimary ?? undefined}
                              options={knowledgeOptions.map((item) => ({ value: item.knowledge_id, label: item.name }))}
                              onChange={setHumanPrimary}
                              placeholder="选择主知识点"
                            />
                            <Select
                              mode="multiple"
                              maxCount={2}
                              value={humanSecondary}
                              options={knowledgeOptions
                                .filter((item) => item.knowledge_id !== humanPrimary)
                                .map((item) => ({ value: item.knowledge_id, label: item.name }))}
                              onChange={setHumanSecondary}
                              placeholder="选择辅助知识点（最多2个）"
                            />
                            <Space>
                              <Typography.Text>难度</Typography.Text>
                              <InputNumber min={1} max={5} value={difficulty} onChange={setDifficulty} />
                            </Space>
                            <Input
                              value={modificationReason}
                              onChange={(event) => setModificationReason(event.target.value)}
                              placeholder="修改原因（可选）"
                              maxLength={500}
                            />
                            <Button
                              type="primary"
                              disabled={!humanPrimary || !difficulty}
                              onClick={() => void savePublishedAiProfileReview()}
                            >
                              确认画像复核
                            </Button>
                            {q.ai_review?.profile_human_review && <Tag color="success">已完成人工画像复核</Tag>}
                          </Space>
                        </Card>}
                        {!isPublished && <Card size="small" title="第二阶段：题目画像审核">
                          <Space direction="vertical" style={{ width: "100%" }}>
                            <Space wrap>
                              <Button onClick={() => void confirmContent()} loading={recommending} disabled={recommending}>确认题目内容并生成 AI 推荐</Button>
                              {classification?.provenance === "llm_suggested" && <Tag color="blue">AI 推荐已锁定</Tag>}
                              {classification && classification.provenance !== "llm_suggested" && <Tag color="orange">当前为规则降级，可再次确认重试 AI</Tag>}
                              {q.content_confirmed && <Tag color="success">题目内容已确认</Tag>}
                            </Space>
                            {recommending && <Typography.Text type="secondary">知识点推荐中...</Typography.Text>}
                            {recommendationError && <Typography.Text type="danger">知识点推荐失败，可点击“重新推荐”；也可以直接手动选择知识点。</Typography.Text>}
                            {classification && <Card size="small" title="AI 推荐（只读快照）">
                              <Space direction="vertical" style={{ width: "100%" }}>
                                <Select disabled value={classification.primary_knowledge_point?.knowledge_id ?? undefined} options={(classification.options ?? []).map(x => ({ value: x.knowledge_id, label: x.name }))} placeholder="主知识点：未确定" />
                                <Select disabled mode="multiple" value={classification.secondary_knowledge_points?.map(x => x.knowledge_id) ?? []} options={(classification.options ?? []).map(x => ({ value: x.knowledge_id, label: x.name }))} placeholder="辅助知识点：无" />
                                <Typography.Text>置信度：{(classification.confidence ?? 0).toFixed(2)}{classification.needs_review ? "（请重点检查）" : ""}</Typography.Text>
                                <Typography.Text type="secondary">理由：{classification.reason}</Typography.Text>
                                <Typography.Text>
                                  AI 难度：{classification.difficulty_result?.provenance === "llm_suggested"
                                    ? classification.difficulty_result.difficulty_level
                                    : classification.difficulty_result
                                      ? "未形成有效推荐"
                                      : "旧推荐未包含难度，请再次点击上方按钮补充"}
                                </Typography.Text>
                                {classification.difficulty_result && <Typography.Text type="secondary">
                                  难度理由：{classification.difficulty_result.reason}；参考人工样例：
                                  {classification.difficulty_result.example_count ?? 0} 道
                                </Typography.Text>}
                              </Space>
                            </Card>}
                            {classification && <Card size="small" title="人工确认（最终真值）">
                              <Space direction="vertical" style={{ width: "100%" }}>
                                <Select value={humanPrimary ?? undefined} options={(classification.options ?? []).map(x => ({ value: x.knowledge_id, label: x.name }))} onChange={setHumanPrimary} placeholder="选择主知识点" />
                                <Select mode="multiple" maxCount={2} value={humanSecondary} options={(classification.options ?? []).filter(x => x.knowledge_id !== humanPrimary).map(x => ({ value: x.knowledge_id, label: x.name }))} onChange={setHumanSecondary} placeholder="选择辅助知识点（最多2个）" />
                                <Input value={modificationReason} onChange={(event) => setModificationReason(event.target.value)} placeholder="修改原因（可选；未填写时自动记录）" maxLength={500} />
                                <Space><Button onClick={() => { setHumanPrimary(classification.primary_knowledge_point?.knowledge_id ?? null); setHumanSecondary(classification.secondary_knowledge_points?.map(x => x.knowledge_id) ?? []); setModificationReason("ai_accepted"); }}>填入 AI 推荐</Button><Button type="primary" onClick={() => void saveHumanKnowledgeReview()}>确认人工知识点</Button></Space>
                              </Space>
                            </Card>}
                            <Space><Typography.Text>难度（可修改）</Typography.Text><InputNumber min={1} max={5} value={difficulty} onChange={(v) => saveMetadata(q.knowledge_points ?? [], v)} placeholder="AI 推荐后可人工修改" /></Space>
                            {classification && <Typography.Text type="secondary">AI 推荐仅供审核；置信度未经统计校准，最终仍需人工确认1～3个知识点。</Typography.Text>}
                          </Space>
                        </Card>}
                      </div>
                    ) : (
                      <Typography.Text type="secondary">当前 PDF 还没有题目草稿</Typography.Text>
                    ),
                  },
                  {
                    key: "page",
                    label: "整页 Markdown",
                    children: source ? (
                      <div className="ocr-review-markdown-scroll">
                        <PageMarkdownPanel
                          sourceId={source.source_file_id}
                          page={page}
                          onRebuilt={reloadAfterResplit}
                        />
                      </div>
                    ) : null,
                  },
                ]}
              />
            </section>
          </div>

          {/* footer */}
          {q && (
            <div className="ocr-review-footer">
              <Row justify="space-between" align="middle">
                <Col>
                  <Space size={4}>
                    {isPublished && <Tag color="gold">已发布</Tag>}
                    <Typography.Text type="secondary">
                      来源页码 {q.page_number} · {q.question_id.slice(0, 12)}...
                    </Typography.Text>
                  </Space>
                </Col>
                <Col>
                  <Space>
                    <Button icon={<ArrowRightOutlined />} onClick={saveAndNext} disabled={isPublished}>
                      保存并下一题
                    </Button>
                    <Button type="primary" icon={<ArrowRightOutlined />} onClick={() => void reviewPublishAndNext()} disabled={isPublished}>
                      发布并下一题
                    </Button>
                  </Space>
                </Col>
              </Row>
            </div>
          )}
          </div>
        </Spin>
      )}
    </Drawer>
      <Modal
        open={splitDebug !== null}
        title={`第${page}页切题诊断`}
        footer={<Button onClick={() => setSplitDebug(null)}>关闭</Button>}
        onCancel={() => setSplitDebug(null)}
        styles={{ body: { maxHeight: "calc(100vh - 220px)", overflowY: "auto" } }}
      >
        {splitDebug && (
          <Space direction="vertical" style={{ width: "100%" }}>
            {splitDebug.pages.map((item) => (
              <Typography.Text key={item.page_number}>
                第{item.page_number}页：识别题号 [{item.major_numbers.join("、") || "无"}]，
                {item.has_continuation ? "存在续页内容" : "无续页内容"}
              </Typography.Text>
            ))}
            <Typography.Text>
              生成候选题：{splitDebug.candidates.map((item) => item.original_number).join("、") || "无"}
            </Typography.Text>
            {splitDebug.warnings.map((warning) => <Tag color="warning" key={warning}>{warning}</Tag>)}
          </Space>
        )}
      </Modal>
    </>
  );
}
