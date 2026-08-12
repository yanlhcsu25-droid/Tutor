const state = {
  sources: [], questions: [], source: null, currentIndex: -1,
  question: null, page: 1, zoom: 1, dirty: false, previewTimer: null,
};

const $ = (id) => document.getElementById(id);
const els = {
  sourceSelect: $('source-select'), pdfInput: $('pdf-input'), upload: $('upload-button'),
  solutionMode: $('solution-mode'), ocrMode: $('ocr-mode'), separateRanges: $('separate-ranges'),
  questionPageStart: $('question-page-start'), questionPageEnd: $('question-page-end'),
  solutionPageStart: $('solution-page-start'), solutionPageEnd: $('solution-page-end'),
  placeholderUpload: $('placeholder-upload'), submit: $('submit-button'),
  progressLabel: $('progress-label'), progressValue: $('progress-value'), progressBar: $('progress-bar'),
  pdfTitle: $('pdf-title'), pdfStage: $('pdf-stage'), placeholder: $('pdf-placeholder'),
  pageCanvas: $('page-canvas'), pageImage: $('page-image'), highlight: $('question-highlight'),
  pagePrev: $('page-prev'), pageNext: $('page-next'), pageNumber: $('page-number'), pageTotal: $('page-total'),
  editPageMarkdown: $('edit-page-markdown'),
  zoomOut: $('zoom-out'), zoomIn: $('zoom-in'), zoomLabel: $('zoom-label'),
  status: $('status-badge'), questionTitle: $('question-title'), questionPrev: $('question-prev'),
  questionNext: $('question-next'), questionIndex: $('question-index'), editor: $('markdown-editor'),
  preview: $('tab-preview'), diff: $('tab-diff'), validation: $('validation-panel'),
  validationIcon: $('validation-icon'), validationSummary: $('validation-summary'),
  validationIssues: $('validation-issues'), sourcePage: $('source-page-chip'), questionId: $('question-id-chip'),
  validate: $('validate-button'), save: $('save-button'), publish: $('publish-button'),
  busy: $('busy-overlay'), busyTitle: $('busy-title'), busyCopy: $('busy-copy'),
  dialog: $('result-dialog'), dialogClose: $('dialog-close'), dialogContent: $('dialog-content'), toast: $('toast'),
};

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = response.headers.get('content-type')?.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(typeof data === 'object' ? (data.detail || JSON.stringify(data)) : data);
  return data;
}

function toast(message) {
  els.toast.textContent = message; els.toast.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { els.toast.hidden = true; }, 2600);
}

function busy(show, title = '正在处理 PDF', copy = '首次加载模型可能需要一些时间，请保持页面开启。') {
  els.busy.hidden = !show; els.busyTitle.textContent = title; els.busyCopy.textContent = copy;
}

async function loadSources(selectId = null) {
  const data = await api('./api/sources'); state.sources = data.items;
  els.sourceSelect.innerHTML = '<option value="">请选择资料</option>' + state.sources.map(source =>
    `<option value="${source.source_file_id}">${escapeHtml(source.original_name)} · ${source.question_count || 0}题</option>`
  ).join('');
  if (selectId) els.sourceSelect.value = selectId;
  if (els.sourceSelect.value) await selectSource(els.sourceSelect.value);
}

async function selectSource(sourceId) {
  if (!sourceId) return;
  state.source = state.sources.find(item => item.source_file_id === sourceId);
  const data = await api(`./api/sources/${sourceId}/questions`); state.questions = data.items;
  els.pdfTitle.textContent = state.source.original_name; els.pageTotal.textContent = state.source.page_count;
  const layout = state.source.layout || {solution_mode:'inline', question_pages:[], solution_pages:[]};
  els.solutionMode.value = layout.solution_mode || 'inline';
  els.separateRanges.hidden = els.solutionMode.value !== 'separate';
  if (layout.question_pages?.length) {
    els.questionPageStart.value = Math.min(...layout.question_pages);
    els.questionPageEnd.value = Math.max(...layout.question_pages);
  }
  if (layout.solution_pages?.length) {
    els.solutionPageStart.value = Math.min(...layout.solution_pages);
    els.solutionPageEnd.value = Math.max(...layout.solution_pages);
  }
  els.editPageMarkdown.disabled = false;
  els.submit.disabled = !state.questions.length; state.currentIndex = state.questions.length ? 0 : -1;
  updateProgress();
  if (state.currentIndex >= 0) await loadQuestion(0); else emptyQuestion('没有识别到可校验题目');
}

function updateProgress() {
  const total = state.questions.length;
  const reviewed = state.questions.filter(q => ['reviewed', 'published'].includes(q.review_status)).length;
  const percent = total ? reviewed / total * 100 : 0;
  els.progressLabel.textContent = total ? `已审核 ${reviewed} 题，剩余 ${total - reviewed} 题` : '等待上传教辅 PDF';
  els.progressValue.textContent = `${reviewed} / ${total}`; els.progressBar.style.width = `${percent}%`;
}

async function loadQuestion(index) {
  if (index < 0 || index >= state.questions.length) return;
  if (state.dirty && !confirm('当前修改尚未保存，确定切换题目吗？')) return;
  state.currentIndex = index;
  const summary = state.questions[index];
  const data = await api(`./api/questions/${summary.question_id}`); state.question = data.question;
  state.source = data.source; state.page = state.question.page_number; state.dirty = false;
  els.editor.value = state.question.edited_markdown; updateQuestionChrome(); renderPdfPage(); schedulePreview(); showValidation(state.question.validation);
}

function updateQuestionChrome() {
  const q = state.question; if (!q) return;
  els.questionTitle.textContent = `第 ${q.original_number} 题 · PDF 第 ${q.page_number} 页`;
  els.questionIndex.textContent = `${state.currentIndex + 1} / ${state.questions.length}`;
  els.questionPrev.disabled = state.currentIndex <= 0; els.questionNext.disabled = state.currentIndex >= state.questions.length - 1;
  els.validate.disabled = false; els.save.disabled = false;
  els.publish.disabled = q.review_status !== 'reviewed';
  els.status.className = `status-badge ${q.review_status}`;
  els.status.textContent = ({pending:'待校验', in_review:'校验中', reviewed:'已审核', published:'已发布'})[q.review_status] || q.review_status;
  els.sourcePage.textContent = `来源页码 ${q.page_number}`; els.questionId.textContent = q.question_id;
  els.pageNumber.value = state.page; els.pageTotal.textContent = state.source.page_count;
}

function emptyQuestion(message) {
  state.question = null; els.questionTitle.textContent = message; els.editor.value = '';
  els.validate.disabled = els.save.disabled = els.publish.disabled = true;
}

function renderPdfPage() {
  if (!state.source) return;
  els.placeholder.hidden = true; els.pageCanvas.hidden = false; els.pdfStage.classList.remove('empty-state');
  els.pageNumber.value = state.page; els.zoomLabel.textContent = `${Math.round(state.zoom * 100)}%`;
  const renderScale = Math.min(3, Math.max(.8, 1.4 * state.zoom));
  els.pageImage.src = `./api/sources/${state.source.source_file_id}/pages/${state.page}?scale=${renderScale.toFixed(1)}`;
  els.pageImage.onload = positionHighlight;
}

function positionHighlight() {
  const box = state.question?.source_bbox;
  if (!box || state.page !== state.question.page_number) { els.highlight.hidden = true; return; }
  els.highlight.hidden = false;
  els.highlight.style.left = `${box.x / box.page_width * 100}%`;
  els.highlight.style.top = `${box.y / box.page_height * 100}%`;
  els.highlight.style.width = `${box.width / box.page_width * 100}%`;
  els.highlight.style.height = `${box.height / box.page_height * 100}%`;
  requestAnimationFrame(() => els.highlight.scrollIntoView({behavior:'smooth', block:'center'}));
}

function schedulePreview() {
  clearTimeout(state.previewTimer); state.previewTimer = setTimeout(renderPreview, 320);
}

async function renderPreview() {
  if (!state.question) return;
  try {
    const data = await api('./api/preview', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({markdown:els.editor.value})});
    els.preview.innerHTML = data.html;
    if (data.issues.length) showValidation({valid:false, issues:data.issues});
  } catch (error) { els.preview.textContent = `预览失败：${error.message}`; }
}

async function validateCurrent() {
  if (!state.question) return null;
  const data = await api(`./api/questions/${state.question.question_id}/validate`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({markdown:els.editor.value})});
  showValidation(data); return data;
}

function showValidation(validation) {
  if (!validation) {
    els.validation.className = 'validation-panel clean'; els.validationIcon.textContent = '○';
    els.validationSummary.textContent = '尚未执行完整校验'; els.validationIssues.innerHTML = ''; return;
  }
  els.validation.className = `validation-panel ${validation.valid ? 'clean' : 'error'}`;
  els.validationIcon.textContent = validation.valid ? '✓' : '!';
  els.validationSummary.textContent = validation.valid ? '格式与字段校验通过' : `发现 ${validation.issues.length} 个问题，提交已被阻止`;
  els.validationIssues.innerHTML = (validation.issues || []).map(issue =>
    `<button class="validation-issue" data-line="${issue.line || ''}">${escapeHtml(issue.field)}：${escapeHtml(issue.message)}${issue.line ? `（第${issue.line}行）` : ''}</button>`
  ).join('');
  els.validationIssues.querySelectorAll('button').forEach(button => button.addEventListener('click', () => jumpToLine(Number(button.dataset.line))));
}

function jumpToLine(line) {
  if (!line) return; switchTab('edit');
  const lines = els.editor.value.split('\n'); const start = lines.slice(0, line - 1).join('\n').length + (line > 1 ? 1 : 0);
  const end = start + (lines[line - 1] || '').length; els.editor.focus(); els.editor.setSelectionRange(start, end);
}

async function saveCurrent() {
  if (!state.question) return;
  const data = await api(`./api/questions/${state.question.question_id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({markdown:els.editor.value})});
  state.question = data.question; state.questions[state.currentIndex] = data.question; state.dirty = false;
  updateQuestionChrome(); showValidation(data.validation); updateProgress(); toast('当前题目草稿已保存');
}

async function loadDiff() {
  if (!state.question) return; els.diff.innerHTML = '<p>正在生成差异…</p>';
  els.diff.innerHTML = await api(`./api/questions/${state.question.question_id}/diff`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({markdown:els.editor.value})});
}

async function submitAll() {
  if (!state.source) return;
  if (state.dirty) await saveCurrent();
  busy(true, '正在校验并导入草稿题库', '每道题独立处理，单题失败不会回滚已成功题目。');
  try {
    const data = await api(`./api/sources/${state.source.source_file_id}/submit`, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    showResult(data); await loadSources(state.source.source_file_id);
  } catch (error) { toast(`提交失败：${error.message}`); }
  finally { busy(false); }
}

function showResult(data) {
  const failures = data.failures || [];
  els.dialogContent.innerHTML = `
    <div class="result-grid">
      <div class="result-card"><strong>${data.success_count}</strong><span>本次成功导入</span></div>
      <div class="result-card"><strong>${data.already_imported_count}</strong><span>已导入未重复写入</span></div>
      <div class="result-card"><strong>${data.failure_count}</strong><span>校验失败</span></div>
    </div>
    ${data.jsonl_path ? `<p><strong>JSONL：</strong><code>${escapeHtml(data.jsonl_path)}</code></p>` : ''}
    ${failures.length ? `<h3>失败题目</h3><ul class="failure-list">${failures.map(item => `<li><code>${item.question_id}</code>：${escapeHtml((item.reasons || []).join('；'))}</li>`).join('')}</ul>` : '<p>全部选中题目均已通过校验。</p>'}`;
  els.dialog.hidden = false;
}

async function publishCurrent() {
  if (!state.question || !confirm('确认将当前已审核草稿发布到正式题库吗？')) return;
  const data = await api('./api/publish', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question_ids:[state.question.question_id]})});
  if (data.published_count) { toast('题目已发布'); await loadQuestion(state.currentIndex); }
  else toast(data.failures[0]?.reason || '发布失败');
}

async function uploadPdf(file) {
  if (!file) return;
  const form = new FormData(); form.append('file', file);
  form.append('solution_mode', els.solutionMode.value);
  form.append('ocr_mode', els.ocrMode.value);
  if (els.solutionMode.value === 'separate') {
    const fields = [els.questionPageStart, els.questionPageEnd, els.solutionPageStart, els.solutionPageEnd];
    if (fields.some(input => !input.value || Number(input.value) < 1)) {
      toast('套卷模式请完整填写题目页和答案页范围'); return;
    }
    form.append('question_page_start', els.questionPageStart.value);
    form.append('question_page_end', els.questionPageEnd.value);
    form.append('solution_page_start', els.solutionPageStart.value);
    form.append('solution_page_end', els.solutionPageEnd.value);
  }
  busy(true);
  try {
    const data = await api('./api/sources', {method:'POST', body:form});
    const unmatched = data.import_diagnostics?.unmatched_solutions?.length || 0;
    toast(`OCR完成，识别 ${data.question_count} 道题${unmatched ? `；${unmatched} 条答案未匹配，请核对` : ''}`); await loadSources(data.source.source_file_id);
  } catch (error) { toast(error.message); }
  finally { busy(false); els.pdfInput.value = ''; }
}

async function editAndResplitCurrentPage() {
  if (!state.source) return;
  try {
    const page = await api(`./api/sources/${state.source.source_file_id}/pages/${state.page}/markdown`);
    const edited = window.prompt(`编辑第 ${state.page} 页 OCR Markdown。确认后先显示重建预览。`, page.edited_markdown);
    if (edited === null || edited === page.edited_markdown) return;
    const url = `./api/sources/${state.source.source_file_id}/pages/${state.page}/resplit`;
    const preview = await api(`${url}/preview`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({markdown:edited})});
    const changes = preview.changes;
    const warning = preview.blocked ? '\n存在已发布题目变更，不能应用。' : '';
    if (!confirm(`预览：新增 ${changes.added.length}，移除 ${changes.removed.length}，保留 ${changes.kept.length}。${warning}`) || preview.blocked) return;
    await api(`${url}/apply`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({markdown:edited, expected_numbers:preview.new_numbers})});
    toast('当前页已重新识别并完成题目/答案匹配');
    await loadSources(state.source.source_file_id);
  } catch (error) { toast(`重新识别失败：${error.message}`); }
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(tab => tab.classList.toggle('active', tab.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(content => content.classList.toggle('active', content.id === `tab-${name}`));
  if (name === 'preview') renderPreview(); if (name === 'diff') loadDiff();
}

function escapeHtml(value) { return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char])); }

els.upload.addEventListener('click', () => els.pdfInput.click()); els.placeholderUpload.addEventListener('click', () => els.pdfInput.click());
els.solutionMode.addEventListener('change', () => { els.separateRanges.hidden = els.solutionMode.value !== 'separate'; });
els.pdfInput.addEventListener('change', () => uploadPdf(els.pdfInput.files[0]));
els.sourceSelect.addEventListener('change', () => selectSource(els.sourceSelect.value));
els.questionPrev.addEventListener('click', () => loadQuestion(state.currentIndex - 1)); els.questionNext.addEventListener('click', () => loadQuestion(state.currentIndex + 1));
els.pagePrev.addEventListener('click', () => { if (state.page > 1) { state.page--; renderPdfPage(); } });
els.pageNext.addEventListener('click', () => { if (state.source && state.page < state.source.page_count) { state.page++; renderPdfPage(); } });
els.pageNumber.addEventListener('change', () => { state.page = Math.max(1, Math.min(Number(els.pageNumber.value), state.source?.page_count || 1)); renderPdfPage(); });
els.editPageMarkdown.addEventListener('click', editAndResplitCurrentPage);
els.zoomOut.addEventListener('click', () => { state.zoom = Math.max(.6, state.zoom - .2); renderPdfPage(); });
els.zoomIn.addEventListener('click', () => { state.zoom = Math.min(2.2, state.zoom + .2); renderPdfPage(); });
els.editor.addEventListener('input', () => { state.dirty = true; schedulePreview(); });
els.validate.addEventListener('click', validateCurrent); els.save.addEventListener('click', saveCurrent); els.submit.addEventListener('click', submitAll); els.publish.addEventListener('click', publishCurrent);
document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => switchTab(tab.dataset.tab)));
els.dialogClose.addEventListener('click', () => { els.dialog.hidden = true; });
window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });

loadSources().catch(error => toast(`初始化失败：${error.message}`));
