import { useState } from "react";
import { Modal, Button, Input, Typography, Alert, Tree, message, Spin } from "antd";
import { ImportOutlined } from "@ant-design/icons";

const API = "/api/v1";

interface PreviewError {
  line: number;
  code: string;
  message: string;
}
interface PreviewTree {
  id: string;
  code: string | null;
  title: string;
  type: string;
  parent_id: string | null;
  children: PreviewTree[] | null;
}
interface PreviewResult {
  valid: boolean;
  errors: PreviewError[];
  statistics: { chapters: number; sections: number; knowledge_points: number };
  tree: PreviewTree[] | null;
}
interface Props {
  open: boolean;
  bookId: string | null;
  onClose: () => void;
  onImported: () => void;
}

type Status = "idle" | "previewing" | "preview_ready" | "confirming" | "error";

export default function DirectoryReimportModal({ open, bookId, onClose, onImported }: Props) {
  const [text, setText] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [errorMsg, setErrorMsg] = useState("");

  const reset = () => {
    setText("");
    setPreview(null);
    setStatus("idle");
    setErrorMsg("");
  };

  const handlePreview = async () => {
    if (!bookId || !text.trim()) return;
    setStatus("previewing");
    setErrorMsg("");
    try {
      const r = await fetch(`${API}/textbooks/${bookId}/import/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data: PreviewResult = await r.json();
      if (!r.ok) {
        setErrorMsg((data as any)?.detail || "预览失败");
        setStatus("error");
        return;
      }
      setPreview(data);
      setStatus("preview_ready");
    } catch {
      setErrorMsg("预览请求失败");
      setStatus("error");
    }
  };

  const handleConfirm = async () => {
    if (!bookId || !preview || !preview.valid || status === "confirming") return;
    setStatus("confirming");
    setErrorMsg("");
    try {
      const r = await fetch(`${API}/textbooks/${bookId}/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, replace: true, strict: true }),
      });
      if (!r.ok) {
        const data = await r.json().catch(() => ({} as any));
        const detail = data?.detail;
        setErrorMsg(
          detail && Array.isArray(detail.errors)
            ? detail.errors.map((e: PreviewError) => `第 ${e.line} 行：${e.message}`).join("；")
            : detail || "导入失败",
        );
        setStatus("error");
        return;
      }
      message.success("目录已重新导入");
      reset();
      onImported();
      onClose();
    } catch {
      setErrorMsg("确认导入失败");
      setStatus("error");
    }
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const treeData = (preview?.tree || []).map(toTreeData);

  return (
    <Modal
      open={open}
      onCancel={handleClose}
      width={680}
      title="重新导入目录"
      okText="确认重新导入"
      okButtonProps={{ disabled: status !== "preview_ready" || !preview?.valid }}
      confirmLoading={status === "confirming"}
      onOk={handleConfirm}
    >
      <Typography.Paragraph type="secondary">
        粘贴完整教材目录文本，生成预览并确认后，将<strong>替换</strong>当前教材目录。替换前请人工核对层级。
      </Typography.Paragraph>

      <Input.TextArea
        rows={10}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (status !== "idle") {
            setStatus("idle");
            setPreview(null);
          }
        }}
        placeholder={"第三章 微分中值定理与导数的应用\n3.1 微分中值定理\n罗尔定理\n拉格朗日中值定理\n..."}
      />

      <Button
        icon={<ImportOutlined />}
        style={{ margin: "12px 0" }}
        onClick={handlePreview}
        loading={status === "previewing"}
        disabled={!text.trim() || !bookId}
      >
        生成预览
      </Button>

      {status === "previewing" && <Spin style={{ marginLeft: 12 }} />}

      {status === "error" && <Alert type="error" showIcon message={errorMsg} style={{ marginBottom: 12 }} />}

      {preview && !preview.valid && (
        <Alert
          type="error"
          showIcon
          message="目录校验未通过"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {preview.errors.map((e, i) => (
                <li key={i}>
                  第 {e.line} 行：{e.message}
                </li>
              ))}
            </ul>
          }
        />
      )}

      {preview && preview.valid && (
        <>
          <Typography.Paragraph style={{ marginBottom: 8 }}>
            章节：<strong>{preview.statistics.chapters}</strong> ｜ 小节：
            <strong>{preview.statistics.sections}</strong> ｜ 知识点：
            <strong>{preview.statistics.knowledge_points}</strong>
          </Typography.Paragraph>
          <Alert type="warning" showIcon message="确认后将用当前预览目录替换该教材现有目录。" style={{ marginBottom: 12 }} />
          <div style={{ maxHeight: "40vh", overflow: "auto", border: "1px solid #f0f0f0", borderRadius: 8, padding: 12 }}>
            <Tree
              treeData={treeData}
              defaultExpandAll
              blockNode
              titleRender={(node: any) => (
                <span>
                  {node.code && <Typography.Text type="secondary" style={{ marginRight: 6 }}>{node.code}</Typography.Text>}
                  <Typography.Text type={node.type === "knowledge_point" ? "secondary" : "secondary"}>{node.title}</Typography.Text>
                </span>
              )}
            />
          </div>
        </>
      )}
    </Modal>
  );
}

function toTreeData(node: PreviewTree): any {
  return {
    key: node.id,
    title: node.title,
    code: node.code,
    type: node.type,
    children: (node.children || []).map(toTreeData),
  };
}
