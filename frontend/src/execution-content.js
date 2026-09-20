// Interpret only retained public tool fields. Unknown shapes stay inspectable.
export const textValue=value=>typeof value==='string'?value:value==null?'':JSON.stringify(value,null,2);
export function parsed(value){if(typeof value!=='string')return value;try{return JSON.parse(value)}catch{return value}}
export const record=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
export function resultValue(value){const row=record(value);return Object.hasOwn(row,'output')?row.output:value}
export function cleanSummary(value){
 const text=typeof value==='string'?value:'';
 return text.replace(/^Tool completed\. Expand any delegated actions below for their progress and results\.\s*/,'');
}
const first=(row,keys)=>keys.map(key=>row[key]).find(value=>typeof value==='string'&&value.length)||'';
const concise=value=>textValue(value).replace(/\s+/g,' ').slice(0,180);
export function actionContent(node,inputText=node.input,outputText=node.output){
 const input=parsed(inputText),args=record(input),result=parsed(outputText),output=resultValue(result),out=record(output);
 const tool=String(node.tool||node.label||'Tool'),name=tool.toLowerCase().replace(/.*[.:/]/,''),phase=node.status||node.phase;
 const running=['running','working','starting','pending','queued','retrying'].includes(phase);
 const failed=['error','failed','cancelled','interrupted'].includes(phase)||record(result).success===false;
 const done=['completed','complete','success','done'].includes(phase)&&!failed;
 const path=first(args,['file_path','path','filename']);
 const command=first(args,['command','cmd']);
 const patch=first(args,['patch','diff'])||first(out,['diff','patch'])||(/apply_patch/.test(name)&&typeof input==='string'?input:'');
 const before=first(args,['old_string','old_text']),after=first(args,['new_string','new_text']);
 const tasks=Array.isArray(args.todos)?args.todos:Array.isArray(out.todos)?out.todos:null;
 const summary=cleanSummary(node.summary||node.detail),fallback=summary.replace(new RegExp(`^(?:Completed|Running|Failed) ${tool.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}(?: · )?`),'');
 const exit=out.returncode??out.exit_code??out.exitCode;
 const verb=(past,active,other)=>done?past:running?active:other;
 const base={tool,input,args,result,output,out,phase,running,failed,done,path,exit,preview:concise(fallback)};
 if(command)return {...base,kind:'command',title:verb('Ran','Running','Run'),target:command,command,preview:typeof out.stdout==='string'?concise(out.stdout.trim().split('\n').filter(Boolean).at(-1))+(exit!=null?` · exit ${exit}`:''):concise(output)};
 if(patch||(/edit_file|apply_patch/.test(name)&&(before||after))){
  const diff=patch||['@@',...before.split('\n').map(line=>'-'+line),...after.split('\n').map(line=>'+'+line)].join('\n');
  const rows=diffRows(diff),files=[...new Set(rows.filter(row=>row.file).map(row=>row.file))];
  return {...base,kind:'patch',title:verb('Edited','Editing','Edit'),target:path||files.join(', ')||tool,diff,rows,added:rows.filter(row=>row.type==='add').length,removed:rows.filter(row=>row.type==='remove').length,preview:concise(output)};
 }
 if(tasks)return {...base,kind:'tasks',title:verb('Updated task list','Updating task list','Task list update'),tasks,preview:`${tasks.filter(task=>task?.status==='completed').length} of ${tasks.length} complete`};
 if(/read(_file)?$/.test(name)&&path)return {...base,kind:'read',title:verb('Read','Reading','Read'),target:path,content:typeof output==='string'?output:first(out,['content','text']),preview:args.offset!=null?`From line ${args.offset}${args.limit!=null?` · up to ${args.limit} lines`:''}`:concise(fallback)};
 if(/write(_file)?$/.test(name)&&path)return {...base,kind:'write',title:verb('Wrote','Writing','Write'),target:path,content:first(args,['content','text'])};
 if(typeof args.action==='string'||typeof args.operation==='string')return {...base,kind:'app',title:'App action',target:typeof args.action==='string'?args.action:args.operation,preview:concise(output)};
 if(/delegate|task|agent/.test(name)&&first(args,['instruction','instructions','prompt','task']))return {...base,kind:'delegate',title:verb('Delegated','Delegating','Delegate'),target:first(args,['agent','name','description']),task:first(args,['instruction','instructions','prompt','task'])};
 return {...base,kind:'generic',title:tool,preview:concise(fallback)};
}

export function diffRows(text){
 let oldLine=null,newLine=null,inHunk=false,oldRemaining=null,newRemaining=null;
 const reset=()=>{oldLine=newLine=oldRemaining=newRemaining=null;inHunk=false};
 return String(text).split('\n').map(text=>{
  const file=text.match(/^\*\*\* (?:Update|Add|Delete) File: (.+)$/);
  if(file){reset();return {type:'header',text,file:file[1]}}
  if(/^(?:diff --git |\*\*\*)/.test(text)){reset();return {type:'header',text}}
  if(!inHunk&&/^(?:--- |\+\+\+ )/.test(text))return {type:'header',text,...(text.startsWith('+++ ')&&text.slice(4)!=='/dev/null'?{file:text.slice(4).replace(/^b\//,'')}: {})};
  const hunk=text.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/);
  if(text.startsWith('@@')){
   oldLine=hunk?Number(hunk[1]):null;newLine=hunk?Number(hunk[3]):null;
   oldRemaining=hunk?Number(hunk[2]??1):null;newRemaining=hunk?Number(hunk[4]??1):null;
   inHunk=true;return {type:'header',text};
  }
  let row;
  if(text.startsWith('+')){row={type:'add',old:null,new:newLine===null?null:newLine++,text:text.slice(1)};if(newRemaining!==null)newRemaining--}
  else if(text.startsWith('-')){row={type:'remove',old:oldLine===null?null:oldLine++,new:null,text:text.slice(1)};if(oldRemaining!==null)oldRemaining--}
  else if(text.startsWith(' ')){row={type:'context',old:oldLine===null?null:oldLine++,new:newLine===null?null:newLine++,text:text.slice(1)};if(oldRemaining!==null)oldRemaining--;if(newRemaining!==null)newRemaining--}
  else return {type:'header',text};
  inHunk=!(oldRemaining===0&&newRemaining===0);
  return row;
 });
}
