import { describe, expect, it } from "vitest";

import { GENERATION_TEMPLATES } from "./generationTemplates";

describe("generation templates", () => {
  it("offers four demo flows backed by supported question types", () => {
    expect(GENERATION_TEMPLATES.map((template) => template.label)).toEqual([
      "章节测试",
      "知识点专练",
      "期中测试",
      "期末综合",
    ]);

    for (const template of GENERATION_TEMPLATES) {
      expect(template.sections.reduce((total, section) => total + section.count, 0))
        .toBe(template.totalQuestions);
      expect(template.prompt).not.toContain("综合题");
      expect(template.prompt).not.toContain("每题");
      expect(template.prompt).not.toContain("总分");
      expect(template.prompt).not.toContain("\n\n");
    }
  });

  it("uses the verified scope and distribution for each demo", () => {
    expect(GENERATION_TEMPLATES.map((template) => ({
      scopeNames: template.scopeNames,
      knowledgePoints: template.knowledgePoints,
      totalQuestions: template.totalQuestions,
      sections: template.sections,
    }))).toEqual([
      {
        scopeNames: ["函数与极限"],
        knowledgePoints: ["函数的极限", "极限运算法则", "无穷小的比较"],
        totalQuestions: 8,
        sections: [
          { questionType: "选择题", count: 2 },
          { questionType: "填空题", count: 2 },
          { questionType: "计算题", count: 3 },
          { questionType: "证明题", count: 1 },
        ],
      },
      {
        scopeNames: ["拐点"],
        knowledgePoints: ["拐点"],
        totalQuestions: 3,
        sections: [
          { questionType: "计算题", count: 3 },
        ],
      },
      {
        scopeNames: ["函数与极限", "导数与微分", "微分中值定理与导数的应用"],
        knowledgePoints: [],
        totalQuestions: 12,
        sections: [
          { questionType: "选择题", count: 4 },
          { questionType: "填空题", count: 3 },
          { questionType: "计算题", count: 3 },
          { questionType: "证明题", count: 2 },
        ],
      },
      {
        scopeNames: [],
        knowledgePoints: [],
        totalQuestions: 16,
        sections: [
          { questionType: "选择题", count: 5 },
          { questionType: "填空题", count: 4 },
          { questionType: "计算题", count: 5 },
          { questionType: "证明题", count: 2 },
        ],
      },
    ]);
  });
});
