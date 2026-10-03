import { mkdir, writeFile, readFile } from "node:fs/promises";
import { join } from "node:path";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import { createServer } from "node:http";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import * as tar from "tar";
import { releaseDigest } from "../dist/index.js";
const execute = promisify(execFile),
  hash = (data) => createHash("sha256").update(data).digest("hex");
export const source = "https://example.com/distribution.git",
  dependencySource = "https://example.com/component.git";
export async function artifact(
  root,
  version,
  origin,
  serverCode = 'console.log("fixture")',
) {
  const dir = join(root, "source-" + version);
  await mkdir(join(dir, "node_modules", "fixture-component"), {
    recursive: true,
  });
  await writeFile(
    join(dir, "package.json"),
    JSON.stringify({
      name: "supervisor-distribution-fixture",
      version: `${version}.0.0`,
      type: "module",
      files: ["server.mjs"],
      dependencies: { "fixture-component": "1.0.0" },
      bundleDependencies: ["fixture-component"],
    }),
  );
  await writeFile(join(dir, "server.mjs"), serverCode);
  await writeFile(
    join(dir, "node_modules", "fixture-component", "package.json"),
    JSON.stringify({
      name: "fixture-component",
      version: "1.0.0",
      main: "index.js",
    }),
  );
  await writeFile(
    join(dir, "node_modules", "fixture-component", "index.js"),
    'module.exports = "installed-component";',
  );
  const packed = JSON.parse(
    (
      await execute(
        "npm",
        ["pack", "--ignore-scripts", "--json", "--pack-destination", root],
        { cwd: dir },
      )
    ).stdout,
  )[0];
  const path = join(root, packed.filename),
    files = [];
  await tar.t({
    file: path,
    onReadEntry: (entry) => {
      if (entry.type !== "File") {
        entry.resume();
        return;
      }
      const chunks = [];
      entry.on("data", (chunk) => chunks.push(chunk));
      entry.on("end", () => {
        const bytes = Buffer.concat(chunks);
        files.push({
          path: entry.path.replace(/^package\//, ""),
          sha256: hash(bytes),
          bytes: bytes.length,
          mode: entry.mode & 0o777,
        });
      });
    },
  });
  files.sort((a, b) => a.path.localeCompare(b.path));
  const bytes = await readFile(path),
    release = {
      identity: {
        id: `fixture-v${version}`,
        version: `${version}.0.0`,
        revision: String(version).repeat(40),
        digest: "",
      },
      artifact: {
        url: origin + `/fixture-v${version}.tgz`,
        sha256: hash(bytes),
        bytes: bytes.length,
      },
      entrypoint: "server.mjs",
      platform: "any",
      arch: "any",
      files,
      components: [
        {
          name: "supervisor-distribution-fixture",
          version: `${version}.0.0`,
          root: "",
          repository: source,
          ref: "main",
          revision: String(version).repeat(40),
        },
        {
          name: "fixture-component",
          version: "1.0.0",
          root: "node_modules/fixture-component",
          repository: dependencySource,
          ref: "main",
          revision: "d".repeat(40),
        },
      ],
    };
  release.identity.digest = releaseDigest(release);
  return { release, bytes, path };
}
export async function publisher() {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const resources = new Map();
  let current = 1,
    sourceProtected = false,
    dropSources = false;
  const server = createServer((req, res) => {
    if (req.url === "/sources") {
      if (dropSources) {
        req.socket.destroy();
        return;
      }
      res.setHeader("Content-Type", "application/json");
      res.end(
        JSON.stringify([
          {
            repository: source,
            ref: "main",
            revision: String(current).repeat(40),
            protected: sourceProtected,
          },
          {
            repository: dependencySource,
            ref: "main",
            revision: "d".repeat(40),
            protected: false,
          },
        ]),
      );
      return;
    }
    const bytes = resources.get(req.url);
    if (!bytes) {
      res.writeHead(404);
      res.end();
      return;
    }
    res.end(bytes);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  return {
    origin,
    resources,
    keys: { fixture: publicKey.export({ type: "spki", format: "pem" }) },
    setSources({
      version = current,
      protected: preserved = false,
      disconnect = false,
    }) {
      current = version;
      sourceProtected = preserved;
      dropSources = disconnect;
    },
    envelope(releases, recommendedId, expiresAt = Date.now() + 600000) {
      const payload = Buffer.from(
        JSON.stringify({
          schema: "distribution-channel-v1",
          expiresAt,
          releases,
          recommendedId,
        }),
      );
      return {
        schema: "distribution-signed-channel-v1",
        keyId: "fixture",
        payload: payload.toString("base64"),
        signature: sign(null, payload, privateKey).toString("base64"),
      };
    },
    publish(items, version) {
      current = version;
      for (const item of items)
        resources.set(
          `/fixture-v${item.release.identity.version.split(".")[0]}.tgz`,
          item.bytes,
        );
      const signed = this.envelope(
        items.map((item) => item.release),
        `fixture-v${version}`,
      );
      resources.set("/channel.json", Buffer.from(JSON.stringify(signed)));
      return signed;
    },
    async close() {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}
