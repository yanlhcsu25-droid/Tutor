import { useMemo } from "react";

const COMMANDS: Record<string, string> = {
  lim: "lim", sin: "sin", cos: "cos", tan: "tan", log: "log", ln: "ln",
  infty: "∞", to: "→", cdot: "·", times: "×", pi: "π", alpha: "α",
  beta: "β", delta: "δ", Delta: "Δ", theta: "θ", lambda: "λ",
  left: "", right: "", quad: "", qquad: "",
  mathrm: "", operatorname: "",
};

const escapeHtml = (value: string) => value
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");

function atom(value: string, kind: "mi" | "mn" | "mo" = "mi") {
  return `<${kind}>${escapeHtml(value)}</${kind}>`;
}

function decodeMathEntities(value: string): string {
  return value
    .replaceAll("&gt;", ">")
    .replaceAll("&lt;", "<")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'")
    .replaceAll("&amp;", "&");
}

function parseLatex(source: string): string {
  source = decodeMathEntities(source);
  let index = 0;
  const readGroup = () => {
    while (source[index] === " ") index += 1;
    if (source[index] !== "{") return parseAtom();
    index += 1;
    const value = parseSequence("}");
    if (source[index] === "}") index += 1;
    return `<mrow>${value}</mrow>`;
  };
  const parseAtom = (): string => {
    while (source[index] === " ") index += 1;
    if (source[index] === "{") return readGroup();
    if (source[index] === "\\") {
      index += 1;
      const match = source.slice(index).match(/^[A-Za-z]+/);
      if (!match) return atom(source[index++] ?? "", "mo");
      index += match[0].length;
      if (match[0] === "frac") {
        return `<mfrac>${readGroup()}${readGroup()}</mfrac>`;
      }
      const value = COMMANDS[match[0]] ?? match[0];
      if (value === "") return "";
      return atom(value, value.length > 1 && !COMMANDS[match[0]] ? "mi" : "mo");
    }
    const value = source[index++] ?? "";
    if (/\d/.test(value)) return atom(value, "mn");
    if (/[A-Za-z]/.test(value)) return atom(value, "mi");
    return atom(value, "mo");
  };
  const parseSequence = (stop?: string): string => {
    const values: string[] = [];
    while (index < source.length && source[index] !== stop) {
      if (source[index] === " ") { index += 1; continue; }
      if (source[index] === "^" || source[index] === "_") {
        const operator = source[index++];
        const value = readGroup();
        const previous = values.pop() ?? atom("", "mi");
        values.push(operator === "^" ? `<msup>${previous}${value}</msup>` : `<msub>${previous}${value}</msub>`);
        continue;
      }
      values.push(parseAtom());
    }
    return values.join("");
  };
  return parseSequence();
}

function formulaHtml(value: string, display: boolean) {
  return `<math xmlns="http://www.w3.org/1998/Math/MathML"${display ? " display=\"block\"" : ""}><mrow>${parseLatex(value)}</mrow></math>`;
}

function renderMarkdown(source: string): string {
  const escaped = escapeHtml(source.replaceAll("\r\n", "\n"));
  const withMath = escaped
    .replace(/\$\$([\s\S]*?)\$\$/g, (_match, value) => formulaHtml(value, true))
    .replace(/\\\(([\s\S]*?)\\\)/g, (_match, value) => formulaHtml(value, false))
    .replace(/\$([^$\n]+)\$/g, (_match, value) => formulaHtml(value, false));

  return withMath.split("\n").map((line) => {
    if (line.startsWith("### ")) return `<h4>${line.slice(4)}</h4>`;
    if (line.startsWith("## ")) return `<h3>${line.slice(3)}</h3>`;
    if (line.startsWith("# ")) return `<h2>${line.slice(2)}</h2>`;
    if (/^[-*] /.test(line)) return `<li>${line.slice(2)}</li>`;
    if (/^\d+\. /.test(line)) return `<li>${line.replace(/^\d+\. /, "")}</li>`;
    if (!line.trim()) return "";
    return `<p>${line}</p>`;
  }).join("")
    .replace(/(<li>.*?<\/li>)+/g, (items) => `<ul>${items}</ul>`)
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

export default function MarkdownMath({ content, className = "" }: { content: string; className?: string }) {
  const html = useMemo(() => renderMarkdown(content), [content]);
  return <div className={`markdown-math ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}
