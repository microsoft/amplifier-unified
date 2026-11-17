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
                "- **OpenAI API:** in our test on a PC, a chat wrote no file outside the folder.",
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