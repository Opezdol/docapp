/* Страница статьи (article.html): режим «Править», публикация/снятие, удаление.
   Только для кураторов (head/editor) — шаблон подключает скрипт условно. */
(function () {
  'use strict';

  var CONFIG = window.ARTICLE || { id: 0, isCurator: false };
  if (!CONFIG.isCurator) return;

  var editBtn = document.getElementById('article-edit');
  var cancelBtn = document.getElementById('article-cancel');
  var viewEl = document.getElementById('article-view');
  var editPanel = document.getElementById('article-edit-panel');
  var bodyEl = document.getElementById('article-edit-body');
  var noteEl = document.getElementById('article-edit-note');
  var saveBtn = document.getElementById('article-save');

  editBtn.addEventListener('click', function () {
    viewEl.hidden = true;
    editPanel.hidden = false;
    editBtn.hidden = true;
    cancelBtn.hidden = false;
    bodyEl.focus();
  });

  cancelBtn.addEventListener('click', function () {
    viewEl.hidden = false;
    editPanel.hidden = true;
    editBtn.hidden = false;
    cancelBtn.hidden = true;
  });

  saveBtn.addEventListener('click', function () {
    var fd = new FormData();
    fd.append('body_md', bodyEl.value);
    fd.append('change_note', noteEl.value);
    fetch('/compendium/articles/' + CONFIG.id + '/edit', { method: 'POST', body: fd })
      .then(function (r) {
        if (r.redirected || r.status === 303) { location.reload(); return; }
        if (!r.ok) throw new Error('HTTP ' + r.status);
        location.reload();
      })
      .catch(function (err) { window.alert('Ошибка: ' + err.message); });
  });

  var pubBtn = document.getElementById('article-publish');
  if (pubBtn) {
    pubBtn.addEventListener('click', function () {
      postAndReload('/compendium/articles/' + CONFIG.id + '/publish');
    });
  }
  var unpubBtn = document.getElementById('article-unpublish');
  if (unpubBtn) {
    unpubBtn.addEventListener('click', function () {
      postAndReload('/compendium/articles/' + CONFIG.id + '/unpublish');
    });
  }
  var delBtn = document.getElementById('article-delete');
  if (delBtn) {
    delBtn.addEventListener('click', function () {
      if (!window.confirm('Удалить статью? Это действие необратимо.')) return;
      fetch('/compendium/articles/' + CONFIG.id, { method: 'DELETE' })
        .then(function (r) {
          if (r.status === 401) { location.href = '/login'; return; }
          if (!r.ok) return r.json().then(function (b) { throw new Error(b.error || 'HTTP ' + r.status); });
          location.href = '/compendium';
        })
        .catch(function (err) { window.alert('Ошибка: ' + err.message); });
    });
  }

  function postAndReload(url) {
    fetch(url, { method: 'POST' })
      .then(function (r) {
        if (r.status === 401) { location.href = '/login'; return; }
        if (!r.ok) return r.json().then(function (b) { throw new Error(b.error || 'HTTP ' + r.status); });
        location.reload();
      })
      .catch(function (err) { window.alert('Ошибка: ' + err.message); });
  }
})();
