import { useCallback, useEffect, useState } from "react";
import { Button, Checkbox, Drawer, Empty, List, Modal, Space, Typography, message } from "antd";
import { DeleteOutlined, ReloadOutlined } from "@ant-design/icons";
import { teacherAgent, type TeacherAgentConversationSummary } from "../api";

interface Props {
  open: boolean;
  currentId: string;
  onClose: () => void;
  onSelect: (id: string) => void;
  onCurrentDeleted: () => void;
}

export default function ConversationHistoryManager({ open, currentId, onClose, onSelect, onCurrentDeleted }: Props) {
  const [items, setItems] = useState<TeacherAgentConversationSummary[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try { setItems(await teacherAgent.listConversations()); }
    catch (error) { message.error(String(error)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { if (open) void load(); }, [open, load]);
  const allSelected = items.length > 0 && selected.length === items.length;
  const remove = () => {
    if (!selected.length) return;
    Modal.confirm({
      title: `删除 ${selected.length} 条历史会话？`,
      content: "聊天记录、运行轨迹和未确认的临时组卷状态将被删除；已生成的试卷不会受影响。",
      okText: "删除", okButtonProps: { danger: true }, cancelText: "取消",
      onOk: async () => {
        try {
          await teacherAgent.deleteConversations(selected);
          const deletingCurrent = selected.includes(currentId);
          setSelected([]);
          await load();
          if (deletingCurrent) onCurrentDeleted();
          message.success("历史会话已删除");
        } catch (error) { message.error(String(error)); }
      },
    });
  };

  return <Drawer title="历史聊天记录管理" open={open} onClose={onClose} width={440}
    extra={<Button type="text" icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button>}>
    <div className="history-manager-toolbar">
      <Checkbox checked={allSelected} indeterminate={selected.length > 0 && !allSelected}
        onChange={(event) => setSelected(event.target.checked ? items.map((item) => item.conversation_id) : [])}>
        全选（{items.length}）
      </Checkbox>
      <Button danger disabled={!selected.length} icon={<DeleteOutlined />} onClick={remove}>批量删除</Button>
    </div>
    {!items.length && !loading ? <Empty description="暂无历史会话" /> : <List loading={loading} dataSource={items} renderItem={(item) => (
      <List.Item className={item.conversation_id === currentId ? "history-manager-current" : ""}>
        <Space align="start">
          <Checkbox checked={selected.includes(item.conversation_id)} onChange={(event) => setSelected((old) => event.target.checked ? [...old, item.conversation_id] : old.filter((id) => id !== item.conversation_id))} />
          <button type="button" className="history-manager-item" onClick={() => { onSelect(item.conversation_id); onClose(); }}>
            <Typography.Text strong ellipsis>{item.title}</Typography.Text>
            <Typography.Text type="secondary">{new Date(item.last_message_at).toLocaleString()}</Typography.Text>
          </button>
        </Space>
      </List.Item>
    )} />}
  </Drawer>;
}
