import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "monitor-competitive-account-signals"
sys.path.insert(0, str(SKILL / "scripts"))
from monitor import validate_profile  # noqa: E402

spec = importlib.util.spec_from_file_location("bootstrap", SKILL / "scripts" / "bootstrap.py")
bootstrap_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap_module)


class BootstrapAndMetadataTests(unittest.TestCase):
    def test_metadata_and_example_profile_are_valid(self):
        metadata = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "竞争与客户战情雷达"', metadata)
        self.assertIn('short_description: "持续追踪竞品与大客户变化，生成可追溯预警和个人销售行动建议"', metadata)
        self.assertEqual(validate_profile(yaml.safe_load((SKILL / "assets" / "monitoring.example.yaml").read_text(encoding="utf-8"))), [])
        frontmatter = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        self.assertIn("name: monitor-competitive-account-signals", frontmatter)
        self.assertIn("description: Use when", frontmatter)

    def test_bootstrap_uses_isolated_interpreter_and_pinned_requirements(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            venv_dir = project / ".competitive-radar" / "venv"
            interpreter = bootstrap_module.interpreter_for(venv_dir)
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()
            (venv_dir / "pyvenv.cfg").write_text("home = test\n", encoding="utf-8")
            (venv_dir.parent / ".bootstrap-managed").write_text("competitive-account-radar\n", encoding="utf-8")
            commands = []
            result = bootstrap_module.bootstrap(project, runner=lambda command: commands.append(command))
            self.assertEqual(result, interpreter)
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0][:3], [str(interpreter), "-m", "pip"])
            self.assertIn("PyYAML==6.0.3", (SKILL / "requirements.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
