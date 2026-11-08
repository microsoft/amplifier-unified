// Compile beside the actual native bootstrap sources with this explicit entry.
// Pure validation tests: no install root, network, process launch or policy edit.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;

namespace UnifiedBootstrap {
    internal static class NativeBootstrapTests {
        [DllImport("shell32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr CommandLineToArgvW(string command, out int count);
        [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr memory);
        static int assertions;
        static void Need(bool actual) { assertions++; if(!actual) throw new Exception("Native bootstrap assertion failed."); }
        static Dictionary<string,object> Valid() {
            return new Dictionary<string,object> {
                {"schemaVersion",1},{"status","prototype"},{"sourceMode","pinned-vcs"},
                {"source",NativePins.Source},{"version",NativePins.Version},{"architecture","x86_64"},
                {"python","3.13.17"},{"capsule",new Dictionary<string,object> {
                    {"url","https://github.com/microsoft/amplifier-unified/releases/download/reviewed-prototype/capsule.zip"},
                    {"sha256",new string('a',64)}
                }}
            };
        }
        static void Reject(Dictionary<string,object> plan) {
            bool refused=false;
            try {NativeInstaller.Plan(new JavaScriptSerializer().Serialize(plan));} catch(Exception){refused=true;}
            Need(refused);
        }
        static int Main() {
            var json=new JavaScriptSerializer();
            Need((string)NativeInstaller.Plan(json.Serialize(Valid()))["source"]==NativePins.Source);
            foreach(var pair in new[]{
                new KeyValuePair<string,object>("status","qualified"),
                new KeyValuePair<string,object>("schemaVersion",2),
                new KeyValuePair<string,object>("schemaVersion","1"),
                new KeyValuePair<string,object>("architecture","arm64"),
                new KeyValuePair<string,object>("sourceMode","offline-wheels"),
                new KeyValuePair<string,object>("source",new string('0',40)),
                new KeyValuePair<string,object>("version","99.0.0"),
                new KeyValuePair<string,object>("python","3.15.0")
            }) {var plan=Valid(); plan[pair.Key]=pair.Value; Reject(plan);}
            foreach(var url in new[]{
                "http://github.com/microsoft/amplifier-unified/releases/download/v1/a.zip",
                "https://example.invalid/microsoft/amplifier-unified/releases/download/v1/a.zip",
                "https://github.com:444/microsoft/amplifier-unified/releases/download/v1/a.zip",
                "https://user:password@github.com/microsoft/amplifier-unified/releases/download/v1/a.zip",
                "https://github.com/microsoft/amplifier-unified/releases/download/main/a.zip",
                "https://github.com/microsoft/amplifier-unified/releases/download/latest/a.zip",
                "https://github.com/microsoft/amplifier-unified/releases/download/v1/a.zip?token=synthetic",
                "https://github.com/microsoft/amplifier-unified/releases/download/v1/a.zip#fragment",
                "https://github.com/other/project/releases/download/v1/a.zip",
                "https://github.com/microsoft/amplifier-unified/releases/download/v1/a.exe"
            }) {var plan=Valid(); ((Dictionary<string,object>)plan["capsule"])["url"]=url; Reject(plan);}
            foreach(var digest in new[]{"",new string('a',63),new string('A',64),new string('g',64)}) {
                var plan=Valid();((Dictionary<string,object>)plan["capsule"])["sha256"]=digest;Reject(plan);
            }
            foreach(var value in new[]{"","a","a b","C:\\path with space\\","a\"b","\\\"","caf\u00e9 \u65e5\u672c\u8a9e","line\tvalue","--option=value"}) {
                int count;var memory=CommandLineToArgvW("test.exe "+NativeInstaller.Quote(value),out count);
                Need(memory!=IntPtr.Zero);
                try {Need(count==2);Need(Marshal.PtrToStringUni(Marshal.ReadIntPtr(memory,IntPtr.Size))==value);}
                finally {LocalFree(memory);}
            }
            Need(NativeInstaller.Main(new string[0])==1);
            Need(NativeInstaller.Main(new[]{"--prototype-manifest"})==1);
            Need(NativeInstaller.Main(new[]{"--background","--background"})==1);
            Console.WriteLine("{\"ok\":true,\"assertions\":"+assertions+",\"actualCSharpParserAndWindowsArgv\":true,\"networkOrInstallEffects\":false}");
            return 0;
        }
    }
}
