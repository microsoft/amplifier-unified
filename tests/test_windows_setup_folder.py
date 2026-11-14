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
    for folder in ("app", "shared", "ownership", "capture", "import", "workspace", "source-store"):
        assert '"' + folder + '"' in marked


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
    assert "using(PinInstalled(root,start.FileName)) Process.Start(start);" in opened
    pin = between("internal static IDisposable PinInstalled(", "internal static string SignInTask(")
    assert pin.index('Need(Marked(root)=="installed"') < pin.index("storage.TrustExecutable(program)")
    saved = between("internal static string SaveDiagnostics(", "static string Shortcut(")
    # A new private folder, created exclusively; never a predictable or adopted one.
    assert "new InstallStorage(parent,name)" in saved and "storage.CreateFile(" in saved
    assert "Directory.CreateDirectory" not in saved and "File.Copy" not in saved


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
