// js/views/homework.js
// 作业：教师（选题布置 / 我的布置）+ 学生（我的作业 / 作答提交判分）

import { api } from '../api.js';
import { escapeHtml, percent, fmtTime } from '../util.js';

let rendered = false;

const $ = (id) => document.getElementById(id);

const TYPE_LABEL = { single_choice: '单选', multi_choice: '多选', fill_blank: '填空', short_answer: '简答' };

// 年级 → 科目联动（与备课/学情一致）：小学只有语数外；初中/高中额外加政史地物化生
const GRADES = ['一年级', '二年级', '三年级', '四年级', '五年级', '六年级', '七年级', '八年级', '九年级', '高一', '高二', '高三'];
const PRIMARY_SUBJECTS = ['数学', '语文', '英语'];
const SECONDARY_SUBJECTS = ['数学', '语文', '英语', '物理', '化学', '生物', '政治', '历史', '地理'];
const subjectsForGrade = (g) => (/^[一二三四五六]年级$/.test(g) ? PRIMARY_SUBJECTS : SECONDARY_SUBJECTS);

function getQueryParam(key) {
  const hash = location.hash || '';
  const m = hash.match(new RegExp(`[?&]${key}=([^&]*)`));
  return m ? decodeURIComponent(m[1]) : '';
}

// 选题过滤状态：kp_ids 来自「备课跳转」或「作业页选教案」
let currentKpIds = getQueryParam('kp_ids');

export function renderHomework(container, user) {
  const isTeacher = user && (user.role === 'teacher' || user.role === 'admin');
  if (!rendered) {
    rendered = true;
    container.innerHTML = isTeacher ? teacherHTML() : studentHTML();
    if (isTeacher) initTeacher(container); else initStudent(container);
  } else if (isTeacher) {
    // 已渲染过（从备课二次跳转）：重新加载教案列表并预填关联教案
    loadLessons();
  }
}

/* ═══════════ 教师视角 ═══════════ */
function teacherHTML() {
  const grade = getQueryParam('grade') || '六年级';
  return `
    <div class="card">
      <div class="card-title">📝 布置作业</div>
      <form id="hw-create-form">
        <label class="field" style="margin-bottom:12px">
          <span class="field-label">作业标题</span>
          <input id="hw-title" placeholder="如：圆的认识 · 课堂练习">
        </label>
        <div class="grid-2" style="margin-bottom:12px">
          <label class="field">
            <span class="field-label">年级</span>
            <select id="hw-grade">${GRADES.map((g) => `<option ${g === grade ? 'selected' : ''}>${g}</option>`).join('')}</select>
          </label>
          <label class="field">
            <span class="field-label">科目</span>
            <select id="hw-subject"></select>
          </label>
        </div>
        <label class="field" style="margin-bottom:14px">
          <span class="field-label">班级</span>
          <select id="hw-class"></select>
        </label>
        <label class="field" style="margin-bottom:14px">
          <span class="field-label">关联教案（按教案知识点选题，可选）</span>
          <select id="hw-lesson"><option value="">全部题目（按学科/年级）</option></select>
        </label>
        <div style="font-size:13px;font-weight:600;margin:8px 0 10px">选择习题（勾选）</div>
        <div id="hw-exercise-list" class="exercise-list"></div>
        <div class="form-actions" style="margin-top:14px">
          <button type="submit" class="btn btn-primary">布置作业</button>
          <span class="form-hint" id="hw-create-msg"></span>
        </div>
      </form>
    </div>
    <div class="card">
      <div class="card-title">📋 我的布置</div>
      <div id="hw-my-list"></div>
    </div>
  `;
}

function initTeacher(container) {
  renderSubjectOptions(getQueryParam('subject'));  // 初始化：年级 → 科目
  loadClasses();                                   // 初始化：年级 → 班级
  loadLessons();
  loadExercises();
  loadTeacherList();
  container.querySelector('#hw-create-form').addEventListener('submit', createHomework);
  container.querySelector('#hw-lesson').addEventListener('change', (e) => selectLesson(e.target.value));
  container.querySelector('#hw-grade').addEventListener('change', () => { renderSubjectOptions(); loadClasses(); });
  container.querySelector('#hw-subject').addEventListener('change', () => { if (!currentKpIds) loadExercises(); });
}

// 年级切换时联动刷新科目下拉（保留当前选中，若不在新列表则回退「数学」）
function renderSubjectOptions(preselect) {
  const subjects = subjectsForGrade($('hw-grade').value);
  const current = preselect || $('hw-subject').value;
  const keep = subjects.includes(current) ? current : '数学';
  $('hw-subject').innerHTML = subjects.map((s) => `<option ${s === keep ? 'selected' : ''}>${s}</option>`).join('');
}

// 按年级加载班级下拉（显示班级名称，value 为 class_id）
async function loadClasses() {
  const sel = $('hw-class');
  const grade = $('hw-grade').value;
  try {
    const res = await api.get(`/api/v1/classes?grade=${encodeURIComponent(grade)}`);
    const items = res.items || [];
    if (!items.length) {
      sel.innerHTML = '<option value="">（该年级暂无班级）</option>';
      return;
    }
    const preselect = getQueryParam('class_id');
    // 默认选中：URL 指定 → 第一个有学生的班级 → 第一个
    const defaultId = (preselect && items.some((c) => c.class_id === preselect))
      ? preselect
      : ((items.find((c) => c.student_count > 0) || items[0]).class_id);
    sel.innerHTML = items.map((c) => `<option value="${escapeHtml(c.class_id)}" ${c.class_id === defaultId ? 'selected' : ''}>${escapeHtml(c.name)}${c.student_count ? `（${c.student_count}人）` : ''}</option>`).join('');
  } catch (err) {
    sel.innerHTML = '<option value="">（班级加载失败）</option>';
  }
}

async function loadLessons() {
  const sel = $('hw-lesson');
  try {
    const res = await api.get('/api/v1/lesson-prep/plans');
    const items = res.items || [];
    sel.innerHTML = '<option value="">全部题目（按学科/年级）</option>' +
      items.map((it) => `<option value="${escapeHtml(it.lesson_id)}">${escapeHtml(it.topic)}（${escapeHtml(it.subject)} ${escapeHtml(it.grade)}）</option>`).join('');
    // 从智能备课跳转过来：自动选中关联教案（按教案知识点选题）
    const lessonId = getQueryParam('lesson_id');
    if (lessonId && items.some((it) => it.lesson_id === lessonId)) {
      sel.value = lessonId;
      selectLesson(lessonId);
    }
  } catch (err) {
    // 教案列表加载失败不阻断（选题仍可用学科/年级过滤）
  }
}

async function selectLesson(lessonId) {
  const listEl = $('hw-exercise-list');
  if (!lessonId) {
    currentKpIds = '';
    loadExercises();
    return;
  }
  listEl.innerHTML = `<div class="loading"><span class="spinner"></span>按教案知识点加载习题…</div>`;
  try {
    const res = await api.get(`/api/v1/lesson-prep/plans/${lessonId}`);
    currentKpIds = (res.kp_ids || []).join(',');
    loadExercises();
  } catch (err) {
    listEl.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

async function loadExercises() {
  const listEl = $('hw-exercise-list');
  listEl.innerHTML = `<div class="loading"><span class="spinner"></span>加载习题…</div>`;
  const subject = $('hw-subject').value || '数学';
  const grade = $('hw-grade').value || '六年级';
  const query = currentKpIds
    ? `kp_ids=${encodeURIComponent(currentKpIds)}`
    : `subject=${encodeURIComponent(subject)}&grade=${encodeURIComponent(grade)}`;
  try {
    const res = await api.get(`/api/v1/exercises?${query}`);
    const items = res.items || [];
    if (!items.length) {
      listEl.innerHTML = currentKpIds
        ? '<p class="empty">该教案关联的知识点暂无可用习题</p>'
        : '<p class="empty">习题库为空，请先运行 seed_lesson_data.py</p>';
      return;
    }
    listEl.innerHTML = items.map((ex, i) => `
      <label class="ex-item">
        <input type="checkbox" name="ex" value="${escapeHtml(ex.exercise_id)}">
        <span class="ex-body">
          <span class="ex-tag">${TYPE_LABEL[ex.question_type] || ex.question_type}</span>
          <span class="ex-content">${i + 1}. ${escapeHtml(ex.content)}</span>
          <span class="ex-score">${ex.score} 分 · ${escapeHtml(ex.knowledge_tag || '')}</span>
        </span>
      </label>`).join('');
  } catch (err) {
    listEl.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

async function createHomework(e) {
  e.preventDefault();
  const msg = $('hw-create-msg');
  const title = $('hw-title').value.trim();
  const subject = $('hw-subject').value.trim();
  const grade = $('hw-grade').value.trim();
  const classId = $('hw-class').value.trim();
  const exerciseIds = [...document.querySelectorAll('input[name="ex"]:checked')].map((c) => c.value);
  if (!title) { msg.textContent = '请填写作业标题'; return; }
  if (!classId) { msg.textContent = '请选择班级'; return; }
  if (!exerciseIds.length) { msg.textContent = '请至少勾选一道习题'; return; }
  msg.textContent = '布置中…';
  try {
    const res = await api.post('/api/v1/homework', { class_id: classId, subject, grade, title, exercise_ids: exerciseIds });
    msg.textContent = `已布置（${res.exercise_count} 题）`;
    $('hw-title').value = '';
    document.querySelectorAll('input[name="ex"]:checked').forEach((c) => (c.checked = false));
    loadTeacherList();
  } catch (err) {
    msg.textContent = err.message || '布置失败';
  }
}

async function loadTeacherList() {
  const listEl = $('hw-my-list');
  try {
    const res = await api.get('/api/v1/homework');
    const items = res.items || [];
    if (!items.length) { listEl.innerHTML = '<p class="empty">还没有布置过作业</p>'; return; }
    listEl.innerHTML = `<div class="table-wrap"><table class="tbl">
      <thead><tr><th>标题</th><th>学科</th><th>年级</th><th>题数</th><th>状态</th><th>布置时间</th><th>操作</th></tr></thead>
      <tbody>${items.map((it) => `
        <tr>
          <td>${escapeHtml(it.title)}</td>
          <td>${escapeHtml(it.subject)}</td>
          <td>${escapeHtml(it.grade)}</td>
          <td>${it.exercise_count}</td>
          <td><span class="status-pill ${escapeHtml(it.status)}">${escapeHtml(it.status)}</span></td>
          <td>${escapeHtml(fmtTime(it.created_at))}</td>
          <td><button class="btn btn-ghost btn-sm danger" data-del="${escapeHtml(it.assignment_id)}">删除</button></td>
        </tr>`).join('')}
      </tbody></table></div>`;
    listEl.querySelectorAll('button[data-del]').forEach((btn) =>
      btn.addEventListener('click', () => deleteHomework(btn.dataset.del)));
  } catch (err) {
    listEl.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

async function deleteHomework(aid) {
  if (!confirm('确定删除这份作业吗？删除后不可恢复。')) return;
  try {
    await api.del(`/api/v1/homework/${aid}`);
    loadTeacherList();
  } catch (err) {
    alert(err.message || '删除失败');
  }
}

/* ═══════════ 学生视角 ═══════════ */
function studentHTML() {
  return `
    <div class="card">
      <div class="card-title">📚 我的作业</div>
      <div id="hw-stu-list"></div>
    </div>
    <div id="hw-detail"></div>
  `;
}

function initStudent(container) {
  loadStudentList();
}

async function loadStudentList() {
  const listEl = $('hw-stu-list');
  try {
    const res = await api.get('/api/v1/homework');
    const items = res.items || [];
    if (!items.length) { listEl.innerHTML = '<p class="empty">暂无作业（可能还没加入班级）</p>'; return; }
    listEl.innerHTML = `<div class="table-wrap"><table class="tbl">
      <thead><tr><th>标题</th><th>学科</th><th>题数</th><th>布置时间</th><th>状态</th></tr></thead>
      <tbody>${items.map((it) => `
        <tr class="clickable" data-aid="${escapeHtml(it.assignment_id)}">
          <td>${escapeHtml(it.title)}</td>
          <td>${escapeHtml(it.subject)}</td>
          <td>${it.exercise_count}</td>
          <td>${escapeHtml(fmtTime(it.created_at))}</td>
          <td>${it.submitted ? '<span class="status-pill saved">已提交</span>' : '<span class="status-pill failed">未提交</span>'}</td>
        </tr>`).join('')}
      </tbody></table></div>`;
    listEl.querySelectorAll('tr.clickable').forEach((tr) =>
      tr.addEventListener('click', () => viewHomework(tr.dataset.aid)));
  } catch (err) {
    listEl.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

async function viewHomework(aid) {
  const detail = $('hw-detail');
  detail.innerHTML = `<div class="card"><div class="loading"><span class="spinner"></span>加载作业…</div></div>`;
  try {
    const hw = await api.get(`/api/v1/homework/${aid}`);
    // 已提交（有 student_answer）→ 显示历史作答与对错；否则显示作答表单
    const submitted = hw.exercises.some((ex) => ex.student_answer !== undefined);
    detail.innerHTML = submitted ? viewSubmittedHomework(hw) : `
      <div class="card">
        <div class="card-title">${escapeHtml(hw.title)}</div>
        <p class="form-hint" style="margin-bottom:14px">${escapeHtml(hw.subject)} · ${escapeHtml(hw.grade)}</p>
        <form id="hw-answer-form">
          ${hw.exercises.map((ex, i) => exerciseInput(ex, i)).join('')}
          <div class="form-actions" style="margin-top:16px">
            <button type="submit" class="btn btn-primary">提交作业</button>
            <span class="form-hint" id="hw-submit-msg"></span>
          </div>
        </form>
      </div>`;
    if (!submitted) $('hw-answer-form').addEventListener('submit', (e) => submitHomework(e, aid));
  } catch (err) {
    detail.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

function viewSubmittedHomework(hw) {
  return `
    <div class="card">
      <div class="card-title">${escapeHtml(hw.title)}</div>
      <p class="form-hint" style="margin-bottom:14px">${escapeHtml(hw.subject)} · ${escapeHtml(hw.grade)}</p>
      ${hw.exercises.map((ex, i) => `
        <div class="hw-question">
          <div class="hw-q-head">
            <span class="ex-tag">${TYPE_LABEL[ex.question_type] || ex.question_type}</span>
            <span class="hw-q-no">第 ${i + 1} 题（${ex.score} 分）</span>
            <span class="status-pill ${ex.is_correct ? 'saved' : 'failed'}">${ex.is_correct ? '✓ 正确' : '✗ 错误'}</span>
          </div>
          <div class="hw-q-content">${escapeHtml(ex.content)}</div>
          ${(ex.options || []).length ? `<div class="ex-options">${ex.options.map((o) => `<span class="opt-item">${escapeHtml(o.key)}. ${escapeHtml(o.text)}</span>`).join('')}</div>` : ''}
          <p style="margin:8px 0 0;color:var(--text-2)">正确答案：<b>${escapeHtml(ex.correct_answer || '—')}</b></p>
          <p style="margin:4px 0 0;color:var(--text-2)">你的答案：<b>${escapeHtml(ex.student_answer || '（未作答）')}</b>${ex.student_score != null ? `（得 ${ex.student_score}/${ex.score} 分）` : ''}</p>
        </div>
      `).join('')}
    </div>`;
}

function exerciseInput(ex, i) {
  const opt = ex.options || [];
  let inputHtml = '';
  if (ex.question_type === 'single_choice' || ex.question_type === 'multi_choice') {
    const inputType = ex.question_type === 'single_choice' ? 'radio' : 'checkbox';
    const name = `q-${i}`;
    inputHtml = `<div class="ex-options">${opt.map((o) => `
      <label class="opt-item"><input type="${inputType}" name="${name}" value="${escapeHtml(o.key)}"> ${escapeHtml(o.key)}. ${escapeHtml(o.text)}</label>`).join('')}</div>`;
  } else if (ex.question_type === 'fill_blank') {
    inputHtml = `<input class="ans-input" name="q-${i}" placeholder="填写答案">`;
  } else {
    inputHtml = `<textarea class="ans-input" name="q-${i}" rows="2" placeholder="写下你的答案"></textarea>`;
  }
  return `<div class="hw-question" data-exid="${escapeHtml(ex.exercise_id)}">
    <div class="hw-q-head"><span class="ex-tag">${TYPE_LABEL[ex.question_type] || ex.question_type}</span>
      <span class="hw-q-no">第 ${i + 1} 题（${ex.score} 分）</span></div>
    <div class="hw-q-content">${escapeHtml(ex.content)}</div>
    ${inputHtml}
  </div>`;
}

async function submitHomework(e, aid) {
  e.preventDefault();
  const msg = $('hw-submit-msg');
  const form = $('hw-answer-form');
  msg.textContent = '提交中…';
  const inputs = collectAnswers(form);
  try {
    const res = await api.post(`/api/v1/homework/${aid}/submit`, { answers: inputs });
    msg.textContent = '提交成功';
    renderResult(res);
  } catch (err) {
    msg.textContent = err.message || '提交失败';
  }
}

function collectAnswers(form) {
  // 每道题用一个带 data-exid 的容器，读取其 input/textarea/radio 答案
  const answers = [];
  form.querySelectorAll('.hw-question').forEach((q) => {
    const exid = q.dataset.exid;
    let val = '';
    const radio = q.querySelector('input[type="radio"]:checked');
    const checks = q.querySelectorAll('input[type="checkbox"]:checked');
    if (checks.length) val = [...checks].map((c) => c.value).join('');
    else if (radio) val = radio.value;
    else {
      const txt = q.querySelector('.ans-input');
      val = txt ? txt.value.trim() : '';
    }
    if (val !== '') answers.push({ exercise_id: exid, answer: val });
  });
  return answers;
}

function renderResult(res) {
  const detail = $('hw-detail');
  const results = res.results || [];
  // 移除旧结果卡片（重复提交只显示最新结果）
  const old = document.getElementById('hw-result-card');
  if (old) old.remove();
  detail.insertAdjacentHTML('beforeend', `
    <div class="card" id="hw-result-card">
      <div class="card-title">✅ 提交成功</div>
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-num">${res.score ?? 0}</div><div class="stat-label">得分</div></div>
        <div class="stat-card"><div class="stat-num">${res.full_score ?? 0}</div><div class="stat-label">满分</div></div>
        <div class="stat-card"><div class="stat-num">${percent(res.score && res.full_score ? res.score / res.full_score : 0)}</div><div class="stat-label">得分率</div></div>
      </div>
      <div class="section"><h3>逐题结果</h3>
        <div class="table-wrap"><table class="tbl">
          <thead><tr><th>#</th><th>结果</th><th>得分</th></tr></thead>
          <tbody>${results.map((r, i) => `
            <tr>
              <td>${i + 1}</td>
              <td>${r.is_correct ? '✅ 正确' : '❌ 错误'}</td>
              <td>${r.score}</td>
            </tr>`).join('')}
          </tbody></table></div>
      </div>
    </div>`);
}
