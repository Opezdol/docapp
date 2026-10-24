/* Приложение на телефон: ярлык на экране «Домой» (PWA).
 *
 * Почему это нужно, хотя манифест в порядке: Android и Chrome показывают системное
 * окно установки сами, но один раз — кто закрыл, тот больше предложения не увидит;
 * а iPhone (Safari) такого окна не показывает вовсе, там установка только руками
 * через «Поделиться → На экран Домой». Поэтому в личном кабинете стоит постоянная
 * кнопка: на Android она вызывает системное окно, на iPhone показывает инструкцию.
 *
 * Как и остальные сценарии страниц: на чужой странице файл молча выходит.
 */
(function () {
  var block = document.getElementById('install-app');
  if (!block) return;

  var button = document.getElementById('install-app-button');
  var help = document.getElementById('install-app-help');
  var deferred = null;

  function alreadyInstalled() {
    if (window.navigator && window.navigator.standalone) return true; // ярлык на iOS
    if (window.matchMedia) {
      return window.matchMedia('(display-mode: standalone)').matches;
    }
    return false;
  }

  function isApplePhone() {
    var agent = (window.navigator && window.navigator.userAgent) || '';
    return /iPhone|iPad|iPod/.test(agent);
  }

  // Уже открыто как приложение — предлагать нечего.
  if (alreadyInstalled()) return;

  if (isApplePhone()) {
    // Системного окна нет: показываем инструкцию сразу, кнопку прячем.
    block.hidden = false;
    if (button) button.hidden = true;
    if (help) {
      help.hidden = false;
      help.open = true;
    }
    return;
  }

  window.addEventListener('beforeinstallprompt', function (event) {
    event.preventDefault(); // своё окно покажем по кнопке, а не сразу
    deferred = event;
    block.hidden = false;
    if (button) button.hidden = false;
  });

  window.addEventListener('appinstalled', function () {
    block.hidden = true;
  });

  if (button) {
    button.addEventListener('click', function () {
      if (!deferred) return;
      deferred.prompt();
      deferred = null;
    });
  }
})();
