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


EMPTIED = ("Setup cannot install Amplifier yet. The folder of an earlier installation is still on this PC:\n{root}\n\n"
           "Amplifier cannot start from that folder, and setup found no chats or settings in it. "
           "Setup does not change the folder: it can hold files that are not Amplifier's.\n\n"
           "If a file in the folder is yours, move it to another place. Then delete the folder and run this setup "
           "file again.")
DAMAGED = ("Amplifier is already installed in {root}, but setup cannot open it: a program file is missing.\n\n"
           "Keep that folder. It holds your chats and settings. This version of setup cannot repair it.")


def literal(body):
    """The text that a C# expression of string literals and `root` gives, with `{root}` for the folder."""
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"|\b(root)\b', body)
    return "".join("{root}" if name else text.replace("\\n", "\n").replace('\\"', '"') for text, name in parts)


def test_setup_without_a_launcher_program_says_what_its_own_look_finds():
    """Setup's text for a finished installation whose launcher program is missing (follow-up A24).

    Seen on a PC on November 17, 2026: an uninstall with the delete choice left the folder, because a file of
    the person was in it. The next setup run said "Keep that folder. It holds your chats and settings." The
    chats and settings were deleted, and the text named no next step. Setup now looks: with a journal that
    records the delete choice, and with none of the person's data, it says what it found and gives the next
    step. Every other case keeps the earlier text. Setup still installs nothing there and deletes nothing.
    The native check compiles these functions and runs them on real folders (tests/windows/native_setup.Tests.cs).
    """
    import inspect
    from amplifier_web import windows_uninstall
    # Uninstall owns the names. Setup has a copy, and only looks.
    assert re.search(r'internal const string Journal="([^"]+)";', SOURCE).group(1) == windows_uninstall.JOURNAL
    names = re.search(r"internal static readonly string\[\] Data=\{([^}]+)\};", SOURCE).group(1)
    assert tuple(name.strip().strip('"') for name in names.split(",")) == windows_uninstall.DATA
    # The values that uninstall's own reader requires. A change there must come here too.
    reader = inspect.getsource(windows_uninstall.pending)
    assert 'value["schema"] == 1' in reader and 're.fullmatch("[0-9a-f]{32}", value["token"])' in reader
    assert "owned.read_private(name, limit=4096)" in inspect.getsource(windows_uninstall._private_json)
    emptied = between("internal static bool Emptied(", "internal static string Unopened(")
    order = [emptied.index(text) for text in (
        "string journal=Path.Combine(root,Journal);",
        "if(new FileInfo(journal).Length>4096) return false;",
        "Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(journal));",
        'if(record==null || !record.TryGetValue("schema",out schema) || !(schema is int) || (int)schema!=1) return false;',
        r'if(Text(record,"choice")!="delete" || !Regex.IsMatch(token,"\\A[0-9a-f]{32}\\z")) return false;',
        "return !Data.Any(name=>{",
        "return File.Exists(entry) || (Directory.Exists(entry) && Directory.EnumerateFileSystemEntries(entry).Any());});",
        "} catch(Exception) { return false; }")]
    assert order == sorted(order)
    assert emptied.count("return") == 6  # Four refusals, the answer, and the look at one data name.
    # The two texts, complete. The one with the next step only after the look said yes.
    unopened = between("internal static string Unopened(", "static int OpenInstalled(")
    code = "\n".join(line for line in unopened.splitlines() if not line.strip().startswith("//"))
    first, second = code.split("if(Emptied(root)) return", 1)[1].split(";\n            return ", 1)
    assert literal(first) == EMPTIED and literal(second.split(";", 1)[0]) == DAMAGED
    assert code.count("return") == 2 and code.count("Emptied(") == 1
    # They only look and build a text.
    for body in (emptied, code):
        for change in ("Delete", "Write", "Create", "Move", "Remove", "Process", "Say(", "Install", "Storage"):
            assert change not in body, change
    # The one place that shows the text: a finished installation with no launcher program. Setup ends with 1.
    opened = between("static int OpenInstalled(", "static int ConsumerWindow()")
    assert opened.index("if(!File.Exists(AppStart(root).FileName)) {\n"
                        "                Say(Unopened(root),MessageBoxButtons.OK,MessageBoxIcon.Warning);\n"
                        "                return 1;\n            }") < opened.index("bool listed=false;")
    assert SOURCE.count("Unopened(") == 2 and SOURCE.count("Emptied(") == 2
    # What the new text does not say.
    assert "your chats and settings" not in EMPTIED and "already installed" not in EMPTIED
    assert "uninstall" not in EMPTIED.lower()  # A file that reads as a journal is no proof of an uninstall.
    # The launcher's program, which the Start-menu entry also starts.
    from amplifier_web import windows_consumer_shell
    assert 'Path.Combine(root,"tools","amplifier-unified","Scripts","pythonw.exe")' in between(
        "static ProcessStartInfo AppStart(", "internal static bool AddEntries(")
    assert '"tools/amplifier-unified/Scripts/pythonw.exe"' in inspect.getsource(windows_consumer_shell.entries)
    # The native check has the same two texts.
    native = (ROOT / "tests/windows/native_setup.Tests.cs").read_text(encoding="utf-8")
    cases = native.split('string left=Make(work,"Left");', 1)[1]
    assert literal(cases.split("string damaged=", 1)[1].split(";", 1)[0].replace("left", "root")) == DAMAGED
    assert literal(cases.split("string emptied=", 1)[1].split(";", 1)[0].replace("left", "root")) == EMPTIED


def test_second_setup_restores_entries_before_start_and_waits_without_pins():
    opened = between("static int OpenInstalled(", "static int ConsumerWindow()")
    assert ("Amplifier is already installed on this PC, but it is not in the Start menu "
            "and does not start when you sign in.\\n\\n") in opened
    assert ("Select Yes to add it to the Start menu again and start it now. "
            "Your chats and settings are kept.") in opened
    assert "Select Yes to open it now." in opened
    # The entries come first, then the app. Both steps run off the window thread.
    order = [opened.index(text) for text in (
        "if(!listed) {", "listed=await Task.Run(()=>AddEntries(root));",
        'Say("Setup could not add Amplifier to the Start menu. Amplifier starts now.',
        "app=await Task.Run(()=>StartApp(root));", "started=true;",
        "if(started && app!=null) { waiting=true; return; }", 'if(!started) Say("Setup could not start Amplifier.')]
    assert order == sorted(order)
    # "Listed" needs the entry record AND the Start-menu entry itself: a removal
    # whose last record write was refused leaves the record with no entry.
    assert ('listed=receipt!=null && Text(receipt,"status")=="installed" &&\n'
            '                    File.Exists(Path.Combine(Text(receipt,"folder"),"Amplifier Unified.lnk"));') in opened
    # A failed entries step does not keep the person from the app, and its
    # message never sends the person to a Start-menu entry that is not there.
    failure = between('Say("Setup could not add Amplifier', "MessageBoxIcon.Warning", opened)
    assert "To open it later, run this setup file again. Your chats and settings are kept." in failure
    assert "Open it from the Start menu" not in failure and "return" not in failure
    unstarted = between('if(!started) Say(', "MessageBoxIcon.Warning", opened)
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
    assert opened.index("return SecondRun(root,listed);") < opened.index(
        "internal static int SecondRun(string root, bool listed) {") < opened.index("using(var form=new Form")
    harness = (ROOT / "tests/windows/native_setup.Tests.cs").read_text(encoding="utf-8")
    for call in ("NativeInstaller.AddEntries(second)", "NativeInstaller.StartApp(empty)",
                 "NativeInstaller.SecondRun(root,listed)", "Window(second,true)", "Window(second,false)",
                 "ClosedEarly(second)"):
        assert call in harness
    assert "runtime status" in DESIGN and "clean-room run" in DESIGN


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
