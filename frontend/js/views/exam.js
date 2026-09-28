// js/views/exam.js
// 试卷批改：学生（提交答卷 / 我的提交 / 查看结果）+ 教师（待复核 / 确认发布）

import { api, getToken } from '../api.js';
import { escapeHtml, percent, fmtTime } from '../util.js';

let rendered = false;
let epExpanded = false;  // 已发布试卷列表是否展开（默认折叠，只显示前几条）

const $ = (id) => document.getElementById(id);

const TYPE_LABEL = { choice: '客观题', short_answer: '主观题', code: '代码题' };
const STATUS_LABEL = {
  ai_processing: '批改中', submitted: '已提交', pending_review: '待教师确认',
  reviewed: '已复核', published: '已发布', failed: '失败',
};

// 已发布筛选：年级 → 科目联动（与备课/学情一致）
const GRADES = ['一年级', '二年级', '三年级', '四年级', '五年级', '六年级', '七年级', '八年级', '九年级', '高一', '高二', '高三'];
const PRIMARY_SUBJECTS = ['数学', '语文', '英语'];
const SECONDARY_SUBJECTS = ['数学', '语文', '英语', '物理', '化学', '生物', '政治', '历史', '地理'];
const subjectsForGrade = (g) => (/^[一二三四五六]年级$/.test(g) ? PRIMARY_SUBJECTS : SECONDARY_SUBJECTS);

export function renderExam(container, user) {
  if (rendered) return;
  rendered = true;
  const isTeacher = user && (user.role === 'teacher' || user.role === 'admin');
  container.innerHTML = isTeacher ? teacherHTML() : studentHTML();
  if (isTeacher) initTeacher(container); else initStudent(container);
}

/* ═══════════ 学生视角 ═══════════ */
function studentHTML() {
  return `
    <div class="card">
      <div class="card-title">📤 提交答卷</div>
      <form id="exam-submit-form">
        <label class="field" style="margin-bottom:12px">
          <span class="field-label">试卷 ID</span>
          <input id="exam-id" value="b3a2c1d0-0000-4000-8000-000000000001">
        </label>
        <label class="field" style="margin-bottom:14px">
          <span class="field-label">Word 答卷（.docx）</span>
          <div style="display:flex;flex-direction:column;gap:8px">
            <input type="file" id="exam-file" accept=".docx">
            <button type="button" class="btn btn-ghost btn-sm" id="exam-sample-btn" style="align-self:flex-start">📄 使用样例答卷</button>
          </div>
          <span class="form-hint" id="exam-sample-msg"></span>
        </label>
        <div class="form-actions">
          <button type="submit" class="btn btn-primary">提交批改</button>
          <span class="form-hint" id="exam-submit-msg"></span>
        </div>
      </form>
    </div>
    <div class="card">
      <div class="card-title">📋 我的提交</div>
      <div id="exam-my-list"></div>
    </div>
    <div id="exam-detail"></div>
  `;
}

function initStudent(container) {
  container.querySelector('#exam-submit-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const examId = $('exam-id').value.trim();
    const fileInput = $('exam-file');
    const msg = $('exam-submit-msg');
    if (!examId) { msg.textContent = '请填写试卷 ID'; return; }
    if (!fileInput.files.length) { msg.textContent = '请选择 .docx 答卷文件'; return; }
    msg.textContent = '提交中…';
    const fd = new FormData();
    fd.append('exam_id', examId);
    fd.append('file', fileInput.files[0]);
    try {
      const res = await api.postForm('/api/v1/exam/submit', fd);
      msg.textContent = res.message || '已提交';
      fileInput.value = '';
      loadMySubmissions();
    } catch (err) {
      msg.textContent = err.message || '提交失败';
    }
  });
  container.querySelector('#exam-sample-btn').addEventListener('click', useSample);
  loadMySubmissions();
}

// 一键载入样例答卷：从后端下载 → 填入文件框，供「提交批改」使用
async function useSample() {
  const fileInput = $('exam-file');
  const msg = $('exam-sample-msg');
  msg.textContent = '载入样例答卷…';
  try {
    const res = await fetch('/api/v1/exam/sample-answer', {
      headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!res.ok) {
      let d = null; try { d = await res.json(); } catch {}
      throw new Error((d && d.detail) || '样例答卷加载失败');
    }
    const blob = await res.blob();
    const file = new File([blob], '示例答卷_六年级数学.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    const dt = new DataTransfer();
    dt.items.add(file);
    fileInput.files = dt.files;
    msg.textContent = '✅ 样例答卷已载入，点「提交批改」即可';
  } catch (err) {
    msg.textContent = err.message || '样例答卷加载失败';
  }
}

async function loadMySubmissions() {
  const listEl = $('exam-my-list');
  try {
    const res = await api.get('/api/v1/exam/my-submissions');
    const items = res.items || [];
    if (!items.length) { listEl.innerHTML = '<p class="empty">暂无提交记录</p>'; return; }
    listEl.innerHTML = `<div class="table-wrap"><table class="tbl">
      <thead><tr><th>试卷</th><th>状态</th><th>提交时间</th><th>操作</th></tr></thead>
      <tbody>${items.map((it) => `
        <tr class="clickable" data-sid="${escapeHtml(it.submission_id)}">
          <td>${escapeHtml(it.exam_title || it.exam_id)}</td>
          <td><span class="status-pill ${escapeHtml(it.status)}">${STATUS_LABEL[it.status] || it.status}</span></td>
          <td>${escapeHtml(fmtTime(it.submitted_at))}</td>
          <td><button class="btn btn-ghost btn-sm danger" data-del="${escapeHtml(it.submission_id)}">删除</button></td>
        </tr>`).join('')}
      </tbody></table></div>`;
    listEl.querySelectorAll('tr.clickable').forEach((tr) =>
      tr.addEventListener('click', () => viewSubmission(tr.dataset.sid)));
    listEl.querySelectorAll('button[data-del]').forEach((btn) =>
      btn.addEventListener('click', (e) => { e.stopPropagation(); deleteSubmission(btn.dataset.del); }));
  } catch (err) {
    listEl.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

async function deleteSubmission(sid) {
  if (!confirm('确定删除这条提交记录吗？删除后不可恢复。')) return;
  try {
    await api.del(`/api/v1/exam/my-submissions/${sid}`);
    loadMySubmissions();
  } catch (err) {
    alert(err.message || '删除失败');
  }
}

async function viewSubmission(sid) {
  const detail = $('exam-detail');
  detail.innerHTML = `<div class="card"><div class="loading"><span class="spinner"></span>加载批改结果…</div></div>`;
  try {
    const res = await api.get(`/api/v1/exam/my-submissions/${sid}`);
    if (res.status !== 'published') {
      detail.innerHTML = `<div class="card">
        <div class="card-title">批改结果</div>
        <p style="margin-bottom:14px">当前状态：<span class="status-pill ${escapeHtml(res.status)}">${STATUS_LABEL[res.status] || res.status}</span></p>
        <p class="form-hint" style="margin-bottom:14px">试卷在后台异步批改中，完成后需教师确认发布。可稍后刷新查看。</p>
        <button class="btn btn-primary btn-sm" onclick="location.reload()">刷新</button>
      </div>`;
      return;
    }
    const s = res.pre_review_summary || {};
    detail.innerHTML = resultCard(s, res.weak_points || [], res.weak_points_summary || '');
  } catch (err) {
    detail.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

/* ═══════════ 教师视角 ═══════════ */
function teacherHTML() {
  return `
    <div class="card">
      <div class="card-title">👩‍🏫 待复核试卷</div>
      <div id="exam-pending"></div>
    </div>
    <div class="card">
      <div class="card-title">📋 已发布试卷</div>
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center">
        <select id="ep-grade"><option value="">全部年级</option>${GRADES.map((g) => `<option ${g === '六年级' ? 'selected' : ''}>${g}</option>`).join('')}</select>
        <select id="ep-subject"><option value="">全部科目</option></select>
        <select id="ep-class"><option value="">全部班级</option></select>
        <button class="btn btn-ghost btn-sm" id="ep-filter-btn">筛选</button>
        <button class="btn btn-ghost btn-sm" id="ep-toggle-btn">展开全部</button>
      </div>
      <div id="exam-published"></div>
    </div>
    <div id="exam-review"></div>
  `;
}

function initTeacher() {
  loadPending();
  loadPublished();
  // 已发布筛选：年级 → 科目/班级联动
  $('ep-grade').addEventListener('change', () => { renderEpSubjectOptions(); loadEpClasses(); });
  $('ep-filter-btn').addEventListener('click', loadPublished);
  $('ep-toggle-btn').addEventListener('click', () => { epExpanded = !epExpanded; loadPublished(); });
  renderEpSubjectOptions();
  loadEpClasses();
}

async function loadPending() {
  const listEl = $('exam-pending');
  try {
    const res = await api.get('/api/v1/exam/pending-reviews');
    const items = res.items || [];
    if (!items.length) {
      listEl.innerHTML = `<p class="empty" style="margin-bottom:14px">暂无待复核的提交</p>
        <div style="text-align:center"><button class="btn btn-primary" id="exam-demo-btn">＋ 生成演示提交</button>
        <p class="form-hint" style="margin-top:8px">自动模拟学生交卷 → AI 批改 → 生成一条待复核示例</p></div>`;
      $('exam-demo-btn').addEventListener('click', generateDemo);
      return;
    }
    listEl.innerHTML = `<div class="table-wrap"><table class="tbl">
      <thead><tr><th>学生</th><th>试卷</th><th>AI 预批改</th><th>需复核题数</th><th>提交时间</th></tr></thead>
      <tbody>${items.map((it) => {
        const pr = it.pre_review || {};
        return `<tr class="clickable" data-sid="${escapeHtml(it.submission_id)}">
          <td>${escapeHtml(it.student_name)}</td>
          <td>${escapeHtml(it.exam_title || '')}</td>
          <td>${pr.total_score ?? 0} / ${pr.full_score ?? 0}</td>
          <td>${pr.needs_review_count ?? 0}</td>
          <td>${escapeHtml(fmtTime(it.submitted_at))}</td>
        </tr>`;
      }).join('')}
      </tbody></table></div>`;
    listEl.querySelectorAll('tr.clickable').forEach((tr) =>
      tr.addEventListener('click', () => reviewSubmission(tr.dataset.sid)));
  } catch (err) {
    listEl.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

function renderEpSubjectOptions() {
  const grade = $('ep-grade').value;
  const subjects = grade ? subjectsForGrade(grade) : [...new Set([...PRIMARY_SUBJECTS, ...SECONDARY_SUBJECTS])];
  const current = $('ep-subject').value;
  const keep = subjects.includes(current) ? current : '数学';
  $('ep-subject').innerHTML = '<option value="">全部科目</option>' + subjects.map((s) => `<option ${s === keep ? 'selected' : ''}>${s}</option>`).join('');
}

async function loadEpClasses() {
  const sel = $('ep-class');
  const grade = $('ep-grade').value;
  try {
    const res = await api.get(`/api/v1/classes?grade=${encodeURIComponent(grade)}`);
    const items = res.items || [];
    const current = sel.value;
    const defaultId = items.some((c) => c.class_id === current)
      ? current
      : ((items.find((c) => c.student_count > 0) || items[0] || {}).class_id || '');
    sel.innerHTML = '<option value="">全部班级</option>' + items.map((c) => `<option value="${escapeHtml(c.class_id)}" ${c.class_id === defaultId ? 'selected' : ''}>${escapeHtml(c.name)}${c.student_count ? `（${c.student_count}人）` : ''}</option>`).join('');
  } catch (err) {
    sel.innerHTML = '<option value="">全部班级</option>';
  }
}

async function loadPublished() {
  const listEl = $('exam-published');
  const grade = $('ep-grade').value;
  const subject = $('ep-subject').value;
  const classId = $('ep-class').value;
  const qs = `grade=${encodeURIComponent(grade)}&subject=${encodeURIComponent(subject)}&class_id=${encodeURIComponent(classId)}`;
  try {
    const res = await api.get(`/api/v1/exam/published-reviews?${qs}`);
    const allItems = res.items || [];
    if (!allItems.length) { listEl.innerHTML = '<p class="empty">暂无已发布的试卷</p>'; return; }
    // 折叠时只显示前 8 条，展开显示全部
    const items = epExpanded ? allItems : allItems.slice(0, 8);
    listEl.innerHTML = `<div class="table-wrap"><table class="tbl">
      <thead><tr><th>学生</th><th>试卷</th><th>准确率</th><th>发布时间</th><th>操作</th></tr></thead>
      <tbody>${items.map((it) => {
        const rate = it.score_rate ?? 0;
        const rateColor = rate >= 0.85 ? 'var(--success)' : (rate < 0.6 ? 'var(--danger)' : '');
        return `<tr class="clickable" data-sid="${escapeHtml(it.submission_id)}">
          <td>${escapeHtml(it.student_name)}</td>
          <td>${escapeHtml(it.exam_title || '')}</td>
          <td><b style="color:${rateColor}">${percent(rate)}</b></td>
          <td>${escapeHtml(fmtTime(it.published_at))}</td>
          <td><button class="btn btn-ghost btn-sm" data-view="${escapeHtml(it.submission_id)}">查看</button></td>
        </tr>`;
      }).join('')}
      </tbody></table></div>`;
    listEl.querySelectorAll('tr.clickable').forEach((tr) =>
      tr.addEventListener('click', () => viewPublishedResult(tr.dataset.sid)));
    listEl.querySelectorAll('button[data-view]').forEach((btn) =>
      btn.addEventListener('click', (e) => { e.stopPropagation(); viewPublishedResult(btn.dataset.view); }));
    const toggleBtn = $('ep-toggle-btn');
    if (toggleBtn) toggleBtn.textContent = epExpanded ? '折叠' : `展开全部（${allItems.length}）`;
  } catch (err) {
    listEl.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

async function viewPublishedResult(sid) {
  const wrap = $('exam-review');
  wrap.innerHTML = `<div class="card"><div class="loading"><span class="spinner"></span>加载批改结果…</div></div>`;
  try {
    const res = await api.get(`/api/v1/exam/submissions/${sid}/result`);
    if (res.status !== 'published') {
      wrap.innerHTML = `<div class="card"><div class="card-title">批改结果</div><p>状态：${escapeHtml(res.status)}</p></div>`;
      return;
    }
    const s = res.pre_review_summary || {};
    wrap.innerHTML = resultCard(s, res.weak_points || [], res.weak_points_summary || '');
  } catch (err) {
    wrap.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

async function generateDemo() {
  const listEl = $('exam-pending');
  listEl.innerHTML = `<div class="loading"><span class="spinner"></span>正在生成演示提交（学生交卷 → AI 批改 → 待复核，约需 30-60 秒）…</div>`;
  try {
    await api.post('/api/v1/exam/demo-submit', {});
    loadPending();
  } catch (err) {
    listEl.innerHTML = `<div class="error-box">${escapeHtml(err.message || '生成失败')}</div>`;
  }
}

async function reviewSubmission(sid) {
  const wrap = $('exam-review');
  wrap.innerHTML = `<div class="card"><div class="loading"><span class="spinner"></span>加载 AI 预批改结果…</div></div>`;
  try {
    const res = await api.get(`/api/v1/exam/submissions/${sid}/review`);
    const s = res.pre_review_summary || {};
    wrap.innerHTML = `<div class="card">
      <div class="card-title">AI 预批改详情</div>
      ${reviewBody(s, res.weak_points || [], res.weak_points_summary || '')}
      <div class="form-actions" style="border-top:1px solid var(--border);padding-top:16px">
        <button class="btn btn-success" id="exam-approve">✓ 通过并发布</button>
        <button class="btn btn-primary" id="exam-modify">💾 保存修改并发布</button>
        <span class="form-hint" id="exam-modify-hint"></span>
      </div>
    </div>`;
    $('exam-approve').addEventListener('click', () => confirmReview(sid, 'approve'));
    $('exam-modify').addEventListener('click', () => confirmReview(sid, 'modify', collectModifications()));
  } catch (err) {
    wrap.innerHTML = `<div class="error-box">${escapeHtml(err.message)}</div>`;
  }
}

async function confirmReview(sid, action, modifications = []) {
  const approveBtn = $('exam-approve');
  const modifyBtn = $('exam-modify');
  const hint = $('exam-modify-hint');
  [approveBtn, modifyBtn].forEach((b) => b && (b.disabled = true));
  if (hint) hint.textContent = '发布中…';
  try {
    const res = await api.post(`/api/v1/exam/submissions/${sid}/confirm`, { action, modifications });
    $('exam-review').innerHTML = `<div class="card">
      <div class="card-title">✓ 已发布</div>
      <p>最终得分 <b>${res.final_score} / ${res.full_score}</b>（${percent(res.score_rate)}）</p>
      <p class="form-hint" style="margin-top:8px">学生端现在可查看到完整批改结果。</p>
    </div>`;
    loadPending();
  } catch (err) {
    $('exam-review').insertAdjacentHTML('beforeend', `<div class="error-box">${escapeHtml(err.message)}</div>`);
  } finally {
    [approveBtn, modifyBtn].forEach((b) => b && (b.disabled = false));
    if (hint) hint.textContent = '';
  }
}

/* ═══════════ 教师复核：可编辑批改 ═══════════ */
function reviewBody(summary, weakPoints, weakSummary) {
  const qs = summary.by_question || [];
  const questions = qs.length ? `<div class="section"><h3>逐题批改（可修改分数与评语）</h3>
    <div class="table-wrap"><table class="tbl">
      <thead><tr><th>#</th><th>题型</th><th>学生答案</th><th>AI 批注</th><th>AI 得分</th><th>教师得分</th><th>教师评语</th></tr></thead>
      <tbody>${qs.map((r) => `
        <tr>
          <td>${r.question_no ?? '—'}</td>
          <td>${TYPE_LABEL[r.question_type] || r.question_type || '—'}</td>
          <td style="max-width:180px;white-space:pre-wrap;word-break:break-word">${escapeHtml(r.student_answer || '（未作答）')}</td>
          <td style="max-width:180px;white-space:pre-wrap;word-break:break-word">${escapeHtml(r.ai_feedback || '')}</td>
          <td>${r.score ?? 0} / ${r.full_score ?? 0}</td>
          <td><input type="number" class="score-input" data-qid="${escapeHtml(r.question_id)}" value="${r.score ?? 0}" min="0" max="${r.full_score ?? 0}" style="width:70px"></td>
          <td><input type="text" class="comment-input" data-qid="${escapeHtml(r.question_id)}" placeholder="（可选）" style="width:150px"></td>
        </tr>`).join('')}
      </tbody></table></div></div>` : '<p class="empty">暂无题目详情</p>';

  const weak = (weakPoints && weakPoints.length)
    ? `<div class="section"><h3>知识薄弱点</h3>
        <p>${escapeHtml(weakSummary || '')}</p>
        <ul style="padding-left:20px;margin-top:6px">${weakPoints.map((w) => `<li>${weakPointHtml(w)}</li>`).join('')}</ul>
      </div>`
    : '';

  return questions + weak;
}

function collectModifications() {
  return [...document.querySelectorAll('.score-input')].map((inp) => {
    const comment = document.querySelector(`.comment-input[data-qid="${inp.dataset.qid}"]`);
    return {
      question_id: inp.dataset.qid,
      new_score: Number(inp.value) || 0,
      comment: comment ? comment.value.trim() : '',
    };
  });
}

/* ═══════════ 共用：题目 / 结果渲染 ═══════════ */
function resultCard(summary, weakPoints, weakSummary) {
  return `<div class="card">
    <div class="card-title">批改结果</div>
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-num">${summary.total_score ?? 0}</div><div class="stat-label">得分</div></div>
      <div class="stat-card"><div class="stat-num">${summary.full_score ?? 0}</div><div class="stat-label">满分</div></div>
      <div class="stat-card"><div class="stat-num">${percent(summary.score_rate)}</div><div class="stat-label">得分率</div></div>
      <div class="stat-card"><div class="stat-num">${summary.needs_review_count ?? 0}</div><div class="stat-label">需复核题数</div></div>
    </div>
    ${resultBody(summary, weakPoints, weakSummary)}
  </div>`;
}

function weakPointHtml(w) {
  if (typeof w === 'string') return escapeHtml(w);
  const tag = w.tag || w.kp_name || w.knowledge_tag || '薄弱知识点';
  const suggestion = w.suggestion || '';
  return `<b>${escapeHtml(tag)}</b>${suggestion ? `：${escapeHtml(suggestion)}` : ''}`;
}

function resultBody(summary, weakPoints, weakSummary) {
  const qs = summary.by_question || [];
  const questions = qs.length ? `<div class="section"><h3>逐题详情</h3>
    <div class="table-wrap"><table class="tbl">
      <thead><tr><th>#</th><th>题型</th><th>得分</th><th>学生答案</th><th>AI 批注</th><th>教师评语</th></tr></thead>
      <tbody>${qs.map((r) => `
        <tr>
          <td>${r.question_no ?? '—'}</td>
          <td>${TYPE_LABEL[r.question_type] || r.question_type || '—'}</td>
          <td><b>${r.final_score ?? r.score ?? r.ai_score ?? 0}</b> / ${r.full_score ?? 0}</td>
          <td style="max-width:220px;white-space:pre-wrap;word-break:break-word">${escapeHtml(r.student_answer || '（未作答）')}</td>
          <td style="max-width:220px;white-space:pre-wrap;word-break:break-word">${escapeHtml(r.ai_feedback || '')}</td>
          <td style="max-width:200px;white-space:pre-wrap;word-break:break-word">${escapeHtml(r.teacher_comment || '—')}</td>
        </tr>`).join('')}
      </tbody></table></div></div>` : '<p class="empty">暂无题目详情</p>';

  const weak = (weakPoints && weakPoints.length)
    ? `<div class="section"><h3>知识薄弱点</h3>
        <p>${escapeHtml(weakSummary || '')}</p>
        <ul style="padding-left:20px;margin-top:6px">${weakPoints.map((w) => `<li>${weakPointHtml(w)}</li>`).join('')}</ul>
      </div>`
    : '';

  return questions + weak;
}
