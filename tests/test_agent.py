"""Prompt-building tests. They use temporary persona/contacts files, so they run on a fresh clone
without anyone's real persona, chats or API keys."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent  # noqa: E402


class PromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "persona.md").write_text("# Persona: Test\n- I say 'bro' a lot", encoding="utf-8")
        (self.tmp / "contacts.json").write_text(json.dumps({"contacts": [
            {"match": ["asha", "kapoor"], "rule": "- Special rule for this contact: be extra gentle with Asha."},
        ]}), encoding="utf-8")
        self.patches = [
            mock.patch.object(agent, "PERSONA_FILE", self.tmp / "persona.md"),
            mock.patch.object(agent, "CONTACTS_FILE", self.tmp / "contacts.json"),
            mock.patch.object(agent, "STYLE_FILE", self.tmp / "missing_style.json"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()

    def test_contact_rule_matches_any_name_part_case_insensitively(self) -> None:
        for name in ("Asha Kapoor", "asha", "KAPOOR"):
            self.assertIn("extra gentle with asha", agent.build_system_prompt(name).lower())

    def test_other_contacts_get_no_special_rule(self) -> None:
        self.assertNotIn("special rule for this contact", agent.build_system_prompt("Riya").lower())

    def test_missing_or_broken_contacts_file_is_harmless(self) -> None:
        (self.tmp / "contacts.json").unlink()
        self.assertNotIn("special rule", agent.build_system_prompt("Asha").lower())
        (self.tmp / "contacts.json").write_text("{not json", encoding="utf-8")
        self.assertNotIn("special rule", agent.build_system_prompt("Asha").lower())

    def test_prompt_contains_persona_and_safety_rules(self) -> None:
        prompt = agent.build_system_prompt("Riya")
        self.assertIn("I say 'bro' a lot", prompt)
        self.assertIn("NEVER share OTPs", prompt)
        self.assertIn("You are texting with: Riya", prompt)

    def test_identity_rule_follows_stay_in_character(self) -> None:
        with mock.patch.dict(os.environ, {"STAY_IN_CHARACTER": "false"}):
            self.assertIn("be honest", agent.build_system_prompt("Riya"))
        with mock.patch.dict(os.environ, {"STAY_IN_CHARACTER": "true"}):
            self.assertIn("Never mention AI", agent.build_system_prompt("Riya"))

    def test_missing_persona_explains_how_to_fix(self) -> None:
        (self.tmp / "persona.md").unlink()
        with self.assertRaisesRegex(RuntimeError, "persona.example.md"):
            agent.build_system_prompt("Riya")


if __name__ == "__main__":
    unittest.main()
