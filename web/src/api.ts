/** 统一 API client — 使用相对路径，开发环境通过 Vite proxy 转发到 FastAPI。 */

async function request<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") ?? "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail = typeof data === "object" && data !== null && "detail" in data
      ? (data as Record<string, unknown>).detail
      : data;
    throw new Error(String(detail ?? "请求失败"));
  }
  return data as T;
}

export const api = {
  get: <T = unknown>(path: string) => request<T>(path),
  post: <T = unknown>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    }),
  patch: <T = unknown>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PATCH",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    }),
  put: <T = unknown>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PUT",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    }),
  delete: <T = unknown>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T = unknown>(path: string, form: FormData) =>
    request<T>(path, { method: "POST", body: form }),
  text: async (path: string, body?: unknown): Promise<string> => {
    const response = await fetch(path, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) throw new Error("请求失败");
    return response.text();
  },
};

// ── Workbench API（所有路径对应 /workbench 子应用）──────────────

export const wb = {
  // 来源
  listSources: () => api.get<WbSourceList>("/workbench/api/sources"),
  uploadPdf: (file: File, layout?: {
    solutionMode: "inline" | "separate";
    questionPageStart?: number;
    questionPageEnd?: number;
    solutionPageStart?: number;
    solutionPageEnd?: number;
  }) => {
    const form = new FormData();
    form.append("file", file);
    form.append("solution_mode", layout?.solutionMode ?? "inline");
    if (layout?.solutionMode === "separate") {
      form.append("question_page_start", String(layout.questionPageStart));
      form.append("question_page_end", String(layout.questionPageEnd));
      form.append("solution_page_start", String(layout.solutionPageStart));
      form.append("solution_page_end", String(layout.solutionPageEnd));
    }
    return api.upload<WbUploadResult>("/workbench/api/sources", form);
  },
  deleteSource: (sourceId: string) =>
    api.delete<WbDeleteSourceResult>(`/workbench/api/sources/${sourceId}`),
  // 题目
  listQuestions: (sourceId: string) =>
    api.get<WbQuestionList>(`/workbench/api/sources/${sourceId}/questions`),
  getQuestion: (questionId: string) =>
    api.get<WbQuestionDetail>(`/workbench/api/questions/${questionId}`),
  saveQuestion: (questionId: string, markdown: string) =>
    api.patch<WbSaveResult>(`/workbench/api/questions/${questionId}`, { markdown }),
  // 结构化元数据保存：knowledge_id 列表 + 难度 + 内容确认，不写 Markdown（名称仅前端展示）
  saveMetadata: (
    questionId: string,
    payload: { knowledge_points: string[]; difficulty_level: number | null; content_confirmed?: boolean },
  ) => api.put<{ question: WbQuestion }>(`/workbench/api/questions/${questionId}/metadata`, payload),
  // 只读：按已保存的 knowledge_id 反显名称（不触发 AI 分类）
  getKnowledgeOptions: (questionId: string) =>
    api.get<WbKnowledgeClassification>(`/workbench/api/questions/${questionId}/knowledge/options`),
  createRevision: (questionId: string) =>
    api.post<WbQuestionDetail>(`/workbench/api/questions/${questionId}/revision`, {}),
  validateQuestion: (questionId: string, markdown: string) =>
    api.post<WbValidation>(`/workbench/api/questions/${questionId}/validate`, { markdown }),
  classifyKnowledge: (questionId: string) =>
    api.post<WbKnowledgeClassification>(`/workbench/api/questions/${questionId}/knowledge/classify`, {}),
  confirmContent: (questionId: string) =>
    api.post<WbQuestion>(`/workbench/api/questions/${questionId}/confirm-content`, {}),
  // 预览 / 差异
  preview: (markdown: string) =>
    api.post<WbPreviewResult>("/workbench/api/preview", { markdown }),
  diff: (questionId: string, markdown: string) =>
    api.text(`/workbench/api/questions/${questionId}/diff`, { markdown }),
  // 整页 Markdown / 重新识别题目
  getPageMarkdown: (sourceId: string, page: number) =>
    api.get<WbPageMarkdown>(`/workbench/api/sources/${sourceId}/pages/${page}/markdown`),
  savePageMarkdown: (sourceId: string, page: number, markdown: string) =>
    api.put<WbPageMarkdown>(
      `/workbench/api/sources/${sourceId}/pages/${page}/markdown`,
      { markdown },
    ),
  restorePageMarkdown: (sourceId: string, page: number) =>
    api.post<WbPageMarkdown>(`/workbench/api/sources/${sourceId}/pages/${page}/markdown/restore`),
  generatePreview: (sourceId: string) =>
    api.post<WbGeneratePreview>(`/workbench/api/sources/${sourceId}/generate/preview`),
  generateApply: (sourceId: string, expectedNumbers: string[]) =>
    api.post<WbGenerateResult>(`/workbench/api/sources/${sourceId}/generate/apply`, { expected_numbers: expectedNumbers }),
  resplitPreview: (sourceId: string, page: number, markdown: string) =>
    api.post<WbResplitPlan>(
      `/workbench/api/sources/${sourceId}/pages/${page}/resplit/preview`,
      { markdown },
    ),
  resplitApply: (sourceId: string, page: number, markdown: string, expectedNumbers: string[]) =>
    api.post<WbResplitResult>(
      `/workbench/api/sources/${sourceId}/pages/${page}/resplit/apply`,
      { markdown, expected_numbers: expectedNumbers },
    ),
  debugSplit: (pages: { page_number: number; markdown: string }[]) =>
    api.post<WbSplitDebugResult>("/workbench/api/debug/split", { pages }),
  // 批量提交 / 发布
  submitAll: (sourceId: string, questionIds?: string[]) =>
    api.post<WbSubmitResult>(`/workbench/api/sources/${sourceId}/submit`, questionIds ? { question_ids: questionIds } : {}),
  publish: (questionIds: string[]) =>
    api.post<WbPublishResult>("/workbench/api/publish", { question_ids: questionIds }),
  // PDF 页面渲染（直接用 img src，不走 api client）
  pageUrl: (sourceId: string, page: number, scale = 1.4) =>
    `/workbench/api/sources/${sourceId}/pages/${page}?scale=${scale.toFixed(1)}`,
};

// ── Workbench 类型──────────────────────────────────────────

export interface WbSource {
  source_file_id: string;
  original_name: string;
  stored_path: string;
  sha256: string;
  page_count: number;
  processing_status: string;
  processing_error: string | null;
  created_at: string;
  question_count: number;
  reviewed_count: number;
  review: { status: "pending" | "in_progress" | "completed"; completed: number; total: number };
  published_count: number;
  can_delete: boolean;
  has_manual_edits: boolean;
  manual_edit_count: number;
  progress?: { current_page?: number; total_pages?: number; status?: string; error?: string; question_count?: number };
}

export interface WbSourceList {
  items: WbSource[];
}

export interface WbDeleteSourceResult {
  source_id: string;
  deleted: boolean;
  deleted_page_count: number;
  deleted_draft_count: number;
  deleted_bank_draft_count: number;
  had_manual_edits: boolean;
  manual_edit_count: number;
  file_cleanup_warnings: string[];
}

export interface WbQuestion {
  question_id: string;
  source_file_id: string;
  page_number: number;
  original_number: string;
  ocr_markdown: string;
  edited_markdown: string;
  review_status: string;
  source_bbox: { x: number; y: number; width: number; height: number; page_width: number; page_height: number } | null;
  validation: WbValidation | null;
  knowledge_points: string[];
  difficulty_level: number | null;
  content_confirmed: boolean;
}

export interface WbQuestionList {
  items: WbQuestion[];
}

export interface WbQuestionDetail {
  question: WbQuestion;
  source: WbSource;
}

export interface WbSaveResult {
  question: WbQuestion;
  validation: WbValidation;
}

export interface WbValidation {
  valid: boolean;
  issues: { field: string; message: string; line?: number }[];
}

export interface WbPreviewResult {
  html: string;
  issues: WbValidation["issues"];
}

export interface WbUploadResult {
  source: WbSource;
  question_count: number;
  deduplicated: boolean;
}

export interface WbSubmitResult {
  success_count: number;
  already_imported_count: number;
  failure_count: number;
  failures: { question_id: string; reasons: string[] }[];
  jsonl_path: string | null;
}

export interface WbPageMarkdown {
  source_file_id: string;
  page_number: number;
  raw_markdown: string;
  edited_markdown: string;
  modified: boolean;
  updated_at: string | null;
  drafts?: WbQuestion[];
}

export interface WbGeneratePreview {
  source_file_id: string;
  new_numbers: string[];
  created: { page_number: number; original_number: string; match_status: string; review_note: string; preview: string }[];
  old_unpublished: WbQuestion[];
  diagnostics: { ambiguous_keys: string[]; missing_questions: string[]; unmatched_solutions: string[] };
  blocked: boolean;
}

export interface WbGenerateResult {
  source_file_id: string;
  created_question_ids: string[];
  created_count: number;
  deleted_count: number;
  new_numbers: string[];
  diagnostics: WbGeneratePreview["diagnostics"];
}

export interface WbResplitDraftSummary {
  question_id: string;
  page_number: number;
  original_number: string;
  review_status: string;
  has_manual_edit: boolean;
  preview: string;
}

export interface WbResplitCandidate {
  page_number: number;
  original_number: string;
  preview: string;
}

export interface WbResplitPlan {
  source_file_id: string;
  target_page: number;
  affected_range: {
    start_page: number;
    end_page: number;
    pages: number[];
    cross_page: boolean;
    description: string;
  };
  old_drafts: WbResplitDraftSummary[];
  old_numbers: string[];
  new_candidates: WbResplitCandidate[];
  new_numbers: string[];
  changes: {
    added: WbResplitCandidate[];
    removed: WbResplitDraftSummary[];
    kept: WbResplitDraftSummary[];
  };
  manual_edits_lost: WbResplitDraftSummary[];
  blocked: boolean;
  blocking_drafts: WbResplitDraftSummary[];
}

export interface WbResplitResult {
  affected_range: { start_page: number; end_page: number; pages: number[] };
  deleted_count: number;
  created_count: number;
  kept_count: number;
  new_numbers: string[];
  created_question_ids: string[];
  questions: WbQuestion[];
}

export interface WbSplitDebugResult {
  pages: {
    page_number: number;
    normalized_length: number;
    preamble_length: number;
    major_numbers: string[];
    has_continuation: boolean;
  }[];
  candidates: {
    page_number: number;
    original_number: string;
    question_type: string;
    body_length: number;
    answer_length: number;
    analysis_length: number;
    needs_review: boolean;
    review_note: string;
  }[];
  warnings: string[];
}

export interface WbPublishResult {
  published_count: number;
  failure_count: number;
  failures: { question_id: string; reason: string }[];
  synced_to_bank: number;
  sync_details: { question_id: string; bank_draft_id: string; bank_question_id: string; cached: boolean }[];
}

export interface WbKnowledgeClassification {
  question_id: string;
  knowledge_points: { knowledge_id: string; name: string; confidence: number }[];
  options?: { knowledge_id: string; name: string }[];
  needs_review: boolean;
  reason: string;
  provenance: "rule_suggested";
}
