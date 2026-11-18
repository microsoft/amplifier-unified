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
                      "Settings > Apps > Installed apps"):
            with self.subTest(limit=limit):
                self.assertIn(limit, plain)
        # Folder: one statement for each AI connection that the app offers, and no more than is known.
        folder = " ".join(section.split("\n- **Folder:**", 1)[1].split("\n- **Uninstall:**", 1)[0].split())
        self.assertTrue(folder.startswith(
            "Amplifier installs into `%LOCALAPPDATA%\\Amplifier` and keeps your chats, settings and accounts there. "
            "What a chat writes outside that folder depends on the AI connection:"), folder)
        import json
        offered = [row["name"] for row in json.loads(source("amplifier_web/ai_services.json"))]
        self.assertEqual(sorted(re.findall(r"\*\*([^*]+?):?\*\*", folder)), sorted(offered))
        statements = (
                # What the tests on a PC looked at, and what they found. Their probe reads names directly in
                # a fixed list of places of the Windows account. It does not look inside other folders, and it
                # does not see every file that a chat can write. So this statement must not say that a chat
                # wrote no file outside the folder.
                "- **OpenAI API:** after a chat in our tests on a PC, we looked for a file or folder with "
                "\"amplifier\" in its name. We looked directly in the usual places of the Windows account, "
                "such as the profile folder, AppData, Documents and the Desktop. Outside the folder, we found "
                "only one that Amplifier made: the Start-menu entry from setup. We did not look inside other "
                "folders there or on the rest of the disk.",
                # Both halves were seen on a PC: the file outside the folder with an earlier version, and
                # the file inside the folder with 0.106.3. The probes of that test read names in the usual
                # per-user places, not every file on the PC. So the statement says where the file was written
                # and that the earlier folder did not appear. It must not say that nothing is outside the folder.
                # An installation that ran an earlier version can hold the file outside, and nothing deletes it.
                "- **Anthropic API:** up to version 0.106.2, a chat writes one small file outside the folder: "
                "`%USERPROFILE%\\.amplifier\\rate-limit-state.json`. We saw this in a test on a PC. "
                "From version 0.106.3 on, Amplifier writes that file inside the folder instead. "
                "We tested a new installation of version 0.106.3 on a PC with one chat and one "
                "\"Send test message\" check of the connection. Both wrote the file inside the folder. "
                "The folder `%USERPROFILE%\\.amplifier` did not appear. "
                "A file that an earlier version wrote stays where it is. "
                "Later versions do not use it, and uninstall does not remove it. You can delete it.",
                "- **GitHub Copilot Subscription:** its component makes two folders, "
                "`%USERPROFILE%\\.amplifier-provider-github-copilot` and "
                "`%LOCALAPPDATA%\\amplifier-provider-github-copilot`. We read this in its source code. "
                "We have not tested it on a PC.",
                "- **ChatGPT Subscription**, **Google Gemini API** and **OpenAI Compatible API:** "
                "we have not tested this.")
        # The item is these sentences and nothing else: it must not say that nothing is outside the folder.
        self.assertEqual(folder.split(" depends on the AI connection: ", 1)[1], " ".join(statements))
        # A tripwire for the broad claim, also when it is added to the pinned text above. It refuses these
        # words in every sentence, also in a true one that limits or denies the claim. Read the wording and
        # its evidence before you change this line.
        self.assertNotIn("no file outside", folder)
        # "Amplifier writes that file inside the folder" is the host's rule, with that file name. The statement
        # names three versions: the last release without the rule, the first release with it, and the release
        # that was tested on a PC. This check refuses a version that does not exist yet, a first release with
        # the rule that is not later than the last one without it, and a tested release that does not have the
        # rule. It cannot see a release or a PC: whoever changes a number here reads the release and the
        # clean-room record first.
        from amplifier_web.provider_state_files import STATE_FILES
        self.assertEqual(STATE_FILES["provider-anthropic"]["rate_limit_state_path"], "rate-limit-state.json")
        self.assertIn('"AMPLIFIER_HOME": "shared"', source("amplifier_web/windows_consumer.py"))

        def number(version):
            return tuple(map(int, version.split(".")))

        anthropic = folder.split("- **Anthropic API:**", 1)[1].split(" - **", 1)[0]
        without, = re.findall(r"up to version (\d+\.\d+\.\d+),", anthropic)
        first, = re.findall(r"From version (\d+\.\d+\.\d+) on,", anthropic)
        tested, = re.findall(r"We tested a new installation of version (\d+\.\d+\.\d+) on a PC ", anthropic)
        self.assertEqual(sorted(re.findall(r"\d+\.\d+\.\d+", anthropic)), sorted([without, first, tested]))
        current = re.search(r'^version = "(\d+\.\d+\.\d+)"$', source("pyproject.toml"), re.M).group(1)
        self.assertLess(number(without), number(first))
        self.assertLessEqual(number(first), number(tested))
        self.assertLessEqual(number(tested), number(current))
        # The item "Uninstall" has its own case, which compares every sentence with what the product shows:
        # tests/test_windows_uninstall.py, test_readme_uninstall_item_is_sentence_for_sentence_what_the_product_shows.
        self.assertNotIn("Native Windows is not yet supported", readme)
        # The design stays a record of what is tested, not a support claim.
        self.assertIn("(docs/design/native-windows-installation.md)", section)
        design = source("docs/design/native-windows-installation.md")
        self.assertIn("**not a native Windows support claim**", design)
        self.assertIn("Qualified native Windows support is not claimed.", " ".join(design.split()))

    def test_windows_preview_names_its_browser_and_three_stops(self):
        def source(path):
            return (ROOT / path).read_text(encoding="utf-8")

        def plain(text):
            return " ".join(text.split())

        section = source("README.md").split("\n### Windows 11 (preview)\n", 1)[1].split("\n## ", 1)[0]
        blocks = [block.strip("\n") for block in re.split(r"\n[ \t]*\n", section) if block.strip()]
        texts = [plain(block) for block in blocks]
        # The steps use Microsoft Edge's names, and the section says so before the first step.
        steps = next(index for index, text in enumerate(texts) if text.startswith("1. Download the setup file:"))
        self.assertIn("The steps below describe Microsoft Edge.", " ".join(texts[:steps]))
        numbered = " ".join(texts[steps:])
        self.assertIn(" 2. Microsoft Edge holds the download ", numbered)
        # "In step 4" below: step 4 is the step with the certificate question.
        self.assertRegex(numbered, r" 4\. (?:(?! 5\. ).)*whether to install a certificate named ")
        # Below the steps and above the limits: three stops. The list is the one that follows this sentence.
        lead, limits = ("In these cases, setup or the first start does not finish. A message says what to do:",
                        "Limits of this preview:")
        self.assertIn(lead, texts)
        self.assertIn(limits, texts)
        self.assertLess(steps, texts.index(lead))
        self.assertLess(texts.index(lead), texts.index(limits))
        setup = source("scripts/windows_bootstrap_native.cs")
        limit = re.search(r"internal const int MaxRootLength=(\d+);", setup).group(1)
        cases = blocks[texts.index(lead) + 1]
        self.assertEqual([plain(text) for text in re.findall(r"^- (.*(?:\n  .*)*)", cases, re.M)], [
            'Setup says: "This Windows sign-in runs every program as administrator." '
            "Sign in to Windows with a standard account, or turn User Account Control on, and then run setup again.",
            'Setup says that the path of the Amplifier folder "is too long for Windows" '
            f"(the limit is {limit} characters). Use a Windows account with a shorter name.",
            "In step 4, Amplifier says that "
            "\"this PC's security settings do not let it finish setting up its private connection to your "
            'browser". If your organization manages this PC, ask its IT support to trust the certificate '
            "file named in the message for your account."])
        # Each quoted text is a part of the text that the program shows for that case.
        first_start = source("amplifier_web/windows_installation.py")
        shown_texts = [node.value for node in ast.walk(ast.parse(first_start))
                       if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        quoted = re.findall(r'"([^"]+)"', plain(cases))
        self.assertEqual(len(quoted), 3, quoted)
        self.assertIn('"' + quoted[0] + " User Account Control is off,", setup)
        self.assertIn('", and that path ' + quoted[1] + ' ("+', setup)
        self.assertIn("Amplifier is running, but " + quoted[2] + ".\n\n", shown_texts)
        # Setup: its message says that setup did not finish, and shows a stop's next step below "What to do:".
        self.assertIn('"Setup did not finish.\\n\\nWhat happened:\\n"+text[0]+"\\n\\nWhat to do:\\n"+text[1]', setup)
        self.assertIn("if(stop!=null) return new[]{stop.Message,stop.Next};", setup)
        # Setup: each cause and its next step are one stop, in these words.
        for cause, next_step in (
                ('"This Windows sign-in runs every program as administrator. ',
                 '"Sign in to Windows with a standard account, or turn User Account Control on. '
                 'Then run setup again.")'),
                ('", and that path is too long for Windows ("+',
                 '" characters; the limit is "+MaxRootLength.ToString(CultureInfo.InvariantCulture)+").",'),
                ('" characters; the limit is "+MaxRootLength.ToString(CultureInfo.InvariantCulture)+").",',
                 '"Use a Windows account with a shorter name.")')):
            with self.subTest(cause=cause):
                self.assertEqual(setup.count(cause), 1)
                between = setup.split(cause, 1)[1]
                self.assertIn(next_step, between)
                self.assertNotIn("SetupStop", between.split(next_step, 1)[0])
        # The first start: the message, the request to IT support, and the certificate file that the message names.
        for shown in ("Amplifier is running, but this PC's security settings do not let it finish "
                      "setting up its private connection to your browser.\n\n",
                      "If your organization manages this PC, ask its IT support to trust this "
                      "certificate for your account:\n"):
            with self.subTest(shown=shown):
                self.assertIn(shown, shown_texts)
        self.assertIn('saved = str(root / "export" / "local-ca.cer")', first_start)
        self.assertIn('"certificate for your account:\\n" + saved)', first_start)
        self.assertIn('"setting up its private connection to your browser.\\n\\n" + support + "\\n\\n"', first_start)
        self.assertIn("owner_notice(blocked)", first_start)
        # The design's list of what the README states names the three stops too.
        self.assertIn("three stops that setup or the first start explains by itself",
                      plain(source("docs/design/native-windows-installation.md")))

if __name__ == "__main__":
    unittest.main()