import json
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory

from services.meals import MODEL_CHAIN, API_KEY_VARIABLE, EstimateError, NotAMealError, estimate_meal, load_api_key, parse_estimate
from services.meals.gemini import ENDPOINT, request_body

KEY = "cle-de-test"


def reply(**content) -> dict:
    values = dict(is_meal=True, calories_kcal=660, carbs_g=96.0, carbs_low_g=85.0, carbs_high_g=110.0, items=[
        dict(name="Pâtes bolognaise (350 g)", calories_kcal=520, carbs_g=72.0),
        dict(name="Pomme (150 g)", calories_kcal=80, carbs_g=19.0),
    ])
    values.update(content)
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(values)}]}}]}


class FakeTransport:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, dict(headers), body, timeout))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]  # la dernière réponse se répète
        if isinstance(answer, Exception):
            raise answer
        status, payload = answer
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


class ParseEstimateTest(unittest.TestCase):
    def test_a_valid_reply_becomes_an_estimate(self):
        estimate = parse_estimate(reply())
        self.assertEqual((estimate.calories_kcal, estimate.carbs_g, estimate.carbs_low_g, estimate.carbs_high_g), (660, 96.0, 85.0, 110.0))
        self.assertEqual([i.name for i in estimate.items], ["Pâtes bolognaise (350 g)", "Pomme (150 g)"])

    def test_the_range_always_contains_the_estimate(self):
        estimate = parse_estimate(reply(carbs_g=50, carbs_low_g=60, carbs_high_g=40))
        self.assertEqual((estimate.carbs_low_g, estimate.carbs_g, estimate.carbs_high_g), (50, 50, 50))

    def test_thinking_parts_are_ignored(self):
        payload = reply()
        payload["candidates"][0]["content"]["parts"].insert(0, {"text": "je réfléchis", "thought": True})
        self.assertEqual(parse_estimate(payload).calories_kcal, 660)

    def test_not_a_meal_is_refused(self):
        with self.assertRaises(NotAMealError):
            parse_estimate(reply(is_meal=False, calories_kcal=0, carbs_g=0, carbs_low_g=0, carbs_high_g=0))

    def test_implausible_values_are_refused(self):
        for bad in (dict(carbs_g=-1), dict(calories_kcal=-5), dict(calories_kcal=20000), dict(carbs_g=900, carbs_high_g=950)):
            with self.subTest(bad), self.assertRaisesRegex(EstimateError, "plausible"):
                parse_estimate(reply(**bad))

    def test_an_empty_estimate_is_refused(self):
        with self.assertRaisesRegex(EstimateError, "rien pu estimer"):
            parse_estimate(reply(calories_kcal=0, carbs_g=0, carbs_low_g=0, carbs_high_g=0))

    def test_garbage_is_refused_with_a_patient_message(self):
        for payload in ({}, {"candidates": []}, {"candidates": [{"content": {"parts": [{"text": "pas du json"}]}}]},
                        {"candidates": [{"content": {"parts": [{"text": "[1]"}]}}]}, reply(carbs_g="beaucoup"), reply(carbs_g=True), None):
            with self.subTest(payload), self.assertRaisesRegex(EstimateError, "illisible|inattendue"):
                parse_estimate(payload)

    def test_a_blocked_prompt_says_so(self):
        with self.assertRaisesRegex(EstimateError, "refusé"):
            parse_estimate({"promptFeedback": {"blockReason": "SAFETY"}})

    def test_broken_items_are_dropped_not_fatal(self):
        estimate = parse_estimate(reply(items=[{"name": "ok", "calories_kcal": 10, "carbs_g": 2}, {"name": "sans chiffres"}, {"calories_kcal": 1, "carbs_g": 1, "name": " "}]))
        self.assertEqual([i.name for i in estimate.items], ["ok"])


class EstimateMealTest(unittest.TestCase):
    def test_request_carries_the_key_in_a_header_never_in_the_url_or_body(self):
        transport = FakeTransport((200, reply()))
        estimate_meal("pâtes bolognaise et une pomme", KEY, transport=transport)
        url, headers, body, _ = transport.calls[0]
        self.assertEqual(url, ENDPOINT.format(model="gemini-flash-latest"))
        self.assertEqual(headers["x-goog-api-key"], KEY)
        self.assertNotIn(KEY, url)
        self.assertNotIn(KEY.encode(), body)
        sent = json.loads(body)
        self.assertEqual(sent["contents"][0]["parts"][0]["text"], "pâtes bolognaise et une pomme")
        self.assertEqual(sent["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(sent["generationConfig"]["temperature"], 0)

    def test_input_is_checked_before_any_request(self):
        transport = FakeTransport()
        for text, key, message in (("   ", KEY, "Décrivez"), ("x" * 501, KEY, "dépasse"), ("pomme", None, "Pas de clé"), ("pomme", "", "Pas de clé")):
            with self.subTest(text[:5], key=key), self.assertRaisesRegex(EstimateError, message):
                estimate_meal(text, key, transport=transport)
        self.assertEqual(transport.calls, [])

    def test_http_errors_become_patient_messages_without_the_key(self):
        for status, message in ((400, "refuse la clé"), (403, "refuse la clé"), (429, "Quota"), (404, "erreur 404")):
            with self.subTest(status), self.assertRaises(EstimateError) as caught:
                estimate_meal("pomme", KEY, transport=FakeTransport((status, {"error": {"message": f"clé {KEY}"}})))
            self.assertRegex(str(caught.exception), message)
            self.assertNotIn(KEY, str(caught.exception))

    def test_server_errors_are_retried_once(self):
        slept = []
        transport = FakeTransport((503, b""), (200, reply()))
        self.assertEqual(estimate_meal("pomme", KEY, transport=transport, sleep=slept.append).calories_kcal, 660)
        self.assertEqual((len(transport.calls), len(slept)), (2, 1))
        transport = FakeTransport((503, b""), (503, b""))
        with self.assertRaisesRegex(EstimateError, "erreur 503"):
            estimate_meal("pomme", KEY, transport=transport, sleep=slept.append)
        self.assertEqual(len(transport.calls), 2)

    def test_a_model_over_its_daily_quota_hands_over_to_the_next(self):
        transport = FakeTransport((429, {}), (404, {}), (200, reply()))
        self.assertEqual(estimate_meal("pomme", KEY, transport=transport).calories_kcal, 660)
        self.assertEqual([c[0].split("/models/")[1].split(":")[0] for c in transport.calls], list(MODEL_CHAIN))

    def test_the_chain_stops_at_the_first_model_that_answers(self):
        transport = FakeTransport((200, reply()))
        estimate_meal("pomme", KEY, transport=transport)
        self.assertEqual(len(transport.calls), 1)

    def test_a_bad_key_does_not_try_the_other_models(self):
        transport = FakeTransport((403, {}))
        with self.assertRaisesRegex(EstimateError, "refuse la clé"):
            estimate_meal("pomme", KEY, transport=transport)
        self.assertEqual(len(transport.calls), 1)

    def test_every_model_over_quota_says_try_tomorrow(self):
        transport = FakeTransport(*[(429, {})] * len(MODEL_CHAIN))
        with self.assertRaisesRegex(EstimateError, "demain"):
            estimate_meal("pomme", KEY, transport=transport)
        self.assertEqual(len(transport.calls), len(MODEL_CHAIN))

    def test_a_single_model_name_is_accepted(self):
        transport = FakeTransport((200, reply()))
        estimate_meal("pomme", KEY, models="gemini-3.5-flash", transport=transport)
        self.assertIn("gemini-3.5-flash:generateContent", transport.calls[0][0])

    def test_offline_is_explained(self):
        for error in (urllib.error.URLError("dns"), TimeoutError(), ConnectionResetError()):
            with self.subTest(type(error).__name__), self.assertRaisesRegex(EstimateError, "injoignable"):
                estimate_meal("pomme", KEY, transport=FakeTransport(error))

    def test_non_json_body_is_explained(self):
        with self.assertRaisesRegex(EstimateError, "illisible"):
            estimate_meal("pomme", KEY, transport=FakeTransport((200, b"<html>")))

    def test_the_request_body_is_valid_json_with_accents(self):
        self.assertIn("pâtes", json.loads(request_body("pâtes"))["contents"][0]["parts"][0]["text"])


class LoadApiKeyTest(unittest.TestCase):
    def test_environment_wins_over_files(self):
        with TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(f"{API_KEY_VARIABLE}=du-fichier\n")
            self.assertEqual(load_api_key({API_KEY_VARIABLE: " de-lenv "}, (env,)), "de-lenv")

    def test_dotenv_file_is_read(self):
        with TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(f"# commentaire\nAUTRE=1\n{API_KEY_VARIABLE} = \"abc.def\"\n")
            self.assertEqual(load_api_key({}, (env,)), "abc.def")

    def test_first_file_with_a_key_wins_and_missing_files_are_skipped(self):
        with TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "a.env", Path(tmp) / "b.env"
            second.write_text(f"{API_KEY_VARIABLE}=deux\n")
            self.assertEqual(load_api_key({}, (Path(tmp) / "absent.env", first, second)), "deux")

    def test_no_key_is_none(self):
        with TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(f"{API_KEY_VARIABLE}=\n")
            self.assertIsNone(load_api_key({}, (env,)))
            self.assertIsNone(load_api_key({API_KEY_VARIABLE: "  "}, ()))


if __name__ == "__main__":
    unittest.main()
