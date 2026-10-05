"""Bounded owned fixture from a standard portable Python distribution, not a venv.
No loader patches, bootstrap imports, provider calls or application service.
Usage: python3 script.py DONOR WHEEL NEW_OWNED_ROOT
"""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys

donor,wheel,owned=map(Path,sys.argv[1:])
assert donor.resolve()==donor and donor.is_dir() and not owned.exists()
assert hashlib.sha256(wheel.read_bytes()).hexdigest()=="2542d40ff4ff92a232df1f64c2a984d4bbfc8ff0a4963f3a724ebe86dd7e8af1"
assert shutil.disk_usage(owned.parent).free>10*1024**3
files={}
for d,dirs,names in os.walk(donor):
 for name in dirs+names:
  f=Path(d)/name
  if f.is_symlink():assert f.resolve().is_relative_to(donor)
  if name in names and f.is_file():files[str(f.relative_to(donor))]=hashlib.sha256(f.read_bytes()).hexdigest()
assert sum((donor/f).stat().st_size for f in files)<150*1024**2
owned.mkdir(mode=0o700);artifact=owned/"python-artifact";artifact.mkdir()
# Standard Python layout; only owned copies are changed. Keep required native
# libraries, stdlib and license/build data; omit donor tools, SDKs and packages.
shutil.copytree(donor/"lib",artifact/"lib",symlinks=False,ignore=shutil.ignore_patterns("site-packages","__pycache__","*.pyc"))
(artifact/"bin").mkdir();shutil.copy2(donor/"bin/python3.13",artifact/"bin/python3.13")
if (donor/"BUILD").is_file():shutil.copy2(donor/"BUILD",artifact/"BUILD")
for directory in artifact.rglob("*"):
 if directory.is_dir():directory.chmod(0o755)
site=artifact/"lib/python3.13/site-packages";site.mkdir()
# pip is a builder from the read-only donor; it installs only the exact wheel.
subprocess.run([str(donor/"bin/python3.13"),"-I","-B","-m","pip","--isolated","install","--no-index","--no-deps","--no-compile","--no-cache-dir","--disable-pip-version-check","--target",str(site),"--find-links",str(wheel.parent),"amplifier-publishing==0.1.1"],check=True,capture_output=True)
for d,dirs,names in os.walk(artifact):
 for name in names:
  f=Path(d)/name;assert f.is_file() and not f.is_symlink();f.chmod(0o755 if f.stat().st_mode&0o111 else 0o644)
assert sum(f.stat().st_size for f in artifact.rglob("*") if f.is_file())<150*1024**2
for path,digest in files.items():assert hashlib.sha256((donor/path).read_bytes()).hexdigest()==digest
(owned/"state").mkdir(mode=0o700)
(owned/"BUILD-RECEIPT.json").write_text(json.dumps({"fixtureOnly":True,"artifact":str(artifact),"wheelSha256":hashlib.sha256(wheel.read_bytes()).hexdigest(),"donorFilesUnchanged":len(files),"newEnvironment":False,"noBootstrap":True,"noServices":True},indent=2)+"\n")
print(str(artifact))
