import { useState, useCallback } from "react";
import { Layout, Menu, Typography, Modal, Button, Space } from "antd";
import {
  RobotOutlined, FilePdfOutlined, BookOutlined,
  FormOutlined, DatabaseOutlined, PlusOutlined,
} from "@ant-design/icons";
import AgentWorkspace from "./components/AgentWorkspace";
import OcrReviewDrawer from "./components/OcrReviewDrawer";
import PaperDrawer from "./components/PaperDrawer";
import QuestionBankDrawer from "./components/QuestionBankDrawer";
import PdfImportPanel from "./components/PdfImportPanel";
import TextbookDrawer from "./components/TextbookDrawer";

const { Sider, Content } = Layout;

type Preview = {
  title: string; total_score: number; feasible: boolean; warnings: string[];
  items: { item_id: string; question_id: string; question_text: string; question_type: string; score: number; knowledge: string[]; locked: boolean }[];
};
type ValidationReport = { passed: boolean; violations: { code: string; field: string; required: unknown; actual: unknown; question_ids: string[]; repairable: boolean; message: string }[] };

export default function App() {
  // ── drawers / modals ──
  const [pdfUploadOpen, setPdfUploadOpen] = useState(false);
  const [ocrReviewOpen, setOcrReviewOpen] = useState(false);
  const [ocrSourceId, setOcrSourceId] = useState<string | null>(null);
  const [questionBankOpen, setQuestionBankOpen] = useState(false);
  const [textbookOpen, setTextbookOpen] = useState(false);

  // ── paper drawer ──
  const [paperDrawerOpen, setPaperDrawerOpen] = useState(false);
  const [paperId, setPaperId] = useState<string | null>(null);
  const [paperPreview, setPaperPreview] = useState<Preview | null>(null);
  const [paperValidation, setPaperValidation] = useState<ValidationReport | null>(null);
  const [paperVersion, setPaperVersion] = useState<number | null>(null);

  const openPaperDrawer = useCallback((pid: string, preview: Preview, validation: ValidationReport, version: number) => {
    setPaperId(pid);
    setPaperPreview(preview);
    setPaperValidation(validation);
    setPaperVersion(version);
    setPaperDrawerOpen(true);
  }, []);

  const refreshPaper = useCallback(async (pid: string) => {
    try {
      const r = await fetch(`/api/v1/papers/${pid}`);
      if (!r.ok) return;
      const data = await r.json();
      setPaperId(data.paper_id);
      setPaperPreview(data.preview);
      setPaperValidation(data.validation_report);
      setPaperVersion(data.version);
    } catch { /* ignore */ }
  }, []);

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
        setPaperDrawerOpen(false);
        setPaperId(null);
        setPaperPreview(null);
        setPaperValidation(null);
        // Reload the page to reset AgentWorkspace state
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
          <AgentWorkspace onOpenPaperDrawer={openPaperDrawer} activePaperId={paperId} />
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

      {/* Paper Drawer */}
      <PaperDrawer
        open={paperDrawerOpen}
        paperId={paperId}
        preview={paperPreview}
        validation={paperValidation}
        version={paperVersion}
        onClose={() => setPaperDrawerOpen(false)}
        onRefresh={refreshPaper}
      />

      {/* Question Bank Drawer */}
      <QuestionBankDrawer open={questionBankOpen} onClose={() => setQuestionBankOpen(false)} />

      {/* Textbook Drawer */}
      <TextbookDrawer open={textbookOpen} onClose={() => setTextbookOpen(false)} />
    </Layout>
  );
}
