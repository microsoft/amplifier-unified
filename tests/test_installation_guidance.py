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
        # Uninstall: the questions and the three buttons are the product's own, and each answer has its result.
        # A sentence of the product can continue in the next string of its source.
        consumer = re.sub(r'"\s*\n\s*"', "", source("amplifier_web/windows_consumer.py"))
        item = section.split("\n- **Uninstall:**", 1)[1].split("\n\nThe [native Windows design", 1)[0]
        opening, *bullets = re.split(r"\n  - \*\*", item)
        bullets[-1], rest = bullets[-1].split("\n\n", 1)
        answers = {bullet.split("**", 1)[0]: " ".join(bullet.split("**", 1)[1].split()) for bullet in bullets}
        opening, rest = " ".join(opening.split()), " ".join(rest.split())
        self.assertEqual(list(answers), ["No", "Yes", "Cancel"])  # The three buttons of the first question.
        first = re.search(r'(Do you also want to delete [^"\\]+\?)', consumer).group(1)
        second = re.search(r'(Delete everything that Amplifier keeps in this folder\?)', consumer).group(1)
        self.assertTrue(opening.endswith(f'Amplifier first asks: "{first}"'), opening)
        for button, product, results in (
                ("No", ("Select No to keep them.",),
                 ("uninstalls Amplifier and keeps the folder.",
                  "Your chats, settings and accounts stay there, together with the program files")),
                ("Yes", ("Select Yes to delete them, together with the program files.",
                         "Amplifier asks you once more before it deletes anything.", "your stored keys.",
                         "files that Amplifier or you saved in its workspace", "This cannot be undone.",
                         "Select No to change nothing. Amplifier stays installed."),
                 (f'leads to a second question: "{second}"',
                  "**Yes** there uninstalls Amplifier and deletes your chats, settings, saved accounts and stored keys, "
                  "the files that Amplifier or you saved in its workspace folder, and the program files.",
                  "This cannot be undone.", "**No** there changes nothing.")),
                ("Cancel", ("Select Cancel to keep Amplifier installed.",),
                 ("changes nothing. Amplifier stays installed.",))):
            with self.subTest(button=button):
                for sentence in product:
                    self.assertIn(sentence, consumer)
                self.assertTrue(answers[button].startswith(results[0]), answers[button])
                for result in results[1:]:
                    self.assertIn(result, answers[button])
        # A delete is said only under Yes.
        for button in ("No", "Cancel"):
            self.assertNotIn("delete", answers[button])
        for product, stated in (
                ("Select Yes to stop that work and uninstall now.",
                 "If Amplifier is working on a chat, it asks before it stops that work."),
                ("Its entries in the Start menu and the Apps list will be removed.",
                 "An uninstall stops Amplifier and removes it from the Start menu and the Apps list."),
                # Windows asks only when it trusts the certificate.
                ("Windows asks whether to delete the certificate",
                 "If Windows asks whether to delete the certificate, select **Yes**."),
                ("Amplifier is uninstalled. It no longer runs, and it no longer starts when you sign in.",
                 'After a delete, Amplifier says "Amplifier is uninstalled."'),
                ("When you close this message, Amplifier starts to remove its last program files.",
                 "When you close that message, it starts to remove its last program files and then the folder."),
                # A delete that stopped: the person's next step, and that Amplifier stays closed.
                ("Close other programs or restart this PC. Then uninstall Amplifier again to finish. "
                 "Until then Amplifier does not open.",
                 "If Amplifier says that it could not finish, close other programs or restart the PC, then uninstall "
                 "again. Until then Amplifier does not open."),
                ("The folder itself stays, because it holds entries that are not Amplifier's.",
                 "A file or folder directly in the Amplifier folder whose name Amplifier does not use stays, and then "
                 "the folder stays too."),
                # The earlier question: an earlier launcher, or a folder without setup's record.
                ("Your chats, settings, accounts and the program files stay in this folder:",
                 'Amplifier can also ask only "Uninstall Amplifier?" with **Yes** and **No**, for example in an '
                 "installation from an earlier setup file. There, **Yes** uninstalls and leaves the folder, and **No** "
                 "changes nothing.")):
            with self.subTest(stated=stated):
                self.assertIn(product, consumer)
                self.assertIn(stated, rest)
        self.assertNotIn("It leaves the folder with your chats", plain)
        self.assertNotIn("Native Windows is not yet supported", readme)
        # The design stays a record of what is tested, not a support claim.
        self.assertIn("(docs/design/native-windows-installation.md)", section)
        design = source("docs/design/native-windows-installation.md")
        self.assertIn("**not a native Windows support claim**", design)
        self.assertIn("Qualified native Windows support is not claimed.", " ".join(design.split()))


if __name__ == "__main__":
    unittest.main()