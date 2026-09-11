/* Чат (compendium.html): чат со стримингом SSE, статьи, источники,
   аналитика (head), настройки (head). Vanilla JS, без CDN. */
(function () {
  'use strict';

  var CONV_KEY = 'compendium_conv_id';
  var CONFIG = window.COMPENDIUM || { isCurator: false, isHead: false };

  var box = document.getElementById('compendium-messages');
  var form = document.getElementById('compendium-form');
  var input = document.getElementById('compendium-input');
  var sendBtn = document.getElementById('compendium-send');
  var newBtn = document.getElementById('compendium-new');
  var busy = false;
  var conversationId = localStorage.getItem(CONV_KEY) || '';

  var esc = dc.esc;

  function renderText(s) {
    return esc(s)
      .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
      .replace(/\n/g, '<br>');
  }

  function addMessage(role, html) {
    var div = document.createElement('div');
    div.className = 'compendium-msg ' + role;
    div.innerHTML = html;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return div;
  }

  function renderCitations(node, citations) {
    if (!citations || !citations.length) return;
    var wrap = document.createElement('div');
    wrap.className = 'compendium-citations';
    citations.forEach(function (cit, i) {
      var det = document.createElement('details');
      var sum = document.createElement('summary');
      var link = document.createElement('a');
      link.textContent = '[' + (i + 1) + '] ' + (cit.article_title || 'Статья') +
        (cit.section ? ' · ' + cit.section : '');
      if (cit.article_id) {
        link.href = '/wiki/articles/' + cit.article_id;
        link.title = 'Открыть статью';
      }
      sum.appendChild(link);
      var body = document.createElement('div');
      body.className = 'cit-body';
      body.innerHTML = esc(cit.snippet || '');
      det.appendChild(sum);
      det.appendChild(body);
      wrap.appendChild(det);
    });
    node.appendChild(wrap);
  }

  function greeting() {
    return '<b>Здравствуйте!</b> Задайте вопрос по базе знаний отделения — ответ придёт с цитатами статей.';
  }

  function showError(message) {
    addMessage('error', esc(message));
  }

  function loadHistory() {
    if (!conversationId) { addMessage('bot', greeting()); return; }
    fetch('/wiki/conversation?conversation_id=' + encodeURIComponent(conversationId))
      .then(function (r) {
        if (r.status === 401) { location.href = '/login'; return null; }
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        var msgs = data.messages || [];
        if (!msgs.length) { addMessage('bot', greeting()); return; }
        msgs.forEach(function (m) {
          var node = addMessage(m.role === 'user' ? 'user' : 'bot', renderText(m.content));
          if (m.role !== 'user' && m.citations && m.citations.length) {
            renderCitations(node, m.citations);
          }
        });
      })
      .catch(function (err) {
        showError('Не удалось загрузить историю: ' + err.message);
      });
  }

  function readStream(response, answerNode) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';
    var full = '';
    var doneEvent = null;

    function handleBlock(block) {
      if (!block.trim()) return;
      var data = '';
      block.split(/\r?\n/).forEach(function (line) {
        if (line.indexOf('data: ') === 0) data += line.slice(6);
        else if (line.indexOf('data:') === 0) data += line.slice(5);
      });
      if (!data) return;
      var evt;
      try { evt = JSON.parse(data); } catch (e) { return; }
      if (evt.type === 'delta') {
        full += evt.text || '';
        answerNode.innerHTML = renderText(full);
        box.scrollTop = box.scrollHeight;
      } else if (evt.type === 'done') {
        doneEvent = evt;
      }
    }

    function pump() {
      return reader.read().then(function (res) {
        if (res.done) {
          handleBlock(buffer);
          if (!full) answerNode.innerHTML = '';
          return doneEvent || { type: 'done', citations: [], conversation_id: conversationId };
        }
        buffer += decoder.decode(res.value, { stream: true });
        var parts = buffer.split('\n\n');
        buffer = parts.pop();
        parts.forEach(handleBlock);
        return pump();
      });
    }
    return pump();
  }

  function send() {
    if (busy) return;
    var q = (input.value || '').trim();
    if (!q) return;
    addMessage('user', renderText(q));
    input.value = '';
    input.style.height = '';
    var answer = addMessage('bot', '<span>…</span>');
    busy = true;
    sendBtn.disabled = true;

    fetch('/wiki/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q, conversation_id: conversationId })
    })
      .then(function (r) {
        if (r.status === 401) { location.href = '/login'; return null; }
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (body) {
            throw new Error(body.error || ('HTTP ' + r.status));
          });
        }
        return readStream(r, answer);
      })
      .then(function (done) {
        if (!done) return;
        if (done.conversation_id) {
          conversationId = done.conversation_id;
          localStorage.setItem(CONV_KEY, conversationId);
        }
        renderCitations(answer, done.citations || []);
      })
      .catch(function (err) {
        showError('Ошибка: ' + err.message);
      })
      .finally(function () {
        busy = false;
        sendBtn.disabled = false;
        input.focus();
      });
  }

  form.addEventListener('submit', function (e) { e.preventDefault(); send(); });
  newBtn.addEventListener('click', function () {
    conversationId = '';
    localStorage.removeItem(CONV_KEY);
    box.innerHTML = '';
    addMessage('bot', greeting());
  });

  /* --- вкладки --- */

  var loaded = { articles: false, sources: false, questions: false, settings: false };
  var tabs = document.querySelectorAll('.compendium-tab');

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var view = tab.getAttribute('data-view');
      tabs.forEach(function (t) { t.classList.toggle('active', t === tab); });
      ['chat', 'articles', 'sources', 'questions', 'settings'].forEach(function (v) {
        var el = document.getElementById('view-' + v);
        if (el) el.hidden = (v !== view);
      });
      if (view === 'articles' && !loaded.articles) { loaded.articles = true; loadArticles(); }
      if (view === 'sources' && !loaded.sources) { loaded.sources = true; loadSources(); }
      if (view === 'questions' && !loaded.questions) { loaded.questions = true; loadQuestions(); }
      if (view === 'settings' && !loaded.settings) { loaded.settings = true; loadSettings(); }
    });
  });

  /* --- статьи --- */

  function statusLabel(status) {
    return { draft: 'Черновик', published: 'Опубликована', archived: 'Архив' }[status] || status;
  }

  function loadArticles() {
    var list = document.getElementById('articles-list');
    list.innerHTML = '<p class="empty">Загрузка…</p>';
    fetch('/wiki/articles')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var articles = data.articles || [];
        if (!articles.length) {
          list.innerHTML = '<p class="empty">Статей пока нет.</p>';
          return;
        }
        list.innerHTML = articles.map(function (a) {
          var actions = '';
          if (CONFIG.isCurator) {
            actions += ' · <button type="button" class="linklike article-edit" data-id="' + a.id + '">Править</button>';
            actions += (a.status === 'published')
              ? ' · <button type="button" class="linklike article-unpublish" data-id="' + a.id + '">Снять с публикации</button>'
              : ' · <button type="button" class="linklike article-publish" data-id="' + a.id + '">Опубликовать</button>';
            actions += ' · <button type="button" class="linklike article-delete" data-id="' + a.id + '">Удалить</button>';
          }
          return '<div class="doc-item">' +
            '<div class="doc-title">' + esc(a.title || '(без названия)') + ' <span class="status ' + esc(a.status) + '">' + statusLabel(a.status) + '</span></div>' +
            '<div class="doc-meta">v' + esc(a.version) + ' · ' + esc(String(a.updated_at || '').slice(0, 10)) + '</div>' +
            '<div class="doc-links"><a href="/wiki/articles/' + a.id + '">Читать</a>' + actions + '</div>' +
            '</div>';
        }).join('');
        attachArticleHandlers();
      })
      .catch(function (err) {
        list.innerHTML = '<p class="empty">Ошибка: ' + esc(err.message) + '</p>';
      });
  }

  function attachArticleHandlers() {
    document.querySelectorAll('.article-edit').forEach(function (btn) {
      btn.addEventListener('click', function () { openArticleEditor(btn.getAttribute('data-id')); });
    });
    document.querySelectorAll('.article-publish').forEach(function (btn) {
      btn.addEventListener('click', function () { articleAction('/wiki/articles/' + btn.getAttribute('data-id') + '/publish'); });
    });
    document.querySelectorAll('.article-unpublish').forEach(function (btn) {
      btn.addEventListener('click', function () { articleAction('/wiki/articles/' + btn.getAttribute('data-id') + '/unpublish'); });
    });
    document.querySelectorAll('.article-delete').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.getAttribute('data-id');
        if (!window.confirm('Удалить статью #' + id + '?')) return;
        fetch('/wiki/articles/' + id, { method: 'DELETE' })
          .then(function (r) { if (!r.ok) return r.json().then(function (b) { throw new Error(b.error); }); return r.json(); })
          .then(function () { loaded.articles = false; loadArticles(); })
          .catch(function (err) { window.alert('Ошибка: ' + err.message); });
      });
    });
  }

  function articleAction(url) {
    fetch(url, { method: 'POST' })
      .then(function (r) { if (!r.ok) return r.json().then(function (b) { throw new Error(b.error); }); return r.json(); })
      .then(function () { loaded.articles = false; loadArticles(); })
      .catch(function (err) { window.alert('Ошибка: ' + err.message); });
  }

  function openArticleEditor(id) {
    fetch('/wiki/articles/' + id)
      .then(function (r) { return r.json(); })
      .then(function (article) {
        document.getElementById('compendium-editor-title').textContent = 'Правка: ' + (article.title || '#');
        document.getElementById('compendium-article-id').value = article.id;
        document.getElementById('compendium-body-md').value = article.body_md || '';
        document.getElementById('view-articles').scrollIntoView({ behavior: 'smooth' });
      })
      .catch(function (err) { window.alert('Ошибка: ' + err.message); });
  }

  var saveBtn = document.getElementById('compendium-save');
  var publishBtn = document.getElementById('compendium-publish');
  var newArticleBtn = document.getElementById('compendium-new-article');

  function resetEditor() {
    document.getElementById('compendium-editor-title').textContent = 'Новая статья';
    document.getElementById('compendium-article-id').value = '';
    document.getElementById('compendium-body-md').value = '';
    document.getElementById('compendium-change-note').value = '';
  }

  newArticleBtn.addEventListener('click', resetEditor);

  function saveArticle(publish) {
    var id = document.getElementById('compendium-article-id').value;
    var body = {
      body_md: document.getElementById('compendium-body-md').value,
      change_note: document.getElementById('compendium-change-note').value
    };
    if (id) body.article_id = parseInt(id, 10);
    fetch('/wiki/articles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
      .then(function (r) { if (!r.ok) return r.json().then(function (b) { throw new Error(b.error); }); return r.json(); })
      .then(function (data) {
        var articleId = data.article.id;
        if (publish) {
          return fetch('/wiki/articles/' + articleId + '/publish', { method: 'POST' })
            .then(function (r) { if (!r.ok) return r.json().then(function (b) { throw new Error(b.error); }); return r.json(); });
        }
        return null;
      })
      .then(function () {
        loaded.articles = false;
        loadArticles();
        if (!publish) window.alert('Черновик сохранён. Нажмите «Опубликовать», чтобы статья участвовала в ответах.');
      })
      .catch(function (err) { window.alert('Ошибка: ' + err.message); });
  }

  saveBtn.addEventListener('click', function () { saveArticle(false); });
  publishBtn.addEventListener('click', function () { saveArticle(true); });

  /* --- источники --- */

  function loadSources() {
    var list = document.getElementById('sources-list');
    list.innerHTML = '<p class="empty">Загрузка…</p>';
    fetch('/wiki/sources')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var sources = data.sources || [];
        if (!sources.length) {
          list.innerHTML = '<p class="empty">Источников пока нет.</p>';
          return;
        }
        list.innerHTML = sources.map(function (s) {
          var del = CONFIG.isCurator
            ? ' · <button type="button" class="linklike source-delete" data-id="' + s.id + '">Удалить</button>'
            : '';
          return '<div class="doc-item">' +
            '<div class="doc-title">' + esc(s.title || s.filename) + '</div>' +
            '<div class="doc-meta">' + esc(s.filename) + ' · OCR: ' + esc(s.ocr_status) + ' · стр.: ' + esc(s.page_count) + '</div>' +
            '<div class="doc-links">' +
            '<a href="/wiki/sources/' + s.id + '/text">Текст</a>' +
            ' · <a href="/wiki/sources/' + s.id + '/download" download>Скачать PDF</a>' +
            del +
            '</div></div>';
        }).join('');
        document.querySelectorAll('.source-delete').forEach(function (btn) {
          btn.addEventListener('click', function () {
            var id = btn.getAttribute('data-id');
            if (!window.confirm('Удалить источник #' + id + '?')) return;
            fetch('/wiki/sources/' + id, { method: 'DELETE' })
              .then(function (r) { if (!r.ok) return r.json().then(function (b) { throw new Error(b.error); }); return r.json(); })
              .then(function () { loaded.sources = false; loadSources(); })
              .catch(function (err) { window.alert('Ошибка: ' + err.message); });
          });
        });
      })
      .catch(function (err) {
        list.innerHTML = '<p class="empty">Ошибка: ' + esc(err.message) + '</p>';
      });
  }

  var uploadForm = document.getElementById('compendium-upload-form');
  if (uploadForm) {
    uploadForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var inputFile = document.getElementById('compendium-upload-input');
      if (!inputFile.files.length) return;
      var fd = new FormData();
      Array.prototype.forEach.call(inputFile.files, function (f) { fd.append('files', f); });
      fetch('/wiki/sources/upload', { method: 'POST', body: fd })
        .then(function (r) { if (!r.ok) return r.json().then(function (b) { throw new Error(b.error); }); return r.json(); })
        .then(function (data) {
          window.alert('Загружено: ' + data.saved + '. Распознавание (OCR) запущено в фоне.');
          loaded.sources = false;
          loadSources();
        })
        .catch(function (err) { window.alert('Ошибка: ' + err.message); });
    });
  }

  /* --- вопросы (head) --- */

  function loadQuestions(from, to) {
    var list = document.getElementById('questions-list');
    var totalsEl = document.getElementById('token-totals');
    var byDayEl = document.getElementById('token-by-day');
    list.innerHTML = '<p class="empty">Загрузка…</p>';
    var params = new URLSearchParams();
    if (from) params.set('from', from);
    if (to) params.set('to', to);
    fetch('/wiki/questions?' + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var qs = data.questions || [];
        list.innerHTML = qs.length
          ? qs.map(function (q) {
              return '<div class="q-item"><div class="q-content">' + esc(q.content) + '</div>' +
                '<div class="q-meta">' + esc(q.employee_name || ('#' + q.employee_id)) + ' · ' + esc(String(q.created_at || '').slice(0, 16)) + '</div></div>';
            }).join('')
          : '<p class="empty">Вопросов нет.</p>';
        renderTotals(totalsEl, data.totals || []);
        renderByDay(byDayEl, data.by_day || []);
      })
      .catch(function (err) { list.innerHTML = '<p class="empty">Ошибка: ' + esc(err.message) + '</p>'; });
  }

  function renderTotals(el, totals) {
    if (!totals.length) { el.innerHTML = '<p class="empty">Расхода токенов нет.</p>'; return; }
    var sumP = 0, sumC = 0;
    var rows = totals.map(function (t) {
      sumP += t.total_prompt || 0; sumC += t.total_completion || 0;
      return '<div class="totals-row"><span>' + esc(t.employee_name || ('#' + t.employee_id)) + '</span>' +
        '<span>' + (t.total_prompt || 0) + ' / ' + (t.total_completion || 0) + '</span></div>';
    }).join('');
    el.innerHTML = '<h3>Расход токенов</h3>' + rows +
      '<div class="totals-row"><b>Итого</b><b>' + sumP + ' / ' + sumC + '</b></div>';
  }

  function renderByDay(el, byDay) {
    if (!byDay.length) { el.innerHTML = ''; return; }
    var rows = byDay.map(function (d) {
      return '<div class="totals-row"><span>' + esc(d.day) + '</span><span>' +
        (d.total_prompt || 0) + ' / ' + (d.total_completion || 0) + '</span></div>';
    }).join('');
    el.innerHTML = '<h3>По дням</h3>' + rows;
  }

  var qFrom = document.getElementById('questions-from');
  var qTo = document.getElementById('questions-to');
  var qApply = document.getElementById('questions-apply');
  var qReset = document.getElementById('questions-reset');
  if (qApply) {
    qApply.addEventListener('click', function () { loadQuestions(qFrom.value, qTo.value); });
    qReset.addEventListener('click', function () { qFrom.value = ''; qTo.value = ''; loadQuestions(); });
  }

  /* --- настройки (head) --- */

  function loadSettings() {
    var panel = document.getElementById('settings-panel');
    panel.innerHTML = '<p class="empty">Загрузка…</p>';
    fetch('/wiki/settings')
      .then(function (r) { return r.json(); })
      .then(function (s) {
        panel.innerHTML =
          '<label>Системный промпт<textarea id="set-prompt" rows="4">' + esc(s.system_prompt) + '</textarea></label>' +
          '<div class="settings-row">' +
          '<label>top_k (разделов в контексте)<input type="number" id="set-topk" value="' + esc(s.top_k) + '" min="1" max="20"></label>' +
          '<label>Температура<input type="number" id="set-temp" value="' + esc(s.temperature) + '" min="0" max="1" step="0.1"></label>' +
          '<label>История (реплик)<input type="number" id="set-hist" value="' + esc(s.history_messages) + '" min="0" max="20"></label>' +
          '</div>' +
          '<button type="button" id="set-save" class="primary">Сохранить</button>';
        document.getElementById('set-save').addEventListener('click', function () {
          fetch('/wiki/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              system_prompt: document.getElementById('set-prompt').value,
              top_k: document.getElementById('set-topk').value,
              temperature: document.getElementById('set-temp').value,
              history_messages: document.getElementById('set-hist').value
            })
          })
            .then(function (r) { if (!r.ok) return r.json().then(function (b) { throw new Error(b.error); }); return r.json(); })
            .then(function () { loadSettings(); })
            .catch(function (err) { window.alert('Ошибка: ' + err.message); });
        });
      })
      .catch(function (err) { panel.innerHTML = '<p class="empty">Ошибка: ' + esc(err.message) + '</p>'; });
  }

  /* --- старт --- */
  loadHistory();
})();
