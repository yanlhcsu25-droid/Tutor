export type SupportedQuestionType = "选择题" | "填空题" | "计算题" | "证明题";

export type GenerationTemplate = {
  label: string;
  intro: string;
  scopeNames: string[];
  knowledgePoints: string[];
  totalQuestions: number;
  sections: { questionType: SupportedQuestionType; count: number }[];
  difficultyRecommendation: string;
  prompt: string;
};

type GenerationTemplateDefinition = Omit<GenerationTemplate, "prompt">;

function buildPrompt(template: GenerationTemplateDefinition): string {
  const lines = [template.intro];
  if (template.knowledgePoints.length > 1) {
    lines.push(`重点知识点包括：${template.knowledgePoints.join("、")}。`);
  }
  lines.push(`共${template.totalQuestions}题。`, "题型推荐：");
  lines.push(...template.sections.map(
    (section) => `- ${section.questionType}${section.count}道`,
  ));
  lines.push(
    `难度推荐：${template.difficultyRecommendation}`,
    "请先生成待确认方案，不要直接创建试卷。",
  );
  return lines.join("\n");
}

const DEFINITIONS: GenerationTemplateDefinition[] = [
  {
    label: "章节测试",
    intro: "帮我出一套第一章“函数与极限”章节测试。",
    scopeNames: ["函数与极限"],
    knowledgePoints: ["函数的极限", "极限运算法则", "无穷小的比较"],
    totalQuestions: 8,
    sections: [
      { questionType: "选择题", count: 2 },
      { questionType: "填空题", count: 2 },
      { questionType: "计算题", count: 3 },
      { questionType: "证明题", count: 1 },
    ],
    difficultyRecommendation: "中等，基础概念与综合运用兼顾。",
  },
  {
    label: "知识点专练",
    intro: "针对“拐点”知识点出一套专项练习。",
    scopeNames: ["拐点"],
    knowledgePoints: ["拐点"],
    totalQuestions: 3,
    sections: [
      { questionType: "计算题", count: 3 },
    ],
    difficultyRecommendation: "中等，重点练习拐点的判断与计算。",
  },
  {
    label: "期中测试",
    intro: "帮我出一套覆盖第一章“函数与极限”、第二章“导数与微分”和第三章“微分中值定理与导数的应用”的期中测试。",
    scopeNames: ["函数与极限", "导数与微分", "微分中值定理与导数的应用"],
    knowledgePoints: [],
    totalQuestions: 12,
    sections: [
      { questionType: "选择题", count: 4 },
      { questionType: "填空题", count: 3 },
      { questionType: "计算题", count: 3 },
      { questionType: "证明题", count: 2 },
    ],
    difficultyRecommendation: "中等，基础题、常规应用题和少量综合性题目合理搭配。",
  },
  {
    label: "期末综合",
    intro: "帮我出一套本学期高等数学期末综合测试。",
    scopeNames: [],
    knowledgePoints: [],
    totalQuestions: 16,
    sections: [
      { questionType: "选择题", count: 5 },
      { questionType: "填空题", count: 4 },
      { questionType: "计算题", count: 5 },
      { questionType: "证明题", count: 2 },
    ],
    difficultyRecommendation: "基础题50%、中等题30%、提高题20%。",
  },
];

export const GENERATION_TEMPLATES: GenerationTemplate[] = DEFINITIONS.map((template) => ({
  ...template,
  prompt: buildPrompt(template),
}));
