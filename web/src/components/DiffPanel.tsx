import { useEffect, useState } from "react";
import { Spin, Typography } from "antd";
import { wb } from "../api";

interface Props {
  questionId: string;
  markdown: string;
}

export default function DiffPanel({ questionId, markdown }: Props) {
  const [html, setHtml] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    wb.diff(questionId, markdown)
      .then(setHtml)
      .catch(() => setHtml("<p>差异生成失败</p>"))
      .finally(() => setLoading(false));
  }, [questionId, markdown]);

  if (loading) return <Spin tip="生成差异..." />;
  return (
    <div style={{ padding: 8 }}>
      <Typography.Text type="secondary" style={{ marginBottom: 8, display: "block" }}>
        绿色 = 新增，红色 = 删除，黄色 = 修改
      </Typography.Text>
      <div dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}
