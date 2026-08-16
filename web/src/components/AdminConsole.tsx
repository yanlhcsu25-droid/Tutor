import { useCallback, useEffect, useState } from "react";
import { Button, Card, Collapse, Empty, Layout, List, Spin, Tag, Typography } from "antd";
import { BugOutlined, ReloadOutlined } from "@ant-design/icons";
import { api } from "../api";
import "./AdminConsole.css";

type SessionSummary = {
  conversation_id: string | null;
  started_at: string;
  last_user_input: string;
  status: "success" | "failed";
  turn_count: number;
};

type ToolTrace = {
  sequence: number;
  tool_name: string;
  arguments: unknown;
  memory_before: unknown;
  result: unknown;
  memory_after: unknown;
};

type TurnTrace = {
  trace_id: string;
  started_at: string;
  duration_ms: number;
  status: "success" | "failed";
  agent_status: string;
  user_input: string;
  memory_before: unknown;
  tool_calls: ToolTrace[];
  memory_after: unknown;
  final_response: string;
};

const formatTime = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
const json = (value: unknown) => JSON.stringify(value ?? null, null, 2);

function StatusTag({ status }: { status: "success" | "failed" }) {
  return <Tag color={status === "success" ? "success" : "error"}>{status}</Tag>;
}

export default function AdminConsole() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [turns, setTurns] = useState<TurnTrace[]>([]);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingTurns, setLoadingTurns] = useState(false);

  const loadSessions = useCallback(async () => {
    setLoadingSessions(true);
    try {
      const data = await api.get<{ items: SessionSummary[] }>("/api/v1/admin/agent-traces/sessions");
      setSessions(data.items);
      setSelected((current) => current && data.items.some((item) => item.conversation_id === current)
        ? current
        : data.items[0]?.conversation_id ?? null);
    } finally {
      setLoadingSessions(false);
    }
  }, []);

  const loadTurns = useCallback(async (conversationId: string | null) => {
    if (!conversationId) {
      setTurns([]);
      return;
    }
    setLoadingTurns(true);
    try {
      const params = new URLSearchParams({ conversation_id: conversationId });
      const data = await api.get<{ items: TurnTrace[] }>(`/api/v1/admin/agent-traces/turns?${params}`);
      setTurns(data.items);
    } finally {
      setLoadingTurns(false);
    }
  }, []);

  useEffect(() => { void loadSessions(); }, [loadSessions]);
  useEffect(() => { void loadTurns(selected); }, [selected, loadTurns]);

  const refresh = async () => {
    await loadSessions();
    await loadTurns(selected);
  };

  return (
    <Layout className="admin-console">
      <Layout.Sider width={330} className="admin-session-sider">
        <div className="admin-brand"><BugOutlined /> Agent Debug</div>
        <div className="admin-session-heading">
          <Typography.Text type="secondary">会话日志</Typography.Text>
          <Button aria-label="刷新日志" type="text" icon={<ReloadOutlined />} onClick={() => void refresh()} />
        </div>
        <Spin spinning={loadingSessions}>
          <List
            className="admin-session-list"
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 Agent 日志" /> }}
            dataSource={sessions}
            renderItem={(item) => (
              <List.Item
                className={item.conversation_id === selected ? "admin-session-active" : ""}
                onClick={() => setSelected(item.conversation_id)}
              >
                <div className="admin-session-item">
                  <div className="admin-session-meta"><StatusTag status={item.status} /><span>{formatTime(item.started_at)}</span></div>
                  <Typography.Paragraph ellipsis={{ rows: 2 }} className="admin-session-message">
                    {item.last_user_input || "(空输入)"}
                  </Typography.Paragraph>
                  <Typography.Text type="secondary">{item.turn_count} 轮</Typography.Text>
                </div>
              </List.Item>
            )}
          />
        </Spin>
      </Layout.Sider>
      <Layout.Content className="admin-trace-content">
        <div className="admin-trace-header">
          <div>
            <Typography.Title level={4}>Agent 对话日志</Typography.Title>
            <Typography.Text type="secondary">{selected ?? "选择左侧会话查看 Trace"}</Typography.Text>
          </div>
          <Button icon={<ReloadOutlined />} onClick={() => void refresh()}>刷新</Button>
        </div>
        <Spin spinning={loadingTurns}>
          {!selected ? <Empty description="暂无可查看会话" /> : (
            <Collapse
              className="admin-turn-list"
              items={turns.map((turn) => ({
                key: turn.trace_id,
                label: <div className="admin-turn-label"><StatusTag status={turn.status} /><span>{formatTime(turn.started_at)}</span><span>{(turn.duration_ms / 1000).toFixed(2)}s</span></div>,
                children: <div className="admin-trace-detail">
                  <section><Typography.Text strong>用户输入</Typography.Text><pre>{turn.user_input}</pre></section>
                  <section><Typography.Text strong>Memory Before</Typography.Text><pre>{json(turn.memory_before)}</pre></section>
                  {turn.tool_calls.map((tool) => <Card size="small" key={tool.sequence} title={`Tool ${tool.sequence}: ${tool.tool_name}`}>
                    <section><Typography.Text strong>参数</Typography.Text><pre>{json(tool.arguments)}</pre></section>
                    <section><Typography.Text strong>Memory Before</Typography.Text><pre>{json(tool.memory_before)}</pre></section>
                    <section><Typography.Text strong>Tool Result</Typography.Text><pre>{json(tool.result)}</pre></section>
                    <section><Typography.Text strong>Memory After</Typography.Text><pre>{json(tool.memory_after)}</pre></section>
                  </Card>)}
                  <section><Typography.Text strong>最终回复</Typography.Text><pre>{turn.final_response}</pre></section>
                  <section><Typography.Text strong>Memory After</Typography.Text><pre>{json(turn.memory_after)}</pre></section>
                  <Typography.Text type="secondary">Agent 状态：{turn.agent_status}</Typography.Text>
                </div>,
              }))}
            />
          )}
        </Spin>
      </Layout.Content>
    </Layout>
  );
}
