/** Stage independent targets concurrently. Every writer of a target must use
 * this scheduler instance; siblings settle before a primary failure escapes. */
export class DownloadScheduler {
  private tails = new Map<string, Promise<unknown>>();
  private active = 0;
  private waiters: {
    signal: AbortSignal;
    resolve: (release: () => void) => void;
    reject: (error: unknown) => void;
    abort: () => void;
  }[] = [];
  constructor(readonly concurrency = 4) {
    if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 16)
      throw Error("invalid_concurrency");
  }
  private acquire(signal: AbortSignal): Promise<() => void> {
    signal.throwIfAborted();
    if (this.active < this.concurrency) {
      this.active++;
      return Promise.resolve(() => this.release());
    }
    return new Promise((resolve, reject) => {
      const waiter = {
        signal,
        resolve,
        reject,
        abort: () => {
          const index = this.waiters.indexOf(waiter);
          if (index >= 0) this.waiters.splice(index, 1);
          reject(signal.reason);
        },
      };
      this.waiters.push(waiter);
      signal.addEventListener("abort", waiter.abort, { once: true });
    });
  }
  private release(): void {
    const waiter = this.waiters.shift();
    if (waiter) {
      waiter.signal.removeEventListener("abort", waiter.abort);
      waiter.resolve(() => this.release());
    } else this.active--;
  }
  async run<T>(
    jobs: { target: string; run: (signal: AbortSignal) => Promise<T> }[],
    signal?: AbortSignal,
  ): Promise<T[]> {
    if (jobs.length > 1000) throw Error("download_limit");
    const controller = new AbortController(),
      results: T[] = new Array(jobs.length);
    const abort = () => controller.abort(signal?.reason);
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
    let primary: unknown,
      failed = false;
    // Locks are acquired in submission order, globally across simultaneous
    // batches. Waiting for a target does not consume a download slot.
    const tasks = jobs.map((job, i) => {
      const previous = this.tails.get(job.target) ?? Promise.resolve();
      const task = previous
        .catch(() => {})
        .then(async () => {
          const release = await this.acquire(controller.signal);
          try {
            controller.signal.throwIfAborted();
            results[i] = await job.run(controller.signal);
          } finally {
            release();
          }
        });
      this.tails.set(job.target, task);
      return task
        .catch((error) => {
          if (!failed) {
            failed = true;
            primary = error;
          }
          controller.abort(error);
        })
        .finally(() => {
          if (this.tails.get(job.target) === task)
            this.tails.delete(job.target);
        });
    });
    try {
      await Promise.all(tasks);
      if (failed) throw primary;
      controller.signal.throwIfAborted();
      return results;
    } finally {
      signal?.removeEventListener("abort", abort);
    }
  }
}
