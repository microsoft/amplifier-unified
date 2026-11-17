"""Keep manual interpreter selection aligned with the executable installer."""

import ast
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
                      "Settings > Apps > Installed apps", "It leaves the folder with your chats",
                      "`%USERPROFILE%\\.amplifier\\rate-limit-state.json`. Uninstall does not remove that file"):
            with self.subTest(limit=limit):
                self.assertIn(limit, plain)
        self.assertNotIn("Native Windows is not yet supported", readme)
        # The design stays a record of what is tested, not a support claim.
        self.assertIn("(docs/design/native-windows-installation.md)", section)
        design = source("docs/design/native-windows-installation.md")
        self.assertIn("**not a native Windows support claim**", design)
        self.assertIn("Qualified native Windows support is not claimed.", " ".join(design.split()))

    def test_windows_preview_says_where_the_file_is_and_names_three_stops(self):
        def source(path):
            return (ROOT / path).read_text(encoding="utf-8")

        def plain(text):
            return " ".join(text.split())

        section = source("README.md").split("\n### Windows 11 (preview)\n", 1)[1].split("\n## ", 1)[0]
        steps = {number: plain(text) for number, text in re.findall(r"^(\d)\. (.*(?:\n   .*)*)", section, re.M)}
        self.assertEqual(sorted(steps), list("12345"))
        # Step 2 uses Microsoft Edge's own names. Another browser gets general words only.
        self.assertIn('says that the file "isn\'t commonly downloaded". In the Downloads list,', steps["2"])
        self.assertTrue(steps["2"].endswith("Another browser can ask in other words. Choose to keep the file."))
        # Step 3 says where the file is and how to open it.
        self.assertTrue(steps["3"].startswith(
            "Open the file. In Microsoft Edge, select **Open file** below the file's name in the Downloads list. "
            "In another browser, use that browser's list of downloads. "
            "You can also double-click the file in the folder where your browser saved it, "
            'usually your Downloads folder. Do not use "Run as administrator".'), steps["3"])
        # Below the steps: three stops. The quoted words and the next step are the product's own.
        after = section.split("\n5. ", 1)[1].split("\nLimits of this preview:\n", 1)[0]
        self.assertIn("Setup or the first start can stop with a message. "
                      "The message says what happened and what to do. Three cases:", plain(after))
        # Setup's message has these two headings; the first start's message has no headings.
        setup = source("scripts/windows_bootstrap_native.cs")
        self.assertIn('"Setup did not finish.\\n\\nWhat happened:\\n"+text[0]+"\\n\\nWhat to do:\\n"+text[1]+', setup)
        limit = re.search(r"internal const int MaxRootLength=(\d+);", setup).group(1)
        self.assertEqual([plain(text) for text in re.findall(r"^- (.*(?:\n  .*)*)", after, re.M)], [
            '**Administrator sign-in.** Setup says: "This Windows sign-in runs every program as administrator." '
            "Sign in to Windows with a standard account, or turn User Account Control on. Then run setup again.",
            '**Long folder path.** Setup says that the path of the Amplifier folder "is too long for Windows". '
            f"The limit is {limit} characters. Use a Windows account with a shorter name.",
            "**Certificate step.** In step 4, Amplifier says that "
            "\"this PC's security settings do not let it finish setting up its private connection to your "
            'browser". The message names a certificate file. If your organization manages this PC, ask its IT '
            "support to trust this certificate for your account."])
        # Setup: each cause and its next step are one stop, in these words.
        for cause, next_step in (
                ('"This Windows sign-in runs every program as administrator. ',
                 '"Sign in to Windows with a standard account, or turn User Account Control on. Then run setup again.")'),
                ('", and that path is too long for Windows ("+\n',
                 '" characters; the limit is "+MaxRootLength.ToString(CultureInfo.InvariantCulture)+").",\n'),
                ('" characters; the limit is "+MaxRootLength.ToString(CultureInfo.InvariantCulture)+").",\n',
                 '"Use a Windows account with a shorter name.")')):
            with self.subTest(cause=cause):
                self.assertEqual(setup.count(cause), 1)
                between = setup.split(cause, 1)[1]
                self.assertIn(next_step, between)
                self.assertNotIn("SetupStop", between.split(next_step, 1)[0])
        # The first start: the message, the request to IT support, and the file that the message names.
        first_start = source("amplifier_web/windows_installation.py")
        texts = [node.value for node in ast.walk(ast.parse(first_start))
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        for shown in ("Amplifier is running, but this PC's security settings do not let it finish "
                      "setting up its private connection to your browser.\n\n",
                      "If your organization manages this PC, ask its IT support to trust this "
                      "certificate for your account:\n"):
            with self.subTest(shown=shown):
                self.assertIn(shown, texts)
        self.assertIn('saved = str(root / "export" / "local-ca.cer")\n', first_start)
        self.assertIn('"certificate for your account:\\n" + saved)\n', first_start)
        self.assertIn('"setting up its private connection to your browser.\\n\\n" + support + "\\n\\n"\n', first_start)
        # The design's list of what the README states names the three stops too.
        self.assertIn("three stops that setup or the first start explains by itself",
                      plain(source("docs/design/native-windows-installation.md")))


if __name__ == "__main__":
    unittest.main()