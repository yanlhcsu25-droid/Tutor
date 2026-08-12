import { Button, Space, Tag, Typography } from "antd";
import { LeftOutlined, RightOutlined } from "@ant-design/icons";

const STATUS_MAP: Record<string, { color: string; label: string }> = {
  pending: { color: "default", label: "待校验" },
  in_review: { color: "processing", label: "校验中" },
  reviewed: { color: "success", label: "已审核" },
  published: { color: "blue", label: "已发布" },
};

interface Props {
  currentIndex: number;
  total: number;
  status: string;
  originalNumber: string;
  pageNumber: number;
  onPrev: () => void;
  onNext: () => void;
}

export default function QuestionNav({ currentIndex, total, status, originalNumber, pageNumber, onPrev, onNext }: Props) {
  const info = STATUS_MAP[status] ?? { color: "default", label: status };
  return (
    <Space>
      <Tag color={info.color}>{info.label}</Tag>
      <Typography.Text>
        第 {originalNumber} 题 · PDF 第 {pageNumber} 页
      </Typography.Text>
      <span style={{ flex: 1 }} />
      <Button icon={<LeftOutlined />} size="small" disabled={currentIndex <= 0} onClick={onPrev}>
        上一题
      </Button>
      <Typography.Text type="secondary">
        {currentIndex + 1} / {total}
      </Typography.Text>
      <Button icon={<RightOutlined />} size="small" disabled={currentIndex >= total - 1} onClick={onNext}>
        下一题
      </Button>
    </Space>
  );
}
