/* Консультант по приказам (consult.html): чат со стримингом SSE, история,
   вкладки «Документы»/«Вопросы» (head), переиндексация. Vanilla JS, без CDN. */
(function () {
  'use strict';

  var CONV_KEY = 'consult_conv_id';
  var CONFIG = window.CONSULT || { isHead: false };

  var box = document.getElementById('consult-messages');
  var form = document.getElementById('consult-form');
  var input = document.getElementById('consult-input');
  var sendBtn = document.getElementById('consult-send');
  var newBtn = document.getElementById('consult-new');
  var busy = false;

  /* --- утилиты --- */

  function uuid() {
    if (window.crypto && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function renderText(s) {
    return esc(s)
      .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
      .replace(/\n/g, '<br>');
  }

  /* --- отрисовка чата --- */

  function addMessage(role, html) {
    var div = document.createElement('div');
    div.className = 'consult-msg ' + role;
    div.innerHTML = html;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return div;
  }

  function renderCitations(node, citations) {
    if (!citations || !citations.length) return;
    var wrap = document.createElement('div');
    wrap.className = 'consult-citations';
    citations.forEach(function (cit, i) {
      var det = document.createElement('details');
      var sum = document.createElement('summary');
      var link = document.createElement('a');
      link.textContent =
        '[' + (i + 1) + '] Приказ №' + (cit.doc_number || '—') +
        ' · раздел ' + (cit.section || '—');
      if (cit.document_id) {
        link.href = '/orders/documents/' + cit.document_id;
        link.title = 'Открыть полный текст приказа';
      }
      sum.appendChild(link);
      var body = document.createElement('div');
      body.className = 'cit-body';
      body.innerHTML = esc(cit.doc_title || '') + '<br>' + esc(cit.snippet || '');
      det.appendChild(sum);
      det.appendChild(body);
      wrap.appendChild(det);
    });
    node.appendChild(wrap);
  }

  function greeting() {
    return '<b>Здравствуйте!</b> Задайте вопрос по внутренним приказам — ответ придёт с цитатами документов.';
  }

  function showError(message) {
    addMessage('error', esc(message));
  }

  /* --- история беседы --- */

  function loadHistory() {
    fetch('/orders/conversation?conversation_id=' + encodeURIComponent(conversationId))
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

  /* --- стриминг ответа (SSE через fetch reader) --- */

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
          handleBlock(buffer); // хвост потока без финального \n\n
          if (!full) answerNode.innerHTML = ''; // убрать индикатор «…»
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

    fetch('/orders/ask', {
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

  /* --- вкладки (head): Документы и Вопросы --- */

  var loaded = { docs: false, questions: false, settings: false };
  var tabs = document.querySelectorAll('.consult-tab');

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var view = tab.getAttribute('data-view');
      tabs.forEach(function (t) { t.classList.toggle('active', t === tab); });
      ['chat', 'docs', 'questions', 'settings'].forEach(function (v) {
        var el = document.getElementById('view-' + v);
        if (el) el.hidden = (v !== view);
      });
      if (view === 'docs' && !loaded.docs) { loaded.docs = true; loadDocuments(); }
      if (view === 'questions' && !loaded.questions) { loaded.questions = true; loadQuestions(); }
      if (view === 'settings' && !loaded.settings) { loaded.settings = true; loadSettings(); }
    });
  });

  function loadDocuments() {
    var list = document.getElementById('docs-list');
    list.innerHTML = '<p class="empty">Загрузка…</p>';
    fetch('/orders/documents')
      .then(function (r) {
        if (r.status === 401) { location.href = '/login'; return null; }
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        var docs = data.documents || [];
        if (!docs.length) {
          list.innerHTML = '<p class="empty">Документов в индексе нет.</p>';
          return;
        }
        list.innerHTML = docs.map(function (d) {
          var title = d.doc_number ? 'Приказ №' + d.doc_number : d.filename;
          var meta = [d.title, d.filename, String(d.added_at || '').slice(0, 10)]
            .filter(Boolean).join(' · ');
          var del = CONFIG.isHead
            ? ' · <button type="button" class="linklike doc-delete" data-id="' + d.id + '" data-name="' + esc(d.filename) + '">Удалить</button>'
            : '';
          return '<div class="doc-item">' +
            '<div class="doc-title">' + esc(title) + '</div>' +
            '<div class="doc-meta">' + esc(meta) + '</div>' +
            '<div class="doc-links">' +
            '<a href="/orders/documents/' + d.id + '">Читать</a>' +
            ' · ' +
            '<a href="/orders/documents/' + d.id + '/download" download>Скачать</a>' +
            del +
            '</div>' +
            '</div>';
        }).join('');
        attachDeleteHandlers();
      })
      .catch(function (err) {
        list.innerHTML = '<p class="empty">Ошибка загрузки: ' + esc(err.message) + '</p>';
      });
  }

  function attachDeleteHandlers() {
    var btns = document.querySelectorAll('.doc-delete');
    btns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.getAttribute('data-id');
        var name = btn.getAttribute('data-name');
        if (!window.confirm('Удалить приказ «' + name + '» и пересобрать индекс?')) return;
        fetch('/orders/documents/' + id, { method: 'DELETE' })
          .then(function (r) {
            if (r.status === 401) { location.href = '/login'; return null; }
            if (!r.ok) return parseError(r);
            return r.json();
          })
          .then(function (data) {
            if (!data) return;
            if (busyEl) busyEl.hidden = false;
            pollStatus();
          })
          .catch(function (err) {
            window.alert('Ошибка удаления: ' + err.message);
          });
      });
    });
  }

  function renderTotals(el, totals) {
    if (!totals.length) {
      el.innerHTML = '<p class="empty">Расхода токенов ещё нет.</p>';
      return;
    }
    var sumP = 0, sumC = 0, sumN = 0;
    var rows = totals.map(function (t) {
      var p = t.total_prompt || 0, c = t.total_completion || 0, n = t.count || 0;
      sumP += p; sumC += c; sumN += n;
      var name = t.employee_name ? esc(t.employee_name) : ('Сотрудник #' + esc(t.employee_id));
      return '<div class="totals-row">' +
        '<span>' + name + '</span>' +
        '<span>prompt ' + p + ' + completion ' + c + ' = ' + (p + c) + ' токенов · ' + n + ' ответов</span>' +
        '</div>';
    }).join('');
    el.innerHTML =
      '<h3>Расход токенов по сотрудникам</h3>' +
      rows +
      '<div class="totals-row totals-grand">' +
      '<span>Всего</span>' +
      '<span>' + (sumP + sumC) + ' токенов · ' + sumN + ' ответов</span>' +
      '</div>';
  }

  function renderByDay(el, byDay) {
    if (!byDay.length) {
      el.innerHTML = '';
      return;
    }
    var rows = byDay.map(function (d) {
      var p = d.total_prompt || 0, c = d.total_completion || 0;
      return '<div class="totals-row">' +
        '<span>' + esc(d.day) + '</span>' +
        '<span>prompt ' + p + ' + completion ' + c + ' = ' + (p + c) + ' токенов · ' + (d.count || 0) + ' ответов</span>' +
        '</div>';
    }).join('');
    el.innerHTML = '<h3>Расход токенов по дням</h3>' + rows;
  }

  function loadQuestions() {
    var list = document.getElementById('questions-list');
    var totals = document.getElementById('token-totals');
    var byDayEl = document.getElementById('token-by-day');
    list.innerHTML = '<p class="empty">Загрузка…</p>';
    totals.innerHTML = '';
    byDayEl.innerHTML = '';
    var url = '/orders/questions';
    var from = document.getElementById('questions-from').value;
    var to = document.getElementById('questions-to').value;
    var qs = [];
    if (from) qs.push('from=' + encodeURIComponent(from));
    if (to) qs.push('to=' + encodeURIComponent(to));
    if (qs.length) url += '?' + qs.join('&');
    fetch(url)
      .then(function (r) {
        if (r.status === 401) { location.href = '/login'; return null; }
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (body) {
            throw new Error(body.error || ('HTTP ' + r.status));
          });
        }
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        var qsList = data.questions || [];
        if (!qsList.length) {
          list.innerHTML = '<p class="empty">Вопросов пока нет.</p>';
        } else {
          list.innerHTML = qsList.map(function (q) {
            var name = q.employee_name ? esc(q.employee_name) : ('Сотрудник #' + esc(q.employee_id));
            return '<div class="q-item">' +
              '<div class="q-content">' + esc(q.content) + '</div>' +
              '<div class="q-meta">' + name +
              ' · ' + esc(String(q.created_at || '').slice(0, 16)) + '</div>' +
              '</div>';
          }).join('');
        }
        renderTotals(totals, data.totals || []);
        renderByDay(byDayEl, data.by_day || []);
      })
      .catch(function (err) {
        list.innerHTML = '<p class="empty">Ошибка загрузки: ' + esc(err.message) + '</p>';
      });
  }

  function loadSettings() {
    var panel = document.getElementById('settings-panel');
    panel.innerHTML = '<p class="empty">Загрузка…</p>';
    fetch('/orders/settings')
      .then(function (r) {
        if (r.status === 401) { location.href = '/login'; return null; }
        if (!r.ok) return parseError(r);
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        var d = data.defaults || {};
        panel.innerHTML =
          '<form id="settings-form">' +
          '<label class="settings-field">Системный промпт' +
          '<textarea id="set-prompt" rows="6">' + esc(data.system_prompt) + '</textarea></label>' +
          '<div class="settings-row">' +
          '<label class="settings-field">Фрагментов (top_k, 1–20)' +
          '<input id="set-topk" type="number" min="1" max="20" value="' + esc(String(data.top_k)) + '"></label>' +
          '<label class="settings-field">Температура (0–1)' +
          '<input id="set-temp" type="number" min="0" max="1" step="0.1" value="' + esc(String(data.temperature)) + '"></label>' +
          '<label class="settings-field">Сообщений истории (0–20)' +
          '<input id="set-history" type="number" min="0" max="20" value="' + esc(String(data.history_messages)) + '"></label>' +
          '</div>' +
          '<div class="consult-actions">' +
          '<button type="button" id="settings-reset" class="btn">Сбросить к значениям по умолчанию</button>' +
          '<button type="submit" class="primary">Сохранить</button>' +
          '</div>' +
          '</form>' +
          '<p class="hint">По умолчанию: top_k ' + esc(String(d.top_k)) +
          ', температура ' + esc(String(d.temperature)) +
          ', история ' + esc(String(d.history_messages)) + ' сообщений.</p>';

        document.getElementById('settings-reset').addEventListener('click', function () {
          document.getElementById('set-prompt').value = d.system_prompt || '';
          document.getElementById('set-topk').value = d.top_k;
          document.getElementById('set-temp').value = d.temperature;
          document.getElementById('set-history').value = d.history_messages;
        });

        document.getElementById('settings-form').addEventListener('submit', function (e) {
          e.preventDefault();
          var payload = {
            system_prompt: document.getElementById('set-prompt').value,
            top_k: document.getElementById('set-topk').value,
            temperature: document.getElementById('set-temp').value,
            history_messages: document.getElementById('set-history').value
          };
          fetch('/orders/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          })
            .then(function (r) {
              if (r.status === 401) { location.href = '/login'; return null; }
              if (!r.ok) return parseError(r);
              return r.json();
            })
            .then(function (res) {
              if (!res) return;
              window.alert('Настройки сохранены');
              loadSettings();
            })
            .catch(function (err) {
              window.alert('Ошибка сохранения: ' + err.message);
            });
        });
      })
      .catch(function (err) {
        panel.innerHTML = '<p class="empty">Ошибка загрузки: ' + esc(err.message) + '</p>';
      });
  }

  /* --- фильтр периода в аналитике (head) --- */

  var qApply = document.getElementById('questions-apply');
  var qReset = document.getElementById('questions-reset');
  if (qApply) {
    qApply.addEventListener('click', function () {
      loaded.questions = true;
      loadQuestions();
    });
  }
  if (qReset) {
    qReset.addEventListener('click', function () {
      document.getElementById('questions-from').value = '';
      document.getElementById('questions-to').value = '';
      loaded.questions = true;
      loadQuestions();
    });
  }

  /* --- переиндексация и загрузка документов (head, F11) --- */

  var busyEl = document.getElementById('consult-busy');

  function pollStatus() {
    fetch('/orders/status')
      .then(function (r) {
        if (r.status === 401) { location.href = '/login'; return null; }
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        if (data.busy) {
          if (busyEl) busyEl.hidden = false;
          setTimeout(pollStatus, 2000);
          return;
        }
        if (busyEl) busyEl.hidden = true;
        if (data.error) {
          window.alert('Ошибка переиндексации: ' + data.error);
        } else {
          window.alert(
            'Индекс обновлён: ' + data.documents + ' документов, ' +
            data.chunks + ' фрагментов'
          );
        }
        location.reload();
      })
      .catch(function (err) {
        if (busyEl) busyEl.hidden = true;
        window.alert('Ошибка проверки статуса: ' + err.message);
      });
  }

  function parseError(r) {
    return r.json().catch(function () { return {}; }).then(function (body) {
      throw new Error(body.error || ('HTTP ' + r.status));
    });
  }

  var uploadForm = document.getElementById('consult-upload-form');
  var uploadInput = document.getElementById('consult-upload-input');
  if (uploadForm && uploadInput) {
    uploadForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var files = uploadInput.files;
      if (!files || !files.length) return;
      var fd = new FormData();
      for (var i = 0; i < files.length; i++) fd.append('files', files[i]);
      fetch('/orders/documents/upload', { method: 'POST', body: fd })
        .then(function (r) {
          if (r.status === 401) { location.href = '/login'; return null; }
          if (!r.ok) return parseError(r);
          return r.json();
        })
        .then(function (data) {
          if (!data) return;
          uploadInput.value = '';            // очистить выбор после отправки
          if (busyEl) busyEl.hidden = false;
          pollStatus();
        })
        .catch(function (err) {
          window.alert('Ошибка загрузки: ' + err.message);
        });
    });
  }

  var reindexBtn = document.getElementById('consult-reindex');
  if (reindexBtn) {
    reindexBtn.addEventListener('click', function () {
      if (!window.confirm('Пересобрать индекс из папки приказов?')) return;
      reindexBtn.disabled = true;
      fetch('/orders/reindex', { method: 'POST' })
        .then(function (r) {
          if (r.status === 401) { location.href = '/login'; return null; }
          if (!r.ok) return parseError(r);
          return r.json();
        })
        .then(function (data) {
          if (!data) return;
          if (busyEl) busyEl.hidden = false;
          pollStatus();
        })
        .catch(function (err) {
          window.alert('Ошибка: ' + err.message);
          reindexBtn.disabled = false;
        });
    });
  }

  /* --- «Новый разговор» --- */

  if (newBtn) {
    newBtn.addEventListener('click', function () {
      conversationId = uuid();
      localStorage.setItem(CONV_KEY, conversationId);
      box.innerHTML = '';
      addMessage('bot', greeting());
      input.focus();
    });
  }

  /* --- события формы --- */

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    send();
  });

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  input.addEventListener('input', function () {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  });

  /* --- запуск --- */

  var conversationId = localStorage.getItem(CONV_KEY);
  if (!conversationId) {
    conversationId = uuid();
    localStorage.setItem(CONV_KEY, conversationId);
  }

  loadHistory();
  input.focus();
})();
