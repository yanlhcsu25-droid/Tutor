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

export default function PdfImportPanel({ open, onReady, onSelectExisting }: Props) {
  const [sources, setSources] = useState<WbSource[]>([]);
  const [uploading, setUploading] = useState(false);
  const [deletingId, setDeletingId] = useState("");
  const [loadError, setLoadError] = useState("");
  const [solutionMode, setSolutionMode] = useState<"inline" | "separate">("inline");
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

  useEffect(() => { if (open) void refresh(); }, [open, refresh]);

  useEffect(() => {
    if (!open) return;
    const active = sources.find((source) =>
      source.processing_status === "queued" || source.processing_status === "processing",
    );
    if (!active) return;
    const progress = active.progress ?? {};
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
    try {
      const result: WbUploadResult = await wb.uploadPdf(file, {
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
          if (result.file_cleanup_warnings.length) message.warning(result.file_cleanup_warnings.join("；"));
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
        {uploading && <Progress percent={99} status="active" size="small" />}
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
            const processing = source.processing_status === "queued" || source.processing_status === "processing";
            const progress = source.progress ?? {};
            const currentPage = progress.current_page ?? 0;
            const totalPages = progress.total_pages || source.page_count || 0;
            const canDelete = source.can_delete === true;
            return (
              <List.Item className="pdf-source-item" actions={[
                <Button key="review" type="primary" ghost disabled={processing} onClick={() => onSelectExisting(source.source_file_id)}>
                  {review.status === "completed" ? "查看审核" : "进入审核"}
                </Button>,
                <Tooltip key="delete" title={source.can_delete ? "删除导入记录" : "已有题目发布到正式题库，不能删除"}>
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
                      {processing && <Typography.Text type="warning">{progress.status === "matching" ? "正在匹配题目与答案" : `正在识别第 ${currentPage} / ${totalPages || "?"} 页`}</Typography.Text>}
                      <Progress percent={processing && totalPages ? Math.round(currentPage / totalPages * 100) : percent} size="small" showInfo={false} status={processing ? "active" : undefined} />
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
