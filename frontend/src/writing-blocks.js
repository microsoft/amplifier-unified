const variants=new Set(['standard','document','email','chat_message','social_post']);
export function writingParts(text){
 const lines=String(text||'').split('\n'),parts=[];let plain=[],fence=null;
 const flush=()=>{if(plain.length){parts.push({type:'text',text:plain.join('\n')});plain=[]}};
 for(let i=0;i<lines.length;i++){
  const line=lines[i],code=/^\s{0,3}(`{3,}|~{3,})/.exec(line);
  if(code){if(!fence)fence=code[1];else if(code[1][0]===fence[0]&&code[1].length>=fence.length)fence=null;plain.push(line);continue}
  const match=!fence&&/^:::writing\{([^{}]*)\}\s*$/.exec(line);
  if(!match){plain.push(line);continue}
  const attrs={},pattern=/([a-z]+)="([^"\r\n]*)"/g;let item;while((item=pattern.exec(match[1])))attrs[item[1]]=item[2];
  const residue=match[1].replace(pattern,'').trim();
  if(residue||!variants.has(attrs.variant)||!/^\d{5}$/.test(attrs.id)||Object.keys(attrs).some(key=>!['variant','id','subject','recipient','cc','bcc'].includes(key))){plain.push(line);continue}
  let end=i+1;for(;end<lines.length&&lines[end]!==':::';end++);
  if(end===lines.length){plain.push(line);continue}
  flush();parts.push({type:'writing',text:lines.slice(i+1,end).join('\n'),...attrs});i=end;
 }
 flush();return parts;
}
