import { useState } from "react";
import { Layout, Menu, Typography, Modal, Button, Space } from "antd";
import {
  RobotOutlined, FilePdfOutlined, BookOutlined,
  DatabaseOutlined, PlusOutlined, ThunderboltOutlined,
} from "@ant-design/icons";
import AgentWorkspace, { clearStoredConversationId } from "./components/AgentWorkspace";
import OcrReviewDrawer from "./components/OcrReviewDrawer";
import QuestionBankDrawer from "./components/QuestionBankDrawer";
import PdfImportPanel from "./components/PdfImportPanel";
import TextbookDrawer from "./components/TextbookDrawer";
import AdminConsole from "./components/AdminConsole";
import SidebarConversations from "./components/SidebarConversations";

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
    <Layout className="app-layout">
      <Sider width={280} breakpoint="md" collapsedWidth={0} className="app-sider">
        <div className="app-brand">
          <span className="app-brand-icon"><ThunderboltOutlined /></span>
          <span><b>MathPaper</b><small>智能组卷工作台</small></span>
        </div>
        <Menu
          mode="inline"
          defaultSelectedKeys={["agent"]}
          className="app-menu"
          style={{ borderRight: 0 }}
          items={[
            { key: "new", icon: <PlusOutlined />, label: "新建组卷", onClick: handleNewSession },
            { type: "divider" },
            { key: "pdf-import", icon: <FilePdfOutlined />, label: "PDF 导入", onClick: () => setPdfUploadOpen(true) },
            { key: "question-bank", icon: <DatabaseOutlined />, label: "题库", onClick: () => setQuestionBankOpen(true) },
            { key: "textbook", icon: <BookOutlined />, label: "教材目录", onClick: () => setTextbookOpen(true) },
          ]}
        />
        <SidebarConversations />
      </Sider>

      {/* main content — always AgentWorkspace */}
      <Layout>
        <Content className="app-content">
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
