import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Empty,
  Input,
  List,
  Modal,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import { ReloadOutlined, SaveOutlined, UndoOutlined } from "@ant-design/icons";
import { wb } from "../api";
import type { WbPageMarkdown, WbResplitPlan } from "../api";
import PreviewPane from "./PreviewPane";

interface Props {
  sourceId: string;
  page: number;
  /** apply 成功后通知外层刷新题目列表 */
  onRebuilt: () => void | Promise<void>;
}

const STATUS_COLOR: Record<string, string> = {
  pending: "default",
  in_review: "blue",
  reviewed: "green",
  published: "gold",
};

/**
 * 整页 Markdown 编辑 + 「重新切题」。
 *
 * OCR 把题号识别坏时（例如 `3.` 变成异常字符、`4.` 变成 `河4.`），
 * splitter 认不出新题号，整页会被并进上一页的题里。用户在这里把题号改回来，
 * 再重新跑一次切题即可恢复。跨页影响范围由后端自动计算，用户无需理解 pending。
 */
export default function PageMarkdownPanel({ sourceId, page, onRebuilt }: Props) {
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [data, setData] = useState<WbPageMarkdown | null>(null);
  const [markdown, setMarkdown] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<WbResplitPlan | null>(null);

  const dirty = data !== null && markdown !== data.edited_markdown;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await wb.getPageMarkdown(sourceId, page);
      setData(result);
      setMarkdown(result.edited_markdown);
    } catch (e: unknown) {
      setData(null);
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, [sourceId, page]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    setBusy(true);
    try {
      const result = await wb.savePageMarkdown(sourceId, page, markdown);
      setData(result);
      message.success("整页 Markdown 已保存；如需更新右侧单题，请继续预览并确认重新切题");
    } catch (e: unknown) {
      message.error(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  const openPreview = async () => {
    setBusy(true);
    try {
      setPlan(await wb.resplitPreview(sourceId, page, markdown));
    } catch (e: unknown) {
      message.error(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!plan) return;
    setBusy(true);
    try {
      const result = await wb.resplitApply(sourceId, page, markdown, plan.new_numbers);
      setPlan(null);
      message.success(
        `重新切题完成：新建 ${result.created_count} 题，删除 ${result.deleted_count} 题，保留 ${result.kept_count} 题`,
      );
      await load();
      await onRebuilt();
    } catch (e: unknown) {
      message.error(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <Spin style={{ margin: 24 }} />;

  if (error || !data) {
    return (
      <Alert
        type="warning"
        showIcon
        message="该页没有整页 Markdown"
        description={error ?? "此 PDF 可能在本功能上线前导入，且原始 OCR 文件已被清理。重新导入该 PDF 后即可使用。"}
      />
    );
  }

  return (
    <div className="ocr-page-markdown-panel">
      <Space wrap>
        <Typography.Text strong>第 {data.page_number} 页整页 Markdown</Typography.Text>
        {data.modified && <Tag color="blue">已人工修改</Tag>}
        {dirty && <Tag color="orange">未保存</Tag>}
      </Space>

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        题号被 OCR 识别错时（如 <code>河4.</code>），在这里改回 <code>4.</code> 再点「预览重新切题」。
        “保存修改”只保存整页 Markdown，不会自动覆盖右侧单题；跨页影响范围会自动计算，已审核或已发布的题目不会被静默改动。
      </Typography.Text>

      <Space wrap className="ocr-page-markdown-toolbar">
        <Button icon={<SaveOutlined />} onClick={save} disabled={!dirty} loading={busy}>
          保存修改
        </Button>
        <Button
          icon={<UndoOutlined />}
          onClick={async () => {
            setBusy(true);
            try {
              const result = await wb.restorePageMarkdown(sourceId, page);
              setData(result);
              setMarkdown(result.edited_markdown);
              message.success("已恢复 OCR 原文，未重新运行 OCR");
            } catch (e: unknown) {
              message.error(String(e instanceof Error ? e.message : e));
            } finally { setBusy(false); }
          }}
          disabled={markdown === data.raw_markdown}
        >
          恢复 OCR 原文
        </Button>
        <Button type="primary" icon={<ReloadOutlined />} onClick={openPreview} loading={busy}>
          预览重新切题
        </Button>
      </Space>

      <div className="ocr-markdown-columns">
        <div className="ocr-markdown-editor-column">
          <Typography.Text strong>Markdown 编辑</Typography.Text>
          <Input.TextArea
            value={markdown}
            onChange={(e) => setMarkdown(e.target.value)}
            className="ocr-page-markdown-editor"
            autoSize={{ minRows: 18 }}
            spellCheck={false}
          />
        </div>
        <div className="ocr-markdown-preview-column">
          <Typography.Text strong>实时预览</Typography.Text>
          <div style={{ marginTop: 8, border: "1px solid #d9d9d9", borderRadius: 8, minHeight: 420, background: "#fff" }}>
            <PreviewPane markdown={markdown} />
          </div>
        </div>
      </div>

      <Modal
        open={plan !== null}
        title="重新切题 — 确认变更"
        width={860}
        onCancel={() => setPlan(null)}
        onOk={apply}
        okText="确认重建"
        okButtonProps={{ danger: true, disabled: plan?.blocked, loading: busy }}
        cancelText="取消"
        styles={{ body: { maxHeight: "calc(100vh - 220px)", overflowY: "auto" } }}
      >
        {plan && (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Alert
              type={plan.affected_range.cross_page ? "warning" : "info"}
              showIcon
              message={plan.affected_range.description}
              description={
                plan.affected_range.cross_page
                  ? "由于题目内容跨页延续，必须整段重新切分，否则旧题里会残留其他题的内容。"
                  : undefined
              }
            />

            {plan.blocked && (
              <Alert
                type="error"
                showIcon
                message="存在不能安全保留的题目，无法重建"
                description={plan.blocking_drafts
                  .map((item) => `第${item.page_number}页 第${item.original_number}题`)
                  .join("、")}
              />
            )}

            {plan.manual_edits_lost.length > 0 && (
              <Alert
                type="warning"
                showIcon
                message={`${plan.manual_edits_lost.length} 道题的人工修改会被覆盖`}
                description={plan.manual_edits_lost
                  .map((item) => `第${item.page_number}页 第${item.original_number}题`)
                  .join("、")}
              />
            )}

            <Space size={24} wrap>
              <Typography.Text>
                旧题号：<Typography.Text code>{plan.old_numbers.join("、") || "无"}</Typography.Text>
              </Typography.Text>
              <Typography.Text>
                新题号：<Typography.Text code>{plan.new_numbers.join("、") || "无"}</Typography.Text>
              </Typography.Text>
            </Space>

            <ChangeList title={`新增 ${plan.changes.added.length} 题`} color="green"
              items={plan.changes.added.map((item) => ({
                key: `a-${item.page_number}-${item.original_number}`,
                label: `第${item.page_number}页 · 第${item.original_number}题`,
                preview: item.preview,
              }))}
            />
            <ChangeList title={`删除重建 ${plan.changes.removed.length} 题`} color="red"
              items={plan.changes.removed.map((item) => ({
                key: item.question_id,
                label: `第${item.page_number}页 · 第${item.original_number}题`,
                preview: item.preview,
                tag: item.review_status,
              }))}
            />
            <ChangeList title={`原样保留 ${plan.changes.kept.length} 题`} color="default"
              items={plan.changes.kept.map((item) => ({
                key: item.question_id,
                label: `第${item.page_number}页 · 第${item.original_number}题`,
                preview: "",
                tag: item.review_status,
              }))}
            />
          </Space>
        )}
      </Modal>
    </div>
  );
}

interface ChangeItem {
  key: string;
  label: string;
  preview: string;
  tag?: string;
}

function ChangeList({ title, color, items }: { title: string; color: string; items: ChangeItem[] }) {
  return (
    <div>
      <Tag color={color}>{title}</Tag>
      {items.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无" style={{ margin: "8px 0" }} />
      ) : (
        <List
          size="small"
          bordered
          style={{ marginTop: 8, maxHeight: 200, overflow: "auto" }}
          dataSource={items}
          renderItem={(item) => (
            <List.Item key={item.key}>
              <Space direction="vertical" size={0} style={{ width: "100%" }}>
                <Space size={6}>
                  <Typography.Text strong>{item.label}</Typography.Text>
                  {item.tag && <Tag color={STATUS_COLOR[item.tag] ?? "default"}>{item.tag}</Tag>}
                </Space>
                {item.preview && (
                  <Typography.Text type="secondary" ellipsis style={{ fontSize: 12 }}>
                    {item.preview}
                  </Typography.Text>
                )}
              </Space>
            </List.Item>
          )}
        />
      )}
    </div>
  );
}
