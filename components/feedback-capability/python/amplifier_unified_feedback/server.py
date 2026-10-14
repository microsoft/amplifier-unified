import argparse
import asyncio
import json
from pathlib import Path
import sys
from .owner import Owner
MAX_FRAME=4_000_000

class Peer:
    def __init__(self,config):self.closing=False;self.pending={};self.counter=0;self.tasks=set();self.owner=Owner(config,self.host,self.notify)
    async def write(self,row):
        text=json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n'
        if len(text.encode())>MAX_FRAME:raise ValueError('Owner frame exceeds4MB; narrow the requested page')
        sys.stdout.write(text);sys.stdout.flush()
    async def notify(self,method,params):await self.write({'jsonrpc':'2.0','method':method,'params':params})
    async def host(self,method,params):
        if self.closing:raise ValueError('Host connection is closing; uncertain work is not replayed')
        self.counter+=1;identity='host:'+str(self.counter);future=asyncio.get_running_loop().create_future();self.pending[identity]=future
        try:
            await self.write({'jsonrpc':'2.0','id':identity,'method':'host/'+method,'params':params});return await future
        finally:self.pending.pop(identity,None)
    async def handle(self,row):
        identity=row.get('id')
        if not row.get('method'):
            future=self.pending.get(identity)
            if future and not future.done():
                if row.get('error'):future.set_exception(ValueError(row['error']['message']))
                else:future.set_result(row.get('result'))
            return
        try:result=await self.owner.request(row['method'],row.get('params',{}));await self.write({'jsonrpc':'2.0','id':identity,'result':result})
        except Exception as exc:await self.write({'jsonrpc':'2.0','id':identity,'error':{'code':-32000,'message':str(exc)[:2000],'data':{'code':getattr(exc,'code',None),'receipt':getattr(exc,'receipt',None),'status':503 if getattr(exc,'code',None)=='unknown_outcome' else 409}}})
    async def run(self):
        reader=asyncio.StreamReader(limit=MAX_FRAME+1);protocol=asyncio.StreamReaderProtocol(reader)
        transport,_=await asyncio.get_running_loop().connect_read_pipe(lambda:protocol,sys.stdin)
        try:
            while line:=await reader.readline():
                row=json.loads(line)
                if len(self.tasks)>=128:raise ValueError('Owner request capacity reached')
                task=asyncio.create_task(self.handle(row));self.tasks.add(task);task.add_done_callback(self.tasks.discard)
        finally:
            transport.close()
            self.closing=True
            # Request handlers may still write command receipts after a host callback.
            # Stop them before the owner closes its database/background jobs.
            tasks=list(self.tasks)
            for task in tasks:task.cancel()
            for future in list(self.pending.values()):
                if not future.done():future.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            await self.owner.close()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);args=parser.parse_args()
    asyncio.run(Peer(json.loads(args.config.read_text())).run())
if __name__=='__main__':main()
