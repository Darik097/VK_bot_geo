import itertools
import unittest

from bot import QUESTIONS, calculate_result, country_score, load_countries


class CalculateResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.countries = load_countries()

    def test_returns_three_countries_when_match_is_approximate(self) -> None:
        answers = {
            "status": ["ВНЖ"],
            "budget": ["Свыше 500 000 $"],
            "motivation": ["Открытие бизнеса"],
            "financing": ["Покупка недвижимости"],
        }

        result_type, countries = calculate_result(self.countries, answers)

        self.assertEqual(result_type, "approximate")
        self.assertEqual(len(countries), 3)
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

    def test_every_answer_combination_returns_best_countries(self) -> None:
        for choices in itertools.product(*(question[2] for question in QUESTIONS)):
            answers = {
                question[0]: [choice]
                for question, choice in zip(QUESTIONS, choices, strict=True)
            }

            _, countries = calculate_result(self.countries, answers)

            self.assertTrue(countries)
            self.assertLessEqual(len(countries), 3)
            best_score = max(country_score(country, answers) for country in self.countries)
            self.assertTrue(
                all(country_score(country, answers) == best_score for country in countries)
            )


if __name__ == "__main__":
    unittest.main()
