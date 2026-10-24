"""Тесты модуля подписей: приём снимка, прозрачный фон, доступ (ТЗ-кровь §6, ADR-0029).

Контракт — поведение, а не внутренние имена: подпись одна на сотрудника, чужая не
отдаётся, фон становится прозрачным, поля обрезаются, готовый PNG клиента не
перекодируется, пустая фотография отвергается с понятным текстом.
"""

from __future__ import annotations

import pymupdf
import pytest
from fastapi.testclient import TestClient

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, EDITOR, HEAD, HEAD_NURSE, NURSE, Employee
from docapp.modules import MODULES
from docapp.people.store import SqliteEmployeeStore
from docapp.signatures import png
from docapp.signatures.errors import SignatureError
from docapp.signatures.service import MAX_BYTES, SignatureService
from docapp.signatures.store import SqliteSignatureStore
from docapp.web.app import create_app
from factories import make_db

#: Сотрудники для сценариев: (логин, пароль, роль, фамилия).
PEOPLE = (
    ("doc", "doc_pass", DOCTOR, "Врач"),
    ("zav", "zav_pass", HEAD, "Заведующий"),
    ("eds", "eds_pass", EDITOR, "Редактор"),
    ("nurse", "nurse_pass", NURSE, "Сестра"),
    ("headn", "headn_pass", HEAD_NURSE, "Старшая"),
)


def photo(
    width: int = 240,
    height: int = 80,
    paper: float | tuple[float, float, float] = 0.66,
    ink: float = 0.1,
) -> bytes:
    """Синтетическая «фотография» подписи: бумага и тёмный штрих (JPEG).

    Бумага по умолчанию серая, а не белая — как в кабинете: порог прозрачности
    должен подстраиваться под освещение. Можно задать цвет кортежем — жёлтая
    бумага на реальном снимке владельца дала ровно ту ошибку, которую ловит
    `test_yellow_paper_does_not_leave_a_rectangle`.
    """
    fill = (paper, paper, paper) if isinstance(paper, (int, float)) else paper
    document = pymupdf.open()
    page = document.new_page(width=width, height=height)
    page.draw_rect(pymupdf.Rect(0, 0, width, height), color=None, fill=fill)
    for start, finish, y in ((20, 90, 34), (90, 160, 26), (160, 220, 40)):
        page.draw_line(
            pymupdf.Point(start, y),
            pymupdf.Point(finish, y + 6),
            color=(ink, ink, ink),
            width=3,
        )
    pixels = page.get_pixmap(dpi=72)
    document.close()
    return pixels.tobytes("jpeg")


def alpha_at(data: bytes, x: int, y: int) -> int:
    """Значение альфа-канала готового PNG в точке (картинка должна быть RGBA)."""
    pixmap = pymupdf.Pixmap(data)
    assert pixmap.n == 4, f"ожидался RGBA, получено каналов: {pixmap.n}"
    return pixmap.samples[y * pixmap.stride + x * 4 + 3]


def photo_with_shadow(width: int = 240, height: int = 80) -> bytes:
    """Снимок подписи с тенью в нижней половине листа.

    Тень — не чернила: она светлее штриха, но темнее бумаги. Владелец поймал
    именно её как «тёмную кляксу, которая остаётся на листе», и лечится это
    обрезкой по плотному штриху плюс более крутым порогом.
    """
    document = pymupdf.open()
    page = document.new_page(width=width, height=height)
    page.draw_rect(pymupdf.Rect(0, 0, width, height), color=None, fill=(0.66, 0.66, 0.66))
    page.draw_rect(
        pymupdf.Rect(0, 52, width, height), color=None, fill=(0.5, 0.5, 0.5)
    )
    for start, finish, y in ((20, 90, 26), (90, 160, 20), (160, 220, 32)):
        page.draw_line(
            pymupdf.Point(start, y),
            pymupdf.Point(finish, y + 5),
            color=(0.1, 0.1, 0.1),
            width=3,
        )
    pixels = page.get_pixmap(dpi=72)
    document.close()
    return pixels.tobytes("jpeg")


def solid_pixels(data: bytes) -> list[bytes]:
    """Пиксели полностью непрозрачного штриха (RGB без альфы)."""
    pixmap = pymupdf.Pixmap(data)
    return [
        pixmap.samples[y * pixmap.stride + x * 4 : y * pixmap.stride + x * 4 + 3]
        for y in range(pixmap.height)
        for x in range(pixmap.width)
        if pixmap.samples[y * pixmap.stride + x * 4 + 3] == 255
    ]


def opaque_png(data: bytes) -> bytes:
    """PNG с альфа-каналом и непрозрачной альфой — ровно так отдаёт canvas.

    Именно этот случай и вылез на проде: канал есть, прозрачности нет, а
    проверка «альфа присутствует» пропускала файл как готовый.
    """
    pixmap = pymupdf.Pixmap(data)
    rows = []
    for y in range(pixmap.height):
        base = y * pixmap.stride
        row = bytearray(pixmap.width * 4)
        for x in range(pixmap.width):
            i = base + x * pixmap.n
            row[x * 4 : x * 4 + 3] = pixmap.samples[i : i + 3]
            row[x * 4 + 3] = 255
        rows.append(row)
    return png._encode(pixmap.width, pixmap.height, rows)


@pytest.fixture()
def service(tmp_path):
    """Сервис на тестовой БД и своей папке файлов."""
    store = SqliteSignatureStore(make_db(tmp_path))
    try:
        yield SignatureService(store, tmp_path / "signatures")
    finally:
        store.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Приложение целиком: вход нужен, чтобы проверить доступ к подписи."""
    monkeypatch.setenv("DOCAPP_SIGNATURES_DIR", str(tmp_path / "signatures"))
    db = make_db(tmp_path)
    with SqliteEmployeeStore(db) as store:
        for login, password, role, last_name in PEOPLE:
            store.add(
                Employee(
                    last_name=last_name,
                    first_name="Тест",
                    role=role,
                    login=login,
                    password_hash=hash_password(password),
                )
            )
    app = create_app(db_path=db, secret="test", modules=MODULES)
    with TestClient(app) as test_client:
        yield test_client


def _login(client, login: str = "doc", password: str = "doc_pass") -> None:
    response = client.post(
        "/login", data={"login": login, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303


class TestImage:
    """Подготовка картинки: прозрачный фон, обрезка полей, готовый PNG как есть."""

    def test_background_becomes_transparent(self):
        prepared = png.prepare(photo())
        assert alpha_at(prepared, 0, 0) == 0
        assert max(alpha_at(prepared, x, y) for x in range(4) for y in range(4)) >= 0

    def test_stroke_stays_opaque(self):
        prepared = png.prepare(photo())
        pixmap = pymupdf.Pixmap(prepared)
        alphas = [
            pixmap.samples[y * pixmap.stride + x * 4 + 3]
            for y in range(pixmap.height)
            for x in range(pixmap.width)
        ]
        assert max(alphas) == 255

    def test_white_margins_are_cropped(self):
        prepared = png.prepare(photo())
        assert png.png_side(prepared) < 240
        assert png.png_side(prepared) > 100

    def test_grey_paper_is_not_a_blob(self):
        """Серая бумага (0,55) — тоже прозрачная: порог считается по снимку."""
        prepared = png.prepare(photo(paper=0.55, ink=0.12))
        assert alpha_at(prepared, 0, 0) == 0

    def test_opaque_png_from_canvas_gets_transparent_background(self):
        """Canvas присылает RGBA без прозрачности — фон обязан исчезнуть."""
        prepared = png.prepare(opaque_png(photo()))
        assert png.has_alpha(prepared)
        assert alpha_at(prepared, 0, 0) == 0
        assert png.png_side(prepared) < 240

    def test_yellow_paper_does_not_leave_a_rectangle(self):
        """Жёлтая бумага: фон прозрачный, цвет бумаги в штрих не переезжает."""
        prepared = png.prepare(photo(paper=(0.82, 0.76, 0.70)))
        assert alpha_at(prepared, 0, 0) == 0
        assert solid_pixels(prepared), "не нашлось непрозрачных пикселей штриха"

    def test_ink_is_blue(self):
        """Подпись печатается синей, как её ставят в отделении."""
        prepared = png.prepare(photo())
        stroke = solid_pixels(prepared)
        assert stroke
        assert all(pixel[2] > pixel[0] and pixel[2] > pixel[1] for pixel in stroke)

    def test_shadow_on_the_sheet_is_cropped_out(self):
        """Тень внизу листа не попадает ни в кадр, ни в полупрозрачную зону."""
        prepared = png.prepare(photo_with_shadow())
        cropped = pymupdf.Pixmap(prepared)
        assert cropped.height <= 55, f"тень осталась в кадре: высота {cropped.height}"
        alphas = [
            cropped.samples[y * cropped.stride + x * 4 + 3]
            for y in range(cropped.height)
            for x in range(cropped.width)
        ]
        semi = sum(1 for alpha in alphas if 0 < alpha < 255)
        assert semi < len(alphas) * 3 // 100, f"полупрозрачных пикселей {semi} из {len(alphas)}"

    def test_ready_png_is_kept_as_is(self):
        prepared = png.prepare(photo())
        assert png.has_alpha(prepared)
        assert png.prepare(prepared) == prepared

    def test_blank_photo_is_rejected(self):
        with pytest.raises(SignatureError):
            png.prepare(photo(paper=0.66, ink=0.66))

    def test_not_an_image_is_rejected(self):
        with pytest.raises(SignatureError):
            png.prepare("это не картинка".encode())

    def test_png_side_of_non_png(self):
        assert png.png_side(b"not a png") == 0
        assert png.has_alpha(b"not a png") is False


class TestService:
    """Хранилище и файл: одна подпись на сотрудника, замена не плодит файлов."""

    def test_save_writes_file_and_row(self, service, tmp_path):
        saved = service.save(1, photo())
        stored = tmp_path / "signatures" / "1.png"
        assert stored.exists()
        assert stored.stat().st_size == saved.size
        assert service.signature(1) is not None

    def test_second_upload_replaces_the_same_file(self, service, tmp_path):
        first = service.save(1, photo())
        second = service.save(1, photo(width=200, height=100))
        files = sorted(name.name for name in (tmp_path / "signatures").iterdir())
        assert files == ["1.png"]
        assert second.uploaded_at >= first.uploaded_at
        assert service.signature(1).size == second.size

    def test_signature_belongs_to_its_employee(self, service):
        service.save(1, photo())
        assert service.signature(2) is None
        assert service.image_path(2) is None

    def test_empty_file_is_rejected(self, service):
        with pytest.raises(SignatureError):
            service.save(1, b"")

    def test_too_big_file_is_rejected(self, service):
        with pytest.raises(SignatureError):
            service.save(1, b"\x00" * (MAX_BYTES + 1))

    def test_forget_removes_row_and_file(self, service, tmp_path):
        service.save(1, photo())
        service.forget(1)
        assert service.signature(1) is None
        assert not (tmp_path / "signatures" / "1.png").exists()


class TestPage:
    """Страница «Моя подпись»: доступ по ролям и загрузка через HTTP."""

    def test_requires_login(self, client):
        response = client.get("/signatures", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    def test_doctor_sees_page(self, client):
        _login(client)
        response = client.get("/signatures")
        assert response.status_code == 200
        assert "Моя подпись" in response.text
        assert "Подписи нет" in response.text

    @pytest.mark.parametrize(
        "role_login,role_password",
        [("nurse", "nurse_pass"), ("headn", "headn_pass"), ("eds", "eds_pass")],
    )
    def test_other_roles_are_refused(self, client, role_login, role_password):
        _login(client, login=role_login, password=role_password)
        assert client.get("/signatures").status_code == 403

    def test_head_sees_page(self, client):
        _login(client, login="zav", password="zav_pass")
        assert client.get("/signatures").status_code == 200

    def test_upload_and_read_back(self, client):
        _login(client)
        response = client.post(
            "/signatures/api/upload",
            files={"image": ("sign.png", photo(), "image/jpeg")},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

        picture = client.get("/signatures/api/image")
        assert picture.status_code == 200
        assert picture.headers["content-type"] == "image/png"
        assert picture.content.startswith(png.PNG_MAGIC)
        assert alpha_at(picture.content, 0, 0) == 0

    def test_upload_keeps_page_state_after_reload(self, client):
        _login(client)
        client.post(
            "/signatures/api/upload",
            files={"image": ("sign.png", photo(), "image/jpeg")},
        )
        page = client.get("/signatures")
        assert "Подписи нет" not in page.text
        assert 'src="/signatures/api/image?v=' in page.text

    def test_bad_file_gives_four_hundred(self, client):
        _login(client)
        response = client.post(
            "/signatures/api/upload",
            files={"image": ("sign.png", b"not an image", "image/png")},
        )
        assert response.status_code == 400
        assert "не читается" in response.json()["detail"]

    def test_empty_state_gives_four_hundred_four(self, client):
        _login(client)
        assert client.get("/signatures/api/image").status_code == 404

    def test_upload_without_login(self, client):
        response = client.post(
            "/signatures/api/upload",
            files={"image": ("sign.png", photo(), "image/jpeg")},
        )
        assert response.status_code == 401

    def test_nurse_cannot_upload(self, client):
        _login(client, login="nurse", password="nurse_pass")
        response = client.post(
            "/signatures/api/upload",
            files={"image": ("sign.png", photo(), "image/jpeg")},
        )
        assert response.status_code == 403

    def test_other_employee_does_not_get_my_signature(self, client):
        _login(client)
        client.post(
            "/signatures/api/upload",
            files={"image": ("sign.png", photo(), "image/jpeg")},
        )
        client.post("/logout", follow_redirects=False)
        _login(client, login="zav", password="zav_pass")
        assert client.get("/signatures/api/image").status_code == 404


class TestCabinet:
    """Личный кабинет: ссылка на подпись есть у того, кому можно."""

    def test_doctor_sees_the_link(self, client):
        _login(client)
        assert 'href="/signatures"' in client.get("/me").text

    def test_nurse_does_not_see_the_link(self, client):
        _login(client, login="nurse", password="nurse_pass")
        assert 'href="/signatures"' not in client.get("/me").text
