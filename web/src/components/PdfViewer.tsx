import { useRef, useEffect, useCallback } from "react";
import { Button, InputNumber, Space, Typography } from "antd";
import { ZoomInOutlined, ZoomOutOutlined, LeftOutlined, RightOutlined } from "@ant-design/icons";
import { wb } from "../api";

interface Props {
  sourceId: string;
  pageCount: number;
  page: number;
  zoom: number;
  bbox: { x: number; y: number; width: number; height: number; page_width: number; page_height: number } | null;
  onPageChange: (page: number) => void;
  onZoomChange: (zoom: number) => void;
}

export default function PdfViewer({ sourceId, pageCount, page, zoom, bbox, onPageChange, onZoomChange }: Props) {
  const imgRef = useRef<HTMLImageElement>(null);
  const highlightRef = useRef<HTMLDivElement>(null);

  const renderScale = Math.min(3, Math.max(0.8, 1.4 * zoom));
  const src = wb.pageUrl(sourceId, page, renderScale);

  const positionHighlight = useCallback(() => {
    const el = highlightRef.current;
    if (!el || !bbox) { if (el) el.style.display = "none"; return; }
    el.style.display = "flex";
    el.style.left = `${(bbox.x / bbox.page_width) * 100}%`;
    el.style.top = `${(bbox.y / bbox.page_height) * 100}%`;
    el.style.width = `${(bbox.width / bbox.page_width) * 100}%`;
    el.style.height = `${(bbox.height / bbox.page_height) * 100}%`;
    requestAnimationFrame(() => el.scrollIntoView({ behavior: "smooth", block: "center" }));
  }, [bbox]);

  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    img.onload = positionHighlight;
  }, [src, positionHighlight]);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <Space style={{ marginBottom: 8 }}>
        <Button icon={<LeftOutlined />} disabled={page <= 1} onClick={() => onPageChange(page - 1)} size="small" />
        <InputNumber min={1} max={pageCount} value={page} onChange={(v) => v && onPageChange(v)} size="small" style={{ width: 60 }} />
        <Typography.Text type="secondary">/ {pageCount}</Typography.Text>
        <Button icon={<RightOutlined />} disabled={page >= pageCount} onClick={() => onPageChange(page + 1)} size="small" />
        <span style={{ margin: "0 8px", color: "#d9d9d9" }}>|</span>
        <Button icon={<ZoomOutOutlined />} disabled={zoom <= 0.6} onClick={() => onZoomChange(Math.max(0.6, zoom - 0.2))} size="small" />
        <Typography.Text type="secondary">{Math.round(zoom * 100)}%</Typography.Text>
        <Button icon={<ZoomInOutlined />} disabled={zoom >= 2.2} onClick={() => onZoomChange(Math.min(2.2, zoom + 0.2))} size="small" />
      </Space>
      <div style={{ flex: 1, overflow: "auto", border: "1px solid #f0f0f0", borderRadius: 8, position: "relative", background: "#fafafa" }}>
        <img ref={imgRef} src={src} alt={`第${page}页`} style={{ display: "block", width: "100%" }} />
        <div
          ref={highlightRef}
          style={{
            position: "absolute",
            border: "2px solid #1677ff",
            background: "rgba(22,119,255,.12)",
            borderRadius: 4,
            pointerEvents: "none",
            display: "flex",
            alignItems: "flex-end",
            justifyContent: "center",
            fontSize: 11,
            color: "#1677ff",
            fontWeight: 600,
          }}
        >
          <span style={{ background: "#1677ff", color: "#fff", padding: "0 4px", borderRadius: 2 }}>当前题目</span>
        </div>
      </div>
    </div>
  );
}
