"""Промпт для сайта лида: транслит, имена файлов, шаблоны, офлайн-фолбэк."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
ROOT_DIR = TESTS_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helpers import load_module

M = load_module("leads_mail")

FULL_ROW = {
    "id": "1", "name": "Барбершоп Борода", "rubric": "барбершоп",
    "phone": "+79001112233", "email": "a@x.ru", "telegram": "barber_boroda",
    "city": "Москва", "address": "ул. Ленина, 1", "score": "88",
    "rating": "4.9", "tier": "hot",
}
POOL_ROW = {
    "name": "Автосервис Гаечка", "topic": "Автосервис", "city": "Казань",
    "phone": "+79991112233", "telegram": "https://t.me/gaechka",
}
NO_NAME_ROW = {"name": "", "phone": "+79991112233"}
WINDOWS_FORBIDDEN = '<>:"/\\|?*'


class DefaultSettingsTest(unittest.TestCase):
    def test_new_site_keys_present(self) -> None:
        """В DEFAULT_SETTINGS есть новые ключи сайта с точными значениями по умолчанию."""
        self.assertEqual(M.DEFAULT_SETTINGS.get("github_base_url"),
                         "https://username.github.io/demo/")
        self.assertEqual(M.DEFAULT_SETTINGS.get("site_prompt_template"), "")

    def test_old_keys_still_present(self) -> None:
        """Старые ключи DEFAULT_SETTINGS на месте — совместимость не сломана."""
        required = ("ai_url", "ai_model", "ai_key", "style", "idea", "api_id",
                    "api_hash", "vk_token", "smtp_host", "mail_subject",
                    "followup_days", "pool_days", "pacing", "dry_run")
        for key in required:
            with self.subTest(key=key):
                self.assertIn(key, M.DEFAULT_SETTINGS)


class TranslitTest(unittest.TestCase):
    def test_known_phrase(self) -> None:
        """«Барбершоп Борода» превращается ровно в «barbershop-boroda»."""
        self.assertEqual(M._translit("Барбершоп Борода"), "barbershop-boroda")

    def test_empty_string(self) -> None:
        """Пустая строка транслитируется в пустую, без исключений."""
        self.assertEqual(M._translit(""), "")

    def test_yo_and_punctuation(self) -> None:
        """«Ёлка!» даёт «elka»: ё переходит в e, знаки препинания исчезают."""
        self.assertEqual(M._translit("Ёлка!"), "elka")

    def test_long_string_capped(self) -> None:
        """Очень длинная строка усекается до 60 символов или короче."""
        text = "Барбершоп Борода " * 30
        self.assertLessEqual(len(M._translit(text)), 60)

    def test_only_safe_charset(self) -> None:
        """В результате нет ничего вне [a-z0-9-] — даже из мусорного входа."""
        result = M._translit('ООО «Ромашка» — Best/Shop: №1 | Test?!*"<>')
        self.assertRegex(result, r"^[a-z0-9\-]*$")


class SitePromptFileTest(unittest.TestCase):
    def test_filename_from_name_translit(self) -> None:
        """Имя файла строится из транслита названия бизнеса, это .txt-путь."""
        with tempfile.TemporaryDirectory() as tmp:
            path = M.site_prompt_file(FULL_ROW, out_dir=Path(tmp))
        self.assertIsInstance(path, Path)
        self.assertEqual(path.suffix, ".txt")
        self.assertIn("barbershop-boroda", path.name)

    def test_empty_name_falls_back_to_row_id(self) -> None:
        """Без name файл всё равно получает непустое имя (из row_id пул-строки)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = M.site_prompt_file(NO_NAME_ROW, out_dir=Path(tmp))
        self.assertEqual(path.suffix, ".txt")
        self.assertTrue(path.stem.strip(), "имя файла не должно быть пустым")

    def test_default_dir_is_leads_prompts(self) -> None:
        """При out_dir=None путь лежит в папке leads_prompts."""
        path = M.site_prompt_file(FULL_ROW)
        self.assertIn("leads_prompts", path.parts)

    def test_windows_safe_filename(self) -> None:
        """Имя файла безопасно для Windows: нет запрещённых символов."""
        row = {**FULL_ROW, "name": 'ООО «Труба|Скат» <test>: ?*" /\\'}
        with tempfile.TemporaryDirectory() as tmp:
            path = M.site_prompt_file(row, out_dir=Path(tmp))
        for ch in WINDOWS_FORBIDDEN:
            with self.subTest(char=ch):
                self.assertNotIn(ch, path.name)


class GenerateSitePromptTest(unittest.TestCase):
    def test_prompt_has_name_and_questions(self) -> None:
        """Промпт содержит название бизнеса и фразу про 2-4 уточняющих вопроса."""
        text = M.generate_site_prompt(FULL_ROW, dict(M.DEFAULT_SETTINGS), save=False)
        self.assertIsInstance(text, str)
        self.assertIn("Барбершоп Борода", text)
        self.assertIn("2-4", text)

    def test_save_true_writes_txt_file(self) -> None:
        """save=True с временным out_dir создаёт .txt-файл с текстом промпта (utf-8)."""
        with tempfile.TemporaryDirectory() as tmp:
            text = M.generate_site_prompt(FULL_ROW, dict(M.DEFAULT_SETTINGS),
                                          out_dir=Path(tmp), save=True)
            files = sorted(Path(tmp).glob("*.txt"))
            self.assertEqual(len(files), 1, "ожидался ровно один .txt файл")
            content = files[0].read_text(encoding="utf-8")
        self.assertTrue(text.strip())
        self.assertIn("Барбершоп Борода", content)
        self.assertIn("2-4", content)

    def test_save_false_writes_nothing(self) -> None:
        """save=False не создаёт файл во временной папке."""
        with tempfile.TemporaryDirectory() as tmp:
            M.generate_site_prompt(FULL_ROW, dict(M.DEFAULT_SETTINGS),
                                   out_dir=Path(tmp), save=False)
            self.assertEqual(list(Path(tmp).glob("*.txt")), [])

    def test_pool_row_without_id_rubric_rating(self) -> None:
        """Пул-строка без id/rubric/rating обрабатывается без исключений."""
        text = M.generate_site_prompt(POOL_ROW, {}, save=False)
        self.assertIn("Автосервис Гаечка", text)
        self.assertIn("2-4", text)

    def test_custom_template_used(self) -> None:
        """Кастомный site_prompt_template заменяет встроенный промпт."""
        text = M.generate_site_prompt(
            FULL_ROW, {"site_prompt_template": "САЙТ: {name} из {city}"}, save=False)
        self.assertTrue(text.startswith("САЙТ: "), f"не применён шаблон: {text!r}")

    def test_broken_template_falls_back_to_builtin(self) -> None:
        """Битый шаблон «{» не роняет генерацию — возвращается встроенный промпт."""
        settings = {**M.DEFAULT_SETTINGS, "site_prompt_template": "{"}
        text = M.generate_site_prompt(FULL_ROW, settings, save=False)
        self.assertIn("Барбершоп Борода", text)
        self.assertIn("2-4", text)


class FallbackTextTest(unittest.TestCase):
    def test_old_two_arg_signature(self) -> None:
        """Старая сигнатура fallback_text(row, idea) продолжает работать."""
        text = M.fallback_text(FULL_ROW, "предложу простой сайт")
        self.assertIsInstance(text, str)
        self.assertIn("Барбершоп Борода", text)

    def test_at_least_four_sentences(self) -> None:
        """Фолбэк-текст содержит не меньше 4 предложений (без ссылок с точками)."""
        text = M.fallback_text(FULL_ROW, "предложу простой сайт",
                               {"github_base_url": ""})
        self.assertGreaterEqual(len(text.split(".")), 5, f"мало предложений: {text!r}")

    def test_github_link_from_settings(self) -> None:
        """С настройкой github_base_url ссылка на демо-сайт присутствует в тексте."""
        settings = {"github_base_url": "https://test.github.io/demo/"}
        text = M.fallback_text(FULL_ROW, "предложу простой сайт", settings)
        self.assertIn("https://test.github.io/demo/", text)

    def test_addresses_client_as_vy(self) -> None:
        """Обращение к клиенту на «вы» (слово «вы» в любом регистре)."""
        text = M.fallback_text(FULL_ROW, "предложу простой сайт")
        self.assertRegex(text, r"(?i)\bвы\b")


class AiGenerateOfflineTest(unittest.TestCase):
    def _assert_offline_fallback(self, settings: dict, idea: str) -> None:
        started = time.monotonic()
        text = M.ai_generate(settings, FULL_ROW, idea)
        elapsed = time.monotonic() - started
        self.assertEqual(text, M.fallback_text(FULL_ROW, idea, settings),
                         "без ИИ должен возвращаться текст fallback_text")
        self.assertLess(elapsed, 2.0, "офлайн-фолбэк должен возвращаться мгновенно")

    def test_empty_ai_url_returns_fallback(self) -> None:
        """Пустой ai_url: фолбэк без сети, быстро и без исключений."""
        settings = {**M.DEFAULT_SETTINGS, "ai_url": "", "github_base_url": ""}
        self._assert_offline_fallback(settings, "предложу простой сайт")

    def test_empty_ai_model_returns_fallback(self) -> None:
        """Пустой ai_model: фолбэк без сети, быстро и без исключений."""
        settings = {**M.DEFAULT_SETTINGS, "ai_model": "", "github_base_url": ""}
        self._assert_offline_fallback(settings, "предложу простой сайт")


if __name__ == "__main__":
    unittest.main()
