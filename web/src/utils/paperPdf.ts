export type PaperPdfVersion = "student" | "teacher";

const API = "/api/v1";

export function paperPdfEndpoint(
  paperId: string,
  version: PaperPdfVersion,
): string {
  return `${API}/papers/${encodeURIComponent(paperId)}/exports/${version}.pdf`;
}

export async function fetchPaperPdf(
  paperId: string,
  version: PaperPdfVersion,
): Promise<Blob> {
  const response = await fetch(paperPdfEndpoint(paperId, version));

  if (!response.ok) {
    let detail = "PDF 生成失败";

    try {
      const payload = await response.json();
      if (typeof payload?.detail === "string") {
        detail = payload.detail;
      } else if (payload?.detail?.message) {
        detail = String(payload.detail.message);
      }
    } catch {
      // Keep the generic message when the backend response is not JSON.
    }

    throw new Error(detail);
  }

  return response.blob();
}

function sanitizeFilename(value: string): string {
  const sanitized = value
    .replace(/[\\/:*?"<>|]/g, "-")
    .replace(/\s+/g, " ")
    .trim();

  return sanitized || "试卷";
}

export function paperPdfFilename(
  title: string,
  version: PaperPdfVersion,
): string {
  const base = sanitizeFilename(title);

  return version === "student"
    ? `${base}.pdf`
    : `${base}-含参考解答.pdf`;
}

export async function openPaperPdf(
  paperId: string,
  version: PaperPdfVersion,
): Promise<void> {
  // Open synchronously inside the click event so the browser does not
  // classify the final PDF tab as an async popup.
  const previewWindow = window.open("", "_blank");

  if (!previewWindow) {
    throw new Error("浏览器阻止了新窗口，请允许弹出窗口后重试");
  }

  // The preview tab does not need access back to the Teacher Agent tab.
  previewWindow.opener = null;

  try {
    previewWindow.document.title = "正在加载试卷…";
    previewWindow.document.body.style.margin = "0";
    previewWindow.document.body.style.fontFamily =
      "-apple-system, BlinkMacSystemFont, sans-serif";
    previewWindow.document.body.innerHTML = [
      '<div style="',
      'min-height:100vh;',
      'display:flex;',
      'align-items:center;',
      'justify-content:center;',
      'color:#666;',
      'font-size:14px;',
      '">',
      '正在编译 PDF…',
      '</div>',
    ].join("");

    const blob = await fetchPaperPdf(paperId, version);
    const url = URL.createObjectURL(blob);

    previewWindow.location.replace(url);

    // The new tab needs the blob URL after this function returns.
    // Revoke later rather than immediately after navigation.
    window.setTimeout(() => {
      URL.revokeObjectURL(url);
    }, 5 * 60 * 1000);
  } catch (error) {
    previewWindow.close();
    throw error;
  }
}

export async function downloadPaperPdf(
  paperId: string,
  version: PaperPdfVersion,
  title: string,
): Promise<void> {
  const blob = await fetchPaperPdf(paperId, version);
  const url = URL.createObjectURL(blob);

  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = paperPdfFilename(title, version);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
