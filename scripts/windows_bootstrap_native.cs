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
            var path=Path.Combine(Storage.Root,"Launch Unified.lnk"); using(var file=Storage.CreateFile(path)) file.Flush(true);
            dynamic shell=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell",true));
            try {
                dynamic shortcut=shell.CreateShortcut(path);
                try {
                    shortcut.TargetPath=python; shortcut.Arguments="-I -B "+Quote(launcher)+(background?" --background":"");
                    shortcut.WorkingDirectory=Storage.Root; shortcut.Description="Experimental Amplifier Unified - owner sign-in background host";
                    shortcut.Save();
                } finally { System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shortcut); }
            } finally { System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell); }
            Storage.HoldFile(path,false); return path;
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
                Process.Start(new ProcessStartInfo(python,"-I -B "+Quote(launcher)+(background?" --background":"")) {UseShellExecute=true,WorkingDirectory=installRoot});
            }
        }
        internal static int Main(string[] values) {
            try {
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
