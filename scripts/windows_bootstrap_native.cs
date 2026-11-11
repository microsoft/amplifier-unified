// Prototype native entry for ordinary Windows 11 accounts. Built on a Windows
// runner against the in-box .NET Framework, not compiled on the user's PC.
// It never loads PowerShell or changes script policy. InstallStorage is compiled
// from the existing install.ps1 C# storage owner by windows_bootstrap_native.py.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Management;
using System.Net;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Drawing;

namespace UnifiedBootstrap {
    internal static class NativeInstaller {
        static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength=1048576, RecursionLimit=16 };
        static InstallStorage Storage;
        static string Stage="arguments";
        static string LogPath;
        static int ChildSequence;
        static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

        static void Need(bool value, string message) { if (!value) throw new InvalidDataException(message); }
        static string Text(Dictionary<string,object> map, string key) {
            object value; Need(map.TryGetValue(key, out value) && value is string, "Missing text field: " + key);
            return (string)value;
        }
        static Dictionary<string,object> Object(Dictionary<string,object> map, string key) {
            object value; Need(map.TryGetValue(key, out value) && value is Dictionary<string,object>, "Missing object field: " + key);
            return (Dictionary<string,object>)value;
        }
        static bool OfficialAsset(string value, string suffix) {
            Uri uri;
            return Uri.TryCreate(value, UriKind.Absolute, out uri) && uri.Scheme=="https" && uri.Host=="github.com" &&
                uri.Port==443 && uri.UserInfo=="" && uri.Query=="" && uri.Fragment=="" &&
                Regex.IsMatch(uri.AbsolutePath, @"^/microsoft/amplifier-unified/releases/download/[^/]+/[^/]+" + Regex.Escape(suffix) + "$", RegexOptions.CultureInvariant) &&
                !uri.AbsolutePath.Contains("/download/latest/") && !uri.AbsolutePath.Contains("/download/main/");
        }
        internal static Dictionary<string,object> Plan(string raw) {
            Need(Utf8.GetByteCount(raw)<=1048576, "Prototype plan exceeds limit.");
            var plan=Json.Deserialize<Dictionary<string,object>>(raw);
            Need(plan!=null && plan.ContainsKey("schemaVersion") && plan["schemaVersion"] is int && (int)plan["schemaVersion"]==1,
                "Invalid prototype schema.");
            Need(Text(plan,"status")=="prototype" && Text(plan,"architecture")=="x86_64" &&
                Text(plan,"sourceMode")=="pinned-vcs" && Text(plan,"source")==NativePins.Source &&
                Text(plan,"version")==NativePins.Version && Regex.IsMatch(Text(plan,"python"), @"^3\.13\.[0-9]+$"),
                "Prototype plan is not the reviewed native candidate.");
            var capsule=Object(plan,"capsule");
            Need(OfficialAsset(Text(capsule,"url"), ".zip") && Regex.IsMatch(Text(capsule,"sha256"), "^[0-9a-f]{64}$"),
                "Prototype capsule requires an exact official HTTPS asset and digest.");
            return plan;
        }
        internal static string Quote(string value) {
            return "\"" + Regex.Replace(Regex.Replace(value, "(\\\\*)\"", "$1$1\\\""), "(\\\\+)$", "$1$1") + "\"";
        }
        static string Hash(string path) {
            using(var file=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read))
            using(var sha=SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(file)).Replace("-", "").ToLowerInvariant();
        }
        static void CheckHash(string path, string expected) { Need(Hash(path)==expected, "Artifact SHA-256 mismatch."); }
        static void PrivateText(string path, string value, bool append=false) {
            using(var stream=append?Storage.OpenAppend(path):Storage.CreateFile(path)) {
                var bytes=Utf8.GetBytes(value); stream.Write(bytes,0,bytes.Length); stream.Flush(true);
            }
            if(!append) Storage.HoldFile(path,path==LogPath);
        }
        static void Log(string value) { PrivateText(LogPath,Stage+": "+value+"\r\n",true); }
        static void CopyOrDownload(string source, string target, long maximum) {
            Storage.AssertOwnedPath(target);
            var temporary=target+".partial";
            using(var output=Storage.CreateFile(temporary)) {
                if(Uri.IsWellFormedUriString(source, UriKind.Absolute) && new Uri(source).Scheme=="https") Download(source,output,maximum);
                else {
                    Need(Path.IsPathRooted(source) && File.Exists(source) && (File.GetAttributes(source)&FileAttributes.ReparsePoint)==0,
                        "Local prototype input must be an ordinary absolute file.");
                    using(var input=new FileStream(source,FileMode.Open,FileAccess.Read,FileShare.Read)) CopyBounded(input,output,maximum);
                }
                output.Flush(true);
            }
            Storage.PublishFile(temporary,target); Storage.HoldFile(target,false);
        }
        static void CopyBounded(Stream input, Stream output, long maximum) {
            var buffer=new byte[65536]; long total=0; int count;
            while((count=input.Read(buffer,0,buffer.Length))>0) {
                total=checked(total+count); Need(total<=maximum,"Artifact size limit exceeded."); output.Write(buffer,0,count);
            }
        }
        static void Download(string source, Stream output, long maximum) {
            var current=new Uri(source);
            for(int n=0;n<6;n++) {
                Need(current.Scheme=="https" && current.UserInfo=="" && current.Port==443,"Unsafe artifact redirect.");
                var request=(HttpWebRequest)WebRequest.Create(current);
                request.AllowAutoRedirect=false; request.UseDefaultCredentials=false; request.Credentials=null;
                request.Timeout=30000; request.ReadWriteTimeout=30000; request.UserAgent="Amplifier-Unified-Native-Prototype";
                using(var response=(HttpWebResponse)request.GetResponse()) {
                    int code=(int)response.StatusCode;
                    if(code==301 || code==302 || code==303 || code==307 || code==308) {
                        Need(n<5 && !String.IsNullOrEmpty(response.Headers["Location"]),"Artifact redirect limit.");
                        current=new Uri(current,response.Headers["Location"]); continue;
                    }
                    Need(code==200 && response.ContentLength<=maximum,"Artifact response refused.");
                    using(var input=response.GetResponseStream()) CopyBounded(input,output,maximum);
                    return;
                }
            }
            throw new IOException("Artifact redirect limit.");
        }
        static void Extract(string archive, string destination) {
            Storage.CreateDirectory(destination);
            long expanded=0; var names=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            using(var zip=ZipFile.OpenRead(archive)) {
                Need(zip.Entries.Count<=4096,"Archive entry limit.");
                foreach(var entry in zip.Entries) {
                    var name=entry.FullName.Replace('/','\\');
                    Need(!String.IsNullOrEmpty(name) && !Path.IsPathRooted(name) && !name.Contains(":") &&
                        name.Split('\\').Where(p=>p.Length>0).All(p=>p!="." && p!="..") && names.Add(name.TrimEnd('\\')),
                        "Unsafe/duplicate archive entry.");
                    int type=(entry.ExternalAttributes>>16)&0xF000;
                    Need(type==0 || type==0x8000 || type==0x4000,"Archive special-file refusal.");
                    var target=Path.GetFullPath(Path.Combine(destination,name));
                    Need(target.StartsWith(destination+"\\",StringComparison.OrdinalIgnoreCase),"Archive escaped its root.");
                    if(name.EndsWith("\\",StringComparison.Ordinal)) { Storage.EnsureDirectory(target.TrimEnd('\\')); continue; }
                    expanded=checked(expanded+entry.Length); Need(expanded<=536870912,"Expanded archive limit.");
                    Storage.EnsureDirectory(Path.GetDirectoryName(target));
                    using(var input=entry.Open()) using(var output=Storage.CreateFile(target)) {
                        CopyBounded(input,output,entry.Length); Need(output.Length==entry.Length,"Truncated archive entry."); output.Flush(true);
                    }
                    Storage.HoldFile(target,false);
                }
            }
            Storage.Validate();
        }
        static Dictionary<string,string> EnvironmentFor(string uv, string git) {
            var root=Storage.Root;
            var env=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase) {
                {"PATH",Path.GetDirectoryName(uv)+";"+Path.GetDirectoryName(git)+";"+Environment.GetEnvironmentVariable("PATH")},
                {"PYTHONUTF8","1"},{"PYTHONDONTWRITEBYTECODE","1"},{"UV_NO_CONFIG","1"},{"UV_NO_PROGRESS","1"},
                {"UV_NO_BUILD_PACKAGE","amplifier-core"},{"UV_HTTP_TIMEOUT","30"},{"UV_HTTP_RETRIES","2"},
                {"AMPLIFIER_GITHUB_ACCESS","anonymous"},{"GIT_CONFIG_GLOBAL","NUL"},{"GIT_CONFIG_SYSTEM","NUL"},
                {"GIT_TERMINAL_PROMPT","0"},{"GIT_CONFIG_COUNT","3"},{"GIT_CONFIG_KEY_0","credential.helper"},
                {"GIT_CONFIG_VALUE_0",""},{"GIT_CONFIG_KEY_1","core.askPass"},{"GIT_CONFIG_VALUE_1",""},
                {"GIT_CONFIG_KEY_2","core.longpaths"},{"GIT_CONFIG_VALUE_2","true"}
            };
            foreach(var pair in new[]{"UV_CACHE_DIR:cache","UV_TOOL_DIR:tools","UV_TOOL_BIN_DIR:tool-bin","UV_PYTHON_INSTALL_DIR:python",
                "UV_PYTHON_BIN_DIR:python-bin","TEMP:temp","TMP:temp","AMPLIFIER_WEB_HOME:app","AMPLIFIER_HOME:shared",
                "AMPLIFIER_SESSION_STATE_HOME:ownership","AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:shared\\projects","AMPLIFIER_UNIFIED_IMPORT_HOME:shared"}) {
                int split=pair.IndexOf(':'); env.Add(pair.Substring(0,split),Path.Combine(root,pair.Substring(split+1)));
            }
            return env;
        }
        static async Task<string> Drain(StreamReader reader, int maximum, bool rejectOverflow) {
            var buffer=new char[4096]; int size; long total=0; var text=new StringBuilder();
            while((size=await reader.ReadAsync(buffer,0,buffer.Length).ConfigureAwait(false))>0) {
                total+=size;
                int remaining=maximum-text.Length;
                if(remaining>0) text.Append(buffer,0,Math.Min(size,remaining));
            }
            if(rejectOverflow && total>maximum) throw new IOException("Child output limit.");
            return text.ToString();
        }
        static string Child(string executable, string[] arguments, Dictionary<string,string> environment) {
            Need(Path.IsPathRooted(executable),"Child executable must be absolute.");
            Storage.TrustExecutable(executable);
            var info=new ProcessStartInfo(executable,String.Join(" ",arguments.Select(Quote))) {
                UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=Storage.Root,
                RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Utf8,StandardErrorEncoding=Utf8
            };
            foreach(var key in info.EnvironmentVariables.Keys.Cast<string>().ToArray()) {
                var upper=key.ToUpperInvariant();
                if(upper.StartsWith("UV_") || upper.StartsWith("PIP_") || upper.StartsWith("GIT_") || upper.EndsWith("_API_KEY") ||
                    upper.EndsWith("_TOKEN") || new[]{"PYTHONHOME","PYTHONPATH","VIRTUAL_ENV","SSH_ASKPASS","AWS_ACCESS_KEY_ID","AWS_SECRET_ACCESS_KEY","AZURE_CLIENT_SECRET"}.Contains(upper))
                    info.EnvironmentVariables.Remove(key);
            }
            foreach(var pair in environment) info.EnvironmentVariables[pair.Key]=pair.Value;
            using(var process=new Process {StartInfo=info}) {
                process.Start();
                // Drain independently and never publish child bodies. Bootstrap is
                // synchronous; no application host starts before the pins release.
                var stdout=Drain(process.StandardOutput,1048576,true);
                var stderr=Drain(process.StandardError,65536,false);
                process.WaitForExit();
                Task.WaitAll(stdout,stderr); Log("child exit "+process.ExitCode.ToString(CultureInfo.InvariantCulture));
                int sequence=++ChildSequence;
                if(process.ExitCode!=0) {
                    // These are sensitive, bounded PRIVATE diagnostics, never
                    // public status fields. Do not discard the only failure cause.
                    string name="child-"+sequence.ToString(CultureInfo.InvariantCulture);
                    PrivateText(Path.Combine(Storage.Root,name+".stderr.txt"),stderr.Result);
                    PrivateText(Path.Combine(Storage.Root,name+".failure.json"),Json.Serialize(new Dictionary<string,object> {
                        {"stage",Stage},{"exitCode",process.ExitCode},{"stderrFile",name+".stderr.txt"},
                        {"stderrCharacterLimit",65536},{"mayBeTruncated",stderr.Result.Length==65536}
                    }));
                }
                Need(process.ExitCode==0,"Bootstrap child failed; partial state retained, no automatic retry.");
                return stdout.Result.Trim();
            }
        }
        static string Helper(string name, string url, string hash, string executable) {
            var archive=Path.Combine(Storage.Root,name+".zip"); CopyOrDownload(url,archive,134217728); CheckHash(archive,hash);
            var destination=Path.Combine(Storage.Root,name); Extract(archive,destination);
            var result=Path.Combine(destination,executable); Need(File.Exists(result),"Helper entry missing.");
            return result;
        }
        static void Platform() {
            Need(Environment.OSVersion.Platform==PlatformID.Win32NT && Environment.Is64BitProcess,"Windows x64 required.");
            var principal=new WindowsPrincipal(WindowsIdentity.GetCurrent());
            Need(!principal.IsInRole(WindowsBuiltInRole.Administrator),"Run setup as your normal non-elevated account.");
            using(var search=new ManagementObjectSearcher("SELECT BuildNumber,ProductType,OSArchitecture FROM Win32_OperatingSystem"))
            foreach(ManagementObject os in search.Get()) Need(Int32.Parse((string)os["BuildNumber"],CultureInfo.InvariantCulture)>=22000 &&
                Convert.ToInt32(os["ProductType"],CultureInfo.InvariantCulture)==1 && (string)os["OSArchitecture"]=="64-bit","Windows 11 x64 client required.");
            using(var search=new ManagementObjectSearcher("SELECT Architecture FROM Win32_Processor"))
            foreach(ManagementObject cpu in search.Get()) Need(Convert.ToInt32(cpu["Architecture"],CultureInfo.InvariantCulture)==9,"AMD64 processor required.");
        }
        static string Shortcut(string python, string launcher, bool background) {
            var entry=background?Path.Combine(Path.GetDirectoryName(python),"pythonw.exe"):python;
            Need(File.Exists(entry),"Installed windowless launcher missing.");
            var path=Path.Combine(Storage.Root,"Launch Unified.lnk"); using(var file=Storage.CreateFile(path)) file.Flush(true);
            dynamic shell=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell",true));
            try {
                dynamic shortcut=shell.CreateShortcut(path);
                try {
                    shortcut.TargetPath=entry; shortcut.Arguments="-I -B "+Quote(launcher)+(background?" --background":"");
                    shortcut.WorkingDirectory=Storage.Root; shortcut.Description="Experimental Amplifier Unified - owner sign-in background host";
                    shortcut.Save();
                } finally { System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shortcut); }
            } finally { System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell); }
            Storage.HoldFile(path,false); return path;
        }
        internal static string ConsumerRequirements(object value) {
            Need(value is object[] || value is ArrayList,"Invalid consumer graph.");
            var rows=((IEnumerable)value).Cast<object>().Take(257).ToArray();
            Need(rows.Length>0 && rows.Length<=256,"Invalid consumer graph.");
            var lines=new List<string>(); var names=new HashSet<string>(StringComparer.Ordinal);
            foreach(var raw in rows) {
                var row=raw as Dictionary<string,object>; Need(row!=null,"Invalid consumer component.");
                string name=Text(row,"name"), version=Text(row,"version");
                Need(Regex.IsMatch(name,@"^[a-z0-9][a-z0-9-]*$") && names.Add(name) &&
                    Regex.IsMatch(version,@"^[A-Za-z0-9.!+_-]+$"),"Invalid consumer component identity.");
                if(name=="amplifier-unified") continue;
                if(row.ContainsKey("url")) {
                    string url=Text(row,"url"), revision=Text(row,"revision");
                    Need(Regex.IsMatch(url,@"^https://github\.com/microsoft/amplifier[-a-z0-9]*(?:\.git)?$") &&
                        Regex.IsMatch(revision,"^[a-f0-9]{40}$"),"Unqualified consumer source.");
                    string subdir=row.ContainsKey("subdirectory")?Text(row,"subdirectory"):"";
                    Need(subdir=="" || (Regex.IsMatch(subdir,@"^[A-Za-z0-9_/-]+$") &&
                        subdir.Split('/').All(p=>p!="" && p!="." && p!="..")),"Unsafe component subdirectory.");
                    lines.Add(name+" @ git+"+url+"@"+revision+(subdir==""?"":"#subdirectory="+subdir));
                } else lines.Add(name+"=="+version);
            }
            Need(names.Contains("amplifier-unified"),"Consumer app identity missing.");
            lines.Sort(StringComparer.Ordinal); return String.Join("\n",lines)+"\n";
        }
        static string ConsumerShortcut(string python, string root, string name, string operation) {
            string entry=Path.Combine(Path.GetDirectoryName(python),"pythonw.exe");
            Need(File.Exists(entry),"Installed windowless launcher missing.");
            string path=Path.Combine(root,name+".lnk");
            using(var file=Storage.CreateFile(path)) file.Flush(true);
            dynamic shell=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell",true));
            try {
                dynamic shortcut=shell.CreateShortcut(path);
                try {
                    shortcut.TargetPath=entry;
                    shortcut.Arguments="-I -m amplifier_web.windows_consumer --root "+Quote(root)+operation;
                    shortcut.WorkingDirectory=Path.Combine(root,"workspace");
                    shortcut.Description="Amplifier Unified - start or reopen your background app";
                    shortcut.Save();
                } finally {System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shortcut);}
            } finally {System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);}
            Storage.HoldFile(path,false);return path;
        }
        static string InstallConsumer(string encoded) {
            Platform();
            byte[] planBytes=Convert.FromBase64String(encoded);
            Need(planBytes.Length<=262144,"Consumer plan exceeds limit.");
            var plan=Json.Deserialize<Dictionary<string,object>>(Utf8.GetString(planBytes));
            Need(plan!=null && Text(plan,"kind")=="windows-consumer-preview" &&
                plan["schema"] is int && (int)plan["schema"]==1 &&
                Regex.IsMatch(Text(plan,"source"),"^[a-f0-9]{40}$") &&
                Regex.IsMatch(Text(plan,"version"),@"^\d+\.\d+\.\d+$") &&
                Regex.IsMatch(Text(plan,"python"),@"^3\.13\.\d+$"),"Invalid consumer release plan.");
            var artifact=Object(plan,"artifact");
            string version=Text(plan,"version");
            string name="amplifier_unified-"+version+"-py3-none-any.whl";
            Need(Text(artifact,"url")=="https://github.com/microsoft/amplifier-unified/releases/download/v"+version+"/"+name &&
                Regex.IsMatch(Text(artifact,"sha256"),"^[a-f0-9]{64}$"),"Invalid consumer artifact.");
            string python=null,root=null,shortcut=null;
            using(Storage=new InstallStorage(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData))) {
                root=Storage.Root;LogPath=Path.Combine(root,"install.log");PrivateText(LogPath,"");
                foreach(string relative in new[]{"app","shared","ownership","capture","import","source-store","workspace","cache","python","python-bin","tools","tool-bin","temp"})
                    Storage.EnsureDirectory(Path.Combine(root,relative));
                foreach(string relative in new[]{"","cache","python","python-bin","tools","tool-bin","temp"})
                    Storage.EnableToolWrites(Path.Combine(root,relative));
                Stage="verified private prerequisites";
                string uv=Helper("uv",NativePins.UvUrl,NativePins.UvHash,"uv.exe");
                string git=Helper("git",NativePins.GitUrl,NativePins.GitHash,"cmd\\git.exe");
                foreach(var license in NativePins.UvLicenses) {
                    string notice=Path.Combine(root,"uv",license.Key);
                    CopyOrDownload(NativePins.UvLicenseSource+"/"+license.Key,notice,1048576);
                    CheckHash(notice,license.Value);
                }
                Need(File.Exists(Path.Combine(root,"git","LICENSE.txt")),"MinGit license missing.");
                var env=EnvironmentFor(uv,git);
                env["AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH"]=Path.Combine(root,"capture");
                env["AMPLIFIER_UNIFIED_IMPORT_HOME"]=Path.Combine(root,"import");
                env["AMPLIFIER_SOURCE_STORE"]=Path.Combine(root,"source-store");
                Stage="private ordinary Python";
                string patch=Text(plan,"python");
                Child(uv,new[]{"python","install",patch,"--no-bin","--no-registry"},env);
                string basePython=Child(uv,new[]{"python","find","--no-project","--managed-python",patch},env);
                Need(basePython.StartsWith(Path.Combine(root,"python")+"\\",StringComparison.OrdinalIgnoreCase),"Interpreter escaped private installation.");
                Child(basePython,new[]{"-I","-S","-c","import sys,sysconfig,struct; assert sys.platform=='win32' and '.'.join(map(str,sys.version_info[:3]))==sys.argv[1] and sys.version_info[:2]==(3,13) and struct.calcsize('P')==8 and not sysconfig.get_config_var('Py_GIL_DISABLED')",patch},env);
                Stage="verified application download";
                string wheel=Path.Combine(root,name);CopyOrDownload(Text(artifact,"url"),wheel,134217728);
                CheckHash(wheel,Text(artifact,"sha256"));
                Need(artifact["bytes"] is int && new FileInfo(wheel).Length==(int)artifact["bytes"],"Consumer artifact size differs.");
                string requirements=Path.Combine(root,"components.txt");
                PrivateText(requirements,ConsumerRequirements(plan["graph"]));
                Stage="qualified application installation";
                Child(uv,new[]{"tool","install","--python",basePython,"--link-mode","copy",
                    "--overrides",requirements,"--with-requirements",requirements,wheel},env);
                python=Path.Combine(root,"tools","amplifier-unified","Scripts","python.exe");
                Need(File.Exists(python),"Installed application interpreter missing.");
                PrivateText(Path.Combine(root,"consumer-install.json"),Json.Serialize(new Dictionary<string,object>{
                    {"schema",1},{"kind","windows-consumer-preview"},{"version",version},{"revision",Text(plan,"source")},
                    {"artifact",new Dictionary<string,object>{{"url",Text(artifact,"url")},{"sha256",Text(artifact,"sha256")},{"path",wheel}}},
                    {"graph",plan["graph"]},{"uv",uv},{"git",git}}));
                Stage="installed source and package audit";
                Child(python,new[]{"-I","-B","-m","amplifier_web.windows_consumer","--root",root,"--audit"},env);
                Child(uv,new[]{"pip","check","--python",python},env);
                shortcut=ConsumerShortcut(python,root,"Launch Unified","");
                ConsumerShortcut(python,root,"Amplifier status"," --status");
                ConsumerShortcut(python,root,"Stop Amplifier"," --stop");
                ConsumerShortcut(python,root,"Remove Amplifier sign-in host"," --uninstall");
                Storage.Validate();
            }
            Stage="background application setup";
            var start=new ProcessStartInfo(Path.Combine(Path.GetDirectoryName(python),"pythonw.exe"),
                "-I -m amplifier_web.windows_consumer --root "+Quote(root)) {
                    UseShellExecute=true,WorkingDirectory=Path.Combine(root,"workspace")};
            Process.Start(start);
            return shortcut;
        }
        static int ConsumerWindow() {
            Application.EnableVisualStyles();
            if(MessageBox.Show("Install Amplifier Unified for your Windows account? Setup downloads private prerequisites. "+
                "You do not need WSL, Python, Git or a terminal. This is an unsigned preview; Windows/browser trust "+
                "is separate and no security policy will be bypassed.","Amplifier Unified Setup",
                MessageBoxButtons.OKCancel,MessageBoxIcon.Information)!=DialogResult.OK) return 2;
            using(var form=new Form {Text="Amplifier Unified Setup",ClientSize=new Size(540,140),
                FormBorderStyle=FormBorderStyle.FixedDialog,MaximizeBox=false,MinimizeBox=false}) {
                var label=new Label {Left=20,Top=20,Width=500,Height=55,Text="Preparing your private installation..."};
                var bar=new ProgressBar {Left=20,Top=85,Width=500,Style=ProgressBarStyle.Marquee};
                form.Controls.Add(label);form.Controls.Add(bar);
                var timer=new System.Windows.Forms.Timer {Interval=250};
                timer.Tick+=(sender,e)=>{label.Text="Installing: "+Stage+"\nKeep this window open. No administrator access is requested.";};
                Task<string> work=null; int result=1;bool settled=false;
                form.FormClosing+=(sender,e)=>{if(!settled)e.Cancel=true;};
                form.Shown+=async(sender,e)=>{
                    timer.Start();work=Task.Run(()=>InstallConsumer(NativePins.ConsumerPlan));
                    try {
                        string shortcut=await work;result=0;
                        MessageBox.Show("Installation is prepared. Background startup and browser entry were requested.\n\n"+
                            "Your private shortcut:\n"+shortcut+"\n\nA browser/certificate decision may still need your attention.",
                            "Amplifier Unified",MessageBoxButtons.OK,MessageBoxIcon.Information);
                    } catch(Exception) {
                        MessageBox.Show("Setup stopped during "+Stage+". No automatic retry was attempted.\n\n"+
                            (Storage==null?"No installation root was created.":"Partial files and private diagnostics remain in:\n"+Storage.Root),
                            "Amplifier Unified Setup",MessageBoxButtons.OK,MessageBoxIcon.Error);
                    } finally {settled=true;timer.Stop();form.Close();}
                };
                Application.Run(form);timer.Dispose();return result;
            }
        }
        static void Install(Dictionary<string,string> args, bool background, bool noOpen) {
            string manifest=null, digest=null;
            Need(args.TryGetValue("--prototype-manifest",out manifest) && args.TryGetValue("--manifest-sha256",out digest) &&
                Regex.IsMatch(digest,"^[0-9a-f]{64}$"),"Explicit prototype manifest and SHA-256 required; public Windows activation remains closed.");
            Need(Path.IsPathRooted(manifest) || OfficialAsset(manifest,".json"),"Manifest must be a local reviewed file or exact official HTTPS asset.");
            Platform();
            string python=null, launcher=null, installRoot=null;
            using(Storage=new InstallStorage(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData))) {
                installRoot=Storage.Root;
                LogPath=Path.Combine(Storage.Root,"install.log"); PrivateText(LogPath,"");
                Console.WriteLine("Experimental native setup; NOT qualified public Windows installation.");
                Console.WriteLine("Private location: "+Storage.Root);
                Console.WriteLine("No administrator, PowerShell policy, PATH or trust-store change.");
                Stage="prototype plan";
                var planPath=Path.Combine(Storage.Root,"prototype-plan.json"); CopyOrDownload(manifest,planPath,1048576); CheckHash(planPath,digest);
                var plan=Plan(File.ReadAllText(planPath,Utf8)); var capsuleInfo=Object(plan,"capsule");
                Stage="verified capsule";
                var archive=Path.Combine(Storage.Root,"capsule.zip"); string local;
                CopyOrDownload(args.TryGetValue("--capsule-file",out local)?local:Text(capsuleInfo,"url"),archive,134217728);
                CheckHash(archive,Text(capsuleInfo,"sha256"));
                var capsule=Path.Combine(Storage.Root,"capsule"); Extract(archive,capsule);
                var identity=Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(capsule,"capsule.json"),Utf8));
                Need(Text(identity,"source")==Text(plan,"source") && Text(identity,"version")==Text(plan,"version") &&
                    Text(identity,"sourceMode")==Text(plan,"sourceMode"),"Capsule identity mismatch.");
                foreach(var relative in new[]{"app","shared","ownership","shared\\projects","workspace","export","cache","python","python-bin","tools","tool-bin","temp"})
                    Storage.EnsureDirectory(Path.Combine(Storage.Root,relative));
                foreach(var relative in new[]{"","cache","python","python-bin","tools","tool-bin","temp"}) Storage.EnableToolWrites(Path.Combine(Storage.Root,relative));
                Stage="verified portable tools";
                var uv=Helper("uv",NativePins.UvUrl,NativePins.UvHash,"uv.exe");
                var git=Helper("git",NativePins.GitUrl,NativePins.GitHash,"cmd\\git.exe");
                foreach(var license in NativePins.UvLicenses) {
                    var notice=Path.Combine(Storage.Root,"uv",license.Key);
                    CopyOrDownload(NativePins.UvLicenseSource+"/"+license.Key,notice,1048576); CheckHash(notice,license.Value);
                }
                Need(File.Exists(Path.Combine(Storage.Root,"git","LICENSE.txt")),"MinGit license missing.");
                var env=EnvironmentFor(uv,git);
                Need(Regex.IsMatch(Child(uv,new[]{"--version"},env),"^uv "+Regex.Escape(NativePins.UvVersion)+@"(?:\s|$)"),"Bootstrap uv mismatch.");
                Stage="private ordinary CPython";
                var patch=Text(plan,"python"); Child(uv,new[]{"python","install",patch,"--no-bin","--no-registry"},env);
                var basePython=Child(uv,new[]{"python","find","--no-project","--managed-python",patch},env);
                Need(basePython.StartsWith(Path.Combine(Storage.Root,"python")+"\\",StringComparison.OrdinalIgnoreCase),"Interpreter escaped private root.");
                Child(basePython,new[]{"-I","-S","-B","-c","import sys,sysconfig,struct; assert sys.platform=='win32' and '.'.join(map(str,sys.version_info[:3]))==sys.argv[1] and sys.version_info[:2]==(3,13) and struct.calcsize('P')==8 and not sysconfig.get_config_var('Py_GIL_DISABLED')",patch},env);
                var checker=Path.Combine(capsule,"source_install.py");
                Stage="source payload validation"; Child(basePython,new[]{"-I","-S","-B",checker,"--validate-source",capsule},env);
                Stage="immutable host installation";
                var venv=Path.Combine(Storage.Root,"host"); Child(uv,new[]{"venv","--python",basePython,venv},env);
                python=Path.Combine(venv,"Scripts","python.exe");
                Child(uv,new[]{"pip","install","--python",python,"--no-index","--no-deps","--link-mode","copy","--require-hashes","--only-binary",":all:","--find-links",Path.Combine(capsule,"wheels"),"-r",Path.Combine(capsule,"requirements.txt")},env);
                Stage="exact Git dependencies";
                Child(uv,new[]{"pip","install","--python",python,"--no-index","--no-deps","--link-mode","copy","--no-require-hashes","--verify-hashes","--only-binary",":all:","--no-binary","amplifier-foundation,amplifier-module-hooks-session-naming,amplifier-memory,amplifier-bundle-ui-use,amplifier-bundle-context-intelligence","--find-links",Path.Combine(capsule,"build-wheels"),"--build-constraint",Path.Combine(capsule,"build-constraints.txt"),"-r",Path.Combine(capsule,"git-requirements.txt")},env);
                Stage="installed audit"; Child(python,new[]{"-I","-B",checker,"--audit-source",capsule},env); Child(uv,new[]{"pip","check","--python",python},env);
                launcher=Path.Combine(capsule,"launch.py");
                PrivateText(Path.Combine(Storage.Root,"prototype.json"),Json.Serialize(new Dictionary<string,object> {
                    {"schemaVersion",1},{"status","prototype"},{"source",Text(plan,"source")},{"version",Text(plan,"version")},
                    {"manifestSha256",digest},{"capsuleSha256",Text(capsuleInfo,"sha256")},{"python",python},{"uv",uv},{"git",git},
                    {"sourceMode","pinned-vcs"},{"bootstrapUv",NativePins.UvVersion},{"bootstrapEntry","native-dotnet-framework"},
                    {"delivery",args.ContainsKey("--capsule-file")?"staged-local-capsule":"official-https-capsule"}
                }));
                // The existing launcher validates this receipt. Publish it only
                // after package audits, but before asking that owner to read it.
                Stage="launcher identity";
                Child(python,new[]{"-I","-B",launcher,"--check"},env);
                var shortcut=Shortcut(python,launcher,background); Storage.Validate();
                Console.WriteLine("Setup prepared. Launch or relaunch: "+shortcut);
                Console.WriteLine("Background selection: "+background+". Start at sign-in; stop at sign-out. Not an SCM service.");
                Console.WriteLine("Prior installs remain untouched. Browser trust is an explicit first-launch decision.");
            }
            if(!noOpen) {
                Stage="selected launcher";
                var entry=background?Path.Combine(Path.GetDirectoryName(python),"pythonw.exe"):python;
                Need(File.Exists(entry),"Installed windowless launcher missing.");
                Process.Start(new ProcessStartInfo(entry,"-I -B "+Quote(launcher)+(background?" --background":"")) {UseShellExecute=true,WorkingDirectory=installRoot});
            }
        }
        [STAThread]
        internal static int Main(string[] values) {
            try {
                if(values.Length==0 && NativePins.ConsumerPlan!="") return ConsumerWindow();
                if(values.Length==1 && values[0]=="--self-test") {
                    Need(Quote("a b")=="\"a b\"" && Quote("a\\")=="\"a\\\\\"","Native quoting regression.");
                    Need(!OfficialAsset("https://github.com/microsoft/amplifier-unified/releases/download/main/a.zip",".zip"),"Moving asset accepted.");
                    Console.WriteLine("Native entry self-test passed; no install, policy or network effects."); return 0;
                }
                var args=new Dictionary<string,string>(); bool background=false,noOpen=false;
                for(int i=0;i<values.Length;i++) {
                    if(values[i]=="--background") {Need(!background,"Duplicate switch."); background=true;}
                    else if(values[i]=="--no-open") {Need(!noOpen,"Duplicate switch."); noOpen=true;}
                    else {Need(new[]{"--prototype-manifest","--manifest-sha256","--capsule-file"}.Contains(values[i]) && i+1<values.Length && !args.ContainsKey(values[i]),"Invalid/duplicate native entry argument.");args.Add(values[i],values[++i]);}
                }
                ServicePointManager.SecurityProtocol|=SecurityProtocolType.Tls12;
                Install(args,background,noOpen); return 0;
            } catch(Exception) {
                // No exception text, raw child output, paths from remote errors or
                // environment values. Retain exact private stage, not a false retry.
                Console.Error.WriteLine("Native prototype stopped at "+Stage+"; no automatic retry. Keep the printed private directory for review.");
                return 1;
            }
        }
    }
}
