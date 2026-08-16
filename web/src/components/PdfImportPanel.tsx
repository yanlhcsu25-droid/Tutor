import { useCallback, useEffect, useState } from "react";
import {
  Alert, Button, Card, Empty, InputNumber, List, Modal, Progress, Radio, Select, Space,
  Tag, Tooltip, Typography, Upload, message,
} from "antd";
import { DeleteOutlined, FilePdfOutlined, PlusOutlined } from "@ant-design/icons";
import { wb } from "../api";
import type { WbSource, WbUploadResult } from "../api";
import "./PdfImportPanel.css";

interface Props {
  open: boolean;
  onReady: (sourceId: string, questionCount: number) => void;
  onSelectExisting: (sourceId: string) => void;
}

const REVIEW_COPY = {
  pending: { text: "待审核", color: "default" },
  in_progress: { text: "审核中", color: "processing" },
  completed: { text: "已审核", color: "success" },
} as const;

type OcrProgress = NonNullable<WbSource["progress"]>;

function ratio(progress: OcrProgress): number {
  const current = progress.current_page ?? 0;
  const total = progress.total_pages ?? 0;
  return total > 0 ? Math.min(1, Math.max(0, current / total)) : 0;
}

function ocrProgressView(progress: OcrProgress): { percent: number; label: string } {
  const stageRatio = ratio(progress);
  const current = progress.current_page ?? 0;
  const total = progress.total_pages ?? 0;
  switch (progress.status) {
    case "mineru_layout":
      return { percent: Math.round(2 + 8 * stageRatio), label: `版面分析 ${current}/${total} · ${Math.round(stageRatio * 100)}%` };
    case "mineru_predict":
      return { percent: Math.round(10 + 80 * stageRatio), label: `内容识别 ${current}/${total} · ${Math.round(stageRatio * 100)}%` };
    case "mineru_ocr":
      return { percent: Math.round(90 + 7 * stageRatio), label: `文字检测 ${current}/${total} · ${Math.round(stageRatio * 100)}%` };
    case "mineru_pages":
      return { percent: Math.round(97 + 2 * stageRatio), label: `正在生成结果 ${Math.round(stageRatio * 100)}%` };
    case "matching":
      return { percent: 99, label: "正在切分并匹配题目与答案" };
    case "ocr":
    case "ocr_page_complete":
      return { percent: Math.round(stageRatio * 100), label: `OCR 识别进度 ${Math.round(stageRatio * 100)}%` };
    case "queued":
      return { percent: 0, label: "OCR 任务排队中" };
    default:
      return { percent: 1, label: "MinerU 正在初始化" };
  }
}

export default function PdfImportPanel({ open, onReady, onSelectExisting }: Props) {
  const [sources, setSources] = useState<WbSource[]>([]);
  const [uploading, setUploading] = useState(false);
  const [activeSourceId, setActiveSourceId] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const [loadError, setLoadError] = useState("");
  const [solutionMode, setSolutionMode] = useState<"inline" | "separate">("inline");
  const [ocrMode, setOcrMode] = useState<"mineru" | "ppstructure">("mineru");
  const [ranges, setRanges] = useState({ questionStart: 1, questionEnd: 1, solutionStart: 2, solutionEnd: 2 });
  const [preview, setPreview] = useState<{ sourceId: string; page: number; total: number; markdown: string } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await wb.listSources();
      setSources(data.items);
      setLoadError("");
    } catch {
      setLoadError("加载历史记录失败");
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void refresh();
    const refreshVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const timer = window.setInterval(refreshVisible, 2500);
    window.addEventListener("focus", refreshVisible);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshVisible);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [open, refresh]);

  useEffect(() => {
    if (!open || !uploading) return;
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 1000);
    return () => window.clearInterval(timer);
  }, [open, uploading, refresh]);

  useEffect(() => {
    if (!open) return;
    const active = sources.find((source) =>
      source.processing_status === "queued" || source.processing_status === "processing",
    );
    if (!active) return;
    const progress = active.progress ?? {};
    if (progress.status?.startsWith("mineru")) return;
    const page = progress.current_page ?? 0;
    if (page < 1) return;
    let cancelled = false;
    const loadPreview = async () => {
      try {
        const result = await wb.getPageMarkdown(active.source_file_id, page);
        if (!cancelled) setPreview({ sourceId: active.source_file_id, page, total: progress.total_pages ?? active.page_count, markdown: result.edited_markdown });
      } catch { /* 当前页尚未写入数据库，下一轮继续尝试 */ }
    };
    void loadPreview();
    const timer = window.setInterval(() => { void refresh(); void loadPreview(); }, 1800);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [open, sources, refresh]);

  const handleUpload = async (file: File) => {
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      message.error("仅支持 PDF 文件");
      return false;
    }
    if (file.size > 200 * 1024 * 1024) {
      message.error("PDF 不能超过 200MB");
      return false;
    }
    if (solutionMode === "separate" && (
      ranges.questionStart > ranges.questionEnd || ranges.solutionStart > ranges.solutionEnd
    )) {
      message.error("页码范围起始页不能大于结束页");
      return false;
    }
    setUploading(true);
    const sourceFileId = `src_${crypto.randomUUID().replaceAll("-", "")}`;
    setActiveSourceId(sourceFileId);
    try {
      const result: WbUploadResult = await wb.uploadPdf(file, {
        sourceFileId,
        ocrMode,
        solutionMode,
        questionPageStart: ranges.questionStart,
        questionPageEnd: ranges.questionEnd,
        solutionPageStart: ranges.solutionStart,
        solutionPageEnd: ranges.solutionEnd,
      });
      message.success(result.deduplicated ? "已找到相同的历史 PDF" : `OCR 完成，识别 ${result.question_count} 道题`);
      await refresh();
      if (result.question_count > 0) onReady(result.source.source_file_id, result.question_count);
    } catch (error: unknown) {
      message.error(String(error));
    } finally {
      setUploading(false);
      setActiveSourceId("");
      await refresh();
    }
    return false;
  };

  const confirmDelete = (source: WbSource) => {
    Modal.confirm({
      title: `删除“${source.original_name}”？`,
      content: (
        <div>
          <p>将删除 PDF 导入记录、OCR Markdown、未发布题目及相关缓存。删除后可以重新上传同一 PDF。</p>
          {source.has_manual_edits && <Alert type="warning" showIcon message="检测到人工修改内容，删除后无法恢复。" />}
        </div>
      ),
      okText: "确认删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        setDeletingId(source.source_file_id);
        try {
          const result = await wb.deleteSource(source.source_file_id);
          if (result.status === "deleting") message.success("已请求停止 OCR，当前页结束后自动删除");
          else if (result.file_cleanup_warnings?.length) message.warning(result.file_cleanup_warnings.join("；"));
          else message.success("PDF 导入记录已删除");
          await refresh();
        } catch (error: unknown) {
          message.error(String(error));
        } finally {
          setDeletingId("");
        }
      },
    });
  };

  return (
    <div className="pdf-import-panel">
      <div className="pdf-import-heading">
        <Typography.Title level={4}>上传教辅 PDF</Typography.Title>
        <Typography.Text type="secondary">选择导入方式后上传，系统会自动 OCR 并切分题目。</Typography.Text>
      </div>

      <div className="pdf-upload-compact">
        <Radio.Group value={solutionMode} onChange={(event) => setSolutionMode(event.target.value)}>
          <Radio.Button value="inline">普通习题</Radio.Button>
          <Radio.Button value="separate">套卷</Radio.Button>
        </Radio.Group>
        <Space size="small">
          <Typography.Text>识别引擎</Typography.Text>
          <Select
            value={ocrMode}
            onChange={setOcrMode}
            style={{ width: 220 }}
            options={[
              { value: "mineru", label: "MinerU（推荐）" },
              { value: "ppstructure", label: "Paddle PPStructure（兼容）" },
            ]}
          />
        </Space>
        {solutionMode === "separate" && (
          <Space wrap size="small">
            <Typography.Text>题目页</Typography.Text>
            <InputNumber min={1} value={ranges.questionStart} onChange={(value) => setRanges({ ...ranges, questionStart: value ?? 1 })} />
            <Typography.Text>至</Typography.Text>
            <InputNumber min={1} value={ranges.questionEnd} onChange={(value) => setRanges({ ...ranges, questionEnd: value ?? 1 })} />
            <Typography.Text>答案页</Typography.Text>
            <InputNumber min={1} value={ranges.solutionStart} onChange={(value) => setRanges({ ...ranges, solutionStart: value ?? 1 })} />
            <Typography.Text>至</Typography.Text>
            <InputNumber min={1} value={ranges.solutionEnd} onChange={(value) => setRanges({ ...ranges, solutionEnd: value ?? 1 })} />
          </Space>
        )}
        <Space>
          <Upload accept=".pdf,application/pdf" showUploadList={false} beforeUpload={handleUpload} disabled={uploading}>
            <Button type="primary" icon={<PlusOutlined />} loading={uploading}>选择 PDF 文件</Button>
          </Upload>
          <Typography.Text type="secondary">支持 PDF · 最大 200MB</Typography.Text>
        </Space>
        {uploading && (() => {
          const active = sources.find((source) => source.source_file_id === activeSourceId);
          const progress = active?.progress ?? {};
          const view = ocrProgressView(progress);
          return (
            <div className="pdf-upload-progress">
              <Progress percent={view.percent} status="active" size="small" />
              <Typography.Text type="secondary">
                {active ? view.label : "正在上传并创建 OCR 任务…"}
              </Typography.Text>
            </div>
          );
        })()}
      </div>

      <div className="pdf-source-header">
        <Typography.Title level={5}>已上传 PDF</Typography.Title>
        <Typography.Text type="secondary">{sources.length} 份资料</Typography.Text>
      </div>
      <div className="pdf-source-list">
        {sources.length ? (
          <List dataSource={sources} renderItem={(source) => {
            const questionCount = source.question_count ?? 0;
            const review = source.review ?? { status: "pending", completed: source.reviewed_count ?? 0, total: questionCount };
            const copy = REVIEW_COPY[review.status];
            const percent = review.total ? Math.round(review.completed / review.total * 100) : 0;
            const processing = ["queued", "processing", "pausing", "paused", "deleting"].includes(source.processing_status);
            const progress = source.progress ?? {};
            const progressView = ocrProgressView(progress);
            const canDelete = source.can_delete === true && source.processing_status !== "deleting";
            return (
              <List.Item className="pdf-source-item" actions={[
                <Button key="review" type="primary" ghost disabled={processing} onClick={() => onSelectExisting(source.source_file_id)}>
                  {review.status === "completed" ? "查看审核" : "进入审核"}
                </Button>,
                <Tooltip key="delete" title={source.processing_status === "deleting" ? "正在停止 OCR 并删除" : (source.can_delete ? (processing ? "停止 OCR 并删除" : "删除导入记录") : "已有题目发布到正式题库，不能删除")}>
                  <span>
                    <Button danger icon={<DeleteOutlined />} disabled={!canDelete}
                      loading={deletingId === source.source_file_id} onClick={() => confirmDelete(source)}>
                      删除
                    </Button>
                  </span>
                </Tooltip>,
              ]}>
                <List.Item.Meta
                  avatar={<FilePdfOutlined className="pdf-source-icon" />}
                  title={<Space wrap><Typography.Text strong>{source.original_name}</Typography.Text><Tag color={copy.color}>{copy.text}</Tag></Space>}
                  description={(
                    <div className="pdf-source-detail">
                      <Typography.Text type="secondary">{source.page_count} 页 · {questionCount} 题 · {review.completed}/{review.total}</Typography.Text>
                      {processing && <Typography.Text type="warning">{source.processing_status === "deleting" ? "正在停止 OCR 并删除" : progressView.label}</Typography.Text>}
                      {source.processing_status === "failed" && source.processing_error && (
                        <Typography.Text type="danger">处理失败：{source.processing_error}</Typography.Text>
                      )}
                      <Progress percent={processing ? progressView.percent : percent} size="small" showInfo={processing} status={processing ? "active" : undefined} />
                      {(source.published_count ?? 0) > 0 && <Typography.Text type="secondary">已有 {source.published_count} 题发布到正式题库</Typography.Text>}
                    </div>
                  )}
                />
              </List.Item>
            );
          }} />
        ) : <Empty description="尚未上传 PDF" />}
      </div>
      {preview && (
        <Card
          size="small"
          title={`实时整页 OCR · 第 ${preview.page} / ${preview.total} 页`}
          extra={<Select size="small" value={preview.page} onChange={async (page) => {
            try {
              const result = await wb.getPageMarkdown(preview.sourceId, page);
              setPreview({ ...preview, page, markdown: result.edited_markdown });
            } catch { message.warning("该页尚未完成 OCR"); }
          }} options={Array.from({ length: preview.total }, (_, index) => ({ value: index + 1, label: `第 ${index + 1} 页` }))} />}
          style={{ marginTop: 16 }}
        >
          <Typography.Text type="secondary">页面完成后自动更新；这里展示的是整页 OCR 原文，题目切分仍在后台继续。</Typography.Text>
          <pre style={{ maxHeight: 360, overflow: "auto", whiteSpace: "pre-wrap", marginTop: 12 }}>{preview.markdown || "正在等待 OCR 文本..."}</pre>
        </Card>
      )}
      {loadError && <Alert message={loadError} type="warning" />}
    </div>
  );
}
