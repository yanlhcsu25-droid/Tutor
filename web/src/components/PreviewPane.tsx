import { useEffect, useRef } from "react";
import { wb } from "../api";

interface Props {
  markdown: string;
  enabled?: boolean;
}

export default function PreviewPane({ markdown, enabled = true }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const requestVersion = useRef(0);
  const lastRendered = useRef<string | null>(null);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (!enabled || markdown === lastRendered.current) return;

    const version = ++requestVersion.current;
    timer.current = setTimeout(async () => {
      if (!ref.current) return;
      try {
        const data = await wb.preview(markdown);
        if (version !== requestVersion.current || !ref.current) return;
        ref.current.innerHTML = data.html;
        lastRendered.current = markdown;
      } catch {
        if (version !== requestVersion.current || !ref.current) return;
        ref.current.textContent = "预览加载失败";
      }
    }, 500);
    return () => {
      if (timer.current) clearTimeout(timer.current);
      requestVersion.current += 1;
    };
  }, [markdown, enabled]);

  return <div ref={ref} className="markdown-body" style={{ padding: 16, minHeight: 200 }} />;
}
