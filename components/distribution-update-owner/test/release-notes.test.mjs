import { publisher, artifact } from "./release-fixtures.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes, generateKeyPairSync, sign } from "node:crypto";
import {
  DistributionUpdateOwner,
  parseReleaseNotes,
  noticeDigest,
  mergeReleaseNotes,
  MAX_RELEASE_NOTES_BYTES,
  readSignedChannel,
  SupervisorClient,
  serveSupervisor,
  SignedReleaseAdapter,
  newestNotesFirst,
} from "../dist/index.js";
const a = {
  id: "one",
  version: "1.0.0",
  revision: "a".repeat(40),
  digest: "a".repeat(64),
};
const b = {
  id: "two",
  version: "2.0.0",
  revision: "b".repeat(40),
  digest: "b".repeat(64),
};
const entry = (version = "1.0.0") => ({
  version,
  title: "Faster manual updates",
  changes: ["Checks dispatch immediately."],
  notices: [
    {
      id: "manual-checks",
      title: "Immediate update checks",
      detail: "Checks bypass cached completed results.",
      action: "Select Check for updates.",
    },
  ],
});
const publication = (entries = [entry()]) => ({
  schema: "distribution-release-notes-publication-v1",
  entries,
});
const review = (page, index = 0) => ({
  version: page.entries[index].version,
  noticeId: page.entries[index].notices[0].id,
  contentDigest: page.entries[index].notices[0].contentDigest,
});
async function setup(t) {
  const directory = await mkdtemp(join(tmpdir(), "release-notes-"));
  let owner,
    events = [],
    checks = 0,
    networkError = false,
    notes = publication(),
    gate;
  const options = {
    directory,
    dataScope: "fixture",
    initial: { identity: a, handle: "one" },
    releases: {
      check: async () => {
        checks++;
        if (gate) await gate;
        if (networkError) throw Error("release_fetch_failed");
        return { releases: [a, b], recommendedId: b.id, releaseNotes: notes };
      },
      prepare: async () => {
        throw Error("must not prepare");
      },
      verify: async () => true,
    },
    lifecycle: {
      inspect: async () => null,
      admitRestart: async () => null,
      restart: async () => {
        throw Error("must not restart");
      },
    },
    onChange: (r) => events.push(r),
  };
  owner = new DistributionUpdateOwner(options);
  t.after(async () => {
    await owner.close();
    await rm(directory, { recursive: true, force: true });
  });
  return {
    get owner() {
      return owner;
    },
    options,
    events,
    get checks() {
      return checks;
    },
    set notes(v) {
      notes = v;
    },
    set networkError(v) {
      networkError = v;
    },
    set gate(v) {
      gate = v;
    },
    async check(id = "check") {
      owner.check(id);
      return owner.waitFor(id);
    },
    async reopen() {
      await owner.close();
      owner = new DistributionUpdateOwner(options);
      return owner;
    },
  };
}
test("history and exact reviews persist offline; unchanged check does not invalidate pages", async (t) => {
  const f = await setup(t);
  f.notes = publication([
    entry("2.0.0"),
    entry("1.10.0"),
    entry("1.9.0"),
    entry(),
  ]);
  assert.equal((await f.check()).status, "succeeded");
  const page = f.owner.releaseNotes({ limit: 2 });
  assert.deepEqual(
    page.entries.map((e) => e.version),
    ["2.0.0", "1.10.0"],
  );
  assert.equal(page.currentVersion, "1.0.0");
  assert.equal(page.recommendedVersion, "2.0.0");
  assert.equal(page.unreviewedCount, 4);
  assert.equal(f.owner.inspect().releaseNotes.entries, undefined);
  assert.equal(f.owner.inspect().catalog.releaseNotes, undefined);
  await f.check("same-check");
  assert.equal(f.owner.releaseNotes().revision, page.revision);
  assert.deepEqual(
    f.owner
      .releaseNotes({ cursor: page.nextCursor, limit: 2 })
      .entries.map((e) => e.version),
    ["1.9.0", "1.0.0"],
  );
  const args = review(page),
    receipt = f.owner.reviewNotice("review", args);
  assert.equal(receipt.status, "succeeded");
  assert.deepEqual(receipt.noticeReview, args);
  assert.equal(f.events.at(-1).phase, "notice_reviewed");
  assert.throws(
    () => f.owner.releaseNotes({ cursor: page.nextCursor }),
    /cursor_stale/,
  );
  assert.deepEqual(f.owner.reviewNotice("review", args), receipt);
  assert.throws(
    () => f.owner.reviewNotice("review", { ...args, noticeId: "another" }),
    /identity_conflict/,
  );
  await f.reopen();
  assert.equal(f.owner.releaseNotes().unreviewedCount, 3);
  assert.deepEqual(f.owner.receipt("review"), receipt);
  const before = f.checks;
  f.owner.releaseNotes();
  f.owner.inspect();
  assert.equal(f.checks, before);
  f.networkError = true;
  assert.equal((await f.check("offline")).status, "failed");
  assert.equal(f.owner.releaseNotes().unreviewedCount, 3);
  assert.equal(f.owner.releaseNotes().entries.length, 4);
});
test("only explicit editorial title aliases preserve review; stale content cannot be acknowledged", async (t) => {
  const f = await setup(t);
  await f.check();
  const args = review(f.owner.releaseNotes());
  f.owner.reviewNotice("old", args);
  const corrected = entry();
  corrected.notices[0].previousTitles = [corrected.notices[0].title];
  corrected.notices[0].title = "Manual checks respond promptly";
  f.notes = publication([corrected]);
  await f.check("corrected");
  assert.equal(f.owner.releaseNotes().unreviewedCount, 0);
  corrected.notices[0].action = "Review updated configuration first.";
  await f.check("changed");
  assert.equal(f.owner.releaseNotes().unreviewedCount, 1);
  const refused = f.owner.reviewNotice("stale", args);
  assert.equal(refused.status, "failed");
  assert.equal(refused.errorCode, "notice_changed");
  assert.equal(f.owner.releaseNotes().unreviewedCount, 1);
  const missing = f.owner.reviewNotice("missing", {
    ...args,
    noticeId: "missing",
  });
  assert.equal(missing.errorCode, "notice_not_found");
  await f.reopen();
  assert.deepEqual(f.owner.receipt("stale"), refused);
  assert.equal(f.owner.releaseNotes().unreviewedCount, 1);
});
test("review commits immediately while a remote check is pending and does not replay it", async (t) => {
  const f = await setup(t);
  await f.check();
  const args = review(f.owner.releaseNotes());
  let release;
  f.gate = new Promise((r) => (release = r));
  f.owner.check("slow");
  await new Promise((r) => setImmediate(r));
  try {
    const receipt = f.owner.reviewNotice("during-check", args);
    assert.equal(receipt.phase, "notice_reviewed");
    assert.equal(f.owner.receipt("slow").status, "running");
    assert.equal(f.owner.releaseNotes().unreviewedCount, 0);
  } finally {
    release();
  }
  await f.owner.waitFor("slow");
  assert.equal(f.owner.releaseNotes().unreviewedCount, 0);
});
test("missing or malformed optional notes preserve verified history and fallback without blocking checks", async (t) => {
  const f = await setup(t);
  await f.check();
  f.notes = { schema: "bad", secret: "never expose me" };
  assert.equal((await f.check("bad-notes")).status, "succeeded");
  const page = f.owner.releaseNotes();
  assert.equal(page.warning, "release_notes_invalid");
  assert.equal(page.entries.length, 1);
  assert.match(
    page.publishedReleasesUrl,
    /^https:\/\/github.com\/microsoft\/amplifier-unified\/releases$/,
  );
  assert.ok(!JSON.stringify(f.owner.diagnostics()).includes("never expose"));
  f.notes = undefined;
  assert.equal((await f.check("absent-notes")).status, "succeeded");
  assert.equal(f.owner.releaseNotes().warning, "release_notes_unavailable");
  assert.equal(f.owner.releaseNotes().entries.length, 1);
});
test("bounded parser rejects excessive text, duplicate identities and unsafe links; projection is plain data", () => {
  for (const mutate of [
    (e) => e.notices.push(e.notices[0]),
    (e) => (e.notices[0].title = "x".repeat(161)),
    (e) => (e.notices[0].previousTitles = Array(6).fill("old")),
    (e) => (e.publishedUrl = "javascript:alert(1)"),
    (e) => (e.publishedUrl = "https://user:secret@example.test/"),
    (e) => (e.publishedUrl = "https://example.test/?token=secret"),
    (e) => (e.publishedUrl = "https://example.test/#x"),
    (e) => (e.version = "../escape"),
    (e) => (e.changes = []),
    (e) => (e.notices[0].id = "bad/id"),
  ]) {
    const e = entry();
    mutate(e);
    assert.throws(() => parseReleaseNotes(publication([e])));
  }
  assert.throws(() => parseReleaseNotes(publication([entry(), entry()])));
  assert.throws(() =>
    parseReleaseNotes(
      publication(Array.from({ length: 101 }, (_, i) => entry(`${i}.0.0`))),
    ),
  );
  const p = parseReleaseNotes(publication());
  assert.equal(
    p.entries[0].publishedUrl,
    "https://github.com/microsoft/amplifier-unified/releases/tag/v1.0.0",
  );
  const changed = { ...p.entries[0].notices[0], action: "A changed action" };
  assert.notEqual(
    noticeDigest("1.0.0", changed),
    noticeDigest("1.0.0", p.entries[0].notices[0]),
  );
});
test("retained history is bounded by versions and UTF-8 bytes while preserving current notes", () => {
  const old = parseReleaseNotes(
    publication(Array.from({ length: 100 }, (_, i) => entry(`${i}.0.0`))),
  );
  const result = mergeReleaseNotes(
    old,
    parseReleaseNotes(publication([entry("100.0.0")])),
    "0.0.0",
  );
  assert.equal(result.entries.length, 100);
  assert.ok(result.entries.some((e) => e.version === "0.0.0"));
  assert.ok(result.entries.some((e) => e.version === "100.0.0"));
  assert.ok(!result.entries.some((e) => e.version === "1.0.0"));
  const long = (i) => ({
    ...entry(`${i}.0.0`),
    changes: Array(20).fill("é".repeat(1000)),
  });
  const large = mergeReleaseNotes(
    parseReleaseNotes(
      publication(Array.from({ length: 6 }, (_, i) => long(i))),
    ),
    parseReleaseNotes(
      publication(Array.from({ length: 6 }, (_, i) => long(i + 6))),
    ),
    "0.0.0",
  );
  assert.ok(
    Buffer.byteLength(JSON.stringify(large)) <= MAX_RELEASE_NOTES_BYTES,
  );
  assert.ok(large.entries.some((e) => e.version === "0.0.0"));
});
test("signed editorial payload is authenticated, malformed notes do not corrupt release validity", () => {
  const keys = generateKeyPairSync("ed25519"),
    trusted = {
      fixture: keys.publicKey.export({ type: "spki", format: "pem" }),
    };
  const envelope = (notes) => {
    const payload = Buffer.from(
      JSON.stringify({
        schema: "distribution-channel-v1",
        expiresAt: Date.now() + 60000,
        recommendedId: null,
        releases: [],
        releaseNotes: notes,
      }),
    );
    return {
      schema: "distribution-signed-channel-v1",
      keyId: "fixture",
      payload: payload.toString("base64"),
      signature: sign(null, payload, keys.privateKey).toString("base64"),
    };
  };
  const good = envelope(publication());
  assert.equal(
    readSignedChannel(good, trusted).channel.releaseNotes.entries.length,
    1,
  );
  const tampered = {
    ...good,
    payload: Buffer.from(
      Buffer.from(good.payload, "base64")
        .toString()
        .replace("Immediate", "Changed"),
    ).toString("base64"),
  };
  assert.throws(() => readSignedChannel(tampered, trusted), /untrusted/);
  assert.equal(
    readSignedChannel(envelope({ unexpected: "secret" }), trusted).channel
      .releaseNotesWarning,
    "release_notes_invalid",
  );
});
test("authenticated transport exposes notes, exact review receipts and sanitized errors", async (t) => {
  const f = await setup(t);
  await f.check();
  const token = randomBytes(32).toString("hex"),
    server = await serveSupervisor({ owner: f.owner, token }),
    client = new SupervisorClient({ url: server.url, token });
  try {
    const page = await client.owner.releaseNotes({ limit: 1 });
    const r = await client.owner.reviewNotice("rpc-review", review(page));
    assert.equal(r.status, "succeeded");
    assert.equal(
      (await client.owner.receipt("rpc-review")).noticeReview.contentDigest,
      page.entries[0].notices[0].contentDigest,
    );
    await assert.rejects(
      client.owner.releaseNotes({ limit: 21 }),
      /release_notes_query_invalid/,
    );
    await assert.rejects(
      client.rpc("review-notice", { ...review(page), unknown: "bad" }, "bad"),
      /invalid_request/,
    );
  } finally {
    client.close();
    await server.close();
  }
});
test("weekly automatic intervals are valid without delaying manual checks", async (t) => {
  const f = await setup(t);
  f.owner.setPreferences("weekly", {
    autoCheck: true,
    autoInstall: false,
    intervalMs: 604800000,
  });
  assert.equal((await f.owner.waitFor("weekly")).status, "succeeded");
  assert.equal((await f.check()).status, "succeeded");
  assert.throws(
    () =>
      f.owner.setPreferences("too-long", {
        autoCheck: true,
        autoInstall: false,
        intervalMs: 604800001,
      }),
    /invalid_preferences/,
  );
});
test("installed release notes hydrate offline once, without release checks or overwriting newer history", async (t) => {
  const f = await setup(t);
  let loads = 0;
  f.options.releases.notes = async () => {
    loads++;
    return { releaseNotes: parseReleaseNotes(publication()) };
  };
  await f.owner.loadInstalledReleaseNotes();
  assert.equal(f.owner.releaseNotes().entries.length, 1);
  assert.equal(f.checks, 0);
  f.notes = publication([entry("2.0.0")]);
  await f.check();
  await f.reopen();
  await f.owner.loadInstalledReleaseNotes();
  assert.equal(loads, 1);
  assert.equal(f.owner.releaseNotes().entries.length, 2);
});

test("exact signed installed receipt supplies offline history with no source or network observation", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "signed-offline-notes-")),
    pub = await publisher();
  t.after(async () => {
    await pub.close();
    await rm(root, { recursive: true, force: true });
  });
  const item = await artifact(root, 1, pub.origin);
  pub.publish([item], 1);
  pub.resources.set(
    "/channel.json",
    Buffer.from(
      JSON.stringify(
        pub.envelope(
          [item.release],
          item.release.identity.id,
          Date.now() + 60000,
          publication(),
        ),
      ),
    ),
  );
  let fetches = 0,
    offline = false,
    sources = 0;
  const adapter = new SignedReleaseAdapter({
    directory: join(root, "releases"),
    channelUrl: pub.origin + "/channel.json",
    trustedKeys: pub.keys,
    accessScope: "fixture",
    allowedArtifactOrigins: [pub.origin],
    allowLoopbackHttp: true,
    fetch: async (...args) => {
      fetches++;
      if (offline) throw Error("offline");
      return fetch(...args);
    },
    resolveSources: async (components) => {
      sources++;
      return components.map((c) => ({ ...c, protected: false }));
    },
  });
  const context = { commandId: "test", signal: new AbortController().signal };
  const checked = await adapter.check({ ...context, fresh: true });
  assert.equal(fetches, 1);
  assert.equal(checked.releaseNotes.entries.length, 1);
  const target = await adapter.prepare(item.release.identity, context),
    priorFetches = fetches,
    priorSources = sources;
  offline = true;
  assert.equal(
    (await adapter.notes(target)).releaseNotes.entries[0].version,
    "1.0.0",
  );
  assert.equal(fetches, priorFetches);
  assert.equal(sources, priorSources);
  await assert.rejects(
    adapter.notes({
      ...target,
      identity: { ...target.identity, version: "2.0.0" },
    }),
    /receipt_invalid/,
  );
});

test("release history orders multi-digit and prerelease versions deterministically", () => {
  const versions = [
    "1.0.0-beta.2",
    "1.0.0-beta.10",
    "1.10.0",
    "1.9.0",
    "1.0.0",
    "1.0.0-beta",
    "1.0.0-alpha",
  ];
  assert.deepEqual(
    versions
      .map((v) => entry(v))
      .sort(newestNotesFirst)
      .map((e) => e.version),
    [
      "1.10.0",
      "1.9.0",
      "1.0.0",
      "1.0.0-beta.10",
      "1.0.0-beta.2",
      "1.0.0-beta",
      "1.0.0-alpha",
    ],
  );
});
