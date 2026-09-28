// js/views/learning-analysis.js
// 学情报告：表单 → 流式生成（进度显示）→ 报告 → 继续调整（多轮记忆）→ 历史回看 → 一键备课

import { api, streamSSE } from '../api.js';
import { escapeHtml, fmt, percent, uuid, fmtTime } from '../util.js';
import { navigate } from '../router.js';

let rendered = false;
let savedReportId = '';
let lastSelection = null;  // 记住上次参数（多轮调整用）

const $ = (id) => document.getElementById(id);
const val = (id) => $(id).value.trim();

const GRADES = ['一年级', '二年级', '三年级', '四年级', '五年级', '六年级', '七年级', '八年级', '九年级', '高一', '高二', '高三'];
const CLASS_BY_GRADE = {
  '六年级': [{ id: 'c1a55e00-0000-4000-8000-000000000001', name: '4班' }],
};
// 科目按年级联动（与备课对齐）：小学（1-6）只有语数外；初中/高中额外加政史地物化生
const PRIMARY_SUBJECTS = ['数学', '语文', '英语'];
const SECONDARY_SUBJECTS = ['数学', '语文', '英语', '物理', '化学', '生物', '政治', '历史', '地理'];
const subjectsForGrade = (g) => (/^[一二三四五六]年级$/.test(g) ? PRIMARY_SUBJECTS : SECONDARY_SUBJECTS);

export function renderLearningAnalysis(container) {
  if (rendered) return;
  rendered = true;
  container.innerHTML = `
    <div class="card">
      <div class="card-title">📊 生成学情报告</div>
      <form id="la-form" class="form-grid">
        <label class="field"><span class="field-label">年级</span>
          <select id="la-grade">${GRADES.map((g) => `<option ${g === '六年级' ? 'selected' : ''}>${g}</option>`).join('')}</select>
        </label>
        <label class="field"><span class="field-label">班级</span>
          <select id="la-class"></select>
        </label>
        <label class="field"><span class="field-label">科目</span>
          <select id="la-subject"></select>
        </label>
        <div class="field full"><span class="field-label">作答记录</span>
          <div class="radio-row">
            <label class="radio-item"><input type="radio" name="la-source" value="practice" checked> 日常练习</label>
            <label class="radio-item"><input type="radio" name="la-source" value="exam"> 考试批改</label>
          </div>
        </div>
        <label class="field full" id="la-range-wrap"><span class="field-label">时间范围（可选，留空 = 全部）</span>
          <div style="display:flex;gap:8px;align-items:center">
            <input type="date" id="la-start"> <span style="color:var(--text-3)">~</span> <input type="date" id="la-end">
          </div>
        </label>
        <div class="field full hidden" id="la-exam-wrap"><span class="field-label">考试批次</span>
          <div id="la-exam-list"></div>
        </div>
        <label class="field full"><span class="field-label">知识点范围（可选）</span><input id="la-scope" placeholder="如：分数乘除 / 第一单元（留空 = 不限）"></label>
        <label class="field full"><span class="field-label">教师补充需求（可选）</span><textarea id="la-req" placeholder="如：重点关注分数除法的掌握情况"></textarea></label>
        <div class="form-actions full">
          <p class="la-hint" id="la-hint"></p>
          <button type="submit" class="btn btn-primary" id="la-gen">生成报告</button>
        </div>
      </form>
    </div>
    <div class="card">
      <div class="card-title">🕘 报告历史</div>
      <div id="la-reports"></div>
    </div>
    <div id="la-result"></div>
  `;

  $('la-form').addEventListener('submit', (e) => { e.preventDefault(); generate(); });
  $('la-grade').addEventListener('change', () => { renderClassOptions(); renderSubjectOptions(); loadExams(); checkData(); });
  $('la-class').addEventListener('change', checkData);
  $('la-subject').addEventListener('change', () => { loadExams(); checkData(); });
  document.querySelectorAll('input[name="la-source"]').forEach((r) =>
    r.addEventListener('change', () => { toggleSourceUI(); checkData(); }));
  $('la-start').addEventListener('change', checkData);
  $('la-end').addEventListener('change', checkData);

  renderClassOptions();
  renderSubjectOptions();
  toggleSourceUI();
  loadExams();
  loadReports();
  checkData();
}

// ── 表单辅助 ────────────────────────────────────────────────

function renderClassOptions() {
  const grade = val('la-grade');
  const classes = CLASS_BY_GRADE[grade] || [];
  $('la-class').innerHTML = classes.length
    ? classes.map((c) => `<option value="${c.id}">${c.name}</option>`).join('')
    : '<option value="">（该年级暂无班级）</option>';
}

// 年级切换时联动刷新科目下拉（保留当前选中，若不在新列表则回退「数学」）
function renderSubjectOptions() {
  const subjects = subjectsForGrade(val('la-grade'));
  const current = $('la-subject').value;
  const keep = subjects.includes(current) ? current : '数学';
  $('la-subject').innerHTML = subjects.map((s) => `<option ${s === keep ? 'selected' : ''}>${s}</option>`).join('');
}

function currentSource() {
  return document.querySelector('input[name="la-source"]:checked').value;
}

function toggleSourceUI() {
  const isExam = currentSource() === 'exam';
  $('la-range-wrap').classList.toggle('hidden', isExam);
  $('la-exam-wrap').classList.toggle('hidden', !isExam);
}

async function loadExams() {
  try {
    const res = await api.get(`/api/v1/learning-analysis/exams?subject=${encodeURIComponent(val('la-subject'))}`);
    const items = res.items || [];
    const el = $('la-exam-list');
    if (!items.length) { el.innerHTML = '<p class="empty">暂无考试批次</p>'; return; }
    el.innerHTML = items.map((it, i) => `
      <label class="exam-item"><input type="checkbox" class="la-exam-check" value="${escapeHtml(it.exam_id)}" ${i === 0 ? 'checked' : ''}> ${escapeHtml(it.title)}</label>`).join('');
    el.querySelectorAll('.la-exam-check').forEach((c) => c.addEventListener('change', checkData));
  } catch (err) {
    $('la-exam-list').innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

function currentSelection() {
  return {
    class_id: val('la-class'),
    subject: val('la-subject'),
    grade: val('la-grade'),
    data_source: currentSource(),
    time_range: { start: val('la-start'), end: val('la-end') },
    exam_ids: Array.from(document.querySelectorAll('.la-exam-check:checked')).map((c) => c.value),
    knowledge_scope: val('la-scope'),
  };
}

async function checkData() {
  const sel = currentSelection();
  const genBtn = $('la-gen');
  const hint = $('la-hint');
  if (!sel.class_id) { genBtn.disabled = true; hint.textContent = ''; return; }
  try {
    const res = await api.post('/api/v1/learning-analysis/check-data', sel);
    if (res.has_data) {
      genBtn.disabled = false;
      hint.textContent = '';
    } else {
      genBtn.disabled = true;
      const className = ($('la-class').selectedOptions[0] || {}).textContent || '';
      const srcLabel = sel.data_source === 'exam' ? '考试' : '练习';
      hint.textContent = `数据库暂无${sel.grade}${className}的${sel.subject}${srcLabel}数据`;
    }
  } catch (err) {
    genBtn.disabled = true;
    hint.textContent = err.message || '';
  }
}

// ── 流式生成 ────────────────────────────────────────────────

function buildBody(sel, teacherRequirement) {
  return {
    session_id: uuid(),
    class_id: sel.class_id,
    subject: sel.subject,
    grade: sel.grade,
    data_source: sel.data_source,
    time_range: sel.time_range,
    exam_ids: sel.exam_ids,
    knowledge_scope: sel.knowledge_scope,
    teacher_requirement: teacherRequirement || '',
  };
}

async function generate(teacherRequirement) {
  const sel = teacherRequirement ? lastSelection : currentSelection();
  if (!sel) return;
  if (!teacherRequirement) lastSelection = sel;

  $('la-result').innerHTML = `<div class="card"><div id="la-progress"></div></div>`;
  const progressEl = $('la-progress');
  const doneStages = [];
  const body = buildBody(sel, teacherRequirement);

  const renderProgress = (stage) => {
    progressEl.innerHTML =
      doneStages.map((s) => `<div class="progress-line" style="color:var(--success)">✓ ${escapeHtml(s)}</div>`).join('') +
      `<div class="loading"><span class="spinner"></span>${escapeHtml(stage)}…</div>`;
  };

  try {
    await streamSSE('/api/v1/learning-analysis/generate/stream', body, (evt) => {
      if (evt.type === 'progress') {
        doneStages.push(evt.stage);
        renderProgress(evt.stage);
      } else if (evt.type === 'report') {
        savedReportId = evt.saved_report_id || '';
        renderReport(evt.report_content || {}, evt.validation || {});
      } else if (evt.type === 'error') {
        $('la-result').innerHTML = `<div class="error-box">${escapeHtml(evt.message)}</div>`;
      }
    });
    loadReports();
  } catch (err) {
    $('la-result').innerHTML = `<div class="error-box">${escapeHtml(err.message || '生成失败')}</div>`;
  }
}

// 典型错题按知识点合并：同知识点多条错因 → 合并为一条，错因去重后用「、」连接
function mergeErrors(errors) {
  const map = new Map();
  for (const e of errors) {
    const key = e.kp_name || e.kp_id || '未知知识点';
    if (!map.has(key)) {
      map.set(key, { kp_name: key, error_types: [], error_count: 0, rate_sum: 0, rate_w: 0 });
    }
    const g = map.get(key);
    if (e.error_type && !g.error_types.includes(e.error_type)) g.error_types.push(e.error_type);
    const w = e.error_count || 1;
    g.error_count += e.error_count || 0;
    if (e.error_rate != null) { g.rate_sum += e.error_rate * w; g.rate_w += w; }
  }
  return [...map.values()].map((g) => ({
    kp_name: g.kp_name,
    error_type: g.error_types.join('、'),
    error_rate: g.rate_w ? g.rate_sum / g.rate_w : 0,
    error_count: g.error_count,
  }));
}

function renderReport(rc, validation) {
  const meta = rc.meta || {};
  const ov = rc.overview || {};
  const mastery = rc.mastery || [];
  const strat = rc.stratification || [];
  const errors = mergeErrors(rc.errors || []);
  const teaching = (rc.suggestions || {}).teaching || {};
  const weakKps = (rc.suggestions || {}).weak_kps || [];

  const stat = (num, label) => `
    <div class="stat-card"><div class="stat-num">${num ?? '—'}</div><div class="stat-label">${label}</div></div>`;

  const valBad = validation && validation.passed === false
    ? `<div class="error-box">⚠️ 质检未通过，报告未落库：${escapeHtml((validation.issues || []).join('；'))}</div>`
    : '';

  $('la-result').innerHTML = `
    <div class="card">
      <div class="plan-meta">
        <span class="tag">${escapeHtml(meta.subject || '—')}</span>
        <span class="tag">${escapeHtml(meta.grade || '—')}</span>
        <span class="tag">${escapeHtml(meta.report_type === 'student' ? '个人报告' : '班级报告')}</span>
        ${meta.generated_at ? `<span class="tag">${escapeHtml(fmtTime(meta.generated_at))}</span>` : ''}
      </div>
      ${valBad}

      <div class="stat-grid">
        ${stat(ov.student_count, '班级学生')}
        ${stat(ov.total_records, '答题记录')}
        ${stat(ov.practice_records, '练习记录')}
        ${stat(ov.exam_records, '考试记录')}
      </div>

      <div class="section"><h3>知识点掌握度分布</h3>
        ${mastery.length ? mastery.map((m) => `
          <div class="mastery-bar">
            <div class="mb-name">${escapeHtml(m.kp_name)}</div>
            <div class="mb-track"><div class="mb-fill" style="width:${Math.max(2, Math.round(m.mastery_score * 100))}%"></div></div>
            <div class="mb-val">${percent(m.mastery_score)}</div>
          </div>`).join('') : '<p class="empty">暂无掌握度数据</p>'}
      </div>

      <div class="section"><h3>学生分层画像</h3>
        ${strat.length ? '<div class="exercise-grid">' + strat.map((s) => `
          <div class="exercise-card">
            <div class="ex-head">${escapeHtml(s.level_name)} · ${s.count ?? 0} 人</div>
            ${s.profile?.avg_rate != null ? `<p class="ps-act">平均掌握度 <b>${percent(s.profile.avg_rate)}</b></p>` : ''}
            ${s.profile?.weak_kps?.length ? `<p class="ps-act">薄弱：${escapeHtml(s.profile.weak_kps.join('、'))}</p>` : ''}
          </div>`).join('') + '</div>' : '<p class="empty">暂无分层数据</p>'}
      </div>

      <div class="section"><h3>典型错题</h3>
        ${errors.length ? `<div class="table-wrap"><table class="tbl">
          <thead><tr><th>知识点</th><th>错因</th><th>错误率</th><th>错误数</th></tr></thead>
          <tbody>${errors.map((e) => `<tr>
            <td>${escapeHtml(e.kp_name || '—')}</td>
            <td>${escapeHtml(e.error_type || '—')}</td>
            <td>${percent(e.error_rate)}</td>
            <td>${e.error_count ?? '—'}</td>
          </tr>`).join('')}</tbody>
        </table></div>` : '<p class="empty">暂无错题数据</p>'}
      </div>

      <div class="section"><h3>教学建议</h3>
        <p><b>分层建议</b>：${fmt(teaching['分层建议']) || '—'}</p>
        <p><b>错题补救</b>：${fmt(teaching['错题补救']) || '—'}</p>
        <p><b>后续教学计划</b>：${fmt(teaching['后续教学计划']) || '—'}</p>
      </div>

      <div class="section"><h3>薄弱知识点</h3>
        <p class="form-hint" style="margin-bottom:10px">勾选要纳入备课的知识点，一次生成一份覆盖所选知识点的复习教案</p>
        ${weakKps.length ? '<div class="kp-list">' + weakKps.map((w, i) => `
          <label class="kp-card low kp-selectable">
            <input type="checkbox" class="kp-check" value="${escapeHtml(w.kp_id || '')}" data-name="${escapeHtml(w.kp_name || '')}" ${i === 0 ? 'checked' : ''}>
            <div class="kp-name">${escapeHtml(w.kp_name)}</div>
            <div class="kp-score">掌握度 ${percent(w.mastery_score)}</div>
          </label>`).join('') + '</div>' : '<p class="empty">无薄弱知识点</p>'}
        ${weakKps.length ? `<div class="form-actions">
          <button class="btn btn-primary" id="la-gen-lesson">批量备课（生成一份教案）</button>
          <span class="form-hint" id="la-gen-hint"></span>
        </div>` : ''}
      </div>
    </div>
    <div id="la-lesson"></div>
    <div class="card">
      <div class="card-title">💬 继续调整</div>
      <div class="chat-input">
        <textarea id="la-adjust" rows="1" placeholder="如：改成最近一周 / 重点看分数除法 / 换成考试批改数据"></textarea>
        <button class="btn btn-primary" id="la-adjust-btn">调整</button>
      </div>
    </div>
  `;

  const genBtn = $('la-gen-lesson');
  if (genBtn) genBtn.addEventListener('click', () => {
    const checked = Array.from(document.querySelectorAll('.kp-check:checked'));
    if (!checked.length) { $('la-gen-hint').textContent = '请至少勾选一个知识点'; return; }
    const topic = checked.map((c) => c.dataset.name).filter(Boolean).join('、');
    const subject = meta.subject || '数学';
    const grade = meta.grade || '六年级';
    navigate(`/lesson-prep?subject=${encodeURIComponent(subject)}&grade=${encodeURIComponent(grade)}&topic=${encodeURIComponent(topic)}`);
  });

  $('la-adjust-btn').addEventListener('click', () => {
    const text = $('la-adjust').value.trim();
    if (!text) return;
    generate(text);
  });
  $('la-adjust').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('la-adjust-btn').click(); }
  });
}

// ── 报告历史 ────────────────────────────────────────────────

async function loadReports() {
  const el = $('la-reports');
  try {
    const res = await api.get('/api/v1/learning-analysis/reports');
    const items = res.items || [];
    if (!items.length) { el.innerHTML = '<p class="empty">暂无历史报告</p>'; return; }
    el.innerHTML = `<div class="table-wrap"><table class="tbl">
      <thead><tr><th>科目</th><th>年级</th><th>状态</th><th>生成时间</th><th>操作</th></tr></thead>
      <tbody>${items.map((it) => `
        <tr class="clickable" data-rid="${escapeHtml(it.report_id)}">
          <td>${escapeHtml(it.subject)}</td>
          <td>${escapeHtml(it.grade)}</td>
          <td><span class="status-pill ${escapeHtml(it.status)}">${escapeHtml(it.status)}</span></td>
          <td>${escapeHtml(fmtTime(it.created_at))}</td>
          <td><button class="btn btn-ghost btn-sm danger" data-del="${escapeHtml(it.report_id)}">删除</button></td>
        </tr>`).join('')}
      </tbody></table></div>`;
    el.querySelectorAll('tr.clickable').forEach((tr) =>
      tr.addEventListener('click', () => viewReport(tr.dataset.rid)));
    el.querySelectorAll('button[data-del]').forEach((btn) =>
      btn.addEventListener('click', (e) => { e.stopPropagation(); deleteReport(btn.dataset.del); }));
  } catch (err) {
    el.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

async function deleteReport(reportId) {
  if (!confirm('确定删除这份报告吗？删除后不可恢复。')) return;
  try {
    await api.del(`/api/v1/learning-analysis/report/${reportId}`);
    loadReports();
  } catch (err) {
    alert(err.message || '删除失败');
  }
}

async function viewReport(reportId) {
  $('la-result').innerHTML = `<div class="card"><div class="loading"><span class="spinner"></span>加载历史报告…</div></div>`;
  try {
    const res = await api.get(`/api/v1/learning-analysis/report/${reportId}`);
    savedReportId = reportId;  // 回看后「一键备课」用当前报告
    renderReport(res.report_content || {}, { passed: true });
  } catch (err) {
    $('la-result').innerHTML = `<div class="error-box">${escapeHtml(err.message || '加载失败')}</div>`;
  }
}

// ── 一键备课（seam①）：勾选薄弱知识点 → 跳转「智能备课」页预填（见 renderReport 的 la-gen-lesson 回调）
