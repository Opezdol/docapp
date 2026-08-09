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
      sum.textContent =
        '[' + (i + 1) + '] Приказ №' + (cit.doc_number || '—') +
        ' · раздел ' + (cit.section || '—');
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

  var loaded = { docs: false, questions: false };
  var tabs = document.querySelectorAll('.consult-tab');

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var view = tab.getAttribute('data-view');
      tabs.forEach(function (t) { t.classList.toggle('active', t === tab); });
      ['chat', 'docs', 'questions'].forEach(function (v) {
        var el = document.getElementById('view-' + v);
        if (el) el.hidden = (v !== view);
      });
      if (view === 'docs' && !loaded.docs) { loaded.docs = true; loadDocuments(); }
      if (view === 'questions' && !loaded.questions) { loaded.questions = true; loadQuestions(); }
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
          return '<div class="doc-item">' +
            '<div class="doc-title">' + esc(title) + '</div>' +
            '<div class="doc-meta">' + esc(meta) + '</div>' +
            '<div class="doc-links">' +
            '<a href="/orders/documents/' + d.id + '">Читать</a>' +
            ' · ' +
            '<a href="/orders/documents/' + d.id + '/download" download>Скачать</a>' +
            '</div>' +
            '</div>';
        }).join('');
      })
      .catch(function (err) {
        list.innerHTML = '<p class="empty">Ошибка загрузки: ' + esc(err.message) + '</p>';
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
      return '<div class="totals-row">' +
        '<span>Сотрудник #' + esc(t.employee_id) + '</span>' +
        '<span>prompt ' + p + ' + completion ' + c + ' = ' + (p + c) + ' токенов · ' + n + ' ответов</span>' +
        '</div>';
    }).join('');
    el.innerHTML =
      '<h3>Расход токенов</h3>' +
      rows +
      '<div class="totals-row totals-grand">' +
      '<span>Всего</span>' +
      '<span>' + (sumP + sumC) + ' токенов · ' + sumN + ' ответов</span>' +
      '</div>';
  }

  function loadQuestions() {
    var list = document.getElementById('questions-list');
    var totals = document.getElementById('token-totals');
    list.innerHTML = '<p class="empty">Загрузка…</p>';
    totals.innerHTML = '';
    fetch('/orders/questions')
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
        var qs = data.questions || [];
        if (!qs.length) {
          list.innerHTML = '<p class="empty">Вопросов пока нет.</p>';
        } else {
          list.innerHTML = qs.map(function (q) {
            return '<div class="q-item">' +
              '<div class="q-content">' + esc(q.content) + '</div>' +
              '<div class="q-meta">Сотрудник #' + esc(q.employee_id) +
              ' · ' + esc(String(q.created_at || '').slice(0, 16)) + '</div>' +
              '</div>';
          }).join('');
        }
        renderTotals(totals, data.totals || []);
      })
      .catch(function (err) {
        list.innerHTML = '<p class="empty">Ошибка загрузки: ' + esc(err.message) + '</p>';
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
