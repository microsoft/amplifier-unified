"""Consumer setup's one folder and plain stops; native behavior is CI evidence."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SOURCE = (SCRIPTS / "windows_bootstrap_native.cs").read_text(encoding="utf-8")
DESIGN = (ROOT / "docs/design/native-windows-installation.md").read_text(encoding="utf-8")


def between(start, end, source=SOURCE):
    return source.split(start, 1)[1].split(end, 1)[0]


def code_of(body):
    """A C# body without its comment lines."""
    return "\n".join(line for line in body.splitlines() if not line.strip().startswith("//"))


def returns(body):
    """The expression of each `return` of a C# body, and the condition that stands before each in its line."""
    code = code_of(body)
    conditions = re.findall(r"^\s*(if\([^\n]*\) )?return\b", code, re.M)
    return conditions, [part.split(";", 1)[0] for part in code.split("return")[1:]]


def names_before_a_bracket(body):
    """Each name that stands before a `(` in a C# body, with the number of its uses. No string literal counts."""
    found = re.findall(r"([A-Za-z_][A-Za-z0-9_.]*)(?:<[A-Za-z0-9_<>,]*>)?\s*\(",
                       re.sub(r'"(?:[^"\\]|\\.)*"', '""', code_of(body)))
    return {name: found.count(name) for name in found}


def in_design(text):
    """Every sentence of a text of setup is in the design, word for word."""
    design = " ".join(DESIGN.split())
    for part in text.replace("{root}", "\n").split("\n"):
        # The design names the folder between two quoted parts, so a part can begin with ", ".
        for sentence in re.split(r"(?<=[.:]) ", part.strip().removeprefix(", ")):
            assert sentence in design, sentence


def test_folder_name_and_agreed_length_limit_match_the_design():
    name = re.search(r'internal const string RootName="([^"]+)";', SOURCE).group(1)
    limit = int(re.search(r"internal const int MaxRootLength=(\d+);", SOURCE).group(1))
    assert name == "Amplifier"
    # The design owns the number. A longer folder name must fail here, not in
    # a person's first chat: the app repeats this path inside its data paths.
    assert f"`%LOCALAPPDATA%\\{name}`" in DESIGN
    assert int(re.search(r"longest supported installation folder path is \*\*(\d+) characters\*\*", DESIGN).group(1)) == limit
    # Agreed with the app's path-length work, which owns this number.
    assert limit == 59
    # An ordinary profile with the longest local account name Windows allows.
    assert len("C:\\Users\\" + "u" * 20 + "\\AppData\\Local\\" + name) <= limit
    precheck = between("internal static string Precheck(", "internal static string[] Explain(")
    assert "Path.Combine(parent,RootName)" in precheck and "root.Length>MaxRootLength" in precheck


def test_setup_checks_the_pc_and_folder_before_it_asks_anything():
    window = between("static int ConsumerWindow() {", "static int InstallWindow(")
    order = [window.index(text) for text in (
        'single=new System.Threading.Mutex(true,"Global\\\\AmplifierUnifiedSetup-"+account,out first)',
        "if(!first) {", "root=Precheck(out state)", 'if(state=="installed") return OpenInstalled(root);',
        "return InstallWindow(root,state);")]
    assert order == sorted(order)
    assert "MessageBoxButtons.OKCancel" not in window  # the question comes after the checks
    precheck = between("internal static string Precheck(", "internal static string[] Explain(")
    steps = [precheck.index(text) for text in (
        'Stage="Checking this PC"', "Platform();", "root.Length>MaxRootLength", "InstallStorage.Check(parent)",
        "state=RootState(root)", 'if(state=="foreign")', 'if(state=="damaged")',
        'state=="installed"?"absent":SignInTask(out program)', 'if(task=="unavailable")', 'if(task=="leftover")',
        'if(task=="present")')]
    assert steps == sorted(steps)
    platform = between("internal static void Platform() {", "internal static string RootState(")
    # Elevation is named first, with the exact next step for each cause.
    assert platform.index("Elevation()") < platform.index("Win32_OperatingSystem")
    assert 'with a double-click, not with \\"Run as administrator\\"' in platform
    assert "turn User Account Control on" in platform
    # A token query that fails is not reported as a fact about the account.
    elevation = between("internal static string Elevation() {", "internal static void Platform() {")
    assert 'if(!GetTokenInformation(identity.Token,18,out type,4,out length)) return "administrator";' in elevation


def test_one_fixed_folder_is_created_marked_and_never_adopted():
    consumer = between("static string InstallConsumer(", "static DialogResult Say(")
    order = [consumer.index(text) for text in (
        "new InstallStorage(Path.GetDirectoryName(root),RootName)", "created=true;",
        "PrivateText(Path.Combine(root,Started)", 'LogPath=Path.Combine(root,"install.log")',
        '"--audit"', "PrivateText(Path.Combine(root,Complete)", "Installed=true;", '"--integrate"',
        # A finished installation does not keep the mark of unfinished work.
        "File.Delete(Path.Combine(root,Started))", "Process.Start(AppStart(root))")]
    assert order == sorted(order)
    assert "Guid.NewGuid" not in consumer and "amplifier-install-" not in consumer
    storage = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    fixed = between("public InstallStorage(string parent, string name, bool existing) {", "public void Validate()", storage)
    assert fixed.index("Unsafe bootstrap root name") < fixed.index("Traverse(parent)") < fixed.index("CreateExact(Root)")
    # An existing folder is only pinned and checked as a private folder; a new
    # one is still created exclusively. Nothing is created or repaired for it.
    assert "if (existing) Hold(Root, true, true, false); else CreateExact(Root);" in fixed
    assert "public InstallStorage(string parent, string name) : this(parent, name, false) {}" in storage
    # The unchanged prototype entry keeps its unique folder per run.
    assert 'this(parent, "amplifier-install-" + Guid.NewGuid().ToString("N"), false)' in storage
    spec = importlib.util.spec_from_file_location("setup_folder_builder", SCRIPTS / "windows_bootstrap_native.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    assert b"public InstallStorage(string parent, string name)" in builder.sources(SCRIPTS)["InstallStorage.cs"]


def test_markers_count_only_inside_a_checked_private_folder():
    state = between("internal static string RootState(", "static string Marked(")
    # The storage owner's checks (owner, access for other accounts, links, the
    # folders above) come before any marker is read.
    assert state.index("using(Existing(root)) return Marked(root)") < state.index('return "foreign"')
    assert "catch(IOException error)" in state  # a folder in use is not classified
    marked = between("static string Marked(", "internal static void RemoveUnfinished(")
    assert marked.index("Complete") < marked.index("Started")  # a finished installation wins
    assert 'return used?"damaged":"unfinished"' in marked  # app data is never "unfinished"
    for folder in ("app", "shared", "ownership", "capture", "import", "workspace"):
        assert '"' + folder + '"' in marked
    # Component sources in either store folder are app data too.
    assert '"workspace",SourceStore,EarlierSourceStore}' in marked


def test_new_installations_get_the_short_source_store_folder():
    assert 'internal const string SourceStore="s", EarlierSourceStore="source-store";' in SOURCE
    consumer = between("static string InstallConsumer(", "static DialogResult Say(")
    # Created with the other private folders, and the first installation step uses the same one.
    assert consumer.index('"import",SourceStore,"workspace"') < consumer.index(
        'env["AMPLIFIER_SOURCE_STORE"]=Path.Combine(root,SourceStore);')
    # Setup never creates, moves or removes the earlier folder: it only recognises it as app data.
    assert SOURCE.count("EarlierSourceStore") == 2 and SOURCE.count('"source-store"') == 1
    assert "store folder is named `s`" in " ".join(DESIGN.split())


def test_unfinished_attempts_are_removed_and_installations_never_are():
    removal = between("internal static void RemoveUnfinished(", "static void RemoveContents(")
    # One removal path: pinned, classified again at the moment of removal.
    order = [removal.index(text) for text in (
        "using(Existing(root))", 'Need(Marked(root)=="unfinished"', "SaveDiagnostics(root)", "RemoveContents(",
        "Directory.Delete(root,false)")]
    assert order == sorted(order)
    assert "RemoveTree" not in SOURCE
    assert SOURCE.count("RemoveContents(") == 3 and SOURCE.count("RemoveUnfinished(root)") == 2
    contents = between("static void RemoveContents(", "internal static IDisposable PinInstalled(")
    assert "FileAttributes.ReparsePoint)!=0) Directory.Delete(entry,false)" in contents  # never enters a link
    assert contents.index("RemoveContents(entry,null)") < contents.index("File.Delete(marker)")  # marker last
    consumer = between("static string InstallConsumer(", "static DialogResult Say(")
    cleanup = between("} catch(Exception) {\n                // Storage has released its pins.", "throw;", consumer)
    assert "if(created && !Installed) {" in cleanup and "RemoveUnfinished(root)" in cleanup
    # A folder that this run did not create is never deleted on failure.
    assert "Directory.Delete" not in cleanup and "!created" not in cleanup
    assert consumer.index('if(state=="unfinished") {') < consumer.index("new InstallStorage(")
    opened = between("static int OpenInstalled(", "static int ConsumerWindow()")
    assert "RemoveUnfinished" not in opened and "RemoveContents" not in opened
    # The installation is checked again, and held, while Windows starts it.
    assert "using(PinInstalled(root,start.FileName)) return Process.Start(start);" in between(
        "static Process StartApp(", "static int OpenInstalled(")
    pin = between("internal static IDisposable PinInstalled(", "internal static string SignInTask(")
    assert pin.index('Need(Marked(root)=="installed"') < pin.index("storage.TrustExecutable(program)")
    saved = between("internal static string SaveDiagnostics(", "static string Shortcut(")
    # A new private folder, created exclusively; never a predictable or adopted one.
    assert "new InstallStorage(parent,name)" in saved and "storage.CreateFile(" in saved
    assert "Directory.CreateDirectory" not in saved and "File.Copy" not in saved


EMPTIED = ("Setup cannot install Amplifier yet. Setup found an installation record in this folder:\n{root}\n\n"
           "Setup cannot find a program file that it needs to open Amplifier from that folder. "
           "Setup found no chats or settings in the folder. "
           "It does not change the folder, because the folder can hold files or folders that are not Amplifier's.\n\n"
           "Move or rename that folder. Then run this setup file again.")
# A folder with setup's completion record and no launcher program, when that look said no. WHOLE: no entry
# has the journal's name, and the folder `app` is there. BARE: every other case.
WHOLE = ("Amplifier is already installed in {root}, but setup cannot open it. "
         "Setup cannot find a program file that it needs to open Amplifier from that folder.\n\n"
         "Keep that folder. Amplifier keeps your chats and settings in it. This version of setup cannot repair it.")
BARE = ("Setup cannot open Amplifier. Setup found an installation record in this folder:\n{root}\n\n"
        "Setup cannot find a program file that it needs to open Amplifier from that folder.\n\n"
        "Keep that folder. Setup deletes nothing in it. This version of setup cannot repair it.")
# A folder with the launcher program and no entry of the journal's name: the question with and without a
# Start-menu entry, and the message after a failed entries step. KEPT ends the last two only when the two
# looks say `kept`.
OPEN = ("Amplifier is already installed on this PC.\n\nSelect Yes to open it now.\n\n"
        "You can also open it from the Start menu: Amplifier Unified.")
ADD = ("Amplifier is already installed on this PC, but setup did not find it in the Start menu.\n\n"
       "Select Yes to add it to the Start menu again and start it now.")
UNADDED = ("Setup could not add Amplifier to the Start menu. Setup starts Amplifier now.\n\n"
           "To open it later, run this setup file again.")
KEPT = " Your chats and settings are kept."
# A folder with the launcher program and an entry of the journal's name: the message with a Start-menu entry,
# the question without one, and the message after the entries step.
HALTED = ("Setup found an uninstall record in this folder:\n{root}\n\n"
          "Amplifier does not open while that record is there. Setup did not start it.\n\n"
          "To finish that uninstall, select Uninstall Amplifier in the Start menu or in the Windows Apps list.")
ASKED = ("Setup found an uninstall record in this folder:\n{root}\n\n"
         "Amplifier does not open while that record is there. Setup does not start it. "
         "Setup did not find Amplifier in the Start menu.\n\n"
         "Select Yes to add Amplifier to the Start menu again, so that you can finish that uninstall from there.")
ADDED = ("Setup added Amplifier to the Start menu. "
         "Setup did not start Amplifier, because it found an uninstall record in this folder:\n{root}\n\n"
         "To finish that uninstall, select Uninstall Amplifier in the Start menu.")
RESTART = ("Setup could not add Amplifier to the Start menu. "
           "Setup did not start Amplifier, because it found an uninstall record in this folder:\n{root}\n\n"
           "Restart Windows. Then run this setup file again.")
# The same state, when the look also finds the journal of a delete and none of the person's data.
MOVED = ("Setup could not add Amplifier to the Start menu. "
         "Setup did not start Amplifier, because it found an uninstall record in this folder:\n{root}\n\n"
         "Setup found no chats or settings in the folder.\n\n"
         "If another Amplifier message is open, answer it first. "
         "If the folder is still there afterwards, move or rename it. Then run this setup file again.")


def literal(body, name="root"):
    """The text that a C# expression of string literals and `name` gives, with `{root}` for the folder."""
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"|\b(' + name + r')\b', body)
    return "".join("{root}" if name else text.replace("\\n", "\n").replace('\\"', '"') for text, name in parts)


def test_setup_without_a_launcher_program_says_what_its_own_look_finds():
    """Setup's text for a finished installation whose launcher program is missing (follow-up A24).

    Seen on a PC on November 17, 2026: an uninstall with the delete choice left the folder, because a file of
    the person was in it. The next setup run said "Keep that folder. It holds your chats and settings." The
    chats and settings were deleted, and the text named no next step. Setup now looks: with a journal that
    records the delete choice, and with none of the person's data, it says what it found and gives the next
    step: move or rename the folder, as at setup's stop for a damaged folder. It does not tell the person to
    delete the
    folder, which can hold something of the person's that the look did not see. Every other case had the
    earlier text until follow-up A55: it said "It holds your chats and settings" also where a removal had
    begun. Setup now makes two more looks (`Signs`), and only one of its three texts names the person's
    chats and settings. Setup still installs nothing there and deletes nothing.
    The native check compiles these functions and runs them on real folders (tests/windows/native_setup.Tests.cs).
    """
    import inspect
    from amplifier_web import windows_uninstall
    # Uninstall owns the names. Setup has a copy, and only looks.
    assert re.search(r'internal const string Journal="([^"]+)";', SOURCE).group(1) == windows_uninstall.JOURNAL
    names = re.search(r"internal static readonly string\[\] Data=\{([^}]+)\};", SOURCE).group(1)
    assert tuple(name.strip().strip('"') for name in names.split(",")) == windows_uninstall.DATA
    # The schema and the token that uninstall's own reader requires. A change there must come here too.
    reader = inspect.getsource(windows_uninstall.pending)
    assert 'value["schema"] == 1' in reader and 're.fullmatch("[0-9a-f]{32}", value["token"])' in reader
    # That reader requires a choice that is a text. Uninstall compares it in its removal steps; setup's
    # look requires the choice "delete" itself.
    assert 'isinstance(value.get("choice"), str)' in reader and '"delete"' not in reader
    for step in (windows_uninstall.remove, windows_uninstall.finish):
        assert 'record["choice"] != choice' in inspect.getsource(step)
    assert "owned.read_private(name, limit=4096)" in inspect.getsource(windows_uninstall._private_json)
    emptied = between("internal static bool Emptied(", "internal static string Signs(")
    order = [emptied.index(text) for text in (
        "string journal=Path.Combine(root,Journal);",
        "if((File.GetAttributes(journal)&(FileAttributes.Directory|FileAttributes.ReparsePoint))!=0) return false;",
        "var bytes=new byte[4097]; int count=0, read;",
        # Shared with every other access: the last step of a removal deletes the journal.
        "new FileStream(journal,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))",
        "while(count<bytes.Length && (read=file.Read(bytes,count,bytes.Length-count))>0) count+=read;",
        "if(count>4096) return false;",
        "Json.Deserialize<Dictionary<string,object>>(Utf8.GetString(bytes,0,count));",
        'if(record==null || !record.TryGetValue("schema",out schema) || !(schema is int) || (int)schema!=1) return false;',
        r'if(Text(record,"choice")!="delete" || !Regex.IsMatch(token,"\\A[0-9a-f]{32}\\z")) return false;',
        "foreach(string name in Data) {",
        "try { kind=File.GetAttributes(entry); } catch(FileNotFoundException) { continue; }",
        # A file and a link are data. Only a real folder is listed.
        "if((kind&(FileAttributes.Directory|FileAttributes.ReparsePoint))!=FileAttributes.Directory ||",
        "Directory.EnumerateFileSystemEntries(entry).Any()) return false;",
        "return true;",
        "} catch(Exception) { return false; }")]
    assert order == sorted(order)
    assert emptied.count("return false;") == 6 and emptied.count("return") == 7  # One yes, after every look.
    # Strict UTF-8: bytes that are not UTF-8 are an error, and an error answers no.
    assert "static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);" in SOURCE
    # The three texts, complete. The one with the next step only after the look said yes; the one that
    # names chats and settings only after the two further looks said `kept`; the third in every other case.
    unopened = between("internal static string Unopened(", "internal static string Offer(")
    code = code_of(unopened)
    conditions, texts = returns(unopened)
    assert conditions == ["if(Emptied(root)) ", 'if(Signs(root)=="kept") ', ""]
    assert [literal(text) for text in texts] == [EMPTIED, WHOLE, BARE]
    assert code.count("return") == 3 and code.count("Emptied(") == 1 and code.count("Signs(") == 1
    # They only look and build a text. The one open is for reading, and it allows a deletion by another
    # program (`FileShare.Delete` is a sharing mode, not a deletion).
    assert emptied.count("FileStream(") == 1 and emptied.count("FileShare.Delete") == 1
    looks = "\n".join(line for line in emptied.splitlines() if not line.strip().startswith("//")).replace(
        "FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete", "")
    # The words of the two texts are not operations: compare the code without its string literals.
    for body in (looks, code):
        operations = re.sub(r'"(?:[^"\\]|\\.)*"', '""', body)
        for change in ("Delete", "Write", "Create", "Move", "Remove", "Process", "Say(", "Install", "Storage",
                       "FileMode", "FileAccess", "Set"):
            assert change not in operations, change
    # A list of words does not name every operation (a property of a `FileInfo`, for example). So the
    # case also holds, for the two functions and for the one function of setup that they call: each name
    # that stands before a `(`, with the number of its uses; that each assignment whose target is a name
    # is to one of the look's own local names; that nothing which ends with a bracket stands directly
    # before an assignment operator; and that no comment stands in a line of code (a comment between
    # two tokens would hide them from these expressions). These are checks of the source text for
    # these forms. A form of C# that is not in this list is not checked, and they do not prove what a
    # call does. The native case holds what the looks leave in the folder and in setup's list of names.
    def text(body):
        lines = "\n".join(line for line in body.splitlines() if not line.strip().startswith("//"))
        return re.sub(r'"(?:[^"\\]|\\.)*"', '""', lines)

    def calls(body):
        found = re.findall(r"([A-Za-z_][A-Za-z0-9_.]*)(?:<[A-Za-z0-9_<>,]*>)?\s*\(", text(body))
        return {name: found.count(name) for name in found}
    assert calls(emptied) == {
        "if": 5, "using": 1, "while": 1, "foreach": 1, "catch": 2,
        "Path.Combine": 2, "File.GetAttributes": 2, "FileStream": 1, "file.Read": 1, "Json.Deserialize": 1,
        "Utf8.GetString": 1, "record.TryGetValue": 1, "Text": 2, "Regex.IsMatch": 1,
        "Directory.EnumerateFileSystemEntries": 1, "Any": 1}
    assert calls(unopened) == {"if": 2, "Emptied": 1, "Signs": 1}
    reads = between("static string Text(", "static Dictionary<string,object> Object(")
    assert calls(reads) == {"Need": 1, "map.TryGetValue": 1, "return": 1}  # `return (string)value;`
    assert ("static void Need(bool value, string message) "
            "{ if (!value) throw new InvalidDataException(message); }") in SOURCE
    # A name, with or without an index, before `=` or before an operator with `=`. Not `==`, `!=`, `<=`,
    # `>=` and not `=>`. No field of setup (`Stage`, `Data[6]`) and no member gets a value.
    target = r"(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_.]*)(?:\s*\[[^\]]*\])?\s*(?:[-+*/%|&^]|<<|>>)?=(?![=>])"
    assert set(re.findall(target, text(emptied))) == {
        "journal", "bytes", "count", "read", "file", "record", "token", "kind", "entry"}
    for body in (unopened, reads):
        assert not re.findall(target, text(body))
    # A target in brackets (`(Data)[6]=`) or after an index (`Data[6][0]=`) has no name directly before the
    # operator. No assignment of the three functions has that form.
    closed = r"[)\]]\s*(?:[-+*/%|&^]|<<|>>)?=(?![=>])"
    for body in (emptied, unopened, reads):
        assert not re.findall(closed, text(body))
        assert "++" not in text(body) and "--" not in text(body)
        # Every comment of these functions is a whole line. (`text` takes those lines and the string
        # literals away.)
        assert "//" not in text(body) and "/*" not in text(body)
    assert re.findall(r"\bnew\s+[A-Za-z_][A-Za-z0-9_.]*", text(emptied)) == ["new byte", "new FileStream"]
    assert "new" not in re.findall(r"[A-Za-z_]+", text(unopened) + text(reads))
    # The one place that shows the text: a finished installation with no launcher program. Setup ends with 1.
    opened = between("static int OpenInstalled(", "static int ConsumerWindow()")
    assert opened.index("if(!File.Exists(AppStart(root).FileName)) {\n"
                        "                Say(Unopened(root),MessageBoxButtons.OK,MessageBoxIcon.Warning);\n"
                        "                return 1;\n            }") < opened.index("bool listed=false;")
    # Nothing but comments stands before that look in `OpenInstalled`.
    first = [line.strip() for line in opened.splitlines() if line.strip() and not line.strip().startswith("//")]
    assert first[:2] == ["string root) {", "if(!File.Exists(AppStart(root).FileName)) {"]
    # The look has one more use: the last text of a second run that could add no entry (`Unstarted`).
    assert SOURCE.count("Unopened(") == 2 and SOURCE.count("Emptied(") == 3 and SOURCE.count("Signs(") == 3
    # The second and the third text. Neither says that a file is missing, tells the person to delete, move
    # or rename anything, or names a step that setup does not have.
    for text in (WHOLE, BARE):
        assert "missing" not in text and "cannot find a program file" in text
        assert "Move or rename" not in text and "delete that" not in text.lower() and "run this setup" not in text
        assert text.split("\n\n")[-1].startswith("Keep that folder. ")
        assert text.endswith(" This version of setup cannot repair it.")
    # Only the second names the person's chats and settings, and it says where Amplifier keeps them.
    assert "Amplifier keeps your chats and settings in it." in WHOLE and "holds" not in WHOLE
    assert "chats" not in BARE and "settings" not in BARE and "already installed" not in BARE
    assert "Setup deletes nothing in it." in BARE and BARE.split(":\n", 1)[0] == EMPTIED.split(":\n", 1)[0].replace(
        "Setup cannot install Amplifier yet.", "Setup cannot open Amplifier.")
    # What the new text does not say.
    assert "your chats and settings" not in EMPTIED and "already installed" not in EMPTIED
    # Nothing about the folder's history: setup knows only that a file with the record's name is there.
    assert "earlier installation" not in EMPTIED and "was installed" not in EMPTIED
    marked = between("static string Marked(", "internal static void RemoveUnfinished(")
    assert 'if(File.Exists(Path.Combine(root,Complete))) return "installed";' in marked
    # Setup names that file in three places: the name, this look for the name, and the write at the end
    # of an installation. It reads the file nowhere.
    assert len(re.findall(r"\bComplete\b", SOURCE)) == 3
    assert "PrivateText(Path.Combine(root,Complete),Json.Serialize(" in SOURCE
    assert "uninstall" not in EMPTIED.lower()  # A file that reads as a journal is no proof of an uninstall.
    assert "cannot start" not in EMPTIED  # Setup looked for one program file only.
    # `File.Exists` also answers no for a file that Windows does not let setup see.
    assert "missing" not in EMPTIED and "cannot find a program file" in EMPTIED
    # The next step loses nothing: the text tells nobody to delete anything.
    assert "delete" not in EMPTIED.lower() and EMPTIED.endswith(
        "\n\nMove or rename that folder. Then run this setup file again.")
    # The same step as at setup's stop for a damaged folder.
    damaged = between('if(state=="damaged") throw new SetupStop(', "string program=null;")
    assert '"Move or rename that folder. Then run setup again.");' in damaged
    # Setup's own lock is against a second setup run. It is not the installation's lock, whose name
    # uninstall gives.
    lock = re.search(r'with named\("Global\\\\([^"]+)", root, wait=wait\):',
                     inspect.getsource(windows_uninstall.single)).group(1)
    assert lock == "AmplifierUnified-installation-"
    assert lock.rstrip("-") not in SOURCE and SOURCE.count("Mutex(") == 1
    # The launcher's program, which the Start-menu entry also starts.
    from amplifier_web import windows_consumer_shell
    assert 'Path.Combine(root,"tools","amplifier-unified","Scripts","pythonw.exe")' in between(
        "static ProcessStartInfo AppStart(", "internal static bool AddEntries(")
    assert '"tools/amplifier-unified/Scripts/pythonw.exe"' in inspect.getsource(windows_consumer_shell.entries)
    # The design has both texts, sentence for sentence, and what the look reads.
    design = " ".join(DESIGN.split())
    for text in (EMPTIED, WHOLE, BARE):
        in_design(text)
    for sentence in (
            "When the launcher's start program is gone, setup shows one message and ends.",
            "When they show a file that is no folder and no link, setup opens that path and reads it.",
            "at most 4096 bytes of UTF-8 that hold a record with `schema` 1, the choice `delete` and a token of 32 "
            "lower-case hexadecimal digits",
            "The schema and the token are what uninstall's own reader requires (`windows_uninstall.pending`).",
            "Setup requires the choice `delete` in the look itself.",
            "A name counts as data when the look at its attributes shows a file or a link, or when the listing of "
            "the folder shows anything in it.",
            "Setup lists an entry only after that look showed a folder that is no link.",
            "Setup does not take the installation's lock for it, and it holds no handle on the folder or on an "
            "entry between two of those steps.",
            "Setup's stop for a damaged folder names the same step.",
            "The text tells the person to move or rename the folder, not to delete it:",
            "Each statement of the first text is what setup found in this run, or what setup does, with the "
            "reason why it does not change the folder; its last two sentences are the next step.",
            "Setup found the record's name when it classified the folder, and it looked for the launcher's start "
            "program directly before this look.",
            "setup's look for the launcher's start program found no file (`File.Exists`).",
            "That look also finds no file when Windows does not let setup see one that is there, so each of the "
            "three texts says \"cannot find\" and not \"is missing\".",
            "An emptied folder that Windows still keeps for a program is not data when Windows answers the look "
            "at its attributes and its listing; an entry that Windows does not let setup examine counts as data.",
            "For an entry with the exact spelling of a name, the removal itself gives the same two answers "
            "(`holding` in `windows_removal.py`).",
            "For an entry under another spelling (`App` for `app`) the two differ.",
            "The removal never examines such an entry: it counts the name as holding something, and it does not "
            "begin in a folder that has one.",
            "Setup's look asks Windows for the path with the name's own spelling and treats the entry that "
            "Windows gives like any other.",
            "Where Windows ignores the case of letters (an ordinary folder), that is the entry `App`: when it is "
            "an empty folder it is not data, and with a file in it it is data.",
            "Setup does not read that file in this look, and it knows no more about the folder's history.",
            "The text does not say that an installation was made in the folder.",
            "In every other case, and when any part of that look fails, setup makes two more looks (`Signs`).",
            "The second text is the only one of the three that names the person's chats and settings.",
            "It says where Amplifier keeps them.",
            "It does not say that a chat is there: setup does not look into `app`.",
            "The text names no chats and no settings: an entry with the journal's name is there, or Windows gave "
            "the look for it no answer, or the folder `app` is not there.",
            "It gives no step after \"Keep that folder\": this version of setup has no repair.",
            "Before this rule setup said in all of these states \"Keep that folder. It holds your chats and "
            "settings.\"",
            "The text does not say that Amplifier was uninstalled, and it does not say that Amplifier cannot start: "
            "a file that reads as a journal is no proof of an uninstall (a program of this account can write one), "
            "and setup looked for one program file only.",
            "With each of the three texts setup installs nothing, deletes nothing and ends with status 1.",
            "Setup opens the file for reading with every sharing mode, so Windows does not refuse another program's "
            "deletion of the journal because of this handle",
            "Setup reads the journal for one purpose only: to choose a text (see Leftovers)."):
        assert sentence in design, sentence
    # Where the look and the removal differ: an entry under another spelling of a name (`App` for `app`).
    # The removal's look at what an entry holds never examines it and counts the name as holding
    # something, and the removal does not begin in a folder that has one. Setup's look asks Windows for
    # the path with the name's own spelling. The native case holds what Windows gives for that path.
    removal = (ROOT / "amplifier_web/windows_removal.py").read_text(encoding="utf-8")
    holding = removal.split("    def holding(self, names):\n", 1)[1].split("\n    def ", 1)[0]
    assert ("            own = wanted.get(same_name(name))\n            if own is None:\n                continue\n"
            "            if name != own:\n                held.add(own)\n                continue\n") in holding
    assert "held = set(owned.holding(names))" in inspect.getsource(windows_uninstall._clear)
    begins = inspect.getsource(windows_uninstall.remove)
    refusal = ("            if altered(owned.names(), short):\n"
               "                raise ValueError(\"An entry differs from this installation's own name only by case.\")\n")
    assert begins.index(refusal) < begins.index("token = begin(root, choice, wait=wait, **fields)")
    assert "string entry=Path.Combine(root,name);" in emptied
    # The numbers of the design's sentence on the native check.
    native = (ROOT / "tests/windows/native_setup.Tests.cs").read_text(encoding="utf-8")
    for message in ("Windows gives the folder App for the path with app",
                    "an empty folder under another spelling of a data name is not data",
                    "a file in a folder under another spelling of a data name is data"):
        assert f'"{message}");' in native, message
    others = native.split("string[] others={", 1)[1].split("};", 1)[0]
    assert "nineteen files that are not such a journal" in design
    assert ("that a look changes no creation time, no write time and no attribute of the journal, of setup's "
            "record and of the person's file") in design
    assert ("a look changes no creation time, no write time and no attribute of the journal, of setup's record "
            "and of the person's file") in native
    # Five literal files in the first line, then one file for each `record.Replace(...)`, then the file
    # that is one byte too large.
    assert others.split("\n", 1)[0] == '"","{","[]","null","\\"delete\\"",'
    assert 5 + others.count("record.Replace(") + others.count("record+new string(") == 19
    assert len(windows_uninstall.DATA) == 7 and "the seven names of the person's data" in design
    # The native check has the same three texts.
    cases = native.split('string left=Make(work,"Left");', 1)[1]
    assert literal(cases.split("Func<string,string> bare=folder=>", 1)[1].split(";", 1)[0], "folder") == BARE
    assert literal(cases.split("string emptied=", 1)[1].split(";", 1)[0].replace("left", "root")) == EMPTIED
    assert literal(cases.split("string whole=", 1)[1].split(";", 1)[0], "seen") == WHOLE
    for message in ("no journal and no folder app: the text that names no chats and no settings",
                    "no journal entry, and the folder app: the text that names chats and settings",
                    "the same with a file in the folder app: setup does not look into it"):
        assert f'"{message}");' in native, message


def test_the_check_that_runs_this_file_requires_every_case_to_run_and_pass(tmp_path):
    """The workflow step that runs this file on a pull request (follow-up A24).

    Before, no check ran this file: its workflow named it only as a path that starts the workflow. The step
    must fail when a case did not run or did not pass. pytest ends with status 0 also for a skipped case and
    for an expected failure, a case with that mark can be set not to run, and a case can end the run early
    with status 0. So the step reads pytest's own lines: exactly one line "collected N items", and a last
    line that names passed cases only, with the same N. This case runs pytest on small files and gives each
    real output to the step's two expressions, with the step's rule. It does not run the step: a hosted
    runner does that. The step does not see a case that pytest never collects (a function that lost its
    `test_` name, for example).
    """
    import os
    import subprocess
    import sys
    workflow = (ROOT / ".github/workflows/windows-private-shell.yml").read_text(encoding="utf-8")
    step = workflow.split(
        "      - name: Check setup's folder rules and texts against its source and the design\n", 1)[1].split(
        "\n      - name: ", 1)[0]
    order = [step.index(line) for line in (
        " -B -m pytest -ra --color=no -p no:cacheprovider tests/test_windows_setup_folder.py"
        " | Tee-Object -Variable log\n",
        "          if ($LASTEXITCODE -ne 0) { throw 'Setup folder and text checks failed.' }\n",
        '          $lines = @($log | ForEach-Object { "$_".Trim() } | Where-Object { $_ })\n',
        "          $summary = if ($lines.Count) { $lines[-1] } else { '' }\n",
        "          $listed = [regex]::Match((@($lines | Where-Object { $_.StartsWith('collected ') }) -join \"`n\"), '",
        "          $passed = [regex]::Match($summary, '",
        "          if (-not ($listed.Success -and $passed.Success -and "
        "$listed.Groups[1].Value -eq $passed.Groups[1].Value)) { throw \"")]
    assert order == sorted(order)
    assert "-k " not in step and "--deselect" not in step and "continue-on-error" not in step
    assert " -q" not in step  # With `-q` pytest does not write the line with the number of collected cases.
    listed = re.search(r"\$listed = \[regex\]::Match\(\(.+\), '([^']+)'\)\n", step).group(1)
    passed = re.search(r"\$passed = \[regex\]::Match\(\$summary, '([^']+)'\)\n", step).group(1)

    def accepts(output):
        """The step's rule for the output of a pytest run that ended with status 0."""
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        first = re.search(listed, "\n".join(line for line in lines if line.startswith("collected ")))
        last = re.search(passed, lines[-1] if lines else "")
        return bool(first and last and first.group(1) == last.group(1))
    # A change of a file that these cases read starts the workflow.
    paths = workflow.split("    paths:\n", 1)[1].split("permissions:", 1)[0]
    for name in (".github/workflows/windows-private-shell.yml", "scripts/windows_bootstrap_native.cs",
                 "scripts/windows_bootstrap_native.py", "scripts/windows_bootstrap_capsule.py",
                 "scripts/windows_private_git.py", "scripts/install.ps1", "amplifier_web/windows_uninstall.py",
                 "amplifier_web/windows_removal.py",
                 "amplifier_web/windows_consumer_shell.py", "tests/windows/native_setup.Tests.cs",
                 "tests/test_windows_setup_folder.py", "docs/design/native-windows-installation.md"):
        assert f"      - {name}\n" in paths, name
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    before = "import pytest\n\n\ndef test_first():\n    assert True\n\n\n"
    after = "\n\ndef test_last():\n    assert True\n"
    outcomes = {
        "passed": ("def test_other():\n    assert True\n", 0),
        "xfail_not_run": ("@pytest.mark.xfail(run=False)\ndef test_other():\n    assert False\n", 0),
        "xfail": ("@pytest.mark.xfail\ndef test_other():\n    assert False\n", 0),
        "xpass": ("@pytest.mark.xfail\ndef test_other():\n    assert True\n", 0),
        "skip": ("@pytest.mark.skip\ndef test_other():\n    assert False\n", 0),
        # The run ends here with status 0; `test_last` does not run.
        "exit": ("def test_other():\n    pytest.exit(\"stop\", returncode=0)\n", 0),
        "fail": ("def test_other():\n    assert False\n", 1)}
    # The step's options. Options that a person's environment adds to every pytest run are left out.
    environment = {name: value for name, value in os.environ.items() if name != "PYTEST_ADDOPTS"}
    for name, (other, status) in outcomes.items():
        (tmp_path / f"test_{name}.py").write_text(before + other + after, encoding="utf-8")
        run = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", "-ra", "--color=no", "-p", "no:cacheprovider", f"test_{name}.py"],
            cwd=tmp_path, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=120)
        # pytest's status alone does not tell a case that did not run or did not pass.
        assert run.returncode == status, (name, run.stdout, run.stderr)
        assert "\ncollected 3 items\n" in run.stdout, (name, run.stdout)
        assert accepts(run.stdout) == (name == "passed"), (name, run.stdout)
    for output in ("collected 12 items\n===== 12 passed in 0.32s =====",
                   "collected 1 item\n= 1 passed, 1 warning in 0.32s =",
                   "collected 1592 items\n== 1592 passed, 2 warnings in 64.17s (0:01:04) =="):
        assert accepts(output), output
    for output in ("", "collected 12 items", "== 12 passed in 0.10s ==",
                   # Fewer cases passed than pytest collected.
                   "collected 12 items\n== 11 passed in 0.10s ==",
                   "collected 12 items / 1 deselected / 11 selected\n== 11 passed, 1 deselected in 0.10s ==",
                   "collected 12 items / 1 deselected / 11 selected\n== 12 passed in 0.10s ==",
                   "collected 12 items\ncollected 12 items\n== 12 passed in 0.10s ==",
                   # A line after pytest's summary.
                   "collected 12 items\n== 12 passed in 0.10s ==\n!!!!! _pytest.outcomes.Exit: stop !!!!!",
                   "collected 12 items\n== 1 failed, 11 passed in 0.10s ==",
                   "collected 12 items\n== 12 passed, 1 warning, 1 error in 0.10s ==",
                   "collected 12 items\n== 11 passed, 1 skipped in 0.10s ==",
                   "collected 12 items\n== 11 passed, 1 xfailed in 0.10s ==",
                   "collected 12 items\n== 12 passed, 1 xpassed in 0.10s ==",
                   "collected 12 items\n== no tests ran in 0.01s ==",
                   # The last line holds more than pytest's summary.
                   "collected 12 items\nx == 12 passed in 0.10s ==", "collected 12 items\n== 12 passed in 0.10s == x",
                   # The form of `-q`, and other letter cases.
                   "collected 12 items\n12 passed in 0.32s",
                   "collected 12 items\n== 12 Passed in 0.10s ==", "Collected 12 items\n== 12 passed in 0.10s =="):
        assert not accepts(output), output


def test_second_setup_restores_entries_before_start_and_waits_without_pins():
    opened = between("static int OpenInstalled(", "static int ConsumerWindow()")
    # The question and the message after a failed entries step are texts of their own functions
    # (the cases of follow-up A55 below hold them, complete).
    assert ('if(Say(Offer(listed,signs=="kept"),MessageBoxButtons.YesNo,MessageBoxIcon.Information)'
            "!=DialogResult.Yes) return 0;") in opened
    # The entries come first, then the app. Both steps run off the window thread.
    order = [opened.index(text) for text in (
        "if(!listed) {", "listed=await Task.Run(()=>AddEntries(root));",
        "if(!listed && start) Say(Unadded(kept),MessageBoxButtons.OK,MessageBoxIcon.Warning);",
        "if(start) {", "app=await Task.Run(()=>StartApp(root));", "started=true;",
        "if(started && app!=null) { waiting=true; return; }",
        'if(start && !started) Say("Setup could not start Amplifier.')]
    assert order == sorted(order)
    # "Listed" needs the entry record AND the Start-menu entry itself: a removal
    # whose last record write was refused leaves the record with no entry.
    assert ('listed=receipt!=null && Text(receipt,"status")=="installed" &&\n'
            '                    File.Exists(Path.Combine(Text(receipt,"folder"),"Amplifier Unified.lnk"));') in opened
    # A failed entries step does not keep the person from the app, and its
    # message never sends the person to a Start-menu entry that is not there.
    failure = between("internal static string Unadded(", "internal static string Removing(")
    assert "To open it later, run this setup file again." in failure
    assert "Open it from the Start menu" not in failure
    # That message does not end the run: nothing returns between it and the start.
    assert "return" not in between("Say(Unadded(kept)", "app=await Task.Run(()=>StartApp(root));", opened)
    unstarted = between('if(start && !started) Say(', "MessageBoxIcon.Warning", opened)
    assert unstarted.index("(listed") < unstarted.index("Open it from the Start menu: Amplifier Unified.") \
        < unstarted.index("Restart Windows. Then run this setup file again.")
    # The person sees a window with the stage for the whole time.
    assert "new Form" in opened and 'Text="Amplifier Unified Setup"' in opened
    assert "ProgressBarStyle.Marquee" in opened and "new System.Windows.Forms.Timer" in opened
    assert 'Stage="Starting Amplifier (this can take a few minutes)";' in opened
    # The window stays while setup adds the entries and starts the launcher.
    # Once the launcher runs, the person can close it: the launcher does not
    # need setup, and the window says so.
    assert "if(!settled && !waiting)e.Cancel=true;" in opened
    assert ('label.Text=Stage+"...\\n\\n"+(waiting?"You can close this window. Amplifier goes on.":'
            '"Keep this window open.");') in opened
    # The result is 0 only when the entries exist and setup saw the launcher
    # finish. A window that the person closed earlier leaves it at 1.
    assert "int result=1;bool settled=false,waiting=false;Process app=null;" in opened
    assert opened.index("if(waiting && !settled && app.HasExited) {") < opened.index(
        "result=app.ExitCode==0 && listed?0:1;")
    # Setup waits for the launcher's end with its timer, not with a pending
    # wait: nothing is left to continue after the window is closed.
    assert opened.count("await ") == 2 and "WaitForExit" not in opened

    entries = between("static bool AddEntries(", "static Process StartApp(")
    assert 'Path.Combine(root,"tools","amplifier-unified","Scripts","python.exe")' in entries
    assert '"-I -B -m amplifier_web.windows_consumer --root "+Quote(root)+" --integrate"' in entries
    # No console window, and the program's output is read: with a valid output
    # the program reports a failure by its exit code and shows no message itself.
    assert "UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true," in entries
    assert entries.index("entries.StandardOutput.ReadToEnd();") < entries.index("entries.WaitForExit();") \
        < entries.index("return entries.ExitCode==0;")
    assert "catch(Exception) { return false; }" in entries
    started = between("static Process StartApp(", "static int OpenInstalled(")
    # Each pin covers one statement, the start. Setup does not hold the folder
    # and the folders above it while a program runs.
    assert "using(PinInstalled(root,python)) entries=Process.Start(integrate);" in entries
    assert "using(PinInstalled(root,start.FileName)) return Process.Start(start);" in started
    for body in (entries, started, opened):
        assert not re.search(r"using\(PinInstalled\([^\n]*\)\s*\{", body)
    # The question and the work are separate, so that the native check can run
    # the work (the window and both steps) with a stand-in program.
    assert opened.index('return SecondRun(root,listed,true,signs=="kept");') < opened.index(
        "internal static int SecondRun(string root, bool listed, bool start, bool kept) {") < opened.index(
        "using(var form=new Form")
    harness = (ROOT / "tests/windows/native_setup.Tests.cs").read_text(encoding="utf-8")
    for call in ("NativeInstaller.AddEntries(second)", "NativeInstaller.StartApp(empty)",
                 "NativeInstaller.SecondRun(root,listed,start,false)", "Window(second,true,true)",
                 "Window(second,false,true)", "Window(second,false,false)", "ClosedEarly(second)"):
        assert call in harness
    assert "runtime status" in DESIGN and "clean-room run" in DESIGN


def test_a_second_setup_run_names_chats_and_settings_only_where_its_two_looks_say_so():
    """What a second setup run says about the person's chats and settings (follow-up A55).

    Read from the source on November 18, 2026, not seen on a PC: after an uninstall with the delete choice
    that stopped, a second setup run said "Keep that folder. It holds your chats and settings." or "Your
    chats and settings are kept." They could be deleted, and setup did not look. Setup now makes two looks
    (`Signs`), each one step on one path: is an entry with the journal's name there, and is the folder
    `app` there. A text names the person's chats and settings only when no entry has the journal's name
    and that folder is there, which is the launcher's rule for its own sentence. Every doubt leaves the
    sentence out. The states of this case: `kept` and `other`, each with and without a Start-menu entry,
    and each after a failed entries step. The native check runs the looks on real folders.
    """
    import inspect
    from amplifier_web import windows_consumer, windows_uninstall
    signs = between("internal static string Signs(", "internal static string Unopened(")
    code = code_of(signs)
    # The whole function: two looks at attributes, and nothing else.
    assert [line.strip() for line in code.splitlines()[1:] if line.strip()] == [
        'try { File.GetAttributes(Path.Combine(root,Journal)); return "removal"; }',
        # Only Windows' answer that no file has that name goes on to the second look.
        "catch(FileNotFoundException) {}",
        'catch(Exception) { return "other"; }',
        "try {",
        # A folder that is no link. Setup does not look into it.
        'return (File.GetAttributes(Path.Combine(root,"app"))&(FileAttributes.Directory|FileAttributes.ReparsePoint))==',
        'FileAttributes.Directory?"kept":"other";',
        '} catch(Exception) { return "other"; }',
        "}"]
    assert names_before_a_bracket(signs) == {"File.GetAttributes": 2, "Path.Combine": 2, "catch": 3, "return": 1}
    assert "=" not in re.sub(r'"(?:[^"\\]|\\.)*"', '""', code).replace("==", "") and "new" not in re.findall(
        r"[A-Za-z_]+", code)
    assert "app" in windows_uninstall.DATA
    # The launcher's rule for its sentence "It keeps your chats and settings": no entry with the journal's
    # name, and the folder `app`. (It also reads setup's record; setup has that record's name already.)
    rule = inspect.getsource(windows_consumer._setup_again)
    assert ("kept = (not windows_uninstall.unfinished(root) and windows_uninstall.completed(root)\n"
            '            and os.path.isdir(os.path.join(root, "app")))') in rule
    assert "return os.path.lexists(os.path.join(root, JOURNAL))" in inspect.getsource(windows_uninstall.unfinished)
    # One use of the looks in a run with the launcher program, before the first message of that run.
    opened = between("static int OpenInstalled(", "internal static int SecondRun(")
    order = [opened.index(text) for text in (
        "if(!File.Exists(AppStart(root).FileName)) {", "bool listed=false;", "string signs=Signs(root);",
        'if(signs=="removal") {',
        'if(Say(Offer(listed,signs=="kept"),MessageBoxButtons.YesNo,MessageBoxIcon.Information)!=DialogResult.Yes) return 0;',
        'return SecondRun(root,listed,true,signs=="kept");')]
    assert order == sorted(order)
    # The first message of that function is for a folder with no launcher program (`Unopened`). Every other
    # message comes after the looks.
    says = [match.start() for match in re.finditer(r"Say\(", opened)]
    assert len(says) == 5 and code_of(opened).count("Signs(") == 1
    assert says[0] < opened.index("bool listed=false;") < opened.index("string signs=Signs(root);") < says[1]
    # The question: with a Start-menu entry, and without one. The sentence only with `kept`.
    ending = '+(kept?" Your chats and settings are kept.":"")'
    conditions, texts = returns(between("internal static string Offer(", "internal static string Unadded("))
    assert conditions == ["if(listed) ", ""] and literal(texts[0]) == OPEN
    assert texts[1].endswith(ending) and literal(texts[1][:-len(ending)]) == ADD
    # The message after a failed entries step, before the start.
    conditions, texts = returns(between("internal static string Unadded(", "internal static string Removing("))
    assert conditions == [""] and texts[0].endswith(ending) and literal(texts[0][:-len(ending)]) == UNADDED
    second = code_of(between("internal static int SecondRun(", "static int ConsumerWindow()"))
    assert second.count("kept") == 2 and "Say(Unadded(kept)," in second
    # No other text of setup has that sentence, and none says that the folder holds chats and settings.
    assert SOURCE.count("Your chats and settings are kept.") == 2 and SOURCE.count(ending) == 2
    assert "It holds your chats and settings" not in SOURCE
    assert KEPT == " Your chats and settings are kept."
    # What setup found: it read the entry record and looked for the entry. A second setup run does not
    # read the start-at-sign-in task, so the text does not say that Amplifier does not start at sign-in.
    assert "setup did not find it in the Start menu" in ADD and "sign in" not in ADD
    assert 'string task=state=="installed"?"absent":SignInTask(out program);' in SOURCE
    # What setup does: it starts the launcher. Whether Amplifier then starts is the launcher's.
    assert "Setup starts Amplifier now." in UNADDED and "Amplifier starts now" not in SOURCE
    for text in (OPEN, ADD, UNADDED):
        assert "chats" not in text and "delete" not in text.lower()
    design = " ".join(DESIGN.split())
    for text in (OPEN, ADD, UNADDED, KEPT.strip()):
        in_design(text)
    for sentence in (
            "Each is one step on one path, and setup holds nothing between them.",
            "First it looks at the attributes of the entry with the journal's name.",
            "It does not open that entry, and it does not follow a link.",
            "Any entry there answers `removal`: a file (also an empty one, and one that another program holds "
            "for itself), a folder, a link.",
            "Only when Windows answers that no file has that name does setup make the second look, at the "
            "attributes of the entry `app`: a folder that is no link answers `kept`.",
            "Setup does not look into that folder.",
            "Every other answer of Windows to either look, and every other entry `app`, answers `other`.",
            "Then it makes the two looks of `Signs` once, before its first message of the run.",
            "Only with `kept` the last two texts end with \"Your chats and settings are kept.\"",
            "The sentence says what setup does: it deletes nothing.",
            "Setup says it only where no entry has the journal's name and the folder `app` is there.",
            "The launcher follows a link with the name `app` (`os.path.isdir`); setup does not count a link.",
            "When Windows gives the launcher's look for the journal's name no answer, that look counts as no "
            "journal (`os.path.lexists` in `windows_uninstall.unfinished`), so the launcher can say its "
            "sentence; setup then says the third text.",
            "Where Windows ignores the case of letters, a folder `App` answers the look for `app`.",
            "a second setup run does not read the start-at-sign-in task.",
            "With `other` because Windows gave the look for the journal's name no answer, setup starts the "
            "launcher as before.",
            "Setup looks once, before its first message: an uninstall that begins after the look is not seen, "
            "setup starts the launcher, and the launcher refuses with its own text."):
        assert sentence in design, sentence
    # The native check: the looks on real folders, and the same texts.
    native = (ROOT / "tests/windows/native_setup.Tests.cs").read_text(encoding="utf-8")
    cases = native.split('string seen=Make(work,"Seen");', 1)[1]
    assert literal(cases.split("string offered=", 1)[1].split(";", 1)[0]) == ADD
    assert literal(cases.split("string opening=", 1)[1].split(";", 1)[0]) == OPEN
    assert literal(cases.split("string unadded=", 1)[1].split(";", 1)[0]) == UNADDED
    assert cases.count('+" Your chats and settings are kept."') == 2
    for message in (
            "no journal entry and no folder app", "an empty file with the journal's name",
            "the look changes no time and no attribute of the entry with the journal's name",
            "a file with the journal's name that is no record",
            "a file with the journal's name that another program holds for itself",
            "Windows refuses the look at the entry with the journal's name",
            "a look that Windows refuses, with the folder app", "Windows answers the look again",
            "a folder with the journal's name", "a file link with the journal's name",
            "a link with the journal's name whose file is gone", "a folder link with the journal's name",
            "without an entry of the journal's name", "a file with the name app", "a link with the name app",
            "a folder under another spelling of the name app", "a folder that is not there",
            "the question without a Start-menu entry names chats and settings only when the looks say so",
            "the question with a Start-menu entry",
            "the message after a failed entries step names chats and settings only when the looks say so"):
        assert f'"{message}");' in native, message


def test_a_second_setup_run_starts_no_launcher_on_a_folder_with_an_uninstall_record():
    """Setup's texts and its start while an entry with the journal's name is in the folder (follow-up A55).

    Read from the source on November 18, 2026, not seen on a PC: the launcher does not open Amplifier while
    any entry with the journal's name is in the folder. Setup said in that state "Select Yes to open it
    now.", "... and start it now." and "Amplifier starts now.", started the launcher, and the launcher
    refused. Setup now starts no launcher there. It says that it found an uninstall record and gives the
    launcher's own step. Without a Start-menu entry it asks whether it adds the entries again: after a
    removal that stopped, the entries are what lets the person uninstall again. The states of this case:
    with a Start-menu entry; without one, after No; after Yes with the entries added; after Yes with the
    entries not added, with and without the journal of a delete and no data. The native check runs the
    entries step without a start.

    The last state: an uninstall with the delete choice deletes the records that the entries step needs
    before its last message. When that message is open, or the PC was switched off while it was open,
    setup can add no entry and installs nothing in the folder, and "Restart Windows. Then run this setup
    file again." brings the same message. With the journal of a delete and none of the person's data
    (`Emptied`, the look of follow-up A24, unchanged) setup gives the step of that follow-up's text: move
    or rename the folder. It names the other Amplifier message first, and it never says "delete".
    """
    import inspect
    from amplifier_web import windows_consumer, windows_uninstall
    # The launcher's rule, and its own step.
    assert ('        if windows_uninstall.unfinished(root):\n'
            '            raise Refused("removing")') in inspect.getsource(windows_consumer._start)
    step = "select Uninstall Amplifier in the Start menu or in the Windows Apps list."
    assert step in windows_consumer._UNFINISHED["removing"] and HALTED.endswith("To finish that uninstall, " + step)
    # What setup does in that state: every line.
    opened = between("static int OpenInstalled(", "internal static int SecondRun(")
    state = between('if(signs=="removal") {', "if(Say(Offer(", opened)
    assert [line.strip() for line in state.splitlines() if line.strip()] == [
        # With a Start-menu entry: one message, no question, no start.
        "if(listed) { Say(Removing(root,true),MessageBoxButtons.OK,MessageBoxIcon.Warning); return 1; }",
        # Without one: the question. No changes nothing.
        "if(Say(Removing(root,false),MessageBoxButtons.YesNo,MessageBoxIcon.Warning)!=DialogResult.Yes) return 0;",
        # Yes: the entries step only, then one message.
        "bool added=SecondRun(root,false,false,false)==0;",
        "Say(Unstarted(root,added),MessageBoxButtons.OK,added?MessageBoxIcon.Information:MessageBoxIcon.Warning);",
        "return 1;", "}"]
    # The window's work without a start: no message, no launcher, and 0 when the entries exist.
    second = code_of(between("internal static int SecondRun(", "static int ConsumerWindow()"))
    assert second.count("Say(") == 2 and second.count("if(!listed && start) Say(") == 1
    assert second.count("if(start && !started) Say(") == 1
    assert ("                        if(start) {\n"
            '                            Stage="Starting Amplifier (this can take a few minutes)";\n'
            "                            app=await Task.Run(()=>StartApp(root));\n"
            "                            started=true;\n"
            "                        }\n") in second
    assert second.count("StartApp(") == 1 and SOURCE.count("StartApp(") == 2 and SOURCE.count("AppStart(root)") == 3
    assert "else result=listed?0:1;" in second
    # The texts, complete.
    conditions, texts = returns(between("internal static string Removing(", "internal static string Unstarted("))
    assert conditions == ["if(listed) ", ""] and [literal(text) for text in texts] == [HALTED, ASKED]
    # After the entries step: the text that names the Start menu only when the entries were added.
    conditions, texts = returns(between("internal static string Unstarted(", "static int OpenInstalled("))
    assert conditions == ["if(added) ", "if(Emptied(root)) ", ""]
    assert [literal(text) for text in texts] == [ADDED, MOVED, RESTART]
    # The text with the step to move the folder: what setup did, what its look found (the words of the
    # text for a folder with no launcher program), the launcher's own words for a message that is open
    # in another window, then the step. It never says "delete".
    assert MOVED.split("\n\n")[0] == RESTART.split("\n\n")[0]
    assert "\n\nSetup found no chats or settings in the folder.\n\n" in MOVED
    assert "Setup found no chats or settings in the folder. " in EMPTIED
    open_message = "If another Amplifier message is open, answer it first."
    assert open_message in windows_consumer._UNFINISHED["busy"]
    assert MOVED.endswith("\n\n" + open_message + " If the folder is still there afterwards, move or rename it. "
                          "Then run this setup file again.")
    assert "Restart Windows" not in MOVED and "delete" not in MOVED.lower() and "your chats" not in MOVED
    # An uninstall deletes the records that the entries step reads before its last message.
    removal = inspect.getsource(windows_uninstall.remove)
    assert removal.index("remove_program(owned, last=True, wait=wait, short=short)") < removal.index("return {")
    assert "consumer-install.json" in windows_uninstall.LAST and windows_consumer.RECEIPT == "consumer-install.json"
    assert "value = json.loads(owned.read_private(RECEIPT, limit=256 * 1024))" in inspect.getsource(
        windows_consumer.installation)
    assert "root, value = installation(root)" in inspect.getsource(windows_consumer.integrate)
    for text in (HALTED, ASKED, ADDED, RESTART, MOVED):
        # What setup found, not a history: a file with that name is no proof of an uninstall.
        assert "Setup found an uninstall record in this folder:\n{root}" in text or (
            "because it found an uninstall record in this folder:\n{root}" in text)
        assert "did not finish" not in text and "is being removed" not in text and "already installed" not in text
        # No start that does not happen, and nothing about the person's chats and settings.
        for words in ("open it now", "start it now", "starts now", "starts Amplifier", "your chats", "kept"):
            assert words not in text, (words, text)
        assert ("chats" in text) == (text == MOVED) and "delete" not in text.lower()
    assert "Setup did not start it." in HALTED and "Setup does not start it." in ASKED
    for text in (ADDED, RESTART, MOVED):
        assert "Setup did not start Amplifier, because it found an uninstall record" in text
    # The step of each text: the launcher's own, where an entry is there or was added.
    assert ASKED.endswith("Select Yes to add Amplifier to the Start menu again, "
                          "so that you can finish that uninstall from there.")
    assert ADDED.endswith("\n\nTo finish that uninstall, select Uninstall Amplifier in the Start menu.")
    assert RESTART.endswith("\n\nRestart Windows. Then run this setup file again.")
    design = " ".join(DESIGN.split())
    for text in (HALTED, ASKED, ADDED, RESTART, MOVED):
        in_design(text)
    for sentence in (
            "The entries were not added, and the look of the first text above (`Emptied`, unchanged) finds the "
            "journal of a delete and none of the person's data:",
            "The entries were not added, in every other case: the same two sentences and the folder, then "
            "\"Restart Windows. Then run this setup file again.\"",
            "The text with the step to move the folder is for a removal that nothing in the product can finish.",
            "An uninstall with the delete choice deletes the records that the entries program needs before it "
            "shows its last message, so no entry can be added, and setup installs nothing in a folder that has "
            "its completion record.",
            "Setup makes that look after the entries step, directly before the message; it is a separate step, "
            "and the look only chooses the text.",
            "\"If another Amplifier message is open, answer it first\" are the launcher's own words.",
            "While the last message of that uninstall is open, the uninstall program still runs from files in "
            "the folder, and Windows does not rename a folder that holds a file which a program keeps open (the "
            "header of `amplifier_web/windows_removal.py`).",
            "When the PC was switched off while the message was open, no message is open afterwards, the last "
            "step never ran, and the folder stays (L3).",
            "The text does not tell the person to delete the folder, for the reason of the first text above.",
            "The folder that the person moved or renamed still holds Amplifier's program files (`python`, "
            "`tools`) and both records.",
            "Nothing removes them.",
            "When that program is still there and the entries cannot be added again, setup's last message gives "
            "the same step.",
            "A folder that the person moved or renamed on that step still holds Amplifier's program files and "
            "both records.",
            "Not proven: that Windows refuses to rename the folder while the last message of an uninstall is "
            "open;",
            "An entry with the journal's name (`removal`): the launcher does not open Amplifier from such a "
            "folder (`Refused(\"removing\")` in `_start` of `amplifier_web/windows_consumer.py`, which looks "
            "with `windows_uninstall.unfinished`).",
            "So setup does not start it.",
            "No text of setup then says that Amplifier opens or starts, and none says that chats or settings "
            "are kept.",
            "Setup says that it found \"an uninstall record\", not that an uninstall began: a file with that "
            "name is no proof of an uninstall (a program of this account can write one), and setup does not "
            "read it for these texts.",
            "Listed: one message, and setup ends with status 1.",
            "The step is the launcher's own (its text `removing`).",
            "On No setup changes nothing and ends with status 0.",
            "On Yes setup's window runs the entries step only (`SecondRun` with `start` false), then setup "
            "shows one of the texts below and ends with status 1.",
            "The entries program adds the entries only when the installation's own records are whole and no "
            "entry exists, and it does not look for the journal.",
            "a second setup run that finds an entry with the journal's name starts no launcher.",
            "With such an entry setup starts no launcher",
            "While an entry with the journal's name is in the folder, no message of setup says that the "
            "person's chats and settings are in the folder or are kept, or that Amplifier opens.",
            "that `SecondRun` without a start runs the entries program and no launcher, shows no message, and "
            "gives 0 when the entries program ended with 0 and 1 when it failed; that the last text gives the "
            "step to move the folder with the journal of a delete and no data, and the restart with no such "
            "journal and with data."):
        assert sentence in design, sentence
    # The native check: the same texts, and the window's work without a start.
    native = (ROOT / "tests/windows/native_setup.Tests.cs").read_text(encoding="utf-8")
    cases = native.split('string seen=Make(work,"Seen");', 1)[1]
    for name, text in (("halted", HALTED), ("asked", ASKED), ("added", ADDED), ("restart", RESTART),
                       ("moved", MOVED)):
        assert literal(cases.split(f"string {name}=", 1)[1].split(";", 1)[0], "seen") == text, name
    for message in (
            "the two first texts of a folder with an uninstall record",
            "after the entries step, with no journal of a delete",
            "after the entries step, with the journal of a delete and no data",
            "after the entries step, with the journal of a delete and data"):
        assert f'"{message}");' in native, message
    assert 'if(ran!=null) File.AppendAllText(ran,integrate?"entries,":"start,");' in native
    assert ("var thread=new Thread(()=>{ result=NativeInstaller.SecondRun(root,listed,start,false); });"
            in native.split("static int Window(string root, bool listed, bool start) {", 1)[1].split("static int ", 1)[0])
    # Each run of the window with the stand-in's record of the commands that ran, in the case's order:
    # with a start, without a start, and without a start when the entries program fails.
    runs = native.split('Environment.SetEnvironmentVariable("AMPLIFIER_SETUP_TEST_RAN",ran);\n', 1)[1].split(
        'Environment.SetEnvironmentVariable("AMPLIFIER_SETUP_TEST_RAN",null);', 1)[0]
    lines = [line.strip() for line in runs.splitlines() if line.strip() and not line.strip().startswith("//")]
    begins = [
        "shown=Window(second,false,true);",
        'Need(shown==0 && File.ReadAllText(ran)=="entries,start,","second run without entries: added, started, closed',
        "File.Delete(ran);",
        "shown=Window(second,false,false);",
        'Need(shown==0 && File.ReadAllText(ran)=="entries,","second run without a start: the entries are added and '
        "no launcher starts",
        'Environment.SetEnvironmentVariable("AMPLIFIER_SETUP_TEST_EXIT","1");', "File.Delete(ran);",
        "shown=Window(second,false,false);",
        'Need(shown==1 && File.ReadAllText(ran)=="entries,","second run without a start: a failed entries step is '
        "the result, with no message and no launcher"]
    assert len(lines) == len(begins) and all(line.startswith(begin) for line, begin in zip(lines, begins)), lines


def test_sign_in_task_states_do_not_guess():
    task = between("internal static string SignInTask(", "internal static string Precheck(")
    # Only "not found" is absence. A Scheduler that cannot be read stops setup.
    assert 'if(inner.HResult==unchecked((int)0x80070002)) return "absent";' in task
    assert task.count('return "unavailable";') == 2
    assert 'return "leftover";' in task and "File.Exists((string)action.Path)" in task
    assert "DeleteTask" not in SOURCE and "RegisterTask" not in SOURCE  # setup changes no task


def test_stop_text_is_plain_and_never_the_old_generic_sentence():
    for retired in ("No automatic retry was attempted", "No installation root was created",
                    "Partial files and private diagnostics remain"):
        assert retired not in between("static void ShowStop(", "static void Install(")
    shown = between("static void ShowStop(", "static ProcessStartInfo AppStart(")
    assert "What happened:" in shown and "What to do:" in shown
    explain = between("internal static string[] Explain(", "static string SaveDiagnostics(")
    # Private exception text, child output and network values are never shown.
    assert "error.Message+" not in explain and "ToString()" not in explain
    stages = re.findall(r'Stage="([^"]+)"', between("static string InstallConsumer(", "static DialogResult Say("))
    assert stages == [
        "Removing an unfinished earlier setup", "Preparing the Amplifier folder", "Downloading setup tools",
        "Installing Python", "Installing Git and shell tools (this takes a few minutes)", "Downloading Amplifier",
        "Installing Amplifier (this takes a few minutes)", "Checking the installation",
        "Adding Amplifier to the Start menu", "Removing the unfinished files", "Starting Amplifier"]


def test_downloads_retry_in_the_same_private_file_and_refusals_do_not():
    copy = between("static void CopyOrDownload(", "static void CopyBounded(")
    assert "attempt==3 || !(error is WebException || error is IOException)" in copy
    assert copy.index("output.SetLength(0)") < copy.index("System.Threading.Thread.Sleep")
    assert copy.count("Storage.CreateFile(temporary)") == 1  # never a second, adopted file
