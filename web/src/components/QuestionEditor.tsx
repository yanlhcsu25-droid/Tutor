import { useEffect, useState } from "react";
import { Tabs, Input } from "antd";
import PreviewPane from "./PreviewPane";
import DiffPanel from "./DiffPanel";
import ValidationPanel from "./ValidationPanel";
import type { WbValidation } from "../api";

interface Props {
  questionId: string;
  markdown: string;
  validation: WbValidation | null;
  onChange: (value: string) => void;
  onJumpLine: (line: number) => void;
  readOnly?: boolean;
}

export default function QuestionEditor({ questionId, markdown, validation, onChange, onJumpLine, readOnly = false }: Props) {
  const [activeTab, setActiveTab] = useState("edit");

  // 切换题目时不能继承上一题停留的“实时预览/修改差异”状态。
  // 每道新题都从可编辑的 Markdown 源码开始，避免用户误以为源码消失。
  useEffect(() => {
    setActiveTab("edit");
  }, [questionId]);

  return (
    <div className="question-editor-layout">
      <Tabs
        className="question-editor-tabs"
        activeKey={activeTab}
        onChange={setActiveTab}
        size="small"
        items={[
          {
            key: "edit",
            label: "Markdown 源码",
            children: (
              <div className="question-editor-tab-scroll question-editor-source-tab">
                <Input.TextArea
                  value={markdown}
                  readOnly={readOnly}
                  onChange={(e) => onChange(e.target.value)}
                  className="question-editor-source"
                  spellCheck={false}
                />
              </div>
            ),
          },
          {
            key: "preview",
            label: "实时预览",
            children: (
              <div className="question-editor-tab-scroll">
                <PreviewPane markdown={markdown} enabled={activeTab === "preview"} />
              </div>
            ),
          },
          {
            key: "diff",
            label: "修改差异",
            children: (
              <div className="question-editor-tab-scroll">
                <DiffPanel questionId={questionId} markdown={markdown} />
              </div>
            ),
          },
        ]}
      />
      <div className="question-editor-validation">
        <ValidationPanel validation={validation} onJumpLine={onJumpLine} />
      </div>
    </div>
  );
}
