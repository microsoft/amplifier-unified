import test from 'node:test';
import assert from 'node:assert/strict';
import {actionContent,diffRows,cleanSummary} from '../src/execution-content.js';
const node=(label,input,output,phase='completed')=>({label,input:JSON.stringify(input),output:JSON.stringify(output),phase});
test('bash keeps real command, stdout, stderr and exit status including failure',()=>{
 const a=actionContent(node('bash',{command:'python -m pytest -q'},{success:false,output:{stdout:'collected 2 items\n1 failed',stderr:'trace',returncode:1}}));
 assert.equal(a.kind,'command');assert.equal(a.title,'Run');assert.equal(a.target,'python -m pytest -q');assert.equal(a.exit,1);assert.equal(a.out.stderr,'trace');assert.match(a.preview,/1 failed · exit 1/);
});
test('read, app and todo preserve actual content instead of boilerplate',()=>{
 const read=actionContent(node('read_file',{file_path:'README.md',offset:2,limit:5},{success:true,output:{content:'2  actual text'}}));
 assert.equal(read.kind,'read');assert.equal(read.content,'2  actual text');assert.equal(read.target,'README.md');
 const todo=actionContent(node('todo',{todos:[{content:'Inspect',status:'completed'},{content:'Review',status:'pending'}]},{success:true,output:{completed:1,count:2}}));
 assert.equal(todo.kind,'tasks');assert.equal(todo.preview,'1 of 2 complete');assert.equal(todo.tasks[1].content,'Review');
 const app=actionContent(node('app_control',{action:'session.rename',title:'Review'},{success:true,output:{title:'Review'}}));
 assert.equal(app.kind,'app');assert.equal(app.target,'session.rename');assert.equal(app.args.title,'Review');
});
test('patches have accurate counts and only report line numbers actually present',()=>{
 const a=actionContent(node('apply_patch',{patch:'*** Begin Patch\n*** Update File: code.py\n@@\n old\n-before\n+after\n+more\n*** End Patch'},{success:true}));
 assert.equal(a.target,'code.py');assert.equal(a.added,2);assert.equal(a.removed,1);
 assert.equal(a.rows.find(r=>r.type==='add').new,null);
 const rows=diffRows('--- a/file\n+++ b/file\n@@ -12,2 +15,2 @@\n context\n-old\n+new');
 assert.equal(diffRows('@@ -1 +1 @@\n--- heading\n+++ heading').filter(r=>r.type==='remove'||r.type==='add').length,2);
 assert.equal(rows.find(r=>r.type==='remove').old,13);assert.equal(rows.find(r=>r.type==='add').new,16);
 assert.equal(actionContent(node('apply_patch',{patch:'-old\n+new'},{success:false},'error')).title,'Edit');
});
test('unknown, missing, truncated, string and malicious-looking content remain literal',()=>{
 const legacy=actionContent({label:'bash',phase:'completed',summary:'Tool completed. Expand any delegated actions below for their progress and results.'});
 assert.equal(legacy.kind,'generic');assert.equal(legacy.preview,'');
 assert.equal(cleanSummary('A real report'),'A real report');
 assert.equal(actionContent({label:'fixture[read]',summary:'Completed fixture[read] · inspect',input:'{"path": "truncated'}).preview,'inspect');
 const unknown=actionContent(node('custom_tool',{thing:'<script>bad()</script>'},{success:true,output:'<img src=x onerror=bad()>'}));
 assert.equal(unknown.kind,'generic');assert.match(unknown.output,/onerror/);
 assert.equal(actionContent(node('bash',{command:'true'},'')).output,'');
});
