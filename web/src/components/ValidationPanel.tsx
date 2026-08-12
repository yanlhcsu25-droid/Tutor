import { Tag, Typography, Button } from "antd";
import { CheckCircleOutlined, WarningOutlined } from "@ant-design/icons";
import type { WbValidation } from "../api";

interface Props {
  validation: WbValidation | null;
  onJumpLine?: (line: number) => void;
}

export default function ValidationPanel({ validation, onJumpLine }: Props) {
  if (!validation) {
    return (
      <div style={{ padding: 12, background: "#fafafa", borderRadius: 8 }}>
        <Typography.Text type="secondary">尚未执行完整校验</Typography.Text>
      </div>
    );
  }

  return (
    <div
      style={{
        padding: 12,
        background: validation.valid ? "#f6ffed" : "#fff2f0",
        border: `1px solid ${validation.valid ? "#b7eb8f" : "#ffccc7"}`,
        borderRadius: 8,
      }}
    >
      <div style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 8 }}>
        {validation.valid ? (
          <CheckCircleOutlined style={{ color: "#52c41a" }} />
        ) : (
          <WarningOutlined style={{ color: "#ff4d4f" }} />
        )}
        <Typography.Text strong>
          {validation.valid ? "格式与字段校验通过" : `发现 ${validation.issues.length} 个问题`}
        </Typography.Text>
      </div>
      {validation.issues.map((issue, i) => (
        <div key={i} style={{ marginBottom: 4 }}>
          {issue.line && onJumpLine ? (
            <Button
              type="link"
              size="small"
              danger
              onClick={() => onJumpLine(issue.line!)}
            >
              <Tag color="error">{issue.field}</Tag> {issue.message}（第{issue.line}行）
            </Button>
          ) : (
            <Typography.Text type="danger">
              <Tag color="error">{issue.field}</Tag> {issue.message}
            </Typography.Text>
          )}
        </div>
      ))}
    </div>
  );
}
