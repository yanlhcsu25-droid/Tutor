import { useRef, useEffect, useCallback } from "react";
import { Button, Select, Space, Typography } from "antd";
import { ZoomInOutlined, ZoomOutOutlined, LeftOutlined, RightOutlined } from "@ant-design/icons";
import { wb } from "../api";

interface Props {
  sourceId: string;
  pageCount: number;
  pages?: number[];
  page: number;
  zoom: number;
  bbox: { x: number; y: number; width: number; height: number; page_width: number; page_height: number } | null;
  onPageChange: (page: number) => void;
  onZoomChange: (zoom: number) => void;
}

export default function PdfViewer({ sourceId, pageCount, pages, page, zoom, bbox, onPageChange, onZoomChange }: Props) {
  const imgRef = useRef<HTMLImageElement>(null);
  const highlightRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const renderScale = Math.min(3, Math.max(0.8, 1.4 * zoom));
  const src = wb.pageUrl(sourceId, page, renderScale);
  const availablePages = pages?.length ? pages : Array.from({ length: pageCount }, (_, index) => index + 1);
  const pageIndex = availablePages.indexOf(page);
  const safeIndex = pageIndex >= 0 ? pageIndex : 0;

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
    const container = scrollRef.current;
    if (!container) return;
    container.scrollTop = 0;
    container.scrollLeft = 0;
  }, [src]);

  useEffect(() => {
    if (imgRef.current?.complete) positionHighlight();
  }, [src, positionHighlight]);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <Space style={{ marginBottom: 8 }}>
        <Button icon={<LeftOutlined />} disabled={safeIndex <= 0} onClick={() => onPageChange(availablePages[safeIndex - 1])} size="small" />
        <Select
          value={availablePages.includes(page) ? page : availablePages[0]}
          onChange={onPageChange}
          size="small"
          style={{ width: 104 }}
          options={availablePages.map((item) => ({ value: item, label: `PDF 第 ${item} 页` }))}
        />
        <Typography.Text type="secondary">{safeIndex + 1} / {availablePages.length}</Typography.Text>
        <Button icon={<RightOutlined />} disabled={safeIndex >= availablePages.length - 1} onClick={() => onPageChange(availablePages[safeIndex + 1])} size="small" />
        <span style={{ margin: "0 8px", color: "#d9d9d9" }}>|</span>
        <Button icon={<ZoomOutOutlined />} disabled={zoom <= 0.6} onClick={() => onZoomChange(Math.max(0.6, zoom - 0.2))} size="small" />
        <Typography.Text type="secondary">{Math.round(zoom * 100)}%</Typography.Text>
        <Button icon={<ZoomInOutlined />} disabled={zoom >= 2.2} onClick={() => onZoomChange(Math.min(2.2, zoom + 0.2))} size="small" />
      </Space>
      <div ref={scrollRef} style={{ flex: 1, overflow: "auto", border: "1px solid #f0f0f0", borderRadius: 8, background: "#fafafa" }}>
        <div style={{ position: "relative", width: `${zoom * 100}%` }}>
          <img ref={imgRef} src={src} alt={`第${page}页`} onLoad={positionHighlight} style={{ display: "block", width: "100%" }} />
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
    </div>
  );
}
