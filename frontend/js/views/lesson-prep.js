// js/views/lesson-prep.js
// 智能备课：表单 → 生成教案 → 教师审阅（满意保存 / 提意见回炉）+ 我的教案列表

import { api, streamSSE } from '../api.js';
import { escapeHtml, fmt, uuid, fmtTime } from '../util.js';
import { navigate } from '../router.js';

let rendered = false;
let sessionId = localStorage.getItem('lp_session_id') || '';
let currentPlan = null;
let currentStatus = '';
let savedLessonId = '';

const $ = (id) => document.getElementById(id);
const val = (id) => $(id).value.trim();

const LESSON_TYPE_LABEL = { new: '新授课', exercise: '习题课', review: '复习课' };

// 年级 → 科目联动：小学（1-6）只有语数外；初中/高中额外加政史地物化生
const GRADES = ['一年级', '二年级', '三年级', '四年级', '五年级', '六年级', '七年级', '八年级', '九年级', '高一', '高二', '高三'];
const PRIMARY_SUBJECTS = ['数学', '语文', '英语'];
const SECONDARY_SUBJECTS = ['数学', '语文', '英语', '物理', '化学', '生物', '政治', '历史', '地理'];
const subjectsForGrade = (g) => (/^[一二三四五六]年级$/.test(g) ? PRIMARY_SUBJECTS : SECONDARY_SUBJECTS);

export function renderLessonPrep(container) {
  if (rendered) { applyPrefill(); return; }
  rendered = true;
  container.innerHTML = `
    <div class="card">
      <div class="card-title">📚 生成教案</div>
      <form id="lp-form" class="form-grid">
        <label class="field"><span class="field-label">年级</span>
          <select id="lp-grade">${GRADES.map((g) => `<option ${g === '六年级' ? 'selected' : ''}>${g}</option>`).join('')}</select>
        </label>
        <label class="field"><span class="field-label">科目</span>
          <select id="lp-subject"></select>
        </label>
        <label class="field full"><span class="field-label">章节 / 知识点主题</span><input id="lp-topic" value="圆的周长与面积" placeholder="如：圆的周长与面积"></label>
        <label class="field"><span class="field-label">课型</span>
          <select id="lp-type">
            <option value="new">新授课</option>
            <option value="exercise">习题课</option>
            <option value="review">复习课</option>
          </select>
        </label>
        <label class="field"><span class="field-label">课时（分钟）</span><input id="lp-duration" type="number" value="40" min="1" max="180"></label>
        <label class="field full"><span class="field-label">教师补充需求（可选）</span><textarea id="lp-req" placeholder="如：重点讲解公式推导，增加互动环节"></textarea></label>
        <div class="form-actions full"><button type="submit" class="btn btn-primary">生成教案</button></div>
      </form>
    </div>
    <div id="lp-result"></div>
    <div class="card">
      <div class="card-title">📁 我的教案</div>
      <div id="lp-plans-list"></div>
      <div id="lp-plan-detail"></div>
    </div>
  `;
  $('lp-form').addEventListener('submit', (e) => { e.preventDefault(); generate(); });
  $('lp-grade').addEventListener('change', renderSubjectOptions);
  renderSubjectOptions();
  loadMyPlans();
  applyPrefill();
}

function getQueryParam(key) {
  const hash = location.hash || '';
  const m = hash.match(new RegExp(`[?&]${key}=([^&]*)`));
  return m ? decodeURIComponent(m[1]) : '';
}

// 从 URL 预填表单（学情报告「一键备课」跳转过来：subject/grade/topic）
function applyPrefill() {
  const grade = getQueryParam('grade');
  const subject = getQueryParam('subject');
  const topic = getQueryParam('topic');
  if (!grade && !subject && !topic) return;
  if (grade) {
    $('lp-grade').value = grade;
    renderSubjectOptions();  // 年级变了，联动刷新科目下拉
  }
  if (subject) $('lp-subject').value = subject;
  if (topic) $('lp-topic').value = topic;
}

// 年级切换时联动刷新科目下拉（保留当前选中，若不在新列表则回退「数学」）
function renderSubjectOptions() {
  const subjects = subjectsForGrade(val('lp-grade'));
  const current = $('lp-subject').value;
  const keep = subjects.includes(current) ? current : '数学';
  $('lp-subject').innerHTML = subjects.map((s) => `<option ${s === keep ? 'selected' : ''}>${s}</option>`).join('');
}

async function generate() {
  $('lp-result').innerHTML = `<div class="card"><div id="lp-progress"></div></div>`;
  sessionId = uuid();
  localStorage.setItem('lp_session_id', sessionId);
  const body = {
    session_id: sessionId,
    subject: val('lp-subject'),
    grade: val('lp-grade'),
    topic: val('lp-topic'),
    lesson_type: val('lp-type'),
    duration: Number(val('lp-duration')) || 40,
    teacher_requirement: val('lp-req'),
  };
  const progressEl = $('lp-progress');
  const doneStages = [];
  const renderProgress = (stage) => {
    progressEl.innerHTML =
      doneStages.map((s) => `<div class="progress-line" style="color:var(--success)">✓ ${escapeHtml(s)}</div>`).join('') +
      `<div class="loading"><span class="spinner"></span>${escapeHtml(stage)}…</div>`;
  };
  try {
    await streamSSE('/api/v1/lesson-prep/generate/stream', body, (evt) => {
      if (evt.type === 'progress') {
        doneStages.push(evt.stage);
        renderProgress(evt.stage);
      } else if (evt.type === 'plan') {
        currentPlan = evt.final_lesson_plan || {};
        currentStatus = evt.status || 'pending_review';
        renderPlan();
      } else if (evt.type === 'error') {
        $('lp-result').innerHTML = `<div class="error-box">${escapeHtml(evt.message)}</div>`;
      }
    });
  } catch (err) {
    $('lp-result').innerHTML = `<div class="error-box">${escapeHtml(err.message || '生成失败')}</div>`;
  }
}

// 习题列表渲染
function exerciseList(list) {
  return list.length
    ? '<div class="exercise-grid">' + list.map((q) => `
        <div class="exercise-card">
          <div class="ex-head">${q.question_type ? '[' + escapeHtml(q.question_type) + '] ' : ''}${escapeHtml(q.content || '（题面缺失）')}</div>
          ${q.options ? `<p class="ps-act">选项：${fmt(q.options)}</p>` : ''}
          <p class="ps-act" style="color:var(--success)"><b>答案</b>：${escapeHtml(q.answer || '—')}</p>
          ${q.analysis ? `<p class="ps-act"><b>解析</b>：${escapeHtml(q.analysis)}</p>` : ''}
          ${q.quality_status === 'needs_review' ? '<p class="status-pill failed">待复核</p>' : ''}
        </div>`).join('') + '</div>'
    : '<p class="empty">暂无习题</p>';
}

// 教案 HTML（withReview 控制是否显示教师审阅区）
function planHtml(p, withReview) {
  const meta = p.meta || {};
  const obj = p.objectives || {};
  const proc = p.process || {};
  const steps = proc.steps || [];
  const ex = p.exercises || {};
  const classEx = ex.class || [];
  const homeworkEx = ex.homework || [];
  const board = p.board || {};

  const review = withReview
    ? `<div class="section">
        <h3>教师审阅</h3>
        <p style="margin-bottom:12px">教案已生成并暂停，等待您审阅。可保存，或提出修改意见让 AI 回炉重跑。</p>
        <div class="form-actions">
          <button class="btn btn-success" id="lp-ok">✓ 满意，保存教案</button>
          <button class="btn btn-ghost" id="lp-fb-btn">✎ 提修改意见</button>
        </div>
        <div id="lp-fb-box" class="hidden" style="margin-top:12px">
          <textarea id="lp-feedback" placeholder="如：增加一个课堂练习环节，板书更精简"></textarea>
          <div class="form-actions"><button class="btn btn-primary" id="lp-resubmit">提交意见，重新生成</button></div>
        </div>
      </div>`
    : `<div class="form-actions">
        <span class="status-pill saved">✓ 已保存教案</span>
        <button class="btn btn-primary btn-sm" id="lp-hw-btn">📝 生成作业</button>
      </div>`;

  return `
    <div class="card">
      <div class="plan-meta">
        <span class="tag">${escapeHtml(meta.subject || '—')}</span>
        <span class="tag">${escapeHtml(meta.grade || '—')}</span>
        <span class="tag">${escapeHtml(meta.topic || '—')}</span>
        <span class="tag">${LESSON_TYPE_LABEL[meta.lesson_type] || meta.lesson_type || ''}</span>
        <span class="tag">${escapeHtml(meta.duration ?? '')} 分钟</span>
      </div>

      <div class="section"><h3>教学目标</h3>
        <p><b>知识与技能</b>：${fmt(obj.knowledge_skills) || '—'}</p>
        <p><b>过程与方法</b>：${fmt(obj.process_methods) || '—'}</p>
        <p><b>情感态度与价值观</b>：${fmt(obj.emotion_values) || '—'}</p>
      </div>

      <div class="section"><h3>重点难点</h3>
        <p><b>教学重点</b>：${fmt(p.key_points) || '—'}</p>
        <p><b>教学难点</b>：${fmt(p.difficult_points) || '—'}</p>
      </div>

      <div class="section"><h3>教学流程</h3>
        ${steps.length ? steps.map((s) => `
          <div class="process-step">
            <div class="ps-time">${escapeHtml(s.duration_min ?? '')}′</div>
            <div>
              <div class="ps-name">${escapeHtml(s.name || '')}</div>
              ${s.teacher_activity ? `<div class="ps-act"><b>教师</b>：${escapeHtml(s.teacher_activity)}</div>` : ''}
              ${s.student_activity ? `<div class="ps-act"><b>学生</b>：${escapeHtml(s.student_activity)}</div>` : ''}
              ${s.design_intent ? `<div class="ps-act" style="color:var(--text-3)">意图：${escapeHtml(s.design_intent)}</div>` : ''}
            </div>
          </div>`).join('') : '<p class="empty">暂无教学流程</p>'}
      </div>

      <div class="section"><h3>板书设计</h3>
        ${board.main ? `<p><b>主板书</b>：${escapeHtml(board.main)}</p>` : ''}
        ${board.secondary ? `<p><b>副板书</b>：${escapeHtml(board.secondary)}</p>` : ''}
        ${!board.main && !board.secondary ? '<p class="empty">暂无板书</p>' : ''}
      </div>

      <div class="section"><h3>课堂习题</h3>${exerciseList(classEx)}</div>
      <div class="section"><h3>课后作业</h3>${exerciseList(homeworkEx)}</div>

      ${review}
    </div>
  `;
}

function renderPlan() {
  $('lp-result').innerHTML = planHtml(currentPlan || {}, currentStatus === 'pending_review');
  const okBtn = $('lp-ok');
  if (okBtn) okBtn.addEventListener('click', () => confirm(''));
  const fbBtn = $('lp-fb-btn');
  if (fbBtn) fbBtn.addEventListener('click', () => $('lp-fb-box').classList.remove('hidden'));
  const reBtn = $('lp-resubmit');
  if (reBtn) reBtn.addEventListener('click', () => confirm($('lp-feedback').value.trim()));
  const hwBtn = $('lp-hw-btn');
  if (hwBtn) hwBtn.addEventListener('click', () => goHomework(savedLessonId));
}

async function confirm(feedback) {
  sessionId = localStorage.getItem('lp_session_id') || sessionId;
  const okBtn = $('lp-ok'), fbBtn = $('lp-fb-btn'), reBtn = $('lp-resubmit');
  [okBtn, fbBtn, reBtn].forEach((b) => b && (b.disabled = true));
  try {
    const res = await api.post('/api/v1/lesson-prep/confirm', { session_id: sessionId, feedback });
    if (res.status === 'saved') {
      currentStatus = 'saved';
      savedLessonId = res.saved_lesson_id || '';
      currentPlan = res.final_lesson_plan || currentPlan;
      renderPlan();
      loadMyPlans();  // 刷新「我的教案」列表
    } else {
      currentStatus = 'pending_review';
      currentPlan = res.final_lesson_plan || {};
      renderPlan();
    }
  } catch (err) {
    const box = $('lp-result');
    box.insertAdjacentHTML('beforeend', `<div class="error-box">${escapeHtml(err.message || '操作失败')}</div>`);
  }
}

async function loadMyPlans() {
  const listEl = $('lp-plans-list');
  try {
    const res = await api.get('/api/v1/lesson-prep/plans');
    const items = res.items || [];
    if (!items.length) { listEl.innerHTML = '<p class="empty">暂无保存的教案，生成并保存后会出现在这里</p>'; return; }
    listEl.innerHTML = `<div class="table-wrap"><table class="tbl">
      <thead><tr><th>主题</th><th>科目</th><th>年级</th><th>状态</th><th>保存时间</th><th>操作</th></tr></thead>
      <tbody>${items.map((it) => `
        <tr class="clickable" data-lid="${escapeHtml(it.lesson_id)}">
          <td>${escapeHtml(it.topic)}</td>
          <td>${escapeHtml(it.subject)}</td>
          <td>${escapeHtml(it.grade)}</td>
          <td><span class="status-pill ${escapeHtml(it.status)}">${escapeHtml(it.status)}</span></td>
          <td>${escapeHtml(fmtTime(it.created_at))}</td>
          <td>
            <button class="btn btn-ghost btn-sm" data-hw="${escapeHtml(it.lesson_id)}">生成作业</button>
            <button class="btn btn-ghost btn-sm danger" data-del="${escapeHtml(it.lesson_id)}">删除</button>
          </td>
        </tr>`).join('')}
      </tbody></table></div>`;
    listEl.querySelectorAll('tr.clickable').forEach((tr) =>
      tr.addEventListener('click', () => viewPlan(tr.dataset.lid)));
    listEl.querySelectorAll('button[data-del]').forEach((btn) =>
      btn.addEventListener('click', (e) => { e.stopPropagation(); deletePlan(btn.dataset.del); }));
    listEl.querySelectorAll('button[data-hw]').forEach((btn) =>
      btn.addEventListener('click', (e) => { e.stopPropagation(); goHomework(btn.dataset.hw); }));
  } catch (err) {
    listEl.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

async function deletePlan(lessonId) {
  if (!window.confirm('确定删除这份教案吗？删除后不可恢复。')) return;
  try {
    await api.del(`/api/v1/lesson-prep/plans/${lessonId}`);
    $('lp-plan-detail').innerHTML = '';
    loadMyPlans();
    alert('删除成功');
  } catch (err) {
    alert(err.message || '删除失败');
  }
}

async function goHomework(lessonId) {
  if (!lessonId) return;
  try {
    const res = await api.get(`/api/v1/lesson-prep/plans/${lessonId}`);
    const kpIds = (res.kp_ids || []).join(',');
    navigate(`/homework?lesson_id=${encodeURIComponent(lessonId)}&kp_ids=${encodeURIComponent(kpIds)}&subject=${encodeURIComponent(res.subject || '')}&grade=${encodeURIComponent(res.grade || '')}`);
  } catch (err) {
    alert(err.message || '加载教案知识点失败');
  }
}

async function viewPlan(lessonId) {
  const detailEl = $('lp-plan-detail');
  detailEl.innerHTML = `<div class="loading"><span class="spinner"></span>加载教案…</div>`;
  try {
    const res = await api.get(`/api/v1/lesson-prep/plans/${lessonId}`);
    detailEl.innerHTML = planHtml(res.lesson_content || {}, false);
    const hwBtn = $('lp-hw-btn');
    if (hwBtn) hwBtn.addEventListener('click', () => goHomework(lessonId));
  } catch (err) {
    detailEl.innerHTML = `<div class="error-box">${escapeHtml(err.message || '加载失败')}</div>`;
  }
}
