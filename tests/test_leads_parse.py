"""Офлайн-тесты парсера: разметка как у 2GIS, без сети."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from leads2gis import Filters, parse_firm, score

HTML = """
<html><body>
<div class="card">
  <div class="hdr">
    <div class="actions"><span>Сохранить</span><span>Отправить</span><span>Маршрут</span></div>
    <div class="title"><h1>Кофейня Тест</h1></div>
    <div class="sub">Кофейня</div>
    <div class="rate">4.7</div>
    <div class="cnt">125 оценок</div>
    <div class="desc">Лучший кофе в районе</div>
  </div>
  <a href="/moscow/geo/4504235290707169">Крымская набережная, 10 ст2</a>
  <a href="/moscow/search/kofejjni/rubricId/162">Кофейни</a>
  <a href="tel:+79966574489">+7 996 657-44-89</a>
  <a href="http://link.2gis.ru/1.2/FF624D62/online/20261001/project32/1/null/x">test-cafe.ru</a>
  <a href="http://link.2gis.ru/1.2/AEA69FB7/online/20261001/project32/1/null/y">Перейти в Telegram-канал</a>
  <a href="https://vk.com/testcafe" aria-label="ВКонтакте">ВКонтакте</a>
  <a href="/moscow/directions/points/%7C37.605268%2C55.735952%3B70000001113359188">Маршрут</a>
  <span>cafe@test.ru</span>
  <div>Открыто</div>
  <div>Пн-Вс 08:00-23:00</div>
</div>
</body></html>
"""

HTML_NO_SITE = HTML.replace(
    '<a href="http://link.2gis.ru/1.2/FF624D62/online/20261001/project32/1/null/x">test-cafe.ru</a>', ""
)


def test_fields() -> None:
    lead = parse_firm(HTML, "moscow", "70000001113359188", "кофейни")
    assert lead["name"] == "Кофейня Тест", lead["name"]
    assert lead["rubric"] == "Кофейня", lead["rubric"]
    assert lead["rating"] == 4.7
    assert lead["reviews"] == 125
    assert lead["description"] == "Лучший кофе в районе"
    assert lead["phone"] == "+79966574489"
    assert lead["address"].startswith("Крымская набережная")
    assert lead["websites"] == ["test-cafe.ru"], lead["websites"]
    assert lead["socials"] == ["telegram", "vk"], lead["socials"]
    assert lead["email"] == "cafe@test.ru"
    assert abs(lead["lat"] - 55.735952) < 1e-6
    assert abs(lead["lon"] - 37.605268) < 1e-6
    assert lead["status"] == "Открыто"
    assert lead["hours"] == "Пн-Вс 08:00-23:00"
    assert lead["has_website"] is True


def test_no_website() -> None:
    lead = parse_firm(HTML_NO_SITE, "moscow", "1")
    assert lead["has_website"] is False
    assert lead["websites"] == []


def test_score_and_filters() -> None:
    lead = parse_firm(HTML_NO_SITE, "moscow", "1")
    lead["score"], lead["reason"] = score(lead)
    assert lead["score"] >= 70, lead["score"]
    assert "нет сайта" in lead["reason"]

    assert Filters(no_website=True).accepts(lead) is True
    assert Filters(has_phone=True).accepts(lead) is True
    assert Filters(min_rating=4.9).accepts(lead) is False
    assert Filters(max_rating=4.0).accepts(lead) is False
    assert Filters(rubric_match="кофей").accepts(lead) is True
    assert Filters(rubric_match="стомат").accepts(lead) is False

    with_site = parse_firm(HTML, "moscow", "2")
    assert Filters(no_website=True).accepts(with_site) is False


if __name__ == "__main__":
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"ok  {name}")
    print("все тесты прошли")
