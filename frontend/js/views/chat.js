// js/views/chat.js
// 统一 AI 助手：SSE 流式对话，渲染路由卡片 / 引导卡片 / 协同计划 / 来源引用

import { streamSSE } from '../api.js';
import { navigate } from '../router.js';
import { escapeHtml, renderMarkdown, percent, uuid } from '../util.js';

let rendered = false;
let sessionId = '';

const MODE_LABEL = {
  rag: '知识库检索', web_augmented: '联网增强', llm_direct: '模型直答', general: '通用问答',
};

export function renderChat(container, user) {
  if (rendered) return;
  rendered = true;
  if (!sessionId) sessionId = uuid();
  const isTeacher = user && (user.role === 'teacher' || user.role === 'admin');

  const placeholder = isTeacher
    ? '输入问题：学科提问 /「帮我备课」/「批改试卷」/「出学情报告」'
    : '输入问题：学科提问 /「批改试卷」';

  container.innerHTML = `
    <div class="chat">
      <div class="chat-messages" id="chat-msgs"></div>
      <div class="chat-input">
        <textarea id="chat-input" rows="1" placeholder="${escapeHtml(placeholder)}"></textarea>
        <button class="btn btn-primary" id="chat-send">发送</button>
      </div>
    </div>
  `;

  const welcome = isTeacher
    ? '您好！我是 **K12TutorAgent AI 助教**。\n\n可以直接问我学科问题，或告诉我：\n- 「帮我备课」— 生成教案与习题\n- 「批改试卷」— 三轨 AI 批改\n- 「出学情报告」— 班级掌握度分析\n\n还可以描述综合需求（如「根据这次学情报告帮我备课」），我会自动串联多个 Agent 协同。'
    : '您好！我是 **K12TutorAgent AI 助教**。\n\n可以直接问我学科问题，或告诉我：\n- 「批改试卷」— 提交答卷，AI 自动批改\n- 「我的作业」— 查看并作答作业\n\n我会从知识库检索解答你的疑问。';

  addBubble('ai', welcome);

  // 欢迎消息下方插入预设快捷按钮（按角色过滤）
  const suggEl = document.createElement('div');
  suggEl.className = 'chat-suggestions';
  suggEl.innerHTML = isTeacher
    ? `
    <button class="suggest-btn" data-msg="圆的面积怎么算？">💡 学科提问</button>
    <button class="suggest-btn" data-msg="帮我备课">📚 帮我备课</button>
    <button class="suggest-btn" data-msg="批改试卷">📝 批改试卷</button>
    <button class="suggest-btn" data-msg="出学情报告">📊 出学情报告</button>
  `
    : `
    <button class="suggest-btn" data-msg="圆的面积怎么算？">💡 学科提问</button>
    <button class="suggest-btn" data-msg="批改试卷">📝 批改试卷</button>
  `;
  msgs().appendChild(suggEl);
  scrollBottom();

  bindSend(container);
}

function msgs() { return document.getElementById('chat-msgs'); }

function scrollBottom() {
  const m = msgs();
  if (m) m.scrollTop = m.scrollHeight;
}

// 添加一条普通消息（user/ai），返回气泡文本元素
function addBubble(role, text) {
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-' + role;
  wrap.innerHTML = `
    <div class="msg-role">${role === 'user' ? '我' : 'K12TutorAgent'}</div>
    <div class="msg-bubble md">${renderMarkdown(text)}</div>
  `;
  msgs().appendChild(wrap);
  scrollBottom();
  return wrap;
}

// 添加一条空 AI 消息（流式填充），返回 { wrap, bubble }
function addStreamingBubble() {
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-ai';
  wrap.innerHTML = `
    <div class="msg-role">K12TutorAgent</div>
    <div class="msg-bubble md"></div>
  `;
  msgs().appendChild(wrap);
  scrollBottom();
  return { wrap, bubble: wrap.querySelector('.msg-bubble') };
}

function appendCard(className, innerHTML) {
  const el = document.createElement('div');
  el.className = className;
  el.innerHTML = innerHTML;
  msgs().appendChild(el);
  return el;
}

function bindSend(container) {
  const input = container.querySelector('#chat-input');
  const sendBtn = container.querySelector('#chat-send');

  const send = async (overrideMsg) => {
    const text = (overrideMsg != null ? overrideMsg : input.value).trim();
    if (!text) return;
    if (overrideMsg == null) input.value = '';
    input.style.height = 'auto';
    sendBtn.disabled = true;

    addBubble('user', text);
    // AI 气泡延迟到首个 token 再创建：保证「路由卡片 → 进度 → 答案」的视觉顺序，
    // 避免空气泡先占顶部，导致答案流进顶部气泡、跑在「已转接到」卡片上方（一坨）。
    let ai = null;
    let acc = '';
    let finalized = false;

    const ensureAi = () => (ai || (ai = addStreamingBubble()));

    const finalize = () => {
      if (finalized) return;
      finalized = true;
      if (ai) {
        if (acc) ai.bubble.innerHTML = renderMarkdown(acc);
        else ai.wrap.remove(); // 无文本（纯引导跳转）时移除空气泡
      }
      scrollBottom(); // 流式结束统一滚动到底部
    };

    try {
      await streamSSE('/api/v1/chat/stream', { session_id: sessionId, message: text }, (evt) => {
        console.log(`[stream ${(performance.now() / 1000).toFixed(2)}s] ${evt.type}`, evt.type === 'token' ? evt.content.slice(0, 20) : (evt.stage || ''));
        switch (evt.type) {
          case 'routing_decision':
            appendCard('route-card', `
              <div class="rc-head">🧭 已转接到「${escapeHtml(evt.agent_display)}」</div>
              <div class="rc-reason">${escapeHtml(evt.reason)}</div>
              <div class="rc-conf">置信度 ${percent(evt.confidence)} · 执行模式 ${escapeHtml(evt.execution_mode)}</div>
            `);
            break;
          case 'progress':
            appendCard('progress-line', `· ${escapeHtml(evt.stage)}`);
            break;
          case 'token':
            acc += evt.content;
            ensureAi().bubble.appendChild(document.createTextNode(evt.content)); // 增量追加，避免整段重设
            break;
          case 'meta': {
            const chips = [];
            if (evt.answer_mode) chips.push(`回答模式：${MODE_LABEL[evt.answer_mode] || evt.answer_mode}`);
            if (evt.confidence != null) chips.push(`置信度 ${percent(evt.confidence)}`);
            if (evt.sources?.length) chips.push(`引用 ${evt.sources.length} 篇`);
            if (chips.length) {
              const srcEl = appendCard('sources', '');
              chips.forEach((c) => {
                const b = document.createElement('span');
                b.className = 'src-badge';
                b.textContent = c;
                srcEl.appendChild(b);
              });
              (evt.sources || []).slice(0, 4).forEach((s) => {
                const b = document.createElement('span');
                b.className = 'src-badge';
                b.textContent = '📄 ' + s;
                srcEl.appendChild(b);
              });
            }
            break;
          }
          case 'guidance': {
            const el = appendCard('guidance-card', `
              <div class="gc-text md">${renderMarkdown(evt.message)}</div>
              ${evt.action_url ? `<button class="btn btn-primary btn-sm">${escapeHtml(evt.action_label || '前往')} →</button>` : ''}
            `);
            const btn = el.querySelector('button');
            if (btn) btn.addEventListener('click', () => navigate(evt.action_url));
            break;
          }
          case 'pipeline_plan': {
            const steps = (evt.steps || []).map((s) => `
              <div class="pc-step">
                <div class="pc-num">${s.step}</div>
                <div class="pc-body">
                  <div class="pc-label">${escapeHtml(s.label)}</div>
                  <div class="pc-desc">${escapeHtml(s.desc)}</div>
                  <button class="btn btn-primary btn-sm" data-url="${escapeHtml(s.action_url)}">${escapeHtml(s.action_label)} →</button>
                </div>
              </div>
            `).join('');
            const el = appendCard('pipeline-card', `
              <div class="pc-title">🔗 ${escapeHtml(evt.title)}</div>
              <div class="pc-intro">${escapeHtml(evt.intro)}</div>
              ${steps}
            `);
            el.querySelectorAll('button[data-url]').forEach((b) =>
              b.addEventListener('click', () => navigate(b.dataset.url))
            );
            break;
          }
          case 'error':
            acc += `\n\n⚠️ ${evt.message}`;
            ensureAi().bubble.textContent = acc;
            break;
          case 'done':
            finalize();
            break;
        }
      });
      finalize();
    } catch (err) {
      acc += `\n\n⚠️ ${err.message || '请求失败'}`;
      ensureAi(); // 请求级异常在首个 token 前抛出时，也要有气泡承载错误信息
      finalize();
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  };

  sendBtn.addEventListener('click', send);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  });
  container.querySelectorAll('.suggest-btn').forEach((btn) =>
    btn.addEventListener('click', () => send(btn.dataset.msg)));
}
