import argparse
import asyncio
import json
from pathlib import Path
import sys
from .owner import Owner
MAX_FRAME=128*1024

class Peer:
    def __init__(self,config):self.tasks=set();self.owner=Owner(config,self.changed,self.idle)
    async def write(self,row):
        encoded=json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n'
        if len(encoded.encode())>MAX_FRAME:raise ValueError('Diagnostics frame exceeds128KiB')
        sys.stdout.write(encoded);sys.stdout.flush()
    async def changed(self):await self.write({'jsonrpc':'2.0','method':'owner/changed','params':{}})
    async def idle(self):await self.write({'jsonrpc':'2.0','method':'owner/idle','params':{}})
    async def handle(self,row):
        try:
            if row.get('jsonrpc')!='2.0' or not isinstance(row.get('params',{}),dict):raise ValueError('Invalid diagnostics request')
            result=await self.owner.request(row.get('method'),row.get('params',{}));await self.write({'jsonrpc':'2.0','id':row.get('id'),'result':result})
        except Exception as error:await self.write({'jsonrpc':'2.0','id':row.get('id'),'error':{'code':-32000,'message':str(error)[:300] if isinstance(error,ValueError) else 'Diagnostics operation failed; inspect the original receipt'}})
    async def run(self):
        reader=asyncio.StreamReader(limit=MAX_FRAME);protocol=asyncio.StreamReaderProtocol(reader);transport,_=await asyncio.get_running_loop().connect_read_pipe(lambda:protocol,sys.stdin)
        try:
            while line:=await reader.readline():
                if len(self.tasks)>=64:raise ValueError('Diagnostics request capacity reached')
                row=json.loads(line);task=asyncio.create_task(self.handle(row));self.tasks.add(task);task.add_done_callback(self.tasks.discard)
        finally:
            transport.close();await asyncio.gather(*list(self.tasks),return_exceptions=True);await self.owner.close()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);args=parser.parse_args();asyncio.run(Peer(json.loads(args.config.read_text())).run())
if __name__=='__main__':main()
