import itertools
import os
import unittest
from unittest.mock import patch

from bot import (
    COUNTRY_FLAGS,
    QUESTIONS,
    calculate_result,
    country_score,
    format_country_list,
    get_admin_vk_ids,
    load_countries,
)


class CalculateResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.countries = load_countries()

    def test_returns_countries_when_match_is_approximate(self) -> None:
        answers = {
            "status": ["Гражданство"],
            "budget": ["Свыше 500 000 $"],
            "motivation": ["Карты / счета / разблокировка счетов"],
            "financing": ["Без подтверждения"],
        }

        result_type, countries = calculate_result(self.countries, answers)

        self.assertEqual(result_type, "approximate")
        self.assertGreaterEqual(len(countries), 1)
        self.assertLessEqual(len(countries), 3)
        self.assertTrue(all(country["name"] for country in countries))
        self.assertGreater(country_score(countries[0], answers), 0)

    def test_full_matches_remain_full(self) -> None:
        answers = {
            "status": ["ПМЖ"],
            "budget": ["5 000–50 000 $"],
            "motivation": ["Миграция"],
            "financing": ["Финансовая независимость"],
        }

        result_type, countries = calculate_result(self.countries, answers)

        self.assertEqual(result_type, "full")
        self.assertEqual(countries[0]["name"], "Парагвай")

    def test_all_answer_combinations_match_html_rules(self) -> None:
        option_sets = [
            [
                list(choice)
                for size in (1, 2)
                for choice in itertools.combinations(question[2], size)
            ]
            for question in QUESTIONS
        ]

        for choices in itertools.product(*option_sets):
            answers = {
                question[0]: choice
                for question, choice in zip(QUESTIONS, choices, strict=True)
            }

            result_type, countries = calculate_result(self.countries, answers)
            full = [
                country for country in self.countries if self.html_score(country, answers) == 4
            ]
            near = [
                country for country in self.countries if self.html_score(country, answers) == 3
            ]

            self.assertTrue(countries)
            if full:
                self.assertEqual(result_type, "full")
                self.assertEqual(countries, full)
            elif near:
                self.assertEqual(result_type, "near")
                self.assertEqual(countries, near)
            else:
                self.assertEqual(result_type, "approximate")
                self.assertLessEqual(len(countries), 3)

    @staticmethod
    def html_score(country: dict, answers: dict[str, list[str]]) -> int:
        statuses = set(answers["status"])
        if "ПМЖ" in statuses:
            statuses.add("Золотая виза")
        effective_motivation = [
            item for item in answers["motivation"] if item != "План Б"
        ]
        checks = [
            any(item in country["status"] for item in statuses),
            any(item in country["budget"] for item in answers["budget"]),
            not effective_motivation
            or any(item in country["motivation"] for item in effective_motivation),
            any(item in country["financing"] for item in answers["financing"]),
        ]
        return sum(checks)

    def test_reference_html_scenarios(self) -> None:
        scenarios = [
            (
                {
                    "status": ["Гражданство"],
                    "budget": ["250 000–500 000 $"],
                    "motivation": ["Карты / счета / разблокировка счетов"],
                    "financing": ["Покупка недвижимости"],
                },
                ["Турция"],
            ),
            (
                {
                    "status": ["Золотая виза"],
                    "budget": ["Свыше 500 000 $"],
                    "motivation": ["Открытие бизнеса"],
                    "financing": ["Инвестиционный взнос"],
                },
                ["США", "Тайланд"],
            ),
        ]

        for answers, expected_names in scenarios:
            result_type, countries = calculate_result(self.countries, answers)
            self.assertEqual(result_type, "full")
            self.assertEqual([country["name"] for country in countries], expected_names)

    def test_every_country_has_flag_and_is_formatted(self) -> None:
        self.assertEqual(
            {country["name"] for country in self.countries},
            set(COUNTRY_FLAGS),
        )
        formatted = format_country_list(self.countries[:2])
        self.assertIn("🇵🇾  Парагвай", formatted)
        self.assertIn("🇸🇹  Сан-Томе и Принсипи", formatted)

    def test_multiple_admin_ids_are_parsed_and_deduplicated(self) -> None:
        with patch.dict(
            os.environ,
            {"ADMIN_VK_IDS": "527723173, 390157332,527723173"},
            clear=False,
        ):
            self.assertEqual(get_admin_vk_ids(), [527723173, 390157332])


if __name__ == "__main__":
    unittest.main()
