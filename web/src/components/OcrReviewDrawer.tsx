import { useState, useEffect, useCallback } from "react";
import { Drawer, Button, Space, Typography, Spin, Modal, message, Progress, Row, Col, Tag, Tabs, InputNumber, Select, Card } from "antd";
import { SaveOutlined, SendOutlined, ArrowRightOutlined, ArrowLeftOutlined } from "@ant-design/icons";
import PdfImportPanel from "./PdfImportPanel";
import PdfViewer from "./PdfViewer";
import QuestionNav from "./QuestionNav";
import QuestionEditor from "./QuestionEditor";
import PageMarkdownPanel from "./PageMarkdownPanel";
import { wb } from "../api";
import type { WbQuestion, WbSource, WbSubmitResult, WbPublishResult } from "../api";
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
  const [zoom, setZoom] = useState(1);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(false);
  const [rightTab, setRightTab] = useState("question");
  const [splitDebug, setSplitDebug] = useState<Awaited<ReturnType<typeof wb.debugSplit>> | null>(null);
  const [preferredQuestion, setPreferredQuestion] = useState<{ page: number; number: string } | null>(null);
  const [classification, setClassification] = useState<Awaited<ReturnType<typeof wb.classifyKnowledge>> | null>(null);
  const [selectedKnowledge, setSelectedKnowledge] = useState<string[]>([]);
  const [difficulty, setDifficulty] = useState<number | null>(null);
  const [recommending, setRecommending] = useState(false);
  const [recommendationError, setRecommendationError] = useState(false);

  const total = questions.length;
  const reviewed = questions.filter((q) => ["reviewed", "published"].includes(q.review_status)).length;
  const q = question;
  const isPublished = q?.review_status === "published";

  // ── load question ──
  const loadQuestion = useCallback(async (index: number) => {
    if (index < 0 || index >= questions.length) return;
    setLoading(true);
    try {
      const data = await wb.getQuestion(questions[index].question_id);
      setQuestion(data.question);
      setMarkdown(stripOcrMetaSections(data.question.edited_markdown));
      setSource(data.source);
      setPage(data.question.page_number);
      setCurrentIndex(index);
      setDirty(false);
      setClassification(null);
      setRecommendationError(false);
      setSelectedKnowledge(data.question.knowledge_points ?? []);
      setDifficulty(data.question.difficulty_level ?? null);
      // 已保存的是 knowledge_id，加载选项以便 Select 反显知识点名称（不触发 AI 分类）。
      if (data.question.knowledge_points?.length) {
        wb.getKnowledgeOptions(data.question.question_id)
          .then((res) =>
            setClassification({
              question_id: res.question_id,
              knowledge_points: [],
              options: res.options,
              needs_review: false,
              reason: "",
              provenance: "rule_suggested",
            }),
          )
          .catch(() => {});
      }
    } catch (e: unknown) { message.error(String(e)); }
    finally { setLoading(false); }
  }, [questions]);

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
      setSelectedKnowledge(points);
      setDifficulty(level);
      message.success("知识点与难度已保存");
    } catch (e: unknown) {
      message.error(String(e));
    }
  };
  const classify = async (questionId = q?.question_id) => {
    if (!questionId || !q || q.review_status === "published" || dirty) return;
    setRecommending(true);
    setRecommendationError(false);
    try {
      const result = await wb.classifyKnowledge(questionId);
      setClassification(result);
      setSelectedKnowledge(result.knowledge_points.map(x => x.knowledge_id));
      message.success("已生成本地规则知识点推荐，请人工确认");
    } catch (e: unknown) {
      setRecommendationError(true);
      message.error("题目内容已确认，但知识点推荐失败");
    } finally {
      setRecommending(false);
    }
  };
  const confirmContent = async () => {
    if (!q || q.review_status === "published" || dirty) { message.warning("请先保存当前题目内容"); return; }
    try {
      const updated = await wb.confirmContent(q.question_id);
      setQuestion(updated);
      setQuestions((prev) => prev.map((item) => item.question_id === updated.question_id ? updated : item));
      message.success("题目内容已确认，正在推荐知识点");
      // 确认状态已经独立提交成功；推荐失败只影响推荐状态，不回滚确认结果。
      await classify(updated.question_id);
    } catch (e: unknown) { message.error(String(e)); }
  };

  const loadSourceForReview = useCallback(async (sourceId: string) => {
    setLoading(true);
    try {
      const data = await wb.listQuestions(sourceId);
      setQuestions(data.items);
      if (!data.items.length) {
        setQuestion(null);
        message.warning("该资料暂未生成题目，请检查 OCR 结果");
        return;
      }
      const firstIncomplete = data.items.findIndex(
        (item) => !["reviewed", "published"].includes(item.review_status),
      );
      const index = firstIncomplete >= 0 ? firstIncomplete : 0;
      const detail = await wb.getQuestion(data.items[index].question_id);
      setQuestion(detail.question);
      setMarkdown(stripOcrMetaSections(detail.question.edited_markdown));
      setSource(detail.source);
      setPage(detail.question.page_number);
      setCurrentIndex(index);
      setDirty(false);
    } catch (error: unknown) {
      message.error(String(error));
    } finally {
      setLoading(false);
    }
  }, []);

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
  const save = async () => {
    if (!q) return;
    setLoading(true);
    try {
      const data = await wb.saveQuestion(q.question_id, markdown);
      setQuestion(data.question);
      setQuestions((prev) => prev.map((item) => item.question_id === data.question.question_id ? data.question : item));
      if (!data.question.content_confirmed) {
        setClassification(null);
        setSelectedKnowledge([]);
        setRecommendationError(false);
      }
      setDirty(false);
      message.success("已保存");
    } catch (e: unknown) { message.error(String(e)); }
    finally { setLoading(false); }
  };

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

  const publish = async () => {
    if (!q) return;
    if (q.review_status !== "reviewed") { message.warning("请先提交审核（批量提交）"); return; }
    Modal.confirm({
      title: "确认发布",
      content: "将当前题目发布到正式题库，组卷流程即可使用。",
      onOk: async () => {
        setLoading(true);
        try {
          const result: WbPublishResult = await wb.publish([q.question_id]);
          if (result.published_count) {
            message.success("已发布到题库");
            await loadQuestion(currentIndex);
          } else {
            message.error(result.failures[0]?.reason ?? "发布失败");
          }
        } catch (e: unknown) { message.error(String(e)); }
        finally { setLoading(false); }
      },
    });
  };

  const publishAndNext = async () => {
    if (!q) return;
    if (q.review_status !== "reviewed") { message.warning("请先提交审核"); return; }
    Modal.confirm({
      title: "确认发布并继续",
      content: "发布当前题目并自动跳转到下一题。",
      onOk: async () => {
        setLoading(true);
        try {
          await wb.publish([q.question_id]);
          message.success("已发布");
          if (currentIndex < total - 1) await loadQuestion(currentIndex + 1);
        } catch (e: unknown) { message.error(String(e)); }
        finally { setLoading(false); }
      },
    });
  };

  const createRevision = async () => {
    if (!q || !isPublished) return;
    setLoading(true);
    try {
      const data = await wb.createRevision(q.question_id);
      setQuestion(data.question);
      setQuestions((prev) => [...prev, data.question]);
      setCurrentIndex(questions.length);
      setMarkdown(stripOcrMetaSections(data.question.edited_markdown));
      setDirty(false);
      message.success("已创建修订版本，请修改后重新提交审核");
    } catch (e: unknown) { message.error(String(e)); }
    finally { setLoading(false); }
  };

  const submitAll = async () => {
    if (!source) return;
    Modal.confirm({
      title: "批量提交审核",
      content: "将所有题目提交校验，校验通过后可发布到题库。",
      onOk: async () => {
        setLoading(true);
        try {
          const result: WbSubmitResult = await wb.submitAll(source.source_file_id);
          const summary = [
            `成功导入：${result.success_count} 题`,
            `校验失败：${result.failure_count} 题`,
          ].join("，");
          message.info(summary);
          // Refresh question list
          const data = await wb.listQuestions(source.source_file_id);
          setQuestions(data.items);
          if (data.items.length > 0) await loadQuestion(currentIndex);
        } catch (e: unknown) { message.error(String(e)); }
        finally { setLoading(false); }
      },
    });
  };

  const generateQuestions = async () => {
    if (!source) return;
    setLoading(true);
    try {
      const plan = await wb.generatePreview(source.source_file_id);
      if (plan.blocked) {
        message.error("当前 Markdown 会影响已发布题目，不能重建");
        return;
      }
      Modal.confirm({
        title: "根据已保存 Markdown 生成题目？",
        content: `将生成 ${plan.created.length} 题，并替换 ${plan.old_unpublished.length} 个未发布草稿。此操作不重新 OCR。`,
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

  const submitCurrent = async () => {
    if (!source || !q || q.review_status === "published") return;
    setLoading(true);
    try {
      if (dirty) {
        await wb.saveQuestion(q.question_id, markdown);
        setDirty(false);
      }
      const result = await wb.submitAll(source.source_file_id, [q.question_id]);
      if (result.success_count === 1) {
        message.success(`第 ${q.original_number} 题已审核通过`);
        const data = await wb.listQuestions(source.source_file_id);
        setQuestions(data.items);
        await loadQuestion(currentIndex);
      } else {
        message.error(result.failures[0]?.reasons.join("；") ?? "当前题校验失败");
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
          <Button onClick={() => setRightTab("page")}>返回 Markdown 校对</Button>
          <Button onClick={() => void generateQuestions()}>{questions.length ? "重新生成题目" : "生成题目"}</Button>
          <Button type="primary" onClick={submitAll}>批量提交</Button>
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
                  {total > 0 ? `已审核 ${reviewed} 题，剩余 ${total - reviewed} 题` : "等待加载"}
                </Typography.Text>
                <Progress percent={total ? Math.round((reviewed / total) * 100) : 0} size="small" style={{ marginBottom: 0 }} />
              </Col>
              <Col>
                <QuestionNav
                  currentIndex={currentIndex}
                  total={total}
                  status={q?.review_status ?? "pending"}
                  originalNumber={q?.original_number ?? ""}
                  pageNumber={q?.page_number ?? 1}
                  onPrev={prev}
                  onNext={next}
                />
              </Col>
            </Row>
          </div>

          {/* workspace */}
          <div className="ocr-review-workspace">
            <section className="ocr-review-pane ocr-review-pdf-pane">
              {source && (
                <PdfViewer
                  sourceId={source.source_file_id}
                  pageCount={source.page_count}
                  page={page}
                  zoom={zoom}
                  bbox={q?.source_bbox ?? null}
                  onPageChange={setPage}
                  onZoomChange={setZoom}
                />
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
                      <Space direction="vertical" className="question-review-stack" style={{ width: "100%" }}>
                        <QuestionEditor questionId={q.question_id} markdown={markdown} validation={q.validation} onChange={(v) => { setMarkdown(v); setDirty(true); }} onJumpLine={jumpLine} readOnly={isPublished} />
                        {!isPublished && <Card size="small" title="第二阶段：题目画像审核">
                          <Space direction="vertical" style={{ width: "100%" }}>
                            <Space wrap>
                              <Button onClick={() => void confirmContent()} disabled={dirty || q.content_confirmed}>确认题目内容</Button>
                              {q.content_confirmed && <Button onClick={() => void classify()} loading={recommending} disabled={dirty || recommending}>重新推荐</Button>}
                              {q.content_confirmed && <Tag color="success">题目内容已确认</Tag>}
                            </Space>
                            {recommending && <Typography.Text type="secondary">知识点推荐中...</Typography.Text>}
                            {recommendationError && <Typography.Text type="danger">知识点推荐失败，可点击“重新推荐”；也可以直接手动选择知识点。</Typography.Text>}
                            {classification && <Select mode="multiple" maxCount={3} style={{ width: "100%" }} value={selectedKnowledge} options={(classification.options ?? []).map(x => ({ value: x.knowledge_id, label: x.name }))} onChange={(v) => saveMetadata(v, difficulty)} placeholder="可跨章节选择，最多3个" />}
                            <Space><Typography.Text>难度</Typography.Text><InputNumber min={1} max={5} value={difficulty} onChange={(v) => saveMetadata(selectedKnowledge, v)} placeholder="人工填写1-5" /></Space>
                            {classification && <Typography.Text type="secondary">本地规则推荐仅供人工审核，最终需选择1～3个知识点。</Typography.Text>}
                          </Space>
                        </Card>}
                      </Space>
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
                    <Button icon={<SaveOutlined />} onClick={save} disabled={!dirty || isPublished}>
                      保存
                    </Button>
                    {isPublished && <Button onClick={() => void createRevision()}>创建修订版本</Button>}
                    {!isPublished && <Button onClick={() => void submitCurrent()} disabled={q.review_status === "reviewed" || !q.content_confirmed || selectedKnowledge.length < 1 || selectedKnowledge.length > 3 || !difficulty}>
                      提交当前题审核
                    </Button>}
                    <Button icon={<ArrowRightOutlined />} onClick={saveAndNext} disabled={isPublished}>
                      保存并下一题
                    </Button>
                    <Button type="primary" icon={<SendOutlined />} onClick={publish}
                      disabled={q.review_status !== "reviewed"}>
                      发布
                    </Button>
                    <Button type="primary" icon={<SendOutlined />} onClick={publishAndNext}
                      disabled={q.review_status !== "reviewed"}>
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
