"""Opt-in stderr tails for synthetic browser fixtures, never production hosts."""
import asyncio
from collections import deque


class WorkerDiagnostics:
    LIMIT = 64 * 1024
    MODULES = {'amplifier_web.event_window_worker', 'amplifier_web.history_projection_worker'}

    def __init__(self):
        self.records = deque(maxlen=8)
        self.tasks = set()
        self.original_spawn = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            if len(args) < 3 or args[1] != '-m' or args[2] not in self.MODULES:
                return await self.original_spawn(*args, **kwargs)
            process = await self.original_spawn(*args, **{**kwargs, 'stderr': asyncio.subprocess.PIPE})
            record = {'module': args[2], 'pid': process.pid, 'bytes': 0, 'tail': b''}
            self.records.append(record)

            async def drain():
                while chunk := await process.stderr.read(4096):
                    record['bytes'] += len(chunk)
                    record['tail'] = (record['tail'] + chunk)[-self.LIMIT:]
            task = asyncio.create_task(drain())
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return process

        self.spawn = spawn
        asyncio.create_subprocess_exec = spawn

    def snapshot(self):
        return [{**{key: row[key] for key in ('module', 'pid', 'bytes')},
                 'stderr': row['tail'].decode('utf-8', errors='replace'),
                 'truncated': row['bytes'] > self.LIMIT} for row in self.records]

    async def close(self):
        if asyncio.create_subprocess_exec is self.spawn:
            asyncio.create_subprocess_exec = self.original_spawn
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
