// Compile beside the actual consumer setup sources with this explicit entry.
// Native checks of the installation folder, removal and plain stop messages.
// Everything stays inside one new folder below the caller's temporary folder,
// except one uniquely named private folder below the caller's own app data.
// No setup window, download, task, registry, trust or policy effect.
using System;
using System.Diagnostics;
using System.IO;
using System.Net;

namespace UnifiedBootstrap {
    internal static class NativeSetupTests {
        static int assertions;
        static void Need(bool actual, string what) {
            assertions++;
            if(!actual) throw new Exception("Native setup assertion failed: "+what);
        }
        static string Long(string path) { return "\\\\?\\"+path; }
        static int Main() {
            string work=Path.Combine(Path.GetTempPath(),"amplifier-setup-test-"+Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(work);
            string outcome="unknown", elevation=NativeInstaller.Elevation();
            try {
                // The fixed folder: short, and the same on every run.
                Need(NativeInstaller.RootName=="Amplifier" && NativeInstaller.MaxRootLength==79,"folder constants");

                // Folder states. Only setup's own unfinished work may be removed.
                string root=Path.Combine(work,"Amplifier");
                Need(NativeInstaller.RootState(root)=="absent","absent");
                File.WriteAllText(root,"a file, not a folder");
                Need(NativeInstaller.RootState(root)=="foreign","file is foreign");
                File.Delete(root); Directory.CreateDirectory(root);
                Need(NativeInstaller.RootState(root)=="foreign","empty folder is foreign");
                File.WriteAllText(Path.Combine(root,"install.log"),"another product's file");
                Need(NativeInstaller.RootState(root)=="foreign","generic file is not setup's marker");
                File.WriteAllText(Path.Combine(root,NativeInstaller.Started),"{}");
                Need(NativeInstaller.RootState(root)=="unfinished","started marker");
                File.WriteAllText(Path.Combine(root,NativeInstaller.Complete),"{}");
                Need(NativeInstaller.RootState(root)=="installed","complete marker wins");

                // Removal: read-only files, a path beyond the ordinary limit, and a
                // junction whose target must survive untouched.
                string outside=Path.Combine(work,"outside"); Directory.CreateDirectory(outside);
                string kept=Path.Combine(outside,"kept.txt"); File.WriteAllText(kept,"keep");
                string tree=Path.Combine(work,"tree"); Directory.CreateDirectory(tree);
                string locked=Path.Combine(tree,"read-only.txt"); File.WriteAllText(locked,"x");
                File.SetAttributes(locked,FileAttributes.ReadOnly|FileAttributes.System);
                string deep=tree;
                while(deep.Length<330) { deep=Path.Combine(deep,new string('d',40)); Directory.CreateDirectory(Long(deep)); }
                File.WriteAllText(Long(Path.Combine(deep,"deep.txt")),"x");
                Need(Path.Combine(deep,"deep.txt").Length>330,"path is beyond the ordinary limit");
                string junction=Path.Combine(tree,"junction");
                var link=Process.Start(new ProcessStartInfo("cmd.exe","/d /c mklink /J \""+junction+"\" \""+outside+"\"") {
                    UseShellExecute=false,CreateNoWindow=true});
                link.WaitForExit();
                Need(link.ExitCode==0 && File.Exists(Path.Combine(junction,"kept.txt")),"junction created");
                NativeInstaller.RemoveTree(tree);
                Need(!Directory.Exists(tree),"tree removed");
                Need(File.ReadAllText(kept)=="keep","junction target survives");
                // A removal that stops part-way keeps the marker, so the next setup
                // run still recognizes the folder as its own unfinished work.
                string partial=Path.Combine(work,"partial"); Directory.CreateDirectory(partial);
                File.WriteAllText(Path.Combine(partial,NativeInstaller.Started),"{}");
                File.WriteAllText(Path.Combine(partial,"a-first.txt"),"x");
                bool stopped=false;
                using(new FileStream(Path.Combine(partial,"z-in-use.txt"),FileMode.Create,FileAccess.Write,FileShare.None)) {
                    try { NativeInstaller.RemoveTree(partial); } catch(Exception) { stopped=true; }
                }
                Need(stopped && !File.Exists(Path.Combine(partial,"a-first.txt")),"removal stopped at the file in use");
                Need(NativeInstaller.RootState(partial)=="unfinished","marker is removed last");
                NativeInstaller.RemoveTree(partial);
                Need(!Directory.Exists(partial),"second removal completes");
                bool refused=false;
                try { NativeInstaller.RemoveTree("C:\\"); } catch(Exception) { refused=true; }
                Need(refused,"a drive root is never removed");
                refused=false;
                try { NativeInstaller.RemoveTree("\\\\server\\share\\folder"); } catch(Exception) { refused=true; }
                Need(refused,"a network path is never removed");

                // Plain stops: a cause and a next step, never exception text.
                string[] text=NativeInstaller.Explain(new SetupStop("cause","next"),"stage",false);
                Need(text[0]=="cause" && text[1]=="next","stop passes through");
                text=NativeInstaller.Explain(new WebException("secret text"),"Downloading Amplifier",false);
                Need(text[0].Contains("Downloading Amplifier") && text[1].Contains("internet") && !text[0].Contains("secret"),"network");
                text=NativeInstaller.Explain(new InvalidDataException("Artifact SHA-256 mismatch."),"Downloading Amplifier",false);
                Need(text[0].Contains("damaged"),"checksum");
                var acl=new IOException("Foreign mutation/access in bootstrap traversal ACL."); acl.Data["path"]="C:\\";
                text=NativeInstaller.Explain(acl,"Preparing the Amplifier folder",false);
                Need(text[0].Contains("other accounts") && text[0].Contains("C:\\") && text[1].Contains("permissions"),"shared folder");
                text=NativeInstaller.Explain(new InvalidDataException("Bootstrap child failed; private."),"Checking the installation",false);
                Need(text[0]=="Setup could not finish this step: Checking the installation." && text[1]=="Run setup again.","generic step");
                text=NativeInstaller.Explain(new InvalidDataException("x"),"Adding Amplifier to the Start menu",true);
                Need(text[0].StartsWith("Amplifier is installed") && text[1].Contains("select Yes to open"),"installed");

                // This runner is not an ordinary Windows 11 sign-in. The checks that
                // run before any question must stop with a plain cause and next step.
                SetupStop stop=null; string state=null;
                try { NativeInstaller.Precheck(out state); } catch(SetupStop error) { stop=error; }
                if(elevation!="none") {
                    Need(stop!=null && stop.Next.Length>20,"elevated sign-in stops plainly");
                    Need(elevation=="run-as-administrator" ? stop.Message.Contains("Run as administrator")
                        : stop.Message.Contains("runs every program as administrator"),"elevation cause");
                }
                outcome=stop==null?"precheck passed: "+state:"precheck stopped";

                // The actual private storage owner with the fixed-name form.
                string parent=Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string name="AmplifierSetupTest-"+Guid.NewGuid().ToString("N").Substring(0,8);
                string storageRoot=null, storage="created";
                try {
                    using(var first=new InstallStorage(parent,name)) {
                        storageRoot=first.Root;
                        Need(first.Root==Path.Combine(parent,name),"fixed folder name");
                        using(var stream=first.CreateFile(Path.Combine(first.Root,NativeInstaller.Started))) stream.WriteByte(123);
                        refused=false;
                        try { using(new InstallStorage(parent,name)) {} }
                        catch(IOException error) { refused=error.Message.Contains("already exists"); }
                        Need(refused,"an existing folder is never adopted");
                    }
                    Need(NativeInstaller.RootState(storageRoot)=="unfinished","storage folder is setup's unfinished work");
                    NativeInstaller.RemoveTree(storageRoot);
                    Need(!Directory.Exists(storageRoot),"storage folder removed");
                } catch(IOException error) {
                    // An elevated or managed runner can have traversal permissions
                    // that the storage owner refuses. Record it; do not weaken it.
                    if(storageRoot!=null || !error.Data.Contains("path")) throw;
                    storage="refused above the folder";
                }
                foreach(string bad in new[]{"","..","a\\b","Amplifier ","con:",new string('a',65)}) {
                    refused=false;
                    try { using(new InstallStorage(parent,bad)) {} } catch(IOException) { refused=true; }
                    Need(refused,"unsafe folder name");
                }
                Console.WriteLine("{\"ok\":true,\"assertions\":"+assertions+",\"elevation\":\""+elevation+
                    "\",\"precheck\":\""+outcome+"\",\"storage\":\""+storage+"\"}");
                return 0;
            } catch(Exception error) {
                Console.WriteLine("{\"ok\":false,\"assertions\":"+assertions+",\"elevation\":\""+elevation+"\"}");
                Console.Error.WriteLine(error.Message);
                return 1;
            } finally {
                try { NativeInstaller.RemoveTree(work); } catch(Exception) {}
            }
        }
    }
}
