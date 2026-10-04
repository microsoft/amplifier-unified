import { createHash } from "node:crypto";
/** Availability only: no cache entry is installation qualification. Pass an
 * opaque access-scope hash, never persist credentials or credential paths. */
export class AvailabilityCache {
  private entries = new Map<string, { at: number; value: unknown }>();
  private flights = new Map<string, Promise<unknown>>();
  constructor(
    readonly limit = 512,
    readonly maxValueBytes = 65536,
  ) {
    if (
      !Number.isSafeInteger(maxValueBytes) ||
      maxValueBytes < 1 ||
      maxValueBytes > 16 * 1024 * 1024
    )
      throw Error("invalid_cache_value_limit");
    if (!Number.isInteger(limit) || limit < 1 || limit > 4096)
      throw Error("invalid_cache_limit");
  }
  async get<T>(
    source: string,
    scope: string,
    loader: () => Promise<T>,
    options: { fresh: boolean; ttlMs: number },
  ): Promise<T> {
    if (
      typeof options.fresh !== "boolean" ||
      !Number.isSafeInteger(options.ttlMs) ||
      options.ttlMs < 0 ||
      options.ttlMs > 86400000
    )
      throw Error("invalid_cache_options");
    const key = createHash("sha256")
      .update(JSON.stringify([source, scope]))
      .digest("hex");
    const flight = this.flights.get(key);
    if (flight) return structuredClone(await flight) as T;
    const entry = this.entries.get(key);
    if (
      !options.fresh &&
      entry &&
      Date.now() - entry.at >= 0 &&
      Date.now() - entry.at < options.ttlMs
    ) {
      this.entries.delete(key);
      this.entries.set(key, entry);
      return structuredClone(entry.value) as T;
    }
    if (this.flights.size >= this.limit) throw Error("cache_inflight_limit");
    const task = Promise.resolve()
      .then(loader)
      .then((value) => {
        if (Buffer.byteLength(JSON.stringify(value)) > this.maxValueBytes)
          throw Error("cache_value_limit");
        this.entries.delete(key);
        this.entries.set(key, {
          at: Date.now(),
          value: structuredClone(value),
        });
        while (this.entries.size > this.limit)
          this.entries.delete(this.entries.keys().next().value!);
        return value;
      })
      .catch((error) => {
        this.entries.delete(key);
        throw error;
      })
      .finally(() => this.flights.delete(key));
    this.flights.set(key, task);
    return structuredClone(await task);
  }
}
