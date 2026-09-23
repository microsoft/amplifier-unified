import React from 'react';
import {AlertCircle,Check} from 'lucide-react';
import {ReadWhenVisible} from './attention.jsx';

const identity=(release,notice)=>`release-notice:${release.version}:${notice.id}`;
const version=value=>String(value||'').replace(/^v/,'').split('.').map(Number);
const newer=(a,b)=>{const x=version(a),y=version(b);for(let i=0;i<3;i++){if(x[i]!==y[i])return x[i]>y[i]}return false};
const status=(release,application)=>release.version===application.current?'Installed':newer(release.version,application.current)?'Upcoming':'Earlier release';
function Notice({notice}){
 return <><strong>{notice.title}</strong><p>{notice.detail}</p><p><b>What to do:</b> {notice.action}</p></>;
}

export function ReleaseNotices({application,state,act}){
 const attention=new Map((state.attention?.items||[]).map(item=>[item.id,item]));
 const unread=(application.releaseNotes||[]).flatMap(release=>release.notices.map(notice=>({release,notice,item:attention.get(identity(release,notice))}))).filter(({item})=>item);
 if(!unread.length)return null;
 return <section className="a-release-notices" aria-label="High-impact changes">
  <h4><AlertCircle aria-hidden="true"/>High-impact changes</h4>
  <p className="a-caption">Changes that may affect your settings or workflow. Notices are marked read as you view them.</p>
  {unread.map(({release,notice,item})=><article key={item.id} className="a-release-notice">
   <span className="a-caption">Version {release.version} · {status(release,application)}</span>
   <ReadWhenVisible item={item} act={act}><Notice notice={notice}/></ReadWhenVisible>
  </article>)}
 </section>;
}

export function ReleaseHistory({application,state,act}){
 const entries=application.releaseNotes||[];
 const reviewed=new Set((state.attention?.items||[]).filter(item=>item.read).map(item=>item.id));
 // Construct URLs from stable versions, never from release-authored text.
 const tag=/^v?\d+\.\d+\.\d+$/.test(application.latest||'')?application.latest:null;
 return <section className="a-release-history" aria-label="Changelog">
  <details className="a-update-disclosure"><summary>Changelog</summary>
  <div className="a-update-disclosure-body">
  <p className="a-caption">Application release history · installed notes are available offline.</p>
  {application.releaseNotesWarning&&<p className="a-release-notes-warning" role="status">{application.releaseNotesWarning} {tag&&<a href={'https://github.com/microsoft/amplifier-unified/releases/tag/'+tag} target="_blank" rel="noreferrer">View published release</a>}</p>}
  {!entries.length&&<p className="a-caption">No release notes are included with this installation.</p>}
  {entries.map(release=><details key={release.version} className="a-release-entry" open={release.version===application.current||newer(release.version,application.current)}>
   <summary><strong>{release.version} · {release.title}</strong><span className="a-caption">{status(release,application)}</span>{release.notices.length>0&&<span className="a-release-impact">High impact</span>}</summary>
   <ul>{release.changes.map((change,i)=><li key={i}>{change}</li>)}</ul>
   {release.notices.map(notice=><div key={notice.id} className="a-release-notice archived"><ReadWhenVisible item={(state.attention?.items||[]).find(item=>item.id===identity(release,notice))} act={act}><Notice notice={notice}/></ReadWhenVisible>{reviewed.has(identity(release,notice))&&<span className="a-release-reviewed"><Check aria-hidden="true"/>Reviewed</span>}</div>)}
  </details>)}
  </div></details>
 </section>;
}
