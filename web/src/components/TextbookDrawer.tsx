import { useState, useEffect } from "react";
import { Drawer, Button, Space, Typography, Select, Input, Modal, Tree, message, Popconfirm } from "antd";
import { PlusOutlined, ImportOutlined, EditOutlined, DeleteOutlined, FolderOutlined, FileOutlined, RedoOutlined } from "@ant-design/icons";
import DirectoryReimportModal from "./DirectoryReimportModal";

const API = "/api/v1";

interface Textbook { id: string; name: string; edition: string | null; is_active: boolean; }
interface TreeNode { id: string; code: string | null; title: string; node_type: string; parent_id: string | null; children: TreeNode[]; }

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function TextbookDrawer({ open, onClose }: Props) {
  const [books, setBooks] = useState<Textbook[]>([]);
  const [activeBookId, setActiveBookId] = useState<string | null>(null);
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [loading, setLoading] = useState(false);

  // ── modals ──
  const [newBookOpen, setNewBookOpen] = useState(false);
  const [newBookName, setNewBookName] = useState("");
  const [newBookEdition, setNewBookEdition] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [reimportOpen, setReimportOpen] = useState(false);
  const [editNodeOpen, setEditNodeOpen] = useState(false);
  const [editNodeId, setEditNodeId] = useState<string | null>(null);
  const [editNodeTitle, setEditNodeTitle] = useState("");

  const loadBooks = async () => {
    try {
      const r = await fetch(`${API}/textbooks`);
      const data: Textbook[] = await r.json();
      setBooks(data);
      const active = data.find((b) => b.is_active);
      if (active) setActiveBookId(active.id);
      else if (data.length > 0 && !activeBookId) {
        setActiveBookId(data[0].id);
      }
    } catch { /* ignore */ }
  };

  const loadTree = async (bookId: string) => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/textbooks/${bookId}/tree`);
      const data: TreeNode[] = await r.json();
      setTree(data);
    } catch { message.error("加载目录树失败"); }
    finally { setLoading(false); }
  };

  useEffect(() => { if (open) loadBooks(); }, [open]);
  useEffect(() => { if (activeBookId) loadTree(activeBookId); }, [activeBookId]);

  const handleCreateBook = async () => {
    if (!newBookName.trim()) return;
    try {
      const r = await fetch(`${API}/textbooks`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newBookName.trim(), edition: newBookEdition.trim() }),
      });
      const book: Textbook = await r.json();
      setBooks((prev) => [book, ...prev]);
      setActiveBookId(book.id);
      setNewBookOpen(false);
      setNewBookName("");
      setNewBookEdition("");
      message.success("教材已创建");
    } catch { message.error("创建失败"); }
  };

  const handleImport = async () => {
    if (!activeBookId || !importText.trim()) return;
    setLoading(true);
    try {
      await fetch(`${API}/textbooks/${activeBookId}/import`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: importText, replace: true }),
      });
      message.success("目录已导入");
      setImportOpen(false);
      setImportText("");
      loadTree(activeBookId);
    } catch { message.error("导入失败"); }
    finally { setLoading(false); }
  };

  const handleActivate = async (bookId: string) => {
    try {
      await fetch(`${API}/textbooks/${bookId}/activate`, { method: "POST" });
      setBooks((prev) => prev.map((b) => ({ ...b, is_active: b.id === bookId })));
      setActiveBookId(bookId);
    } catch { message.error("设置失败"); }
  };

  const handleEditNode = (nodeId: string, currentTitle: string) => {
    setEditNodeId(nodeId);
    setEditNodeTitle(currentTitle);
    setEditNodeOpen(true);
  };

  const handleSaveNode = async () => {
    if (!editNodeId) return;
    try {
      await fetch(`${API}/textbooks/nodes/${editNodeId}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: editNodeTitle }),
      });
      setEditNodeOpen(false);
      if (activeBookId) loadTree(activeBookId);
    } catch { message.error("修改失败"); }
  };

  const handleDeleteNode = async (nodeId: string) => {
    try {
      await fetch(`${API}/textbooks/nodes/${nodeId}`, { method: "DELETE" });
      if (activeBookId) loadTree(activeBookId);
      message.success("已删除");
    } catch { message.error("删除失败"); }
  };

  // Ant Design Tree 数据格式
  const treeData = tree.map((n) => _toTreeData(n));

  return (
    <Drawer open={open} onClose={onClose} width="60%" title="教材目录">
      {/* 教材选择 */}
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
        <Space>
          <Select
            value={activeBookId}
            onChange={(v) => setActiveBookId(v)}
            style={{ minWidth: 240 }}
            options={books.map((b) => ({
              value: b.id,
              label: <span>{b.name} {b.edition ? `· ${b.edition}` : ""} {b.is_active ? "✅" : ""}</span>,
            }))}
          />
          {activeBookId && !books.find((b) => b.id === activeBookId)?.is_active && (
            <Button size="small" onClick={() => handleActivate(activeBookId)}>设为当前教材</Button>
          )}
        </Space>
        <Space>
          <Button icon={<ImportOutlined />} onClick={() => setImportOpen(true)} disabled={!activeBookId}>
            导入目录
          </Button>
          <Button icon={<RedoOutlined />} onClick={() => setReimportOpen(true)} disabled={!activeBookId}>
            重新导入目录
          </Button>
          <Button icon={<PlusOutlined />} onClick={() => setNewBookOpen(true)}>
            新建教材
          </Button>
        </Space>
      </Space>

      {/* 教材目录树 */}
      <div style={{ maxHeight: "60vh", overflow: "auto", border: "1px solid #f0f0f0", borderRadius: 8, padding: 12 }}>
        {tree.length > 0 ? (
          <Tree
            treeData={treeData}
            defaultExpandAll
            blockNode
            titleRender={(node: any) => (
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "2px 0" }}>
                <span>
                  {node.isLeaf ? <FileOutlined style={{ marginRight: 6, color: "#999" }} /> : <FolderOutlined style={{ marginRight: 6, color: "#1677ff" }} />}
                  {node.code && <Typography.Text type="secondary" style={{ marginRight: 6 }}>{node.code}</Typography.Text>}
                  {node.title}
                </span>
                <span>
                  <Button type="link" size="small" icon={<EditOutlined />} onClick={(e) => { e.stopPropagation(); handleEditNode(node.key, node.title); }} />
                  <Popconfirm title="确认删除此节点及所有子节点？" onConfirm={() => handleDeleteNode(node.key)}>
                    <Button type="link" size="small" danger icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} />
                  </Popconfirm>
                </span>
              </div>
            )}
          />
        ) : (
          <Typography.Text type="secondary" style={{ padding: 16, display: "block", textAlign: "center" }}>
            {activeBookId ? "暂无目录，点击「导入目录」添加" : "请先选择或新建教材"}
          </Typography.Text>
        )}
      </div>

      {/* 新建教材 Modal */}
      <Modal open={newBookOpen} onCancel={() => setNewBookOpen(false)} onOk={handleCreateBook} title="新建教材">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Input placeholder="教材名称（必填）" value={newBookName} onChange={(e) => setNewBookName(e.target.value)} />
          <Input placeholder="版本（如：同济版第八版）" value={newBookEdition} onChange={(e) => setNewBookEdition(e.target.value)} />
        </Space>
      </Modal>

      {/* 导入目录 Modal */}
      <Modal open={importOpen} onCancel={() => setImportOpen(false)} onOk={handleImport} title="导入目录" width={600}>
        <Typography.Paragraph type="secondary">
          粘贴教材目录文本，每行一个章节或知识点，格式如：<br />
          第一章 函数与极限<br />
          1.1 映射与函数<br />
          1.2 数列的极限
        </Typography.Paragraph>
        <Input.TextArea
          rows={12}
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          placeholder="粘贴目录文本..."
        />
      </Modal>

      {/* 重新导入目录 Modal */}
      <DirectoryReimportModal
        open={reimportOpen}
        bookId={activeBookId}
        onClose={() => setReimportOpen(false)}
        onImported={() => { if (activeBookId) loadTree(activeBookId); }}
      />

      {/* 编辑节点 Modal */}
      <Modal open={editNodeOpen} onCancel={() => setEditNodeOpen(false)} onOk={handleSaveNode} title="编辑节点">
        <Input value={editNodeTitle} onChange={(e) => setEditNodeTitle(e.target.value)} />
      </Modal>
    </Drawer>
  );
}

function _toTreeData(node: TreeNode): any {
  return {
    key: node.id,
    title: node.title,
    code: node.code,
    isLeaf: node.children.length === 0,
    children: node.children.map(_toTreeData),
  };
}
