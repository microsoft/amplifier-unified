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
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Drawing;

// The in-box compiler otherwise emits an un-targeted entry that opts into
// legacy .NET networking compatibility (SSL3/TLS1.0), even on .NET 4.8 hosts.
// Retarget this process, not machine policy; OS TLS and certificate checks own
// the actual connection. Do not pin older protocols or bypass validation.
[assembly:System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8")]

namespace UnifiedBootstrap {
    // A stop that the person can act on: one plain cause and one next step.
    internal sealed class SetupStop : Exception {
        internal readonly string Next;
        internal SetupStop(string cause, string next) : base(cause) { Next=next; }
    }
    internal static class NativeInstaller {
        static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength=1048576, RecursionLimit=16 };
        static InstallStorage Storage;
        static string Stage="arguments";
        // The consumer installation's one folder: %LOCALAPPDATA%\Amplifier. The
        // app repeats this path inside its own data paths, and Windows refuses
        // paths of 260 characters or more unless long paths are enabled. Keep the
        // name short. docs/design/native-windows-installation.md owns the limit.
        internal const string RootName="Amplifier";
        internal const int MaxRootLength=59;
        internal const string Started="unified-setup-started.json", Complete="unified-setup-complete.json";
        internal static string Diagnostics;
        static string FailedStage, Aftermath="Setup changed nothing on this PC.";
        static bool Installed;
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
                if(Uri.IsWellFormedUriString(source, UriKind.Absolute) && new Uri(source).Scheme=="https") {
                    for(int attempt=1;;attempt++) {
                        try { Download(source,output,maximum); break; }
                        catch(Exception error) {
                            // A dropped connection is ordinary: empty the same private
                            // file and try again. A refused artifact is not retried.
                            if(attempt==3 || !(error is WebException || error is IOException)) throw;
                            Log("download attempt "+attempt.ToString(CultureInfo.InvariantCulture)+" did not finish");
                            output.SetLength(0); output.Position=0;
                            System.Threading.Thread.Sleep(2000*attempt);
                        }
                    }
                } else {
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
                // BASH_ENV and exported functions are shell startup code: a setup
                // child must not run them or let them replace the commands it checks.
                if(upper.StartsWith("UV_") || upper.StartsWith("PIP_") || upper.StartsWith("GIT_") || upper.EndsWith("_API_KEY") ||
                    upper.EndsWith("_TOKEN") || upper.StartsWith("BASH_FUNC_") ||
                    new[]{"PYTHONHOME","PYTHONPATH","VIRTUAL_ENV","SSH_ASKPASS","AWS_ACCESS_KEY_ID","AWS_SECRET_ACCESS_KEY","AZURE_CLIENT_SECRET","BASH_ENV"}.Contains(upper))
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
        static string PrivateGit(string python, string archive, Dictionary<string,string> environment) {
            // The agent's shell tool needs bash.exe and core utilities; MinGit has
            // neither. The complete official distribution is tar.bz2 only, which
            // the in-box framework cannot read. Setup's private Python unpacks the
            // already verified archive: no other tool is acquired or executed.
            string root=Storage.Root, destination=Path.Combine(root,"git"), unpacker=Path.Combine(root,"private-git.py");
            PrivateText(unpacker,Utf8.GetString(Convert.FromBase64String(NativePins.ShellGitUnpacker)));
            Storage.CreateDirectory(destination); Storage.EnableToolWrites(destination);
            PrivateText(Path.Combine(root,"private-git.json"),
                Child(python,new[]{"-I","-S","-B",unpacker,"--archive",archive,"--destination",destination},environment));
            // The archive keeps its components' notices but not Git's own license.
            string notice=Path.Combine(destination,"LICENSE.txt");
            CopyOrDownload(NativePins.ShellGitLicenseUrl,notice,1048576); CheckHash(notice,NativePins.ShellGitLicenseHash);
            string git=Path.Combine(destination,"cmd","git.exe"), bash=Path.Combine(destination,"bin","bash.exe");
            Need(File.Exists(git) && File.Exists(bash),"Private Git or its shell is missing.");
            // The agent's shell tool starts this same entry. Prove on this PC that
            // it runs and that its Git is this installation's pinned Git, by
            // location and version, before setup reports a usable installation.
            // A program outside this private tree would appear as a drive path (/c/...).
            string[] shell=Child(bash,new[]{"-c","command -v git && git --version"},environment)
                .Split(new[]{'\r','\n'},StringSplitOptions.RemoveEmptyEntries);
            Need(shell.Length==2 && Regex.IsMatch(shell[0],"^/(ucrt64/bin|cmd|bin)/git$",RegexOptions.CultureInvariant) &&
                shell[1]=="git version "+NativePins.ShellGitVersion,"The private shell did not run its own Git.");
            return git;
        }
        [DllImport("advapi32.dll",SetLastError=true)]
        static extern bool GetTokenInformation(IntPtr token,int kind,out int value,int size,out int length);
        internal static string Elevation() {
            // "none": an ordinary sign-in. "run-as-administrator": this one program
            // was elevated. "always-administrator": the account runs every program
            // elevated (User Account Control off, or the built-in Administrator).
            using(var identity=WindowsIdentity.GetCurrent()) {
                if(!new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator)) return "none";
                int type,length;  // TokenElevationType: 2 is a full token beside a limited one.
                if(!GetTokenInformation(identity.Token,18,out type,4,out length)) return "administrator";
                return type==2 ? "run-as-administrator" : "always-administrator";
            }
        }
        internal static void Platform() {
            // Setup never continues elevated: its files would belong to the
            // administrators group, not to the person, and it needs no such right.
            string elevation=Elevation();
            if(elevation=="run-as-administrator") throw new SetupStop(
                "Setup was started with \"Run as administrator\". Amplifier installs for your own Windows account and does not need administrator rights.",
                "Close this window. Open the setup file again with a double-click, not with \"Run as administrator\".");
            if(elevation=="always-administrator") throw new SetupStop(
                "This Windows sign-in runs every program as administrator. User Account Control is off, or this is the built-in Administrator account. Amplifier installs for one person without administrator rights.",
                "Sign in to Windows with a standard account, or turn User Account Control on. Then run setup again.");
            if(elevation!="none") throw new SetupStop(
                "Setup is running with administrator rights. Amplifier installs for your own Windows account and does not need them.",
                "Close this window. Open the setup file again with a double-click, from a standard Windows sign-in.");
            var unsupported=new SetupStop("Amplifier needs Windows 11 on a PC with a 64-bit Intel or AMD processor. This PC has a different Windows version or processor.",
                "Use a PC with Windows 11 and an Intel or AMD processor. Windows on ARM processors is not supported yet.");
            if(Environment.OSVersion.Platform!=PlatformID.Win32NT || !Environment.Is64BitProcess) throw unsupported;
            using(var search=new ManagementObjectSearcher("SELECT BuildNumber,ProductType,OSArchitecture FROM Win32_OperatingSystem"))
            foreach(ManagementObject os in search.Get()) if(!(Int32.Parse((string)os["BuildNumber"],CultureInfo.InvariantCulture)>=22000 &&
                Convert.ToInt32(os["ProductType"],CultureInfo.InvariantCulture)==1 && (string)os["OSArchitecture"]=="64-bit")) throw unsupported;
            using(var search=new ManagementObjectSearcher("SELECT Architecture FROM Win32_Processor"))
            foreach(ManagementObject cpu in search.Get()) if(Convert.ToInt32(cpu["Architecture"],CultureInfo.InvariantCulture)!=9) throw unsupported;
        }
        static InstallStorage Existing(string root) {
            // Pin an existing folder and every folder above it, with the checks of
            // the private folders that setup creates. Creates and repairs nothing.
            return new InstallStorage(Path.GetDirectoryName(root),Path.GetFileName(root),true);
        }
        internal static string RootState(string root) {
            // Setup removes or starts only what it made. Marker files alone do not
            // prove that: another account could plant them where it may create
            // folders. They count only inside a folder that this person owns, that
            // no other account can change, and that is not a link.
            if(!Directory.Exists(root)) return File.Exists(root)?"foreign":"absent";
            try { using(Existing(root)) return Marked(root); }
            catch(IOException error) {
                // The folder itself failed a policy check (link, owner, access for
                // another account). A refused folder above it is a different stop,
                // which names that folder. A folder that Windows would not open
                // (in use) is not classified at all.
                if(error.Data.Contains("path") && String.Equals(Path.GetFullPath((string)error.Data["path"]),
                    Path.GetFullPath(root),StringComparison.OrdinalIgnoreCase)) return "foreign";
                throw;
            }
        }
        static string Marked(string root) {
            // "installed" only after setup finished its installation. "unfinished"
            // is setup's own earlier attempt, or an empty folder: the app never ran
            // from it, so it holds no chats or settings. "damaged" has setup's first
            // marker and app data, but no completion record: it is never removed.
            if(File.Exists(Path.Combine(root,Complete))) return "installed";
            bool used=new[]{"app","shared","ownership","capture","import","workspace","source-store"}.Any(name=>{
                string folder=Path.Combine(root,name);
                return Directory.Exists(folder) && Directory.EnumerateFileSystemEntries(folder).Any();});
            if(File.Exists(Path.Combine(root,Started))) return used?"damaged":"unfinished";
            return Directory.EnumerateFileSystemEntries(root).Any()?"foreign":"unfinished";
        }
        internal static void RemoveUnfinished(string root) {
            // The only removal of setup's folder. Check again at the moment of
            // removal, not from an earlier answer. The folder and every folder
            // above it stay pinned while its contents go, so nothing can put a
            // link in its place meanwhile. Extended-length paths reach entries
            // beyond the ordinary Windows limit.
            using(Existing(root)) {
                Need(Marked(root)=="unfinished","The folder is not an unfinished setup attempt.");
                string saved=SaveDiagnostics(root); if(saved!=null) Diagnostics=saved;
                RemoveContents("\\\\?\\"+Path.GetFullPath(root),Started);
            }
            Directory.Delete(root,false);  // Empty now. A link here would go as a link.
        }
        static void RemoveContents(string folder, string last) {
            // The started marker goes last: a removal that stops part-way leaves a
            // folder that the next setup run still recognizes as its own.
            foreach(string entry in Directory.GetFileSystemEntries(folder)) {
                if(last!=null && String.Equals(Path.GetFileName(entry),last,StringComparison.OrdinalIgnoreCase)) continue;
                var attributes=File.GetAttributes(entry);
                if((attributes&FileAttributes.ReadOnly)!=0) File.SetAttributes(entry,attributes&~FileAttributes.ReadOnly);
                if((attributes&FileAttributes.Directory)==0) File.Delete(entry);
                // A link goes as a link; its target is never entered.
                else if((attributes&FileAttributes.ReparsePoint)!=0) Directory.Delete(entry,false);
                else { RemoveContents(entry,null); Directory.Delete(entry,false); }
            }
            string marker=last==null?null:Path.Combine(folder,last);
            if(marker!=null && File.Exists(marker)) { File.SetAttributes(marker,FileAttributes.Normal); File.Delete(marker); }
        }
        internal static IDisposable PinInstalled(string root, string program) {
            // Check again at the moment of use. The caller keeps the result while
            // Windows starts the program: the folder, the folders above it and the
            // program file cannot be replaced meanwhile.
            var storage=Existing(root);
            try {
                Need(Marked(root)=="installed","The folder is not a finished installation.");
                storage.TrustExecutable(program);
                return storage;
            } catch { storage.Dispose(); throw; }
        }
        internal static string SignInTask(out string program) {
            program=null;
            // One sign-in task exists per Windows account: "present" means that an
            // Amplifier installation already starts for this person. The app reads
            // the same registration with the same rule: only "not found" is absent.
            try {
                dynamic service=Activator.CreateInstance(Type.GetTypeFromProgID("Schedule.Service",true));
                try {
                    service.Connect();
                    string name;
                    using(var identity=WindowsIdentity.GetCurrent()) name="AmplifierUnified-"+identity.User.Value;
                    dynamic task;
                    try { task=service.GetFolder("\\").GetTask(name); }
                    catch(Exception error) {
                        for(Exception inner=error;inner!=null;inner=inner.InnerException)
                            if(inner.HResult==unchecked((int)0x80070002)) return "absent";
                        return "unavailable";
                    }
                    // A registration whose program was deleted can never start.
                    try {
                        foreach(dynamic action in task.Definition.Actions)
                            if((int)action.Type==0 && File.Exists((string)action.Path)) { program=(string)action.Path; return "present"; }
                        return "leftover";
                    } catch(Exception) { return "present"; }
                } finally { Marshal.FinalReleaseComObject(service); }
            } catch(Exception) { return "unavailable"; }
        }
        internal static string Precheck(out string state) {
            Stage="Checking this PC";
            Platform();
            string parent=Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string root=Path.Combine(parent,RootName);
            if(root.Length>MaxRootLength) throw new SetupStop(
                "Amplifier cannot install for this Windows account. Its folder would be "+root+", and that path is too long for Windows ("+
                root.Length.ToString(CultureInfo.InvariantCulture)+" characters; the limit is "+MaxRootLength.ToString(CultureInfo.InvariantCulture)+").",
                "Use a Windows account with a shorter name.");
            try { InstallStorage.Check(parent); }
            catch(IOException error) {
                if(error.Data.Contains("path")) throw;  // Explain names the refused folder.
                throw new SetupStop(
                    "Amplifier cannot install for this Windows account. Setup does not accept the path of its folder: "+root,
                    "Use a Windows account whose profile folder is on a local disk and has an ordinary name.");
            }
            state=RootState(root);
            if(state!="installed" && new DriveInfo(Path.GetPathRoot(root)).AvailableFreeSpace<4L*1024*1024*1024) throw new SetupStop(
                "The disk "+Path.GetPathRoot(root)+" has less than 4 GB of free space. Amplifier needs that space for its files.",
                "Make at least 4 GB of disk space free. Then run setup again.");
            if(state=="foreign") throw new SetupStop(
                "The folder "+root+" already exists, and this setup did not create it. Setup does not change a folder that belongs to something else.",
                "If another program uses that folder, do not change it: Amplifier cannot install for this Windows account yet. "+
                "If you do not need the folder, move or rename it. Then run setup again.");
            if(state=="damaged") throw new SetupStop(
                "The folder "+root+" holds an Amplifier installation, but its setup record is incomplete. Setup does not remove it, because it can hold your chats and settings.",
                "Move or rename that folder. Then run setup again.");
            string program=null;
            string task=state=="installed"?"absent":SignInTask(out program);
            if(task=="unavailable") throw new SetupStop(
                "Setup cannot read Windows Task Scheduler. Amplifier uses it to start when you sign in.",
                "Restart Windows. Then run setup again.");
            if(task=="leftover") {
                string entry;
                using(var identity=WindowsIdentity.GetCurrent()) entry="AmplifierUnified-"+identity.User.Value;
                throw new SetupStop(
                    "Windows still has a start entry for an earlier Amplifier installation whose files were deleted. Windows can start only one Amplifier for your account.",
                    "Open the Start menu, type Task Scheduler and open it. Select Task Scheduler Library. Select the task named "+entry+
                    ", press the Delete key and confirm. Then run setup again.");
            }
            if(task=="present") {
                string earlier=Directory.GetDirectories(parent,"amplifier-install-*")
                    .FirstOrDefault(path=>File.Exists(Path.Combine(path,"consumer-install.json")));
                if(earlier!=null) throw new SetupStop(
                    "An earlier Amplifier installation is on this PC, in the folder "+earlier+". Windows can start only one Amplifier for your account.",
                    "Remove the earlier installation first: open Windows Settings, then Apps, then Installed apps. Find Amplifier Unified and select Uninstall. "+
                    "Then run this setup file again. The new installation uses a short folder path, which Windows needs for chats. "+
                    "The earlier folder stays on this PC; the new installation does not read its chats.");
                throw new SetupStop(
                    "Another Amplifier installation already starts when you sign in to this Windows account. Windows can start only one Amplifier for your account."+
                    (program==null?"":" It starts from this program: "+program),
                    "Remove that installation first. Then run this setup file again.");
            }
            return root;
        }
        internal static string[] Explain(Exception error, string stage, bool installed) {
            // Cause and next step in plain language. Never exception text, child
            // output or values from the network.
            var stop=error as SetupStop;
            if(stop!=null) return new[]{stop.Message,stop.Next};
            if(installed) return new[]{"Amplifier is installed, but setup stopped at this step: "+stage+".",
                "Run this setup file again and select Yes to open Amplifier."};
            if(error.Data.Contains("path")) {
                string path=(string)error.Data["path"];
                if(error is System.ComponentModel.Win32Exception) return new[]{
                    "Windows did not let setup check the folder "+path+". Another program may be using it.",
                    "Restart Windows. Then run setup again."};
                return error.Message.StartsWith("Foreign mutation",StringComparison.Ordinal)
                    ? new[]{"Windows lets other accounts on this PC change the folder "+path+". Setup does not use a folder below it, because another account could then replace Amplifier's files.",
                        "Ask the person who manages this PC to correct the permissions of that folder. Then run setup again."}
                    : new[]{"Setup cannot create its private folder. Windows refused the folder "+path+", or its permissions are not the usual ones.",
                        "Check that "+path+" is on a local disk of this PC and belongs to your account. Then run setup again."};
            }
            if(error.Message=="Artifact SHA-256 mismatch.") return new[]{"A file that setup downloaded was damaged or changed on the way.",
                "Run setup again. If this message returns, download a new copy of the setup file."};
            // ERROR_DISK_FULL and ERROR_HANDLE_DISK_FULL as Win32 HRESULTs.
            if(error.HResult==unchecked((int)0x80070070) || error.HResult==unchecked((int)0x80070027)) return new[]{
                "The disk of this PC is full.","Make at least 4 GB of disk space free. Then run setup again."};
            bool network=error is WebException || stage.StartsWith("Downloading",StringComparison.Ordinal) ||
                stage.StartsWith("Installing Python",StringComparison.Ordinal) || stage.StartsWith("Installing Amplifier",StringComparison.Ordinal);
            return network
                ? new[]{"Setup could not finish this step: "+stage+". It downloads files from the internet during this step.",
                    "Check that this PC is connected to the internet. Then run setup again. If your organization filters downloads, github.com and pypi.org must be reachable."}
                : new[]{"Setup could not finish this step: "+stage+".","Run setup again."};
        }
        internal static string SaveDiagnostics(string root) {
            // The unfinished folder is removed. Keep its small private records for
            // support in a new private folder of the same kind, created exclusively
            // in the person's own temporary folder. Their text is never shown.
            try {
                var files=Directory.GetFiles(root,"install.log").Concat(Directory.GetFiles(root,"child-*")).Take(32).ToArray();
                if(files.Length==0) return null;
                string parent=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Temp");
                string name="AmplifierSetupLog-"+DateTime.Now.ToString("yyyyMMdd-HHmmss",CultureInfo.InvariantCulture)+
                    "-"+Guid.NewGuid().ToString("N").Substring(0,6);
                using(var storage=new InstallStorage(parent,name)) {
                    foreach(string file in files) {
                        if(new FileInfo(file).Length>1048576) continue;
                        using(var input=new FileStream(file,FileMode.Open,FileAccess.Read,FileShare.ReadWrite))
                        using(var output=storage.CreateFile(Path.Combine(storage.Root,Path.GetFileName(file)))) {
                            CopyBounded(input,output,1048576); output.Flush(true);
                        }
                    }
                    return storage.Root;
                }
            } catch(Exception) { return null; }
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
        static string InstallConsumer(string encoded, string root, string state) {
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
            string python=null,shortcut=null;
            bool created=false;
            try {
            if(state=="unfinished") {
                Stage="Removing an unfinished earlier setup";
                try { RemoveUnfinished(root); Aftermath="Setup removed the files of an earlier unfinished attempt."; }
                catch(Exception) {
                    Aftermath="Files of the earlier attempt remain in "+root+".";
                    throw new SetupStop(
                        "Setup could not remove the files of an earlier setup attempt in "+root+". A program may still use them.",
                        "Restart Windows. Then run setup again.");
                }
            }
            Stage="Preparing the Amplifier folder";
            using(Storage=new InstallStorage(Path.GetDirectoryName(root),RootName)) {
                created=true;
                Need(String.Equals(Storage.Root,root,StringComparison.OrdinalIgnoreCase),"Installation folder differs.");
                // The first file marks this folder as setup's own unfinished work.
                PrivateText(Path.Combine(root,Started),"{\"schema\":1}");
                LogPath=Path.Combine(root,"install.log");PrivateText(LogPath,"");
                foreach(string relative in new[]{"app","shared","ownership","capture","import","source-store","workspace","cache","python","python-bin","tools","tool-bin","temp"})
                    Storage.EnsureDirectory(Path.Combine(root,relative));
                foreach(string relative in new[]{"","cache","python","python-bin","tools","tool-bin","temp"})
                    Storage.EnableToolWrites(Path.Combine(root,relative));
                Stage="Downloading setup tools";
                string uv=Helper("uv",NativePins.UvUrl,NativePins.UvHash,"uv.exe");
                foreach(var license in NativePins.UvLicenses) {
                    string notice=Path.Combine(root,"uv",license.Key);
                    CopyOrDownload(NativePins.UvLicenseSource+"/"+license.Key,notice,1048576);
                    CheckHash(notice,license.Value);
                }
                string gitArchive=Path.Combine(root,"git.tar.bz2");
                CopyOrDownload(NativePins.ShellGitUrl,gitArchive,268435456); CheckHash(gitArchive,NativePins.ShellGitHash);
                string git=Path.Combine(root,"git","cmd","git.exe");
                var env=EnvironmentFor(uv,git);
                env["AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH"]=Path.Combine(root,"capture");
                env["AMPLIFIER_UNIFIED_IMPORT_HOME"]=Path.Combine(root,"import");
                env["AMPLIFIER_SOURCE_STORE"]=Path.Combine(root,"source-store");
                Stage="Installing Python";
                string patch=Text(plan,"python");
                Child(uv,new[]{"python","install",patch,"--no-bin","--no-registry"},env);
                string basePython=Child(uv,new[]{"python","find","--no-project","--managed-python",patch},env);
                Need(basePython.StartsWith(Path.Combine(root,"python")+"\\",StringComparison.OrdinalIgnoreCase),"Interpreter escaped private installation.");
                Child(basePython,new[]{"-I","-S","-c","import sys,sysconfig,struct; assert sys.platform=='win32' and '.'.join(map(str,sys.version_info[:3]))==sys.argv[1] and sys.version_info[:2]==(3,13) and struct.calcsize('P')==8 and not sysconfig.get_config_var('Py_GIL_DISABLED')",patch},env);
                Stage="Installing Git and shell tools (this takes a few minutes)";
                Need(PrivateGit(basePython,gitArchive,env)==git,"Private Git entry differs.");
                Stage="Downloading Amplifier";
                string wheel=Path.Combine(root,name);CopyOrDownload(Text(artifact,"url"),wheel,134217728);
                CheckHash(wheel,Text(artifact,"sha256"));
                Need(artifact["bytes"] is int && new FileInfo(wheel).Length==(int)artifact["bytes"],"Consumer artifact size differs.");
                string requirements=Path.Combine(root,"components.txt");
                PrivateText(requirements,ConsumerRequirements(plan["graph"]));
                Stage="Installing Amplifier (this takes a few minutes)";
                Child(uv,new[]{"tool","install","--python",basePython,"--link-mode","copy",
                    "--overrides",requirements,"--with-requirements",requirements,wheel},env);
                python=Path.Combine(root,"tools","amplifier-unified","Scripts","python.exe");
                Need(File.Exists(python),"Installed application interpreter missing.");
                PrivateText(Path.Combine(root,"consumer-install.json"),Json.Serialize(new Dictionary<string,object>{
                    {"schema",1},{"kind","windows-consumer-preview"},{"version",version},{"revision",Text(plan,"source")},
                    {"artifact",new Dictionary<string,object>{{"url",Text(artifact,"url")},{"sha256",Text(artifact,"sha256")},{"path",wheel}}},
                    {"graph",plan["graph"]},{"uv",uv},{"git",git}}));
                Stage="Checking the installation";
                Child(python,new[]{"-I","-B","-m","amplifier_web.windows_consumer","--root",root,"--audit"},env);
                Child(uv,new[]{"pip","check","--python",python},env);
                // The checked installation is usable from here. After its first
                // start it holds the person's chats and settings: a later setup
                // run opens this folder and never removes it.
                PrivateText(Path.Combine(root,Complete),Json.Serialize(new Dictionary<string,object>{{"schema",1},{"version",version}}));
                Installed=true;
                Stage="Adding Amplifier to the Start menu";
                Child(python,new[]{"-I","-B","-m","amplifier_web.windows_consumer","--root",root,"--integrate"},env);
                shortcut=ConsumerShortcut(python,root,"Launch Unified","");
                ConsumerShortcut(python,root,"Amplifier status"," --status");
                ConsumerShortcut(python,root,"Stop Amplifier"," --stop");
                ConsumerShortcut(python,root,"Remove Amplifier sign-in host"," --uninstall");
                Storage.Validate();
            }
            } catch(Exception) {
                // Storage has released its pins. One fixed folder means that an
                // unfinished attempt must not block the next one: remove what this
                // run created, unless it already is a usable installation.
                FailedStage=Stage;
                if(created && !Installed) {
                    Stage="Removing the unfinished files";
                    try { RemoveUnfinished(root); Aftermath="Setup removed the files of this unfinished attempt."; }
                    catch(Exception) { Aftermath="Unfinished files remain in "+root+"."; }
                }
                throw;
            }
            // Storage has released its pins. The verified archive is unpacked and
            // private-git.json records it; do not keep a second 118 MB copy.
            try {File.Delete(Path.Combine(root,"git.tar.bz2"));} catch(IOException) {} catch(UnauthorizedAccessException) {}
            // A finished installation must not keep the mark of unfinished work:
            // if its completion record were ever lost, setup must not remove it.
            try {File.Delete(Path.Combine(root,Started));} catch(IOException) {} catch(UnauthorizedAccessException) {}
            Stage="Starting Amplifier";
            Process.Start(AppStart(root));
            return shortcut;
        }
        static DialogResult Say(string text, MessageBoxButtons buttons, MessageBoxIcon icon) {
            return MessageBox.Show(text,"Amplifier Unified Setup",buttons,icon);
        }
        static void ShowStop(Exception error) {
            string[] text=Explain(error,FailedStage??Stage,Installed);
            Say("Setup did not finish.\n\nWhat happened:\n"+text[0]+"\n\nWhat to do:\n"+text[1]+
                (Installed?"":"\n\n"+Aftermath)+(Diagnostics==null?"":"\n\nDetails for support are in:\n"+Diagnostics),
                MessageBoxButtons.OK,MessageBoxIcon.Error);
        }
        static ProcessStartInfo AppStart(string root) {
            return new ProcessStartInfo(Path.Combine(root,"tools","amplifier-unified","Scripts","pythonw.exe"),
                "-I -m amplifier_web.windows_consumer --root "+Quote(root)) {
                    UseShellExecute=true,WorkingDirectory=Path.Combine(root,"workspace")};
        }
        static int OpenInstalled(string root) {
            // A second setup run never installs again or changes the installation.
            if(!File.Exists(AppStart(root).FileName)) {
                Say("Amplifier is already installed in "+root+", but setup cannot open it: a program file is missing.\n\n"+
                    "Keep that folder. It holds your chats and settings. This version of setup cannot repair it.",
                    MessageBoxButtons.OK,MessageBoxIcon.Warning);
                return 1;
            }
            bool listed=false;  // False after the person removed Amplifier in the Windows Apps list.
            try {
                var receipt=Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(root,"consumer-shell.json")));
                listed=receipt!=null && Text(receipt,"status")=="installed";
            } catch(Exception) {}
            if(Say(listed
                ? "Amplifier is already installed on this PC.\n\nSelect Yes to open it now.\n\n"+
                  "You can also open it from the Start menu: Amplifier Unified."
                : "Amplifier is already installed on this PC, but it is not in the Start menu and does not start when you sign in.\n\n"+
                  "Select Yes to start it again now. Your chats and settings are kept.\n\n"+
                  "To open it later, run this setup file again.",
                MessageBoxButtons.YesNo,MessageBoxIcon.Information)!=DialogResult.Yes) return 0;
            try {
                var start=AppStart(root);
                using(PinInstalled(root,start.FileName)) Process.Start(start);
                return 0;
            } catch(Exception) {
                Say("Setup could not start Amplifier. Open it from the Start menu: Amplifier Unified.",
                    MessageBoxButtons.OK,MessageBoxIcon.Warning);
                return 1;
            }
        }
        static int ConsumerWindow() {
            Application.EnableVisualStyles();
            // One setup per Windows account, across its sign-in sessions. A lock
            // that cannot be read counts as held: two setups must never work in
            // the one folder at the same time.
            bool first=false; string account; System.Threading.Mutex single=null;
            using(var identity=WindowsIdentity.GetCurrent()) account=identity.User.Value;
            try { single=new System.Threading.Mutex(true,"Global\\AmplifierUnifiedSetup-"+account,out first); }
            catch(Exception) {}
            using(single) {
                if(!first) {
                    Say("Amplifier setup is already running. Use the setup window that is already open.\n\n"+
                        "If you cannot find that window, restart Windows and run setup again.",
                        MessageBoxButtons.OK,MessageBoxIcon.Information);
                    return 2;
                }
                // Check the PC and the installation folder before asking anything:
                // a person must not confirm an installation that cannot start.
                string root,state;
                try { root=Precheck(out state); }
                catch(Exception error) { ShowStop(error); return 1; }
                if(state=="installed") return OpenInstalled(root);
                return InstallWindow(root,state);
            }
        }
        static int InstallWindow(string root, string state) {
            if(MessageBox.Show("Install Amplifier Unified for your Windows account? Setup downloads private prerequisites. "+
                "You do not need WSL, Python, Git or a terminal. This is an unsigned preview; Windows/browser trust "+
                "is separate and no security policy will be bypassed.","Amplifier Unified Setup",
                MessageBoxButtons.OKCancel,MessageBoxIcon.Information)!=DialogResult.OK) return 2;
            using(var form=new Form {Text="Amplifier Unified Setup",ClientSize=new Size(540,140),
                FormBorderStyle=FormBorderStyle.FixedDialog,MaximizeBox=false,MinimizeBox=false}) {
                var label=new Label {Left=20,Top=20,Width=500,Height=55,Text="Preparing the Amplifier folder..."};
                var bar=new ProgressBar {Left=20,Top=85,Width=500,Style=ProgressBarStyle.Marquee};
                form.Controls.Add(label);form.Controls.Add(bar);
                var timer=new System.Windows.Forms.Timer {Interval=250};
                timer.Tick+=(sender,e)=>{label.Text=Stage+"...\n\nKeep this window open. Setup needs no administrator rights.";};
                Task<string> work=null; int result=1;bool settled=false;
                form.FormClosing+=(sender,e)=>{if(!settled)e.Cancel=true;};
                form.Shown+=async(sender,e)=>{
                    timer.Start();work=Task.Run(()=>InstallConsumer(NativePins.ConsumerPlan,root,state));
                    try {
                        await work;result=0;
                        Say("Amplifier is installed.\n\nAmplifier now starts in the background and then opens in your browser. "+
                            "The first start can take a few minutes. Follow the messages that appear.\n\n"+
                            "Later, open Amplifier from the Start menu: Amplifier Unified.",
                            MessageBoxButtons.OK,MessageBoxIcon.Information);
                    } catch(Exception error) {
                        ShowStop(error);
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
