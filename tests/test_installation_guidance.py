"""Keep manual interpreter selection aligned with the executable installer."""

from pathlib import Path
import re
import shlex
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallationGuidanceTests(unittest.TestCase):
    def test_manual_installs_select_the_installers_python(self):
        installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        selected = re.search(r'python install (\d+\.\d+)', installer)
        self.assertIsNotNone(selected)
        python = selected.group(1)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide_path = "docs/DEPLOYMENT.md"
        self.assertIn(f"{guide_path}#manual-installation", readme)
        guide = (ROOT / guide_path).read_text(encoding="utf-8")
        commands = [
            shlex.split(line)
            for line in (readme + "\n" + guide).splitlines()
            if line.startswith("uv tool install ")
        ]
        self.assertGreaterEqual(len(commands), 4)
        for command in commands:
            with self.subTest(command=command):
                self.assertIn("--python", command)
                self.assertEqual(command[command.index("--python") + 1], python)

    def test_windows_preview_offers_one_download_and_states_its_limits(self):
        def source(path):
            return (ROOT / path).read_text(encoding="utf-8")

        readme = source("README.md")
        self.assertIn("(#windows-11-preview)", readme)
        section = readme.split("\n### Windows 11 (preview)\n", 1)[1].split("\n## ", 1)[0]
        # One link, and it is the name that every release publishes.
        stable = re.search(r'^STABLE = "([^"]+)"$', source("scripts/windows_consumer_release.py"), re.M).group(1)
        link = f"https://github.com/microsoft/amplifier-unified/releases/latest/download/{stable}"
        self.assertEqual(re.findall(r"https://[^)\s]+\.exe", readme), [link])
        self.assertIn(f"[{stable}]({link})", section)
        # The names in the steps are the ones that the product shows.
        certificate = re.search(r'^ROOT_NAME = "([^"]+)"$', source("amplifier_web/windows_tls.py"), re.M).group(1)
        shell = source("amplifier_web/windows_consumer_shell.py")
        apps_entry = re.search(r'"DisplayName": "([^"]+)"', shell).group(1)
        start_entry = re.search(r'LINKS = \{"([^"]+)\.lnk": ""', shell).group(1)
        folder = re.search(r'internal const string RootName="([^"]+)";',
                           source("scripts/windows_bootstrap_native.cs")).group(1)
        for shown in (f'"{certificate}"', f"**{apps_entry}**", f"Start menu: **{start_entry}**",
                      f"`%LOCALAPPDATA%\\{folder}`"):
            with self.subTest(shown=shown):
                self.assertIn(shown, section)
        # The limits are stated, not implied.
        plain = " ".join(section.split())
        for limit in ("This is a preview.", "not signed yet", "Windows 11 with an Intel or AMD processor",
                      "ARM processors are not supported yet", "your own Windows account only",
                      "Settings > Apps > Installed apps",
                      "`%USERPROFILE%\\.amplifier\\rate-limit-state.json`. Uninstall does not remove that file"):
            with self.subTest(limit=limit):
                self.assertIn(limit, plain)
        # Uninstall: the question and its three buttons are the product's own, and each answer has its result.
        consumer = source("amplifier_web/windows_consumer.py")
        uninstall = plain.split("- **Uninstall:**", 1)[1]
        asked = re.search(r'"(Do you also want to delete [^"\\]+\?)', consumer).group(1)
        self.assertIn(f'Amplifier asks one question: "{asked}"', uninstall)
        for button, product, result in (
                ("No", "Select No to keep them.", "uninstalls Amplifier and keeps the folder."),
                ("Yes", "Select Yes to delete them, together with the program files.",
                 "uninstalls Amplifier and deletes the folder:"),
                ("Cancel", "Select Cancel to keep Amplifier installed.", "changes nothing. Amplifier stays installed.")):
            with self.subTest(button=button):
                self.assertIn(product, consumer)
                self.assertIn(f"- **{button}** {result}", uninstall)
        for stated in ("Amplifier asks once more before it deletes anything; select **Yes** again.",
                       "This cannot be undone.", "Anything in the folder that is not Amplifier's stays",
                       # An installed launcher keeps the uninstall that its setup file had.
                       'An installation from an earlier setup file can ask only "Uninstall Amplifier?" '
                       "with **Yes** and **No**.",
                       "There, **Yes** uninstalls and leaves the folder."):
            with self.subTest(stated=stated):
                self.assertIn(stated, uninstall)
        self.assertIn('"Your chats, settings, accounts and the program files stay in this folder:\\n"', consumer)
        self.assertNotIn("It leaves the folder with your chats", plain)
        self.assertNotIn("Native Windows is not yet supported", readme)
        # The design stays a record of what is tested, not a support claim.
        self.assertIn("(docs/design/native-windows-installation.md)", section)
        design = source("docs/design/native-windows-installation.md")
        self.assertIn("**not a native Windows support claim**", design)
        self.assertIn("Qualified native Windows support is not claimed.", " ".join(design.split()))


if __name__ == "__main__":
    unittest.main()