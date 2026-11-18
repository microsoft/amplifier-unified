// Compile beside the actual consumer setup sources with this explicit entry.
// Native checks of the installation folder, its ownership checks, removal and
// plain stop messages. Everything stays inside two new folders below the
// caller's own temporary folder, and both are removed. No download, task
// change, registry, trust or policy effect. The one window is the second
// setup run's progress window, with this program in place of the
// installation's Python; it closes by itself and no message is shown.
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Threading;

namespace UnifiedBootstrap {
    internal static class NativeSetupTests {
        static int assertions;
        static void Need(bool actual, string what) {
            assertions++;
            if(!actual) throw new Exception("Native setup assertion failed: "+what);
        }
        static bool Refused(Action action) {
            try { action(); return false; } catch(Exception) { return true; }
        }
        static string Long(string path) { return "\\\\?\\"+path; }
        // A folder as setup creates it: this person is the owner, no other access.
        static string Make(string parent, string name) {
            using(var storage=new InstallStorage(parent,name)) return storage.Root;
        }
        static void Junction(string link, string target) {
            var process=Process.Start(new ProcessStartInfo("cmd.exe","/d /c mklink /J \""+link+"\" \""+target+"\"") {
                UseShellExecute=false,CreateNoWindow=true});
            process.WaitForExit();
            Need(process.ExitCode==0,"junction created");
        }
        static void FileLink(string link, string target) {
            // A symbolic link to a file. Windows creates one for an elevated
            // account (a hosted runner's) or with Developer Mode on.
            var process=Process.Start(new ProcessStartInfo("cmd.exe","/d /c mklink \""+link+"\" \""+target+"\"") {
                UseShellExecute=false,CreateNoWindow=true});
            process.WaitForExit();
            Need(process.ExitCode==0,"file link created (this needs an elevated account or Developer Mode)");
        }
        static void OpenToOthers(string folder) {
            var security=Directory.GetAccessControl(folder);
            security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid,null),
                FileSystemRights.Modify,AccessControlType.Allow));
            Directory.SetAccessControl(folder,security);
        }
        static void Clean(string folder) {
            foreach(string entry in Directory.GetFileSystemEntries(folder)) {
                var attributes=File.GetAttributes(entry);
                if((attributes&FileAttributes.Directory)==0) { File.SetAttributes(entry,FileAttributes.Normal); File.Delete(entry); }
                else if((attributes&FileAttributes.ReparsePoint)!=0) Directory.Delete(entry,false);
                else Clean(entry);
            }
            Directory.Delete(folder,false);
        }
        static int StandIn(string[] args) {
            // This test program in place of the installation's Python, for the
            // second setup run. It accepts only the exact arguments that setup
            // gives, and ends with the exit code that the test asked for.
            string root=Environment.GetEnvironmentVariable("AMPLIFIER_SETUP_TEST_ROOT");
            bool integrate=args.Length==7;
            string[] expected=integrate
                ? new[]{"-I","-B","-m","amplifier_web.windows_consumer","--root",root,"--integrate"}
                : new[]{"-I","-m","amplifier_web.windows_consumer","--root",root};
            if(root==null || args.Length!=expected.Length) return 9;
            for(int index=0;index<args.Length;index++) if(args[index]!=expected[index]) return 9;
            int wait, code;
            if(Int32.TryParse(Environment.GetEnvironmentVariable("AMPLIFIER_SETUP_TEST_WAIT_MS"),out wait)) Thread.Sleep(wait);
            if(integrate) Console.WriteLine("{\"startMenu\":true}");
            return Int32.TryParse(Environment.GetEnvironmentVariable("AMPLIFIER_SETUP_TEST_EXIT"),out code)?code:0;
        }
        static int Window(string root, bool listed) {
            // Setup's second-run window on a thread of its own, as setup's entry
            // runs it. A window that does not close by itself fails the case.
            int result=-1;
            var thread=new Thread(()=>{ result=NativeInstaller.SecondRun(root,listed); });
            thread.IsBackground=true; thread.SetApartmentState(ApartmentState.STA); thread.Start();
            return thread.Join(45000)?result:-2;
        }
        static int ClosedEarly(string root) {
            // The person closes setup's window while setup only waits for the
            // launcher. A request that comes before that is refused; ask again.
            int result=-1;
            var thread=new Thread(()=>{ result=NativeInstaller.SecondRun(root,true); });
            thread.IsBackground=true; thread.SetApartmentState(ApartmentState.STA); thread.Start();
            Thread.Sleep(1500);
            for(int attempt=0;attempt<12 && !thread.Join(500);attempt++) {
                try {
                    if(System.Windows.Forms.Application.OpenForms.Count>0) {
                        var form=System.Windows.Forms.Application.OpenForms[0];
                        form.BeginInvoke(new Action(form.Close));
                    }
                } catch(Exception) {}
            }
            return thread.Join(1000)?result:-2;
        }
        static int Account() {
            // What setup's own code reads for the account that runs this program:
            // its elevation, the state of its real installation folder and its
            // sign-in task. It only reads. It creates and changes nothing.
            string local=Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string root=Path.Combine(local,NativeInstaller.RootName), folder, program;
            try { InstallStorage.Check(local); folder=NativeInstaller.RootState(root); }
            catch(Exception) { folder="refused"; }
            string task=NativeInstaller.SignInTask(out program);
            Console.WriteLine("{\"elevation\":\""+NativeInstaller.Elevation()+"\",\"folder\":\""+folder+
                "\",\"signInTask\":\""+task+"\",\"taskProgramExists\":"+(program!=null?"true":"false")+
                ",\"folderLength\":"+root.Length+",\"folderLimit\":"+NativeInstaller.MaxRootLength+"}");
            return 0;
        }
        static int Main(string[] args) {
            // Only a stand-in inherits this value: the checks below set it, and
            // nothing sets it for the checks themselves. Any other arguments end
            // the stand-in with 9; they never run the checks a second time.
            if(Environment.GetEnvironmentVariable("AMPLIFIER_SETUP_TEST_ROOT")!=null) return StandIn(args);
            if(args.Length==1 && args[0]=="--account") return Account();
            string local=Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string work=Path.Combine(local,"Temp","amplifier-setup-test-"+Guid.NewGuid().ToString("N").Substring(0,8));
            Directory.CreateDirectory(work);
            string outcome="unknown", task="unknown", plain="unknown", reached="unknown", elevation=NativeInstaller.Elevation();
            try {
                // The fixed folder: short, and the same on every run.
                Need(NativeInstaller.RootName=="Amplifier" && NativeInstaller.MaxRootLength==59,"folder constants");
                Need(NativeInstaller.SourceStore=="s" && NativeInstaller.EarlierSourceStore=="source-store","source store folder names");
                InstallStorage.Check(work);

                // Folder states, in a folder that this person owns privately.
                string root=Path.Combine(work,"Amplifier");
                Need(NativeInstaller.RootState(root)=="absent","absent");
                File.WriteAllText(root,"a file, not a folder");
                Need(NativeInstaller.RootState(root)=="foreign","a file is foreign");
                File.Delete(root);
                Need(Make(work,"Amplifier")==root,"fixed folder name");
                Need(Refused(()=>Make(work,"Amplifier")),"an existing folder is never adopted for writing");
                Need(NativeInstaller.RootState(root)=="unfinished","an empty private folder is removable");
                File.WriteAllText(Path.Combine(root,"other.txt"),"another product's file");
                Need(NativeInstaller.RootState(root)=="foreign","content without setup's marker is foreign");
                File.WriteAllText(Path.Combine(root,NativeInstaller.Started),"{}");
                Need(NativeInstaller.RootState(root)=="unfinished","started marker");
                Directory.CreateDirectory(Path.Combine(root,"app"));
                Need(NativeInstaller.RootState(root)=="unfinished","empty data folders are not use");
                // Component sources are app data under both store folder names.
                foreach(string store in new[]{NativeInstaller.SourceStore,NativeInstaller.EarlierSourceStore}) {
                    Directory.CreateDirectory(Path.Combine(root,store));
                    Need(NativeInstaller.RootState(root)=="unfinished","an empty source store is not use: "+store);
                    File.WriteAllText(Path.Combine(root,store,"object"),"component source");
                    Need(NativeInstaller.RootState(root)=="damaged","component sources without a completion record: "+store);
                    File.Delete(Path.Combine(root,store,"object")); Directory.Delete(Path.Combine(root,store));
                }
                Need(NativeInstaller.RootState(root)=="unfinished","the store folders are gone again");
                File.WriteAllText(Path.Combine(root,"app","settings.yaml"),"a person's settings");
                Need(NativeInstaller.RootState(root)=="damaged","app data without a completion record");
                Need(Refused(()=>NativeInstaller.RemoveUnfinished(root)) && File.Exists(Path.Combine(root,"app","settings.yaml")),
                    "a folder with app data is never removed");
                File.WriteAllText(Path.Combine(root,NativeInstaller.Complete),"{}");
                Need(NativeInstaller.RootState(root)=="installed","complete marker wins");
                Need(Refused(()=>NativeInstaller.RemoveUnfinished(root)) && File.Exists(Path.Combine(root,"app","settings.yaml")),
                    "an installation is never removed");
                // A finished installation has no started marker. If its completion
                // record is ever lost it is foreign, not removable.
                File.Delete(Path.Combine(root,NativeInstaller.Started)); File.Delete(Path.Combine(root,NativeInstaller.Complete));
                Need(NativeInstaller.RootState(root)=="foreign","lost completion record does not make it removable");

                // Markers count only in a folder that no other account can change.
                string planted=Make(work,"Planted");
                File.WriteAllText(Path.Combine(planted,NativeInstaller.Started),"{}");
                File.WriteAllText(Path.Combine(planted,"keep.txt"),"keep");
                Need(NativeInstaller.RootState(planted)=="unfinished","private folder with marker");
                OpenToOthers(planted);
                Need(NativeInstaller.RootState(planted)=="foreign","a folder that other accounts can change is foreign");
                Need(Refused(()=>NativeInstaller.RemoveUnfinished(planted)) && File.Exists(Path.Combine(planted,"keep.txt")),
                    "and it is never removed");
                // A folder that an ordinary program created. On an elevated sign-in
                // its owner is the administrators group, not this person.
                string ordinary=Path.Combine(work,"Ordinary"); Directory.CreateDirectory(ordinary);
                File.WriteAllText(Path.Combine(ordinary,NativeInstaller.Started),"{}");
                plain=NativeInstaller.RootState(ordinary);
                Need(plain=="foreign" || plain=="unfinished","ordinary folder state");

                // The folder itself must not be a link: its target is never touched.
                string target=Make(work,"Target");
                File.WriteAllText(Path.Combine(target,NativeInstaller.Started),"{}");
                File.WriteAllText(Path.Combine(target,"keep.txt"),"keep");
                string link=Path.Combine(work,"Linked"); Junction(link,target);
                Need(NativeInstaller.RootState(link)=="foreign","a link in place of the folder is foreign");
                Need(Refused(()=>NativeInstaller.RemoveUnfinished(link)) && File.ReadAllText(Path.Combine(target,"keep.txt"))=="keep",
                    "a link's target is never removed");

                // Removal of setup's own unfinished folder: read-only files, a path
                // beyond the ordinary limit, a link inside it, and its saved log.
                string outside=Path.Combine(work,"outside"); Directory.CreateDirectory(outside);
                string kept=Path.Combine(outside,"kept.txt"); File.WriteAllText(kept,"keep");
                string tree=Make(work,"Tree");
                File.WriteAllText(Path.Combine(tree,NativeInstaller.Started),"{}");
                File.WriteAllText(Path.Combine(tree,"install.log"),"stage: child exit 1\r\n");
                string locked=Path.Combine(tree,"read-only.txt"); File.WriteAllText(locked,"x");
                File.SetAttributes(locked,FileAttributes.ReadOnly|FileAttributes.System);
                string deep=Path.Combine(tree,"tools");
                Directory.CreateDirectory(deep);
                while(deep.Length<330) { deep=Path.Combine(deep,new string('d',40)); Directory.CreateDirectory(Long(deep)); }
                File.WriteAllText(Long(Path.Combine(deep,"deep.txt")),"x");
                Junction(Path.Combine(tree,"junction"),outside);
                Need(File.Exists(Path.Combine(tree,"junction","kept.txt")),"junction reaches its target");
                NativeInstaller.Diagnostics=null;
                NativeInstaller.RemoveUnfinished(tree);
                Need(!Directory.Exists(tree),"unfinished folder removed");
                Need(File.ReadAllText(kept)=="keep","a link's target survives");
                string log=NativeInstaller.Diagnostics;
                Need(log!=null && log.StartsWith(Path.Combine(local,"Temp","AmplifierSetupLog-"),StringComparison.OrdinalIgnoreCase) &&
                    File.ReadAllText(Path.Combine(log,"install.log")).StartsWith("stage:"),"log kept in a new private folder");
                var owner=new DirectoryInfo(log).GetAccessControl().GetOwner(typeof(SecurityIdentifier));
                using(var identity=WindowsIdentity.GetCurrent()) Need(owner.Equals(identity.User),"the log folder belongs to this person");
                Clean(log);

                // A removal that stops part-way keeps the marker, so the next setup
                // run still recognizes the folder as its own unfinished work.
                string partial=Make(work,"Partial");
                File.WriteAllText(Path.Combine(partial,NativeInstaller.Started),"{}");
                File.WriteAllText(Path.Combine(partial,"a-first.txt"),"x");
                using(new FileStream(Path.Combine(partial,"z-in-use.txt"),FileMode.Create,FileAccess.Write,FileShare.None))
                    Need(Refused(()=>NativeInstaller.RemoveUnfinished(partial)),"removal stops at a file in use");
                Need(!File.Exists(Path.Combine(partial,"a-first.txt")) && NativeInstaller.RootState(partial)=="unfinished","marker is removed last");
                NativeInstaller.RemoveUnfinished(partial);
                Need(!Directory.Exists(partial),"second removal completes");
                Need(Refused(()=>NativeInstaller.RemoveUnfinished("C:\\")),"a drive root is never removed");
                Need(Refused(()=>NativeInstaller.RemoveUnfinished("\\\\server\\share\\folder")),"a network path is never removed");

                // Opening an installation: checked again and held at the moment of use.
                string installed, program;
                using(var storage=new InstallStorage(work,"Installed")) {
                    installed=storage.Root;
                    string scripts=Path.Combine(installed,"tools","amplifier-unified","Scripts");
                    storage.EnsureDirectory(scripts);
                    program=Path.Combine(scripts,"pythonw.exe");
                    using(var stream=storage.CreateFile(program)) stream.WriteByte(77);
                    using(var stream=storage.CreateFile(Path.Combine(installed,NativeInstaller.Complete))) stream.WriteByte(123);
                }
                using(NativeInstaller.PinInstalled(installed,program))
                    Need(Refused(()=>Directory.Move(installed,installed+"-moved")),"the folder cannot be replaced while its program starts");
                Directory.Move(installed,installed+"-moved"); Directory.Move(installed+"-moved",installed);
                File.Delete(Path.Combine(installed,NativeInstaller.Complete));
                Need(Refused(()=>NativeInstaller.PinInstalled(installed,program)),"only a finished installation is opened");

                // The second setup run: its two steps and its window, with this test
                // program in place of the installation's Python. Nothing is installed.
                // Every case here ends without a message.
                byte[] self=File.ReadAllBytes(Assembly.GetExecutingAssembly().Location);
                string second, empty;
                using(var storage=new InstallStorage(work,"Second")) {
                    second=storage.Root;
                    string scripts=Path.Combine(second,"tools","amplifier-unified","Scripts");
                    storage.EnsureDirectory(scripts); storage.EnsureDirectory(Path.Combine(second,"workspace"));
                    foreach(string name in new[]{"python.exe","pythonw.exe"})
                        using(var stream=storage.CreateFile(Path.Combine(scripts,name))) stream.Write(self,0,self.Length);
                    using(var stream=storage.CreateFile(Path.Combine(second,NativeInstaller.Complete))) stream.WriteByte(123);
                }
                Environment.SetEnvironmentVariable("AMPLIFIER_SETUP_TEST_ROOT",second);
                Environment.SetEnvironmentVariable("AMPLIFIER_SETUP_TEST_EXIT","0");
                Need(NativeInstaller.AddEntries(second),"entries step: the program got setup's exact arguments and ended with 0");
                Environment.SetEnvironmentVariable("AMPLIFIER_SETUP_TEST_WAIT_MS","1500");
                var clock=Stopwatch.StartNew();
                int shown=Window(second,true);
                Need(shown==0 && clock.ElapsedMilliseconds>=1400,"second run with entries: setup waits for the launcher, then the window closes (result "+shown+")");
                Environment.SetEnvironmentVariable("AMPLIFIER_SETUP_TEST_WAIT_MS",null);
                shown=Window(second,false);
                Need(shown==0,"second run without entries: added, started, closed (result "+shown+")");
                Environment.SetEnvironmentVariable("AMPLIFIER_SETUP_TEST_EXIT","1");
                Need(!NativeInstaller.AddEntries(second),"entries step: a program that fails is a failure");
                shown=Window(second,true);
                Need(shown==1,"second run: the launcher's failure is setup's result, with no second message (result "+shown+")");
                Environment.SetEnvironmentVariable("AMPLIFIER_SETUP_TEST_EXIT","0");
                // The window is closed while the launcher still runs (10 seconds
                // here). Setup did not see the outcome: 1, although the launcher
                // will end with 0. Then wait for the stand-in to end.
                Environment.SetEnvironmentVariable("AMPLIFIER_SETUP_TEST_WAIT_MS","10000");
                clock=Stopwatch.StartNew();
                shown=ClosedEarly(second);
                Need(shown==1 && clock.ElapsedMilliseconds<9000,"second run: the window closes on request while the launcher runs (result "+shown+")");
                Environment.SetEnvironmentVariable("AMPLIFIER_SETUP_TEST_WAIT_MS",null);
                while(clock.ElapsedMilliseconds<11500) Thread.Sleep(250);
                // A finished installation whose programs are missing: no start.
                using(var storage=new InstallStorage(work,"Empty")) {
                    empty=storage.Root;
                    storage.EnsureDirectory(Path.Combine(empty,"workspace"));
                    using(var stream=storage.CreateFile(Path.Combine(empty,NativeInstaller.Complete))) stream.WriteByte(123);
                }
                Need(!NativeInstaller.AddEntries(empty),"entries step: a missing program is a failure");
                Need(Refused(()=>NativeInstaller.StartApp(empty)),"start step: a missing launcher is refused");

                // The text for a finished installation whose launcher program is
                // missing. Only setup's own look at the folder chooses it.
                string left=Make(work,"Left");
                File.WriteAllText(Path.Combine(left,NativeInstaller.Complete),"{}");
                string damaged="Amplifier is already installed in "+left+", but setup cannot open it: a program file is missing.\n\n"+
                    "Keep that folder. It holds your chats and settings. This version of setup cannot repair it.";
                string emptied="Setup cannot install Amplifier yet. The folder of an earlier installation is still on this PC:\n"+left+"\n\n"+
                    "Setup cannot open Amplifier from that folder: a program file is missing. "+
                    "Setup found no chats or settings in the folder. "+
                    "It does not change the folder, because the folder can hold files that are not Amplifier's.\n\n"+
                    "If a file in the folder is yours, move it to another place. Then delete the folder and run this setup file again.";
                string journal=Path.Combine(left,NativeInstaller.Journal), notes=Path.Combine(left,"my notes.txt");
                string record="{\"schema\": 1, \"choice\": \"delete\", \"token\": \"0123456789abcdef0123456789abcdef\"}";
                Need(NativeInstaller.Journal=="unified-uninstall.json" && String.Join(",",NativeInstaller.Data)==
                    "shared,app,capture,ownership,import,workspace,export","uninstall's names");
                Need(!NativeInstaller.Emptied(left) && NativeInstaller.Unopened(left)==damaged,"no journal: the text of a damaged installation");
                // What an uninstall with the delete choice leaves when the folder
                // holds a file of the person: that file, setup's record, the journal.
                File.WriteAllText(notes,"mine");
                File.WriteAllText(journal,record);
                Need(NativeInstaller.Emptied(left) && NativeInstaller.Unopened(left)==emptied,"a delete journal and no data: the text with the next step");
                // What stays when the last step did not finish: program files.
                Directory.CreateDirectory(Path.Combine(left,"unified-uninstall-last"));
                File.WriteAllText(Path.Combine(left,"unified-uninstall-last","python313.dll"),"program");
                Need(NativeInstaller.Unopened(left)==emptied,"program files that stayed are not data");
                // Each data name: an empty folder is not data; a folder with
                // anything in it, and a file of that name, are data.
                foreach(string name in NativeInstaller.Data) {
                    string entry=Path.Combine(left,name);
                    Directory.CreateDirectory(entry);
                    Need(NativeInstaller.Unopened(left)==emptied,"an empty data folder is not data: "+name);
                    Directory.CreateDirectory(Path.Combine(entry,"inner"));
                    Need(!NativeInstaller.Emptied(left) && NativeInstaller.Unopened(left)==damaged,"a folder in a data folder is data: "+name);
                    Directory.Delete(Path.Combine(entry,"inner"));
                    File.WriteAllText(Path.Combine(entry,"kept.txt"),"data");
                    Need(NativeInstaller.Unopened(left)==damaged,"a file in a data folder is data: "+name);
                    File.Delete(Path.Combine(entry,"kept.txt")); Directory.Delete(entry);
                    File.WriteAllText(entry,"data");
                    Need(NativeInstaller.Unopened(left)==damaged,"a file with a data name is data: "+name);
                    File.Delete(entry);
                    Need(NativeInstaller.Unopened(left)==emptied,"without that entry: "+name);
                }
                // A link with a data name is a doubt, also when it leads to an
                // empty folder: the look never follows a link.
                string elsewhere=Path.Combine(work,"elsewhere"); Directory.CreateDirectory(elsewhere);
                Junction(Path.Combine(left,"app"),elsewhere);
                Need(NativeInstaller.Unopened(left)==damaged,"a link with a data name is data");
                Directory.Delete(Path.Combine(left,"app"),false);
                Need(Directory.Exists(elsewhere) && NativeInstaller.Unopened(left)==emptied,"without the link; its target stays");
                // A link with the journal's name is no journal, also when it leads
                // to a file that is one.
                File.Move(journal,Path.Combine(elsewhere,"journal.json"));
                FileLink(journal,Path.Combine(elsewhere,"journal.json"));
                Need(File.ReadAllText(journal)==record,"the link leads to a journal");
                Need(NativeInstaller.Unopened(left)==damaged,"a file link with the journal's name");
                File.Delete(journal);
                Junction(journal,elsewhere);
                Need(NativeInstaller.Unopened(left)==damaged,"a folder link with the journal's name");
                Directory.Delete(journal,false);
                File.Move(Path.Combine(elsewhere,"journal.json"),journal);
                Need(NativeInstaller.Unopened(left)==emptied,"the journal is back");
                // A file that uninstall does not read as a journal with the
                // delete choice: the text of a damaged installation.
                string[] others={"","{","[]","null","\"delete\"",
                    record.Replace("\"delete\"","\"keep\""),record.Replace("\"delete\"","\"Delete\""),
                    record.Replace("\"choice\": \"delete\"","\"choice\": 1"),
                    record.Replace("\"schema\": 1","\"schema\": 2"),record.Replace("\"schema\": 1","\"schema\": \"1\""),
                    record.Replace("\"schema\": 1","\"schema\": true"),record.Replace("\"schema\": 1","\"schema\": 1.5"),
                    record.Replace("\"schema\": 1, ",""),
                    record.Replace("0123456789abcdef0123456789abcdef","0123456789ABCDEF0123456789abcdef"),
                    record.Replace("0123456789abcdef0123456789abcdef","0123456789abcdef0123456789abcde"),
                    record.Replace("0123456789abcdef0123456789abcdef","0123456789abcdef0123456789abcdef0"),
                    record.Replace("0123456789abcdef0123456789abcdef","0123456789abcdef0123456789abcdef\\n"),
                    record.Replace(", \"token\": \"0123456789abcdef0123456789abcdef\"",""),
                    record+new string(' ',4097-record.Length)};
                for(int index=0;index<others.Length;index++) {
                    File.WriteAllText(journal,others[index]);
                    Need(!NativeInstaller.Emptied(left) && NativeInstaller.Unopened(left)==damaged,"not a journal with the delete choice: file "+index);
                }
                // The largest file that is read, with a further field, as uninstall accepts it.
                string largest=record.Replace("{","{\"more\": \""+new string('x',4096-record.Length-12)+"\", ");
                File.WriteAllText(journal,largest);
                Need(new FileInfo(journal).Length==4096 && NativeInstaller.Unopened(left)==emptied,"a journal of 4096 bytes is read");
                // Bytes that are not UTF-8 are no journal.
                File.WriteAllBytes(journal,new byte[]{0x7b,0xff,0x7d});
                Need(NativeInstaller.Unopened(left)==damaged,"bytes that are not UTF-8");
                // A folder with the journal's name is no journal.
                File.Delete(journal); Directory.CreateDirectory(journal);
                Need(NativeInstaller.Unopened(left)==damaged,"a folder with the journal's name");
                Directory.Delete(journal); File.WriteAllText(journal,record);
                // The look shares the journal with every other access. A program
                // that has it open for writing does not stop the look, and while
                // the look's kind of handle is open, the file can be deleted (the
                // last step of a removal deletes it).
                using(new FileStream(journal,FileMode.Open,FileAccess.ReadWrite,FileShare.ReadWrite|FileShare.Delete))
                    Need(NativeInstaller.Unopened(left)==emptied,"a journal that another program has open for writing is read");
                using(new FileStream(journal,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete)) {
                    File.Delete(journal);
                    // The name goes at once, also while that handle is open: the
                    // last step lists the folder directly after its deletion.
                    Need(!Directory.GetFileSystemEntries(left).Any(entry=>Path.GetFileName(entry)==NativeInstaller.Journal),
                        "the journal's name is gone while a handle of the look's kind is open");
                    Need(NativeInstaller.Unopened(left)==damaged,"a journal that was deleted is no journal");
                }
                Need(!File.Exists(journal),"the journal could be deleted while a handle of the look's kind was open");
                File.WriteAllText(journal,record);
                // A journal that another program holds for itself is a doubt.
                using(new FileStream(journal,FileMode.Open,FileAccess.ReadWrite,FileShare.None))
                    Need(NativeInstaller.Unopened(left)==damaged,"a journal that cannot be opened");
                Need(NativeInstaller.Unopened(left)==emptied,"the journal is read again");
                // The looks changed nothing in the folder.
                string[] names=Directory.GetFileSystemEntries(left).Select(entry=>Path.GetFileName(entry)).OrderBy(name=>name,StringComparer.Ordinal).ToArray();
                Need(String.Join("|",names)=="my notes.txt|unified-setup-complete.json|unified-uninstall-last|unified-uninstall.json",
                    "the folder holds what the case put there: "+String.Join("|",names));
                Need(File.ReadAllText(notes)=="mine" && File.ReadAllText(journal)==record &&
                    File.ReadAllText(Path.Combine(left,NativeInstaller.Complete))=="{}","the person's file and both records are unchanged");
                Need(NativeInstaller.RootState(left)=="installed","setup still takes the folder for a finished installation, and installs nothing there");

                // A folder above the target that other accounts can change is named.
                string open=Path.Combine(work,"open"); Directory.CreateDirectory(Path.Combine(open,"inner"));
                InstallStorage.Check(Path.Combine(open,"inner"));
                OpenToOthers(open);
                IOException above=null;
                try { InstallStorage.Check(Path.Combine(open,"inner")); } catch(IOException error) { above=error; }
                Need(above!=null && (string)above.Data["path"]==open,"the refused folder is named");
                string[] text=NativeInstaller.Explain(above,"Checking this PC",false);
                Need(text[0].Contains("other accounts") && text[0].Contains(open) && text[1].Contains("permissions"),"shared folder text");

                // Plain stops: a cause and a next step, never exception text.
                text=NativeInstaller.Explain(new SetupStop("cause","next"),"stage",false);
                Need(text[0]=="cause" && text[1]=="next","stop passes through");
                text=NativeInstaller.Explain(new WebException("secret text"),"Downloading Amplifier",false);
                Need(text[0].Contains("Downloading Amplifier") && text[1].Contains("internet") && !text[0].Contains("secret"),"network");
                text=NativeInstaller.Explain(new InvalidDataException("Artifact SHA-256 mismatch."),"Downloading Amplifier",false);
                Need(text[0].Contains("damaged"),"checksum");
                var busy=new System.ComponentModel.Win32Exception(32); busy.Data["path"]="C:\\Users\\name";
                text=NativeInstaller.Explain(busy,"Checking this PC",false);
                Need(text[0].Contains("Another program may be using it") && text[1].StartsWith("Restart Windows"),"folder in use");
                text=NativeInstaller.Explain(new InvalidDataException("Bootstrap child failed; private."),"Checking the installation",false);
                Need(text[0]=="Setup could not finish this step: Checking the installation." && text[1]=="Run setup again.","generic step");
                text=NativeInstaller.Explain(new InvalidDataException("x"),"Adding Amplifier to the Start menu",true);
                Need(text[0].StartsWith("Amplifier is installed") && text[1].Contains("select Yes to open"),"installed");

                // The sign-in task read: this runner has no Amplifier task.
                string started;
                task=NativeInstaller.SignInTask(out started);
                Need((task=="absent" || task=="unavailable") && started==null,"no sign-in task on this runner");

                // This runner is not an ordinary Windows 11 sign-in. The checks that
                // run before any question must stop with a plain cause and next step.
                SetupStop stop=null; string state=null;
                try { NativeInstaller.Precheck(out state); } catch(SetupStop error) { stop=error; }
                if(elevation!="none") {
                    Need(stop!=null && stop.Next.Length>20,"elevated sign-in stops plainly");
                    Need(elevation=="run-as-administrator" ? stop.Message.Contains("Run as administrator")
                        : elevation=="always-administrator" ? stop.Message.Contains("runs every program as administrator")
                        : stop.Message.Contains("administrator rights"),"elevation cause");
                }
                outcome=stop==null?"precheck passed: "+state:"precheck stopped";
                // Which check stopped it, from setup's own platform check and not
                // from a text: a standard account passes the elevation part, so
                // its platform stop is the Windows version or the processor.
                SetupStop platform=null;
                try { NativeInstaller.Platform(); } catch(SetupStop error) { platform=error; }
                reached=stop==null?"none":platform==null || stop.Message!=platform.Message?"after-platform"
                    :elevation!="none"?"elevation":"windows-or-processor";

                foreach(string bad in new[]{"","..","a\\b","Amplifier ","con:",new string('a',65)})
                    Need(Refused(()=>Make(work,bad)),"unsafe folder name");
                Console.WriteLine("{\"ok\":true,\"assertions\":"+assertions+",\"elevation\":\""+elevation+
                    "\",\"precheck\":\""+outcome+"\",\"precheckStop\":\""+reached+"\",\"signInTask\":\""+task+
                    "\",\"ordinaryFolder\":\""+plain+"\"}");
                return 0;
            } catch(Exception error) {
                Console.WriteLine("{\"ok\":false,\"assertions\":"+assertions+",\"elevation\":\""+elevation+"\"}");
                Console.Error.WriteLine(error.Message);
                return 1;
            } finally {
                try { Clean(Long(work)); } catch(Exception) {}
            }
        }
    }
}
