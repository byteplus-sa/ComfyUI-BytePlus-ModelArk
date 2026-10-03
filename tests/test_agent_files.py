import os
import unittest


PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AgentFilesAreSymlinked(unittest.TestCase):
    """CLAUDE.md and .claude/ are the sources; the AGENTS.md and .agents/ copies must be symlinks to them."""

    def assert_link(self, link, target):
        path = os.path.join(PLUGIN_ROOT, link)
        self.assertTrue(os.path.islink(path), f"{link} must be a symlink to {target}")
        self.assertEqual(os.readlink(path), target)
        self.assertTrue(os.path.exists(path), f"{link} is a broken symlink")

    def test_agents_md(self):
        self.assert_link("AGENTS.md", "CLAUDE.md")

    def test_agents_dir_md(self):
        self.assert_link(os.path.join(".agents", "AGENTS.md"), os.path.join("..", "CLAUDE.md"))

    def test_agents_skills(self):
        self.assert_link(os.path.join(".agents", "skills"), os.path.join("..", ".claude", "skills"))

    def test_no_skill_only_in_agents(self):
        agents = os.path.join(PLUGIN_ROOT, ".agents")
        extra = [name for name in os.listdir(agents) if name not in ("AGENTS.md", "skills")]
        self.assertEqual(extra, [], ".agents/ may only hold the AGENTS.md and skills symlinks; put real files in .claude/")


if __name__ == "__main__":
    unittest.main()
