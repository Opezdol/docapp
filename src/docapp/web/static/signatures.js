/* Моя подпись: поворот, уменьшение и отправка (ТЗ-кровь §6, ADR-0029).
 *
 * Телефон делает то, что требует пальца: поворот и уменьшение до 1600 точек.
 * Прозрачность и обрезку полей делает сервер (`signatures/png.py`): порог зависит
 * от освещения, и одна реализация с тестами надёжнее двух.
 *
 * Как и остальные сценарии страниц: на чужой странице файл молча выходит.
 */
(function () {
  var app = document.getElementById('signature-app');
  if (!app) return;

  var input = document.getElementById('signature-file');
  var canvas = document.getElementById('signature-canvas');
  var cropBox = document.getElementById('signature-crop');
  var saveBtn = document.getElementById('signature-save');
  var rotateBtn = document.getElementById('signature-rotate');
  var banner = document.getElementById('signature-banner');

  /* Подпись печатается в графе 3–4 см, поэтому 1600 точек — с большим запасом
     и по весу (200–400 КБ), и по времени отправки с телефона. */
  var MAX_SIDE = 1600;
  var image = null;
  var angle = 0;

  function draw() {
    if (!image || !canvas) return;
    var scale = Math.min(1, MAX_SIDE / Math.max(image.width, image.height));
    var width = Math.round(image.width * scale);
    var height = Math.round(image.height * scale);
    var turned = (angle % 180) !== 0;
    canvas.width = turned ? height : width;
    canvas.height = turned ? width : height;
    var ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();
    ctx.translate(canvas.width / 2, canvas.height / 2);
    ctx.rotate((angle * Math.PI) / 180);
    ctx.drawImage(image, -width / 2, -height / 2, width, height);
    ctx.restore();
  }

  function picked(file) {
    var reader = new FileReader();
    reader.onload = function () {
      var picture = new Image();
      picture.onload = function () {
        image = picture;
        angle = 0;
        if (cropBox) cropBox.hidden = false;
        draw();
      };
      picture.onerror = function () {
        dc.banner(banner, 'Файл не читается как картинка', 'err');
      };
      picture.src = reader.result;
    };
    reader.readAsDataURL(file);
  }

  function send() {
    if (!canvas) return;
    canvas.toBlob(function (blob) {
      if (!blob) {
        dc.banner(banner, 'Не удалось подготовить картинку', 'err');
        return;
      }
      var data = new FormData();
      data.append('image', blob, 'signature.png');
      /* fetch, а не dc.apiFetch: multipart нельзя отправлять с JSON-заголовками. */
      fetch('/signatures/api/upload', { method: 'POST', body: data })
        .then(function (response) {
          return response
            .json()
            .catch(function () { return {}; })
            .then(function (body) {
              if (!response.ok) {
                throw new Error(body.detail || body.error || 'HTTP ' + response.status);
              }
              return body;
            });
        })
        .then(function () {
          location.reload();
        })
        .catch(function (error) {
          dc.banner(banner, 'Ошибка: ' + error.message, 'err');
        });
    }, 'image/png');
  }

  if (input) {
    input.addEventListener('change', function () {
      if (input.files && input.files.length) picked(input.files[0]);
    });
  }
  if (rotateBtn) {
    rotateBtn.addEventListener('click', function () {
      angle = (angle + 90) % 360;
      draw();
    });
  }
  if (saveBtn) saveBtn.addEventListener('click', send);
})();
