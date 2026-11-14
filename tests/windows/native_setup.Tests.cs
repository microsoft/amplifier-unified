// Compile beside the actual consumer setup sources with this explicit entry.
// Native checks of the installation folder, its ownership checks, removal and
// plain stop messages. Everything stays inside two new folders below the
// caller's own temporary folder, and both are removed. No setup window,
// download, task change, registry, trust or policy effect.
using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Security.AccessControl;
using System.Security.Principal;

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
        static int Main() {
            string local=Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string work=Path.Combine(local,"Temp","amplifier-setup-test-"+Guid.NewGuid().ToString("N").Substring(0,8));
            Directory.CreateDirectory(work);
            string outcome="unknown", task="unknown", plain="unknown", elevation=NativeInstaller.Elevation();
            try {
                // The fixed folder: short, and the same on every run.
                Need(NativeInstaller.RootName=="Amplifier" && NativeInstaller.MaxRootLength==59,"folder constants");
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

                foreach(string bad in new[]{"","..","a\\b","Amplifier ","con:",new string('a',65)})
                    Need(Refused(()=>Make(work,bad)),"unsafe folder name");
                Console.WriteLine("{\"ok\":true,\"assertions\":"+assertions+",\"elevation\":\""+elevation+
                    "\",\"precheck\":\""+outcome+"\",\"signInTask\":\""+task+"\",\"ordinaryFolder\":\""+plain+"\"}");
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
