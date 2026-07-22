import ast
import unittest
from pathlib import Path


PROMPT_PATH = Path(__file__).resolve().parents[1] / "article_prompt.py"
POST_PROCESS_PATH = Path(__file__).resolve().parents[1] / "article_post_process.py"


def load_function(name):
    tree = ast.parse(PROMPT_PATH.read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(PROMPT_PATH), "exec"), namespace)
    return namespace[name]


class CalendarDayGenerationContractTests(unittest.TestCase):
    def test_pattern_window_metadata_names_duration_unit_explicitly(self):
        metadata = load_function("_pattern_window_metadata")(47)
        self.assertEqual(metadata, {
            "pattern_window_days": 47,
            "pattern_window_unit": "calendar_days",
        })

    def test_prompt_context_uses_the_typed_window_metadata_helper(self):
        tree = ast.parse(PROMPT_PATH.read_text(encoding="utf-8"))
        build = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_article_context")
        calls = [node for node in ast.walk(build) if isinstance(node, ast.Call)]
        self.assertTrue(any(isinstance(call.func, ast.Name) and call.func.id == "_pattern_window_metadata" for call in calls))

    def test_downloadable_dataset_uses_calendar_day_key(self):
        source = POST_PROCESS_PATH.read_text(encoding="utf-8")
        self.assertIn('"window_calendar_days": days', source)
        self.assertNotIn('"window_trading_days": days', source)


if __name__ == "__main__":
    unittest.main()
