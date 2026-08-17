import { useState } from "react";
import { Drawer, Button, Space, Tag, Typography, InputNumber, message, Row, Col, Statistic, Modal, List } from "antd";
import {
  LockOutlined, UnlockOutlined, ReloadOutlined, ArrowUpOutlined,
  ArrowDownOutlined, FilePdfOutlined, SafetyCertificateOutlined,
  UndoOutlined, RedoOutlined, HistoryOutlined,
} from "@ant-design/icons";

type Preview = {
  title: string; total_score: number; feasible: boolean; warnings: string[];
  items: { item_id: string; question_id: string; question_text: string; question_type: string; score: number; knowledge: string[]; locked: boolean; source_name?: string | null; source_page?: number | null; review_status?: string }[];
};
type Violation = { code: string; field: string; required: unknown; actual: unknown; question_ids: string[]; repairable: boolean; message: string };
type ValidationReport = { passed: boolean; violations: Violation[] };
type HistoryOperation = { operation_id: string; source_paper_id: string; result_paper_id: string; operation_type: string; operations: Record<string, unknown>[]; created_at: string };

const API = "/api/v1";

interface Props {
  open: boolean;
  paperId: string | null;
  preview: Preview | null;
  validation: ValidationReport | null;
  version: number | null;
  onClose: () => void;
  onRefresh: (paperId: string) => void;
}

export default function PaperDrawer({ open, paperId, preview, validation, version, onClose, onRefresh }: Props) {
  const [loading, setLoading] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<HistoryOperation[]>([]);

  if (!preview) return null;

  const mutate = async (path: string, body?: unknown, method = "POST") => {
    if (!paperId) return;
    setLoading(true);
    try {
      const r = await fetch(`${API}/papers/${paperId}${path}`, {
        method, headers: body !== undefined ? { "Content-Type": "application/json" } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      if (!r.ok) throw new Error(JSON.stringify((await r.json()).detail));
      const saved = await r.json();
      onRefresh(saved.paper_id);
    } catch (e) { message.error(String(e)); }
    finally { setLoading(false); }
  };

  const sectionCounters: Record<string, number> = {};
  let currentSection = "";
  let sectionNumber = 0;
  const numberedItems = preview.items.map((item) => {
    if (item.question_type !== currentSection) {
      currentSection = item.question_type;
      sectionNumber += 1;
    }
    const sectionOrder = (sectionCounters[item.question_type] ?? 0) + 1;
    sectionCounters[item.question_type] = sectionOrder;
    return { ...item, sectionOrder, sectionNumber };
  });
  const chineseSectionNumber = [
    "零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
  ];

  const download = async (ver: "student" | "teacher") => {
    try {
      const r = await fetch(`${API}/papers/${paperId}/exports/${ver}.pdf`);
      if (!r.ok) throw new Error("导出失败");
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = ver === "student" ? "试卷.pdf" : "题目与答案解析.pdf";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) { message.error(String(e)); }
  };

  const openHistory = async () => {
    if (!paperId) return;
    try {
      const r = await fetch(`${API}/papers/${paperId}/history`);
      if (!r.ok) throw new Error("版本历史加载失败");
      setHistory(await r.json());
      setHistoryOpen(true);
    } catch (e) { message.error(String(e)); }
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width="60%"
      title={preview.title}
      extra={
        <Space>
          {version && <Tag color="blue">版本 {version}</Tag>}
          <Button
            size="small"
            icon={<UndoOutlined />}
            disabled={!version || version <= 1 || loading}
            onClick={() => mutate("/undo", { count: 1 })}
          >撤销</Button>
          <Button
            size="small"
            icon={<RedoOutlined />}
            disabled={loading}
            onClick={() => mutate("/redo")}
          >重做</Button>
        </Space>
      }
    >
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Statistic title="题目" value={preview.items.length} /></Col>
        <Col span={6}><Statistic title="总分" value={preview.total_score} /></Col>
        <Col span={12}>
          {validation?.passed ? (
            <Tag icon={<SafetyCertificateOutlined />} color="success">全部硬约束通过</Tag>
          ) : (
            validation?.violations.map((v) => (
              <Tag color="error" key={`${v.code}-${v.field}`}>{v.message}</Tag>
            ))
          )}
        </Col>
      </Row>

      {numberedItems.map((item, i) => (
        <div key={item.item_id}>
          {(i === 0 || numberedItems[i - 1]?.question_type !== item.question_type) && (
            <Typography.Title level={5} style={{ marginTop: 18, marginBottom: 10 }}>
              {chineseSectionNumber[item.sectionNumber] ?? item.sectionNumber}、{item.question_type}
            </Typography.Title>
          )}
          <div style={{ marginBottom: 16, padding: 12, background: item.locked ? "#fffbe6" : "#fafafa", borderRadius: 8, border: "1px solid #f0f0f0" }}>
          <Typography.Text strong>{item.sectionOrder}. {item.question_text}</Typography.Text>
          <div style={{ marginTop: 8 }}>
            <Space wrap>
              <Tag>{item.question_type}</Tag>
              <Tag color="blue">来源：{item.source_name ?? "未知"}{item.source_page ? ` · 第${item.source_page}页` : ""}</Tag>
              <Tag color={item.review_status === "approved" ? "green" : "orange"}>
                审核：{item.review_status === "approved" ? "已发布" : item.review_status ?? "未知"}
              </Tag>
              <InputNumber size="small" min={1} value={item.score} onChange={(v) => v && mutate(`/items/${item.item_id}`, { score: v }, "PATCH")} addonAfter="分" />
              <Button size="small" icon={<ArrowUpOutlined />} disabled={i === 0 || numberedItems[i - 1]?.question_type !== item.question_type} onClick={() => {
                const order = numberedItems.map((q) => q.item_id);
                [order[i], order[i - 1]] = [order[i - 1], order[i]];
                mutate("/items/reorder", { item_ids: order });
              }} />
              <Button size="small" icon={<ArrowDownOutlined />} disabled={i === numberedItems.length - 1 || numberedItems[i + 1]?.question_type !== item.question_type} onClick={() => {
                const order = numberedItems.map((q) => q.item_id);
                [order[i], order[i + 1]] = [order[i + 1], order[i]];
                mutate("/items/reorder", { item_ids: order });
              }} />
              <Button size="small" type={item.locked ? "primary" : "default"}
                icon={item.locked ? <UnlockOutlined /> : <LockOutlined />}
                onClick={() => mutate(`/items/${item.item_id}/lock`, { locked: !item.locked })}>
                {item.locked ? "取消锁定" : "锁定"}
              </Button>
              <Button size="small" icon={<ReloadOutlined />} disabled={item.locked || loading}
                onClick={() => mutate(`/items/${item.item_id}/replace`)}>
                换一题
              </Button>
            </Space>
          </div>
          </div>
        </div>
      ))}

      <Space style={{ marginTop: 16 }}>
        <Button icon={<HistoryOutlined />} onClick={() => void openHistory()}>版本历史</Button>
        <Button icon={<FilePdfOutlined />} onClick={() => download("student")}>试卷 PDF</Button>
        <Button type="primary" ghost icon={<FilePdfOutlined />} onClick={() => download("teacher")}>题目与答案解析 PDF</Button>
      </Space>
      <Modal title="试卷版本历史" open={historyOpen} footer={null} onCancel={() => setHistoryOpen(false)}>
        <List
          locale={{ emptyText: "当前还没有修改记录" }}
          dataSource={history}
          renderItem={(entry, index) => (
            <List.Item actions={[
              <Button key="restore" size="small" onClick={async () => {
                await mutate("/restore", { version_id: entry.result_paper_id });
                setHistoryOpen(false);
              }}>恢复此版本</Button>,
            ]}>
              <List.Item.Meta
                title={`版本 ${index + 2} · ${entry.operation_type}`}
                description={new Date(entry.created_at).toLocaleString()}
              />
            </List.Item>
          )}
        />
      </Modal>
    </Drawer>
  );
}
