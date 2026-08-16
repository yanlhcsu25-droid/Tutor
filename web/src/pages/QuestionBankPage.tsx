import { useState, useEffect } from "react";
import { Table, Tag, Typography, Empty, Select, Space } from "antd";
import type { WbQuestion, WbSource, WbChapter } from "../api";
import { wb } from "../api";
import PreviewPane from "../components/PreviewPane";
import "../components/QuestionBankDrawer.css";

// 题库页面 — 展示正式发布的题目；仅发布后才可用于组卷。
// 支持按教材一级章节（大章节）筛选，与「已发布」过滤叠加（AND）。

const ALL_CHAPTERS = "all";

export default function QuestionBankPage() {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<Array<WbQuestion & { sourceName?: string }>>([]);
  const [chapters, setChapters] = useState<WbChapter[]>([]);
  const [chapterId, setChapterId] = useState<string | null>(null);

  // 一级章节下拉选项（仅加载一次）
  useEffect(() => {
    wb.listChapters()
      .then((result) => setChapters(result.items))
      .catch(() => setChapters([]));
  }, []);

  useEffect(() => {
    // 获取所有来源，再汇总所有题目；chapterId 变化时重新拉取
    setLoading(true);
    fetch("/workbench/api/sources")
      .then((r) => r.json())
      .then(async (result: { items: WbSource[] }) => {
        const allQuestions: Array<WbQuestion & { sourceName?: string }> = [];
        for (const src of result.items) {
          try {
            const qs = await wb.listQuestions(src.source_file_id, chapterId);
            for (const q of qs.items) {
              allQuestions.push({ ...q, sourceName: src.original_name });
            }
          } catch { /* skip failed */ }
        }
        // 保持现有「仅展示已发布」行为；章节筛选与发布状态为 AND 关系。
        setData(allQuestions.filter((q) => q.review_status === "published"));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [chapterId]);

  const columns = [
    { title: "题号", dataIndex: "original_number", width: 80 },
    {
      title: "题干",
      dataIndex: "edited_markdown",
      render: (md: string) => <div className="question-bank-card-preview"><PreviewPane markdown={md} /></div>,
    },
    { title: "来源", dataIndex: "sourceName", render: (v: string) => <Tag>{v}</Tag> },
    { title: "页码", dataIndex: "page_number", width: 70 },
    {
      title: "状态", dataIndex: "review_status", width: 90,
      render: (s: string) => <Tag color={s === "published" ? "blue" : "green"}>{s === "published" ? "已发布" : "已审核"}</Tag>,
    },
  ];

  return (
    <div>
      <Typography.Title level={4}>题库</Typography.Title>
      <Typography.Paragraph type="secondary">
        只有人工审核并正式发布的题目会出现在这里，并可用于组卷。
      </Typography.Paragraph>
      <Space style={{ marginBottom: 16 }}>
        <span>章节：</span>
        <Select
          style={{ width: 260 }}
          value={chapterId ?? ALL_CHAPTERS}
          onChange={(value: string) => setChapterId(value === ALL_CHAPTERS ? null : value)}
          options={[
            { value: ALL_CHAPTERS, label: "全部章节" },
            ...chapters.map((c) => ({ value: c.id, label: c.name })),
          ]}
        />
      </Space>
      {data.length === 0 && !loading ? (
        <Empty description="暂无题目，请先通过 PDF 导入添加题目" />
      ) : (
        <Table dataSource={data} columns={columns} rowKey="question_id" loading={loading} size="small" />
      )}
    </div>
  );
}
