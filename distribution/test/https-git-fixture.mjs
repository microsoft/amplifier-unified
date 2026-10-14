import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createServer } from "node:https";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
const execute = promisify(execFile);
export async function createHTTPSGitFixture() {
  const root = await mkdtemp(join(tmpdir(), "installer-git-")),
    repositories = join(root, "repositories"),
    repository = join(repositories, "fixture.git");
  await mkdir(repositories);
  const env = {
    ...process.env,
    GIT_CONFIG_GLOBAL: "/dev/null",
    GIT_CONFIG_NOSYSTEM: "1",
    GIT_CONFIG_COUNT: "0",
    GIT_TERMINAL_PROMPT: "0",
    GIT_AUTHOR_NAME: "Fixture",
    GIT_AUTHOR_EMAIL: "fixture@example.invalid",
    GIT_COMMITTER_NAME: "Fixture",
    GIT_COMMITTER_EMAIL: "fixture@example.invalid",
  };
  for (const key of Object.keys(env))
    if (/^GIT_CONFIG_(KEY|VALUE)_/.test(key)) delete env[key];
  await execute("git", ["init", "--bare", repository], { env });
  const git = async (args) =>
    (
      await execute("git", ["--git-dir", repository, ...args], { env })
    ).stdout.trim();
  const tree = await git(["hash-object", "-w", "-t", "tree", "/dev/null"]);
  const first = await git(["commit-tree", tree, "-m", "Fixture revision one"]);
  const second = await git([
    "commit-tree",
    tree,
    "-p",
    first,
    "-m",
    "Fixture revision two",
  ]);
  await git(["update-ref", "refs/heads/main", first]);
  const key = join(root, "key.pem"),
    certificate = join(root, "certificate.pem"),
    config = join(root, "openssl.cnf");
  await writeFile(
    config,
    "[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n",
  );
  await execute(
    "openssl",
    [
      "req",
      "-x509",
      "-newkey",
      "rsa:2048",
      "-nodes",
      "-days",
      "1",
      "-keyout",
      key,
      "-out",
      certificate,
      "-config",
      config,
    ],
    { env },
  );
  const children = new Set();
  let requests = 0;
  const server = createServer(
    { key: await readFile(key), cert: await readFile(certificate) },
    (req, res) => {
      const url = new URL(req.url, "https://localhost");
      if (
        !["/fixture.git/info/refs", "/fixture.git/git-upload-pack"].includes(
          url.pathname,
        ) ||
        !["GET", "POST"].includes(req.method)
      ) {
        res.writeHead(403);
        res.end();
        return;
      }
      requests++;
      const child = spawn("git", ["http-backend"], {
        env: {
          ...env,
          GIT_PROJECT_ROOT: repositories,
          GIT_HTTP_EXPORT_ALL: "1",
          PATH_INFO: url.pathname,
          QUERY_STRING: url.search.slice(1),
          REQUEST_METHOD: req.method,
          CONTENT_TYPE: req.headers["content-type"] ?? "",
          CONTENT_LENGTH: req.headers["content-length"] ?? "",
          SERVER_PROTOCOL: "HTTP/1.1",
          GIT_PROTOCOL: req.headers["git-protocol"] ?? "",
        },
        stdio: ["pipe", "pipe", "pipe"],
      });
      children.add(child);
      let pending = Buffer.alloc(0),
        headerDone = false;
      child.stderr.resume();
      child.on("error", () => {
        res.destroy();
      });
      child.stdout.on("data", (chunk) => {
        if (headerDone) {
          res.write(chunk);
          return;
        }
        pending = Buffer.concat([pending, chunk]);
        const index = pending.indexOf("\r\n\r\n");
        if (index < 0) return;
        const headers = pending.subarray(0, index).toString().split("\r\n");
        let status = 200;
        for (const line of headers) {
          const colon = line.indexOf(":");
          const name = line.slice(0, colon),
            value = line.slice(colon + 1).trim();
          if (name.toLowerCase() === "status")
            status = Number(value.split(" ")[0]);
          else res.setHeader(name, value);
        }
        res.writeHead(status);
        res.write(pending.subarray(index + 4));
        headerDone = true;
      });
      child.on("close", () => {
        children.delete(child);
        res.end();
      });
      req.pipe(child.stdin);
      child.stdin.on("error", () => {});
    },
  );
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return {
    repository: "https://127.0.0.1:" + server.address().port + "/fixture.git",
    first,
    second,
    env: {
      GIT_CONFIG_GLOBAL: "/dev/null",
      GIT_CONFIG_NOSYSTEM: "1",
      GIT_CONFIG_COUNT: "0",
      GIT_SSL_CAINFO: certificate,
    },
    advance: (revision) => git(["update-ref", "refs/heads/main", revision]),
    get requests() {
      return requests;
    },
    async close() {
      server.closeAllConnections();
      for (const child of children) child.kill("SIGTERM");
      await new Promise((resolve) => server.close(resolve));
      await rm(root, { recursive: true, force: true });
    },
  };
}
