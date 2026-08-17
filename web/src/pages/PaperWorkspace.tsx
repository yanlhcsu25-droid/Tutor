import { useState } from "react";
import {
  Button, Card, Col, Empty, Form, Input, InputNumber, message, Row,
  Select, Space, Spin, Statistic, Tag, Typography, Upload,
} from "antd";
import {
  ArrowDownOutlined, ArrowUpOutlined, FileImageOutlined, FilePdfOutlined,
  LockOutlined, ReloadOutlined, RobotOutlined, SafetyCertificateOutlined,
  UnlockOutlined, ScanOutlined, SaveOutlined, InboxOutlined, EditOutlined, CheckOutlined,
} from "@ant-design/icons";

// 使用相对路径，Vite proxy 转发
type Blueprint = {
  title: string; total_questions: number; total_score: number;
  question_type_counts: Record<string, number>;
  sections: { question_type: string; count: number; score_per_question: number; total_score: number }[];
  knowledge_quotas: { name: string; count: number }[]; soft_knowledge_preferences: string[];
  locked_question_ids: string[];
  manual_question_ids: string[]; excluded_question_ids: string[]; question_order: string[];
  score_overrides: Record<string, number>; seed: number;
};
type BlueprintRecord = { blueprint_id: string; status: "draft" | "confirmed" | "used"; blueprint: Blueprint; cached: boolean };
type Preview = { title: string; total_score: number; feasible: boolean; warnings: string[]; constraints: { name: string; required: string | number; actual: string | number; satisfied: boolean }[]; items: { item_id: string; question_id: string; question_text: string; question_type: string; score: number; knowledge: string[]; locked: boolean }[] };
type Violation = { code: string; field: string; required: unknown; actual: unknown; question_ids: string[]; repairable: boolean; message: string };
type ValidationReport = { passed: boolean; violations: Violation[] };
type SavedPaper = { paper_id: string; version: number; preview: Preview; validation_report: ValidationReport };
type SupplyCheck = { feasible: boolean; violations: Violation[] };
type PrepMatch = { question_id: string; question_text: string; question_type: string; final_answer: string | null; solution_steps: string[]; knowledge: string[]; match_reasons: string[] };
type PrepResult = { id: string; error_reason: string; knowledge_names: string[]; matches: PrepMatch[] };
type VisionExtract = { question_text: string; options: string[]; question_type: string; final_answer: string; solution_text: string; knowledge_names: string[]; needs_review: boolean; warnings: string[] };
type OcrBlock = { block_id: string; block_order: number; page_number: number; block_type: string; bbox: number[]; original_text: string; original_latex: string | null; confidence: number; corrected_text: string | null; corrected_latex: string | null; review_status: string };
type OcrResult = { task_id: string; original_filename: string; image_path: string; page_images: string[]; engine: string; status: string; image_width: number; image_height: number; duration_ms: number; warnings: string[]; created_at: string; blocks: OcrBlock[] };
type OcrSaveResult = { draft_id: string; question_id: string; question_text: string; question_type: string; block_count: number };

const initial: Blueprint = { title: "高等数学测试卷", total_questions: 10, total_score: 100, question_type_counts: { 选择题: 4, 填空题: 2, 证明题: 4 }, sections: [{question_type:"选择题",count:4,score_per_question:5,total_score:20},{question_type:"填空题",count:2,score_per_question:10,total_score:20},{question_type:"证明题",count:4,score_per_question:15,total_score:60}], knowledge_quotas: [], soft_knowledge_preferences: [], locked_question_ids: [], manual_question_ids: [], excluded_question_ids: [], question_order: [], score_overrides: {}, seed: 42 };

export default function PaperWorkspace() {
  const [requirement, setRequirement] = useState("生成一套一元函数微分学测试卷，共10题100分，其中选择题4道、填空题2道、证明题4道，导数运算至少5题");
  const [blueprint, setBlueprint] = useState<Blueprint>(initial);
  const [blueprintId, setBlueprintId] = useState<string | null>(null);
  const [paperId, setPaperId] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [validationReport, setValidationReport] = useState<ValidationReport | null>(null);
  const [paperVersion, setPaperVersion] = useState<number | null>(null);
  const [supplyCheck, setSupplyCheck] = useState<SupplyCheck | null>(null);
  const [prepInput, setPrepInput] = useState({ question_text: "", final_answer: "", solution_text: "", error_reason: "", question_type: "计算题", knowledge_names: "极限运算", match_count: 5 });
  const [prepResult, setPrepResult] = useState<PrepResult | null>(null);
  const [prepLoading, setPrepLoading] = useState(false);
  const [questionImage, setQuestionImage] = useState<string | null>(null);
  const [solutionImage, setSolutionImage] = useState<string | null>(null);
  const [questionImageName, setQuestionImageName] = useState<string | null>(null);
  const [solutionImageName, setSolutionImageName] = useState<string | null>(null);
  const [visionWarnings, setVisionWarnings] = useState<string[]>([]);
  const [visionLoading, setVisionLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [ocrLoading, setOcrLoading] = useState(false);
  const [ocrResult, setOcrResult] = useState<OcrResult | null>(null);
  const [ocrMergedText, setOcrMergedText] = useState("");
  const [ocrSaveResult, setOcrSaveResult] = useState<OcrSaveResult | null>(null);
  const [ocrQuestionType, setOcrQuestionType] = useState("计算题");

  const API_PATH = "/api/v1";

  const call = async (path: string, body: unknown) => {
    const response = await fetch(`${API_PATH}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!response.ok) throw new Error((await response.json()).detail ?? "请求失败");
    return response;
  };
  const applySavedPaper = (saved: SavedPaper) => { setPaperId(saved.paper_id); setPaperVersion(saved.version); setPreview(saved.preview); setValidationReport(saved.validation_report); };
  const readSupply = async (id: string) => { const response = await fetch(`${API_PATH}/blueprints/${id}/supply-check`); if (!response.ok) throw new Error("题库供给检查失败"); const result: SupplyCheck = await response.json(); setSupplyCheck(result); return result; };
  const parse = async () => { setLoading(true); try { const r = await call("/blueprints/parse", { requirement }); const parsed: BlueprintRecord = await r.json(); setBlueprintId(parsed.blueprint_id); setPaperId(null); setBlueprint({ ...parsed.blueprint, locked_question_ids: [], manual_question_ids: [], excluded_question_ids: [], question_order: [], score_overrides: {} }); setPreview(null); const supply = await readSupply(parsed.blueprint_id); if (!supply.feasible) message.warning(`蓝图已解析，但当前题库有 ${supply.violations.length} 项供给缺口`); else message.success(parsed.cached ? "已复用本地解析缓存，未调用云 API" : "已创建可编辑的组卷蓝图"); } catch (e) { message.error(String(e)); } finally { setLoading(false); } };
  const confirmAndCompose = async () => { if (!blueprintId) return message.warning("请先解析组卷要求"); setLoading(true); try { const patched = await fetch(`${API_PATH}/blueprints/${blueprintId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(blueprint) }); if (!patched.ok) throw new Error((await patched.json()).detail ?? "保存蓝图失败"); const supply = await readSupply(blueprintId); if (!supply.feasible) { message.error("当前题库无法满足蓝图，请先调整要求或审核更多题目"); return; } await call(`/blueprints/${blueprintId}/confirm`, {}); const response = await call("/papers", { blueprint_id: blueprintId }); applySavedPaper(await response.json()); message.success("蓝图已确认，试卷已保存并完成审核"); } catch (e) { message.error(String(e)); } finally { setLoading(false); } };
  const download = async (version: "student" | "teacher") => { try { if (!paperId) throw new Error("请先确认并生成试卷"); const r = await fetch(`${API_PATH}/papers/${paperId}/exports/${version}.pdf`); if (!r.ok) throw new Error("导出失败"); const url = URL.createObjectURL(await r.blob()); const a = document.createElement("a"); a.href = url; a.download = version === "student" ? "试卷.pdf" : "题目与答案解析.pdf"; a.click(); URL.revokeObjectURL(url); } catch (e) { message.error(String(e)); } };
  const downloadLatex = async (version: "student" | "teacher") => { try { if (!paperId) throw new Error("请先确认并生成试卷"); const r = await fetch(`${API_PATH}/papers/${paperId}/exports/${version}.tex`); if (!r.ok) throw new Error("导出失败"); const url = URL.createObjectURL(await r.blob()); const a = document.createElement("a"); a.href = url; a.download = version === "student" ? "学生试卷.tex" : "教师解析卷.tex"; a.click(); URL.revokeObjectURL(url); } catch (e) { message.error(String(e)); } };
  const setSection = (type: string, field: "count" | "score_per_question", raw: number | null) => { const value = raw ?? 0; const sections = blueprint.sections.map(section => section.question_type === type ? {...section,[field]:value,total_score:(field === "count" ? value : section.count) * (field === "score_per_question" ? value : section.score_per_question)} : section); setBlueprint({...blueprint,sections,question_type_counts:Object.fromEntries(sections.map(section=>[section.question_type,section.count])),total_questions:sections.reduce((sum,section)=>sum+section.count,0),total_score:sections.reduce((sum,section)=>sum+section.total_score,0)}); };
  const mutatePaper = async (path: string, body?: unknown, method="POST") => { if (!paperId) return; setLoading(true); try { const response = await fetch(`${API_PATH}/papers/${paperId}${path}`, {method,headers:{"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body)}); if (!response.ok) throw new Error(JSON.stringify((await response.json()).detail)); applySavedPaper(await response.json()); } catch (error) { message.error(String(error)); } finally { setLoading(false); } };
  const toggleLock = async (itemId: string, locked: boolean) => mutatePaper(`/items/${itemId}/lock`, {locked:!locked});
  const replaceQuestion = async (itemId: string) => mutatePaper(`/items/${itemId}/replace`);
  const moveQuestion = async (itemId: string, offset: -1 | 1) => { if (!preview) return; const order = preview.items.map(item => item.item_id); const index = order.indexOf(itemId); const target = index + offset; if (target < 0 || target >= order.length) return; [order[index], order[target]] = [order[target], order[index]]; await mutatePaper("/items/reorder", {item_ids:order}); };
  const setQuestionScore = async (itemId: string, score: number | null) => { if (score === null) return; await mutatePaper(`/items/${itemId}`, {score}, "PATCH"); };
  const createPrepTask = async () => { setPrepLoading(true); try { const response = await call("/prep/mistakes", { ...prepInput, knowledge_names: prepInput.knowledge_names.split(/[，,、]/).map(value => value.trim()).filter(Boolean), }); const result: PrepResult = await response.json(); setPrepResult(result); message.success(`已保存错题任务并匹配 ${result.matches.length} 道巩固题`); } catch (error) { message.error(String(error)); } finally { setPrepLoading(false); } };
  const selectImage = async (file: File, kind: "question" | "solution") => { if (!(["image/jpeg", "image/png", "image/webp"].includes(file.type))) { message.error("仅支持 JPG、PNG 或 WebP 图片"); return; } if (file.size > 10 * 1024 * 1024) { message.error("单张图片不能超过 10 MB"); return; } const dataUrl = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error("读取图片失败")); reader.readAsDataURL(file); }); if (kind === "question") { setQuestionImage(dataUrl); setQuestionImageName(file.name); } else { setSolutionImage(dataUrl); setSolutionImageName(file.name); } };
  const recognizeImages = async () => { if (!questionImage) return; setVisionLoading(true); try { const response = await call("/prep/vision/extract", { question_image: questionImage, solution_image: solutionImage }); const result: VisionExtract = await response.json(); const questionText = [result.question_text, ...result.options].filter(Boolean).join("\n"); setPrepInput({ ...prepInput, question_text: questionText, final_answer: result.final_answer, solution_text: result.solution_text, question_type: result.question_type, knowledge_names: result.knowledge_names.join("，"), }); setVisionWarnings(result.warnings); message.success("图片识别完成，请检查并修改识别结果后再保存"); } catch (error) { message.error(String(error)); } finally { setVisionLoading(false); } };
  const ocrUpload = async (file: File) => { const allowedTypes = ["image/jpeg", "image/png", "image/webp", "image/bmp", "application/pdf"]; if (!allowedTypes.includes(file.type)) { message.error("仅支持 JPG、PNG、WebP、BMP 图片或 PDF 文件"); return false; } const maxSize = file.type === "application/pdf" ? 200 * 1024 * 1024 : 50 * 1024 * 1024; if (file.size > maxSize) { message.error(`文件不能超过 ${Math.round(maxSize / 1024 / 1024)} MB`); return false; } setOcrLoading(true); setOcrResult(null); setOcrSaveResult(null); try { const form = new FormData(); form.append("file", file); const response = await fetch(`${API_PATH}/ocr/upload`, { method: "POST", body: form }); if (!response.ok) throw new Error((await response.json()).detail ?? "OCR 请求失败"); const result: OcrResult = await response.json(); setOcrResult(result); setOcrMergedText(result.blocks.map(b => b.corrected_text ?? b.original_text).filter(Boolean).join("\n")); message.success(`OCR 完成，识别到 ${result.blocks.length} 个文本块（${result.duration_ms}ms）`); } catch (error) { message.error(String(error)); } finally { setOcrLoading(false); } return false; };
  const ocrUpdateBlock = async (blockId: string, text: string) => { if (!ocrResult) return; try { const response = await fetch(`${API_PATH}/ocr/blocks/${blockId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ corrected_text: text, review_status: "approved" }), }); if (!response.ok) throw new Error("更新失败"); const updated: OcrBlock = await response.json(); setOcrResult({ ...ocrResult, blocks: ocrResult.blocks.map(b => b.block_id === blockId ? updated : b), }); } catch (error) { message.error(String(error)); } };
  const ocrSave = async () => { if (!ocrResult) return; setOcrLoading(true); try { const response = await fetch(`${API_PATH}/ocr/tasks/${ocrResult.task_id}/save`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ merged_text: ocrMergedText, question_type: ocrQuestionType, subject: "高等数学" }), }); if (!response.ok) throw new Error((await response.json()).detail ?? "保存失败"); const result: OcrSaveResult = await response.json(); setOcrSaveResult(result); message.success(`已保存入库：${result.draft_id}`); } catch (error) { message.error(String(error)); } finally { setOcrLoading(false); } };

  return <div>
    <section className="hero"><Typography.Title>高等数学备课 Agent</Typography.Title><Typography.Paragraph>从题目入库到智能组卷，再到答案解析与教学材料生成。</Typography.Paragraph></section>
    <Card title={<><ScanOutlined /> 题目 OCR 入库</>} className="ocr-card" extra={<Tag color="green">PaddleOCR 本地识别</Tag>}>
      <Typography.Paragraph type="secondary">上传题目图片，PaddleOCR 自动识别文字和公式，审核订正后一键入库。</Typography.Paragraph>
      <Row gutter={16}>
        <Col xs={24} md={8}>
          <Upload.Dragger accept=".jpg,.jpeg,.png,.webp,.bmp,.pdf" showUploadList={false} beforeUpload={ocrUpload} disabled={ocrLoading}>
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">点击或拖拽题目图片/PDF 到此处</p>
            <p className="ant-upload-hint">支持 JPG、PNG、WebP、BMP、PDF，单文件最大 50MB</p>
          </Upload.Dragger>
          {!ocrResult && !ocrLoading && <Empty className="ocr-empty" description="上传图片或 PDF 后自动开始 OCR 识别" />}
        </Col>
        <Col xs={24} md={16}>
          <Spin spinning={ocrLoading} tip="PaddleOCR 识别中...">
            {ocrResult && (
              <div className="ocr-review">
                <Row gutter={16}>
                  <Col xs={24} lg={12}>
                    <Typography.Title level={5}>📷 原图</Typography.Title>
                    <div className="ocr-image-wrap">
                      {ocrResult.page_images && ocrResult.page_images.length > 0 ? (
                        ocrResult.page_images.map((imgPath, idx) => (
                          <div key={idx} style={{ marginBottom: 8 }}>
                            {ocrResult.page_images!.length > 1 && <Tag color="geekblue" style={{ marginBottom: 4 }}>第 {idx + 1} 页</Tag>}
                            <img src={`/uploads/${imgPath.split("/").pop()}`} alt={`第${idx + 1}页`} className="ocr-image" />
                          </div>
                        ))
                      ) : (
                        <img src={`/uploads/${ocrResult.image_path.split("/").pop()}`} alt="原图" className="ocr-image" />
                      )}
                    </div>
                    <Typography.Text type="secondary">{ocrResult.original_filename} · {ocrResult.image_width}×{ocrResult.image_height} · {ocrResult.duration_ms}ms · {ocrResult.blocks.length} 块{(ocrResult.page_images?.length ?? 0) > 1 ? ` · ${ocrResult.page_images.length} 页` : ""}</Typography.Text>
                  </Col>
                  <Col xs={24} lg={12}>
                    <Typography.Title level={5}>📝 OCR 识别结果（点击编辑）</Typography.Title>
                    <div className="ocr-blocks">
                      {ocrResult.blocks.map(block => {
                        const displayText = block.corrected_text ?? block.original_text;
                        const isEdited = block.corrected_text !== null;
                        return (
                          <div key={block.block_id} className={`ocr-block ${block.block_type}`}>
                            <div className="ocr-block-header">
                              {ocrResult.page_images && ocrResult.page_images.length > 1 && <Tag color="geekblue">P{block.page_number ?? 1}</Tag>}
                              <Tag color={block.block_type === "formula" ? "purple" : "blue"}>{block.block_type === "formula" ? "公式" : "文字"}</Tag>
                              <Tag color={block.review_status === "approved" ? "success" : "warning"}>{block.review_status === "approved" ? "已审核" : "待审核"}</Tag>
                              <Typography.Text type="secondary" style={{ fontSize: 11 }}>置信度 {(block.confidence * 100).toFixed(0)}%</Typography.Text>
                              {isEdited && <Tag color="orange"><EditOutlined /> 已订正</Tag>}
                            </div>
                            <Input.TextArea value={displayText} onChange={e => { const updated = e.target.value; setOcrResult({ ...ocrResult, blocks: ocrResult.blocks.map(b => b.block_id === block.block_id ? { ...b, corrected_text: updated } : b), }); }} onBlur={() => { if (displayText !== block.original_text || !block.corrected_text) ocrUpdateBlock(block.block_id, displayText); }} autoSize={{ minRows: 1, maxRows: 6 }} style={block.block_type === "formula" ? { fontFamily: "monospace", background: "#f0f5ff" } : {}} />
                          </div>
                        );
                      })}
                    </div>
                    <div style={{ marginTop: 12 }}><Form.Item label="合并文本（可直接修改后保存）"><Input.TextArea rows={6} value={ocrMergedText} onChange={e => setOcrMergedText(e.target.value)} /></Form.Item></div>
                    <Space><Form.Item label="题型" style={{ marginBottom: 0 }}><Select value={ocrQuestionType} options={["选择题", "填空题", "计算题", "证明题"].map(v => ({ value: v, label: v }))} onChange={setOcrQuestionType} /></Form.Item><Button type="primary" icon={<SaveOutlined />} loading={ocrLoading} onClick={ocrSave} disabled={!ocrMergedText.trim()}>保存入库</Button></Space>
                  </Col>
                </Row>
              </div>
            )}
          </Spin>
          {ocrSaveResult && (<div className="ocr-save-result"><Tag icon={<CheckOutlined />} color="success">已保存！草稿 ID: {ocrSaveResult.draft_id}</Tag><Typography.Text type="secondary">题目 ID: {ocrSaveResult.question_id} · 题型: {ocrSaveResult.question_type} · {ocrSaveResult.block_count} 个文本块</Typography.Text></div>)}
        </Col>
      </Row>
    </Card>
    <Card title="错题备课任务" className="prep-card" extra={<Tag color="purple">第一版：教师提供错误原因</Tag>}>
      <Typography.Paragraph type="secondary">填写错题、标准答案、解析以及你从 ChatGPT 得到的错误原因；系统从已审核题库匹配同知识点、相近难度的巩固题。</Typography.Paragraph>
      <div className="vision-upload">
        <Space wrap>
          <Upload accept=".jpg,.jpeg,.png,.webp" showUploadList={false} beforeUpload={file=>{void selectImage(file,"question");return false;}}><Button icon={<FileImageOutlined/>}>选择题目图片</Button></Upload>
          <Typography.Text type={questionImageName?undefined:"secondary"}>{questionImageName??"必选：标准印刷题图片"}</Typography.Text>
          <Upload accept=".jpg,.jpeg,.png,.webp" showUploadList={false} beforeUpload={file=>{void selectImage(file,"solution");return false;}}><Button icon={<FileImageOutlined/>}>选择答案/解析图片</Button></Upload>
          <Typography.Text type={solutionImageName?undefined:"secondary"}>{solutionImageName??"可选"}</Typography.Text>
          <Button type="primary" ghost loading={visionLoading} disabled={!questionImage} onClick={recognizeImages}>用硅基流动识别并回填</Button>
        </Space>
        {!!visionWarnings.length&&<div className="vision-warnings">{visionWarnings.map(warning=><Tag color="warning" key={warning}>{warning}</Tag>)}</div>}
      </div>
      <Row gutter={14}><Col xs={24} lg={12}><Form.Item label="错题题目" required><Input.TextArea rows={4} value={prepInput.question_text} onChange={event=>setPrepInput({...prepInput,question_text:event.target.value})}/></Form.Item></Col><Col xs={24} lg={12}><Form.Item label="标准解析" required><Input.TextArea rows={4} value={prepInput.solution_text} onChange={event=>setPrepInput({...prepInput,solution_text:event.target.value})}/></Form.Item></Col></Row>
      <Row gutter={14}><Col xs={24} lg={12}><Form.Item label="标准答案" required><Input value={prepInput.final_answer} onChange={event=>setPrepInput({...prepInput,final_answer:event.target.value})}/></Form.Item></Col><Col xs={24} lg={12}><Form.Item label="学生错误原因（由教师或 ChatGPT 提供）" required><Input value={prepInput.error_reason} onChange={event=>setPrepInput({...prepInput,error_reason:event.target.value})}/></Form.Item></Col></Row>
      <Row gutter={14}><Col xs={12} md={6}><Form.Item label="题型"><Select value={prepInput.question_type} options={["选择题","填空题","计算题","证明题"].map(value=>({value,label:value}))} onChange={question_type=>setPrepInput({...prepInput,question_type})}/></Form.Item></Col><Col xs={24} md={8}><Form.Item label="目标知识点（逗号分隔）"><Input value={prepInput.knowledge_names} onChange={event=>setPrepInput({...prepInput,knowledge_names:event.target.value})}/></Form.Item></Col><Col xs={12} md={4}><Form.Item label="匹配数量"><InputNumber min={1} max={20} value={prepInput.match_count} onChange={value=>setPrepInput({...prepInput,match_count:value??5})}/></Form.Item></Col></Row>
      <Button type="primary" icon={<RobotOutlined/>} loading={prepLoading} disabled={!prepInput.question_text.trim()||!prepInput.final_answer.trim()||!prepInput.solution_text.trim()||!prepInput.error_reason.trim()||!prepInput.knowledge_names.trim()} onClick={createPrepTask}>保存并匹配巩固题</Button>
      {prepResult && <div className="prep-results"><Typography.Title level={4}>匹配结果</Typography.Title>{!prepResult.matches.length?<Empty description="当前题库没有匹配题，请调整知识点或先扩充题库"/>:prepResult.matches.map((item,index)=><div className="prep-match" key={item.question_id}><div><Typography.Text strong>{index+1}. {item.question_text}</Typography.Text><div className="prep-tags"><Tag>{item.question_type}</Tag>{item.knowledge.map(name=><Tag color="geekblue" key={name}>{name}</Tag>)}{item.match_reasons.map(reason=><Tag color="green" key={reason}>{reason}</Tag>)}</div></div><Typography.Paragraph><b>答案：</b>{item.final_answer??"暂无"}</Typography.Paragraph><Typography.Paragraph><b>解析：</b>{item.solution_steps.join(" ")||"暂无"}</Typography.Paragraph></div>)}</div>}
    </Card>
    <Row gutter={20} align="stretch">
      <Col xs={24} lg={10}><Card title="1. 描述组卷要求" className="panel"><Input.TextArea value={requirement} onChange={e => setRequirement(e.target.value)} rows={8}/><Space direction="vertical" className="action-stack"><Button type="primary" icon={<RobotOutlined />} block size="large" onClick={parse} loading={loading}>解析组卷要求</Button></Space></Card></Col>
      <Col xs={24} lg={14}><Card title="2. 检查组卷蓝图" className="panel"><Form layout="vertical"><Row gutter={12}><Col span={24}><Form.Item label="试卷标题"><Input value={blueprint.title} onChange={e => setBlueprint({...blueprint,title:e.target.value})}/></Form.Item></Col></Row><Row gutter={12}><Col span={8}><Form.Item label="题目总数（自动汇总）"><InputNumber disabled value={blueprint.total_questions}/></Form.Item></Col><Col span={8}><Form.Item label="总分（自动汇总）"><InputNumber disabled value={blueprint.total_score}/></Form.Item></Col><Col span={8}><Form.Item label="随机种子"><InputNumber value={blueprint.seed} onChange={v=>setBlueprint({...blueprint,seed:v??42})}/></Form.Item></Col></Row>{blueprint.sections.map(section=><Row gutter={12} key={section.question_type}><Col span={8}><Form.Item label="题型"><Input disabled value={section.question_type}/></Form.Item></Col><Col span={6}><Form.Item label="题数"><InputNumber min={0} value={section.count} onChange={value=>setSection(section.question_type,"count",value)}/></Form.Item></Col><Col span={6}><Form.Item label="每题分值"><InputNumber min={0.5} step={0.5} value={section.score_per_question} onChange={value=>setSection(section.question_type,"score_per_question",value)}/></Form.Item></Col><Col span={4}><Form.Item label="部分总分"><InputNumber disabled value={section.total_score}/></Form.Item></Col></Row>)}<Space wrap>{blueprint.knowledge_quotas.map(q=><Tag color="geekblue" key={q.name}>{q.name} ≥ {q.count}题</Tag>)}</Space>{supplyCheck&&!supplyCheck.feasible&&<div className="checks">{supplyCheck.violations.map(v=><Tag color="error" key={`${v.code}-${v.field}`}>{v.message}：实际 {String(v.actual)} / 要求 {String(v.required)}</Tag>)}</div>}<Button block size="large" type="primary" disabled={!blueprintId||supplyCheck?.feasible===false} onClick={confirmAndCompose} loading={loading}>确认并生成试卷</Button></Form></Card></Col>
    </Row>
    <Spin spinning={loading}><Card title={`3. 组卷结果${paperVersion ? ` · 版本 ${paperVersion}` : ""}`} className="result">{!preview ? <Empty description="生成后显示题目和结构化审核报告"/> : <><Row gutter={16}><Col span={8}><Statistic title="题目" value={preview.items.length}/></Col><Col span={8}><Statistic title="总分" value={preview.total_score}/></Col><Col span={8}><Statistic title="审核状态" value={validationReport?.passed?"全部通过":"存在违规"} valueStyle={{color:validationReport?.passed?"#16a34a":"#dc2626"}}/></Col></Row><div className="checks">{validationReport?.passed?<Tag icon={<SafetyCertificateOutlined/>} color="success">全部硬约束通过</Tag>:validationReport?.violations.map(v=><Tag color="error" key={`${v.code}-${v.field}`}>{v.message}：实际 {String(v.actual)} / 要求 {String(v.required)}</Tag>)}</div><Typography.Paragraph type="secondary">每次换题、锁题、调序或改分都会创建并审核一个新的持久化 Paper 版本，预览与导出始终使用当前 paper_id。</Typography.Paragraph>{preview.items.map((q,i)=><div className={`question ${q.locked?"question-locked":""}`} key={q.item_id}><span>{i+1}. {q.question_text}</span><Space wrap><Tag>{q.question_type}</Tag><InputNumber size="small" min={1} max={blueprint.total_score} value={q.score} onChange={value=>void setQuestionScore(q.item_id,value)} addonAfter="分"/><Button size="small" icon={<ArrowUpOutlined/>} disabled={i===0||loading} onClick={()=>void moveQuestion(q.item_id,-1)}/><Button size="small" icon={<ArrowDownOutlined/>} disabled={i===preview.items.length-1||loading} onClick={()=>void moveQuestion(q.item_id,1)}/><Button size="small" type={q.locked?"primary":"default"} icon={q.locked?<UnlockOutlined/>:<LockOutlined/>} onClick={()=>void toggleLock(q.item_id,q.locked)}>{q.locked?"取消锁定":"锁定"}</Button><Button size="small" icon={<ReloadOutlined/>} disabled={q.locked||loading} onClick={()=>void replaceQuestion(q.item_id)}>换一题</Button></Space></div>)}<Space className="downloads" wrap><Button disabled={!validationReport?.passed} icon={<FilePdfOutlined />} onClick={()=>download("student")}>学生卷 PDF</Button><Button type="primary" ghost disabled={!validationReport?.passed} icon={<FilePdfOutlined />} onClick={()=>download("teacher")}>教师卷 PDF</Button><Button icon={<FilePdfOutlined />} onClick={()=>downloadLatex("student")}>学生卷 LaTeX</Button><Button type="primary" ghost icon={<FilePdfOutlined />} onClick={()=>downloadLatex("teacher")}>教师卷 LaTeX</Button></Space></>}</Card></Spin>
  </div>;
}
