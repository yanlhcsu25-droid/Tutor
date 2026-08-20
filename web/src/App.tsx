import { useState } from "react";
import { Layout, Menu, Typography, Modal, Button, Space } from "antd";
import {
  RobotOutlined, FilePdfOutlined, BookOutlined,
  DatabaseOutlined, PlusOutlined,
} from "@ant-design/icons";
import AgentWorkspace, { clearStoredConversationId } from "./components/AgentWorkspace";
import OcrReviewDrawer from "./components/OcrReviewDrawer";
import QuestionBankDrawer from "./components/QuestionBankDrawer";
import PdfImportPanel from "./components/PdfImportPanel";
import TextbookDrawer from "./components/TextbookDrawer";
import AdminConsole from "./components/AdminConsole";

const { Sider, Content } = Layout;

export default function App() {
  if (window.location.pathname === "/admin" || window.location.pathname.startsWith("/admin/")) {
    return <AdminConsole />;
  }

  // ── drawers / modals ──
  const [pdfUploadOpen, setPdfUploadOpen] = useState(false);
  const [ocrReviewOpen, setOcrReviewOpen] = useState(false);
  const [ocrSourceId, setOcrSourceId] = useState<string | null>(null);
  const [questionBankOpen, setQuestionBankOpen] = useState(false);
  const [textbookOpen, setTextbookOpen] = useState(false);

  // ── PDF import → OCR review flow ──
  const handlePdfImportReady = (_sourceId: string, _count: number) => {
    setPdfUploadOpen(false);
    setOcrSourceId(_sourceId);
    setOcrReviewOpen(true);
  };
  const handleSelectExistingPdf = (_sourceId: string) => {
    setPdfUploadOpen(false);
    setOcrSourceId(_sourceId);
    setOcrReviewOpen(true);
  };

  // ── "新建组卷" → clear session ──
  const handleNewSession = () => {
    Modal.confirm({
      title: "开始新的组卷任务？",
      content: "当前未保存的组卷状态将被清空。",
      okText: "新建",
      cancelText: "取消",
      onOk: () => {
        clearStoredConversationId();
        window.location.reload();
      },
    });
  };

  return (
    <Layout style={{ minHeight: "100vh" }}>
      {/* sidebar */}
      <Sider width={160} style={{ background: "#fff", borderRight: "1px solid #f0f0f0" }}>
        <div style={{ padding: "16px", borderBottom: "1px solid #f0f0f0" }}>
          <Typography.Title level={5} style={{ margin: 0, fontSize: 15 }}>
            <RobotOutlined /> MathPaper
          </Typography.Title>
        </div>
        <Menu
          mode="inline"
          defaultSelectedKeys={["agent"]}
          style={{ borderRight: 0, marginTop: 4 }}
          items={[
            { key: "new", icon: <PlusOutlined />, label: "新建组卷", onClick: handleNewSession },
            { type: "divider" },
            { key: "pdf-import", icon: <FilePdfOutlined />, label: "PDF 导入", onClick: () => setPdfUploadOpen(true) },
            { key: "question-bank", icon: <DatabaseOutlined />, label: "题库", onClick: () => setQuestionBankOpen(true) },
            { key: "textbook", icon: <BookOutlined />, label: "教材目录", onClick: () => setTextbookOpen(true) },
          ]}
        />
      </Sider>

      {/* main content — always AgentWorkspace */}
      <Layout>
        <Content style={{ background: "#fff", overflow: "auto", height: "100vh" }}>
          <AgentWorkspace />
        </Content>
      </Layout>

      {/* PDF Import Modal */}
      <Modal
        open={pdfUploadOpen}
        onCancel={() => setPdfUploadOpen(false)}
        footer={null}
        width={760}
      >
        <PdfImportPanel
          open={pdfUploadOpen}
          onReady={handlePdfImportReady}
          onSelectExisting={handleSelectExistingPdf}
        />
      </Modal>

      {/* OCR Review Fullscreen Drawer */}
      <OcrReviewDrawer
        open={ocrReviewOpen}
        initialSourceId={ocrSourceId}
        onClose={() => {
          setOcrReviewOpen(false);
          setOcrSourceId(null);
        }}
      />

      {/* Question Bank Drawer */}
      <QuestionBankDrawer open={questionBankOpen} onClose={() => setQuestionBankOpen(false)} />

      {/* Textbook Drawer */}
      <TextbookDrawer open={textbookOpen} onClose={() => setTextbookOpen(false)} />
    </Layout>
  );
}
