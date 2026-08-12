import { useState, useEffect } from "react";
import { Table, Tag, Typography, Empty } from "antd";
import type { WbQuestion, WbSource } from "../api";
import PreviewPane from "../components/PreviewPane";
import "../components/QuestionBankDrawer.css";

// 题库页面 — 展示正式发布的题目；仅发布后才可用于组卷。

export default function QuestionBankPage() {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<Array<WbQuestion & { sourceName?: string }>>([]);

  useEffect(() => {
    // 获取所有来源，再汇总所有题目
    fetch("/workbench/api/sources")
      .then((r) => r.json())
      .then(async (result: { items: WbSource[] }) => {
        const allQuestions: Array<WbQuestion & { sourceName?: string }> = [];
        for (const src of result.items) {
          try {
            const qs = await fetch(`/workbench/api/sources/${src.source_file_id}/questions`).then((r) => r.json());
            for (const q of qs.items) {
              allQuestions.push({ ...q, sourceName: src.original_name });
            }
          } catch { /* skip failed */ }
        }
        setData(allQuestions.filter((q) => q.review_status === "published"));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

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
      {data.length === 0 && !loading ? (
        <Empty description="暂无题目，请先通过 PDF 导入添加题目" />
      ) : (
        <Table dataSource={data} columns={columns} rowKey="question_id" loading={loading} size="small" />
      )}
    </div>
  );
}
