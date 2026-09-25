// Summarize the authoritative sequence; never infer all-current from the app alone.
export function updateOverview(updates={},options={}){
 const app=updates.application||(updates.items||[]).find(i=>i.kind==='app')||{},seq=updates.sequence||{};
 const rows=(updates.items||[]).filter(i=>i.kind!=='app'&&i.id!=='application');
 const issues=rows.filter(i=>['check_failed','local_changes'].includes(i.status));
 const components=Math.max(rows.filter(i=>i.status==='update').length,(seq.included?.available||0)+(seq.other?.available||0));
 const appAvailable=app.status==='update',installable=appAvailable||components>0||!!updates.pendingApp||!!updates.pendingRelease;
 const blocked=appAvailable&&app.canInstall===false||updates.pendingReplacement!=null||!!updates.pendingRestart||!!(seq.stage==='included'&&seq.included?.issues);
 const failed=updates.pendingReplacement!=null||!!updates.error||['error','interrupted'].includes(updates.phase)||updates.pendingRestart?.requestStatus==='rejected'||app.status==='check_failed'||!!(seq.stage==='included'&&seq.included?.issues);
 const active=['checking','staging','validating','activating'].includes(updates.phase);
 const stage=seq.nextStage||seq.stage||'application';
 const result={stage,installable,blocked,busy:active||!!seq.nextStage||updates.pendingReplacement!=null,continuing:!!seq.install,title:'Check for updates',detail:'We’ll check Amplifier and the components it uses.',tone:'neutral'};
 if(failed)return {...result,title:'Updates need attention',tone:'error',detail:updates.pendingReplacement!=null?'The installation needs verification before work can continue. Open details for the recovery steps.':updates.pendingRestart?'The app update needs help finishing its restart. Open details for the next step.':seq.included?.issues?'Some required components could not be updated. Your existing configuration is preserved. Review the issue below.':'The update could not finish. Review the issue below before trying again.'};
 if(updates.pendingRestart)return {...result,title:updates.pendingRestart.requestStatus==='uncertain'?'Waiting for restart confirmation':'Restarting Amplifier',detail:'Waiting for a healthy restarted app before continuing with the remaining updates.',tone:'working'};
 if(updates.pendingApp||updates.pendingRelease||updates.pendingSmartTools?.length)return {...result,title:'Waiting for your work to finish',detail:'The update is ready. It will continue automatically when conversations, tools, and calls are idle.',tone:'working'};
 if(active)return {...result,title:updates.phase==='checking'?'Checking for updates':'Updating Amplifier',detail:updates.detail||(updates.phase==='checking'?(stage==='application'?'Checking for a new app version first.':stage==='included'?'Checking included components…':'Checking other components…'):'Preparing and verifying the update…'),tone:'working'};
 if(seq.nextStage||seq.install&&installable)return {...result,title:'Updating Amplifier',detail:stage==='included'?'Continuing with included components…':'Continuing with other components…',tone:'working'};
 if(installable)return {...result,title:'Updates available',detail:(appAvailable?(String(app.current||'').replace(/^v/,'')===String(app.latest||'').replace(/^v/,'')?'App component updates are ready. ':'A new app version is ready. '):'')+(components?`${components} component ${components===1?'update is':'updates are'} ${appAvailable?'also ':''}ready. `:'')+(options.autoInstall!==false?'They’ll install automatically when work is idle.':'One update request handles the app and all eligible components.'),tone:'available'};
 if(issues.length||seq.included?.issues||seq.other?.issues)return {...result,title:'Some updates need attention',detail:'No installable updates remain, but some checks or local changes need review.',tone:'error'};
 if(seq.stage==='complete')return {...result,title:'You’re up to date',detail:(seq.included?.protected||seq.other?.protected)?'All eligible updates are complete. Pinned versions and local overrides were kept.':'Amplifier and its eligible components are up to date.',tone:'ready'};
 if(app.status==='release_channel_needed')return {...result,title:'Automatic app updates aren’t configured',detail:'This installation needs a release channel. Component updates and technical details are available below.'};
 if(updates.lastCheck)return {...result,title:'App checked · components not fully checked',detail:'Run a check to confirm the full update status.'};
 return result;
}
