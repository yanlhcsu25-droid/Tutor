// 题干预览抽取（前端共用，仅读取 edited_markdown，不修改任何数据）
//
// 兼容新版标题 `## 题目内容` 与旧版标题 `## 题目`。
// 定位题目标题后，从下一行起跳过空行与其他 Markdown 标题，
// 取第一行非空正文作为 preview；到达结束 section（参考解答/答案/解析）仍未
// 取到正文则返回 "—"。若不存在题目标题，则 fallback 到整篇第一行有效正文。

const TITLE_PREFIX = "## 题目";
const STOP_SECTIONS = ["参考解答", "答案", "解析"];

function isHeading(line: string): boolean {
  return line.trimStart().startsWith("#");
}

function isStopHeading(line: string): boolean {
  if (!isHeading(line)) return false;
  const text = line.trimStart().replace(/^#+\s*/, "");
  return STOP_SECTIONS.some((s) => text.includes(s));
}

export function extractQuestionPreview(markdown: string): string {
  if (!markdown) return "—";

  const lines = markdown.split("\n");

  // 1) 定位题目标题行（兼容 `## 题目内容` 与 `## 题目`）
  let titleIdx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trimStart().startsWith(TITLE_PREFIX)) {
      titleIdx = i;
      break;
    }
  }

  // 2) 从题目标题之后扫描正文
  if (titleIdx >= 0) {
    for (let i = titleIdx + 1; i < lines.length; i++) {
      const trimmed = lines[i].trim();
      if (trimmed === "") continue; // 跳过空行
      if (isHeading(lines[i])) {
        if (isStopHeading(lines[i])) return "—"; // 到达结束 section，无正文
        continue; // 跳过其他 Markdown 标题
      }
      return trimmed; // 第一行非空正文
    }
    return "—"; // 标题后无任何正文
  }

  // 3) 无题目标题：fallback 到整篇第一行有效正文（跳过标题行）
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed === "") continue;
    if (isHeading(line)) continue;
    return trimmed;
  }
  return "—";
}

/** 题库列表用的短文本预览：去掉 Markdown/LaTeX 标记，避免源码直接露出。 */
export function extractPlainQuestionPreview(markdown: string): string {
  const value = extractQuestionPreview(markdown);
  return value
    .replace(/\\left\s*|\\right\s*/g, "")
    .replace(/\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, "($1)/($2)")
    .replace(/\\sqrt\s*\{([^{}]*)\}/g, "√($1)")
    .replace(/_\s*\{([^{}]*)\}/g, "_$1")
    .replace(/\^\s*\{([^{}]*)\}/g, "^($1)")
    .replace(/\\to\b/g, "→")
    .replace(/\\infty\b/g, "∞")
    .replace(/\\(?:lim|sin|cos|tan|log|ln)\b/g, (command) => command.slice(1))
    .replace(/\\(?:mathrm|mathbf|text)\s*\{([^{}]*)\}/g, "$1")
    .replace(/\\[,;! ]/g, "")
    .replace(/\$\$([\s\S]*?)\$\$/g, "$1")
    .replace(/\$([^$]+)\$/g, "$1")
    .replace(/[{}*_`#]/g, "")
    .replace(/\\/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
