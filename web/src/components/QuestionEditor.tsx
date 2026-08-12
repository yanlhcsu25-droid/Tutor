import { useState } from "react";
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
