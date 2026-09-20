import {request} from './api.js';
export async function readDetail(reference,read=request,signal){
 let offset=0,text='';
 do{const page=await read('/api/conversation/detail?'+new URLSearchParams({...reference,offset}),{signal});text+=page.value;offset=page.nextOffset}while(offset!==null);
 return text;
}
