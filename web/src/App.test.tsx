import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const loaded = vi.hoisted(() => ({
  admin: 0,
  ocr: 0,
  questionBank: 0,
  pdfImport: 0,
  textbook: 0,
}));

vi.mock("./components/AgentWorkspace", () => ({
  default: () => <div>Agent workspace</div>,
  clearStoredConversationId: vi.fn(),
}));
vi.mock("./components/SidebarConversations", () => ({
  default: () => <div>Conversation list</div>,
}));
vi.mock("./components/AdminConsole", () => {
  loaded.admin += 1;
  return { default: () => <div>Admin console</div> };
});
vi.mock("./components/OcrReviewDrawer", () => {
  loaded.ocr += 1;
  return { default: () => <div>OCR review</div> };
});
vi.mock("./components/QuestionBankDrawer", () => {
  loaded.questionBank += 1;
  return { default: () => <div>Question bank</div> };
});
vi.mock("./components/PdfImportPanel", () => {
  loaded.pdfImport += 1;
  return { default: () => <div>PDF import</div> };
});
vi.mock("./components/TextbookDrawer", () => {
  loaded.textbook += 1;
  return { default: () => <div>Textbook</div> };
});

import App from "./App";

describe("App module loading", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/");
  });

  it("loads optional workspaces only when the user opens them", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(loaded).toEqual({
      admin: 0,
      ocr: 0,
      questionBank: 0,
      pdfImport: 0,
      textbook: 0,
    });

    await user.click(screen.getByText("PDF 导入"));

    await waitFor(() => expect(loaded.pdfImport).toBe(1));
    expect(loaded.admin).toBe(0);
    expect(loaded.ocr).toBe(0);
    expect(loaded.questionBank).toBe(0);
    expect(loaded.textbook).toBe(0);
  });
});
