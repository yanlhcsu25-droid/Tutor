import { useState } from "react";
import { Button, Card, Col, Divider, InputNumber, Row, Space, Tag, Typography } from "antd";

export type GenerationSection = {
  question_type: string;
  count: number;
  score_each?: number | null;
  total_score?: number | null;
};

export type GenerationPlanPatch = {
  question_type: string;
  count?: number;
  score_each?: number;
};

type GenerationPlanCardProps = {
  title: string;
  initialSections: GenerationSection[];
  totalQuestions: number;
  totalScore: number;
  loading: boolean;
  disabled?: boolean;
  onUpdate: (patches: GenerationPlanPatch[]) => void;
  onConfirm: () => void;
};

export default function GenerationPlanCard({
  title,
  initialSections,
  totalQuestions: plannedQuestionCount,
  totalScore: plannedTotalScore,
  loading,
  disabled,
  onUpdate,
  onConfirm,
}: GenerationPlanCardProps) {
  const [sections, setSections] = useState(initialSections);
  const changed = JSON.stringify(sections) !== JSON.stringify(initialSections);
  const hasEditableSections = sections.length > 0;
  const totalQuestions = hasEditableSections
    ? sections.reduce((sum, item) => sum + item.count, 0)
    : plannedQuestionCount;
  const totalScore = hasEditableSections && sections.every((item) => item.score_each != null)
    ? sections.reduce((sum, item) => sum + item.count * Number(item.score_each), 0)
    : plannedTotalScore;

  const update = () => {
    const patches = sections.flatMap((item, index) => {
      const original = initialSections[index];
      const patch: GenerationPlanPatch = { question_type: item.question_type };
      if (item.count !== original.count) patch.count = item.count;
      if (item.score_each !== original.score_each && item.score_each != null) {
        patch.score_each = item.score_each;
      }
      return Object.keys(patch).length > 1 ? [patch] : [];
    });
    onUpdate(patches);
  };

  return (
    <Card
      size="small"
      title={<span>📋 待确认组卷方案 — {title}</span>}
      style={{ maxWidth: 560, background: "#fafafa" }}
    >
      <Typography.Paragraph>共 {totalQuestions} 题，{totalScore} 分</Typography.Paragraph>
      {!hasEditableSections && (
        <Typography.Text type="secondary">
          本方案按错题知识点定向组题；如需调整题量或题型，请直接说明要求。
        </Typography.Text>
      )}
      {sections.map((section, index) => (
        <Row key={section.question_type} gutter={8} align="middle" style={{ marginBottom: 8 }}>
          <Col flex="100px"><Tag>{section.question_type}</Tag></Col>
          <Col>
            <InputNumber
              aria-label={`${section.question_type}题数`}
              min={1}
              max={100}
              value={section.count}
              suffix="题"
              onChange={(value) => setSections((items) => items.map(
                (item, itemIndex) => itemIndex === index ? { ...item, count: value ?? 1 } : item,
              ))}
            />
          </Col>
          <Col>
            <InputNumber
              aria-label={`${section.question_type}每题分值`}
              min={0.5}
              max={300}
              step={0.5}
              value={section.score_each ?? undefined}
              placeholder="每题分值"
              suffix="分/题"
              onChange={(value) => setSections((items) => items.map(
                (item, itemIndex) => itemIndex === index ? { ...item, score_each: value } : item,
              ))}
            />
          </Col>
        </Row>
      ))}
      <Divider style={{ margin: "12px 0" }} />
      <Space>
        <Button
          size="small"
          disabled={disabled || !hasEditableSections || !changed}
          loading={loading}
          onClick={update}
        >
          更新方案
        </Button>
        <Button
          type="primary"
          size="small"
          loading={loading}
          disabled={disabled || changed}
          onClick={onConfirm}
        >
          确认并组卷
        </Button>
      </Space>
      {changed && (
        <Typography.Text type="warning" style={{ display: "block", marginTop: 8 }}>
          方案已修改，请先更新方案并重新校验。
        </Typography.Text>
      )}
    </Card>
  );
}
