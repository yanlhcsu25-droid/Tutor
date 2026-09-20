import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import GenerationPlanCard from "./GenerationPlanCard";

describe("GenerationPlanCard", () => {
  it("allows confirming a reinforcement plan without editable sections", async () => {
    const onConfirm = vi.fn();
    const user = userEvent.setup();

    render(
      <GenerationPlanCard
        title="错题强化卷"
        initialSections={[]}
        totalQuestions={6}
        totalScore={60}
        loading={false}
        onUpdate={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    expect(screen.getByText("共 6 题，60 分")).toBeInTheDocument();
    expect(screen.getByText(/按错题知识点定向组题/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "更新方案" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "确认并组卷" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("requires updating a changed section before confirmation", async () => {
    const onUpdate = vi.fn();
    const onConfirm = vi.fn();
    const user = userEvent.setup();

    render(
      <GenerationPlanCard
        title="章节练习"
        initialSections={[{ question_type: "计算题", count: 4, score_each: 10 }]}
        totalQuestions={4}
        totalScore={40}
        loading={false}
        onUpdate={onUpdate}
        onConfirm={onConfirm}
      />,
    );

    const countInput = screen.getByRole("spinbutton", { name: "计算题题数" });
    fireEvent.change(countInput, { target: { value: "5" } });

    expect(screen.getByText("共 5 题，50 分")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认并组卷" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "更新方案" }));

    expect(onUpdate).toHaveBeenCalledWith([{ question_type: "计算题", count: 5 }]);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("disables both actions when the plan is no longer active", () => {
    render(
      <GenerationPlanCard
        title="旧方案"
        initialSections={[{ question_type: "选择题", count: 2, score_each: 5 }]}
        totalQuestions={2}
        totalScore={10}
        loading={false}
        disabled
        onUpdate={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "更新方案" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "确认并组卷" })).toBeDisabled();
  });
});
