import { Empty, Typography } from "antd";
import { BookOutlined } from "@ant-design/icons";

export default function TextbookPage() {
  return (
    <div style={{ textAlign: "center", padding: 60 }}>
      <BookOutlined style={{ fontSize: 48, color: "#d9d9d9" }} />
      <Typography.Title level={4} type="secondary" style={{ marginTop: 16 }}>
        教材目录管理
      </Typography.Title>
      <Empty description="教材目录功能即将上线，届时可在此管理章节目录并与题目关联。" />
    </div>
  );
}
