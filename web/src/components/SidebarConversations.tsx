import { useCallback, useEffect, useState } from "react";
import { Button, Typography } from "antd";
import { HistoryOutlined, MessageOutlined, PlusOutlined } from "@ant-design/icons";
import { teacherAgent, type TeacherAgentConversationSummary } from "../api";
import ConversationHistoryManager from "./ConversationHistoryManager";

const STORAGE_KEY = "teacher-agent.conversation-id";
const EVENT_NAME = "teacher-agent:conversation-change";

function currentConversationId() {
  try { return localStorage.getItem(STORAGE_KEY) ?? ""; } catch { return ""; }
}

export default function SidebarConversations() {
  const [items, setItems] = useState<TeacherAgentConversationSummary[]>([]);
  const [currentId, setCurrentId] = useState(currentConversationId);
  const [managerOpen, setManagerOpen] = useState(false);
  const load = useCallback(async () => {
    try { setItems(await teacherAgent.listConversations()); } catch { /* 主聊天区会显示接口异常 */ }
  }, []);
  useEffect(() => {
    void load();
    const sync = () => { setCurrentId(currentConversationId()); void load(); };
    window.addEventListener(EVENT_NAME, sync);
    return () => window.removeEventListener(EVENT_NAME, sync);
  }, [load]);
  const select = (id: string) => {
    try { localStorage.setItem(STORAGE_KEY, id); } catch { /* noop */ }
    setCurrentId(id);
    window.dispatchEvent(new Event(EVENT_NAME));
  };
  const create = () => select(globalThis.crypto?.randomUUID?.() ?? `agent-${Date.now()}`);

  return <>
    <section className="sider-conversations">
      <div className="sider-conversations-title"><span>最近会话</span></div>
      <Button className="sider-new-chat" type="text" icon={<PlusOutlined />} onClick={create}>新建对话</Button>
      <div className="sider-conversation-list">
        {items.map((item) => <button key={item.conversation_id} type="button" className={item.conversation_id === currentId ? "sider-conversation active" : "sider-conversation"} onClick={() => select(item.conversation_id)}>
          <MessageOutlined /><span>{item.title}</span>
        </button>)}
        {!items.length && <Typography.Text className="sider-conversation-empty">暂无历史会话</Typography.Text>}
      </div>
      <Button className="sider-history-manage" type="text" icon={<HistoryOutlined />} onClick={() => setManagerOpen(true)}>管理历史记录</Button>
    </section>
    <ConversationHistoryManager open={managerOpen} currentId={currentId} onClose={() => setManagerOpen(false)} onSelect={select} onCurrentDeleted={create} />
  </>;
}
