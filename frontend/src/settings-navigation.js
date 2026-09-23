// Keep the existing public view contract. Old agent links and saved settings
// pages resolve to their new home without a configuration migration.
export const settingsSections=[
 {id:'overview',title:'Your setup',group:'Start',scope:'This app',pages:[['overview','Your setup']]},
 {id:'appearance',title:'Appearance',group:'Personal',scope:'This interface',pages:[['appearance','Appearance']]},
 {id:'workspaces',title:'Workspaces',group:'Personal',scope:'This host',pages:[['workspaces','Workspaces']]},
 {id:'voice',title:'Voice',group:'Personal',scope:'Future voice connections',pages:[['voice','Voice']]},
 {id:'notifications',title:'Notifications',group:'Personal',scope:'This app',pages:[['notifications','Notifications']]},
 {id:'models',title:'Models & routing',group:'Configuration',scope:'Scope selected below',pages:[['providers','Connections'],['routing','Routing']]},
 {id:'bundles',title:'Bundles & modules',group:'Configuration',scope:'App or conversation',pages:[['app-bundles','Configured bundles'],['add-bundles','Discover'],['defaults','Defaults'],['loaded-modules','Conversation modules'],['share-bundle','Save & share']]},
 {id:'smart-tools',title:'Smart Tools',group:'Configuration',scope:'This host',pages:[['smart-tools','Smart Tools']]},
 {id:'desktop',title:'Desktop & browser',group:'Configuration',scope:'App host and selected conversation',pages:[['desktop','Desktop & browser']]},
 {id:'updates',title:'Updates',group:'Application',scope:'This host',pages:[['updates','Updates']]},
 {id:'diagnostics',title:'Diagnostics',group:'Application',scope:'Unified app capture',pages:[['diagnostics','Diagnostics']]},
 {id:'history',title:'History & recovery',group:'Application',scope:'This host',pages:[['history','Import & export'],['recall','Recall & memory'],['conversation','Current conversation'],['outputs','Outputs & review'],['publishing','Publishing'],['repair','Backup & repair'],['reset','Advanced recovery']]},
 {id:'advanced',title:'Advanced',group:'Application',scope:'Scope selected below',pages:[['ready-conversations','Readiness'],['registries','Module & source registries'],['permissions','File access'],['automation','Terminal & automation'],['install-app','Install app'],['runtime','Session controls']]},
];
const legacySections={setup:['overview','appearance','voice','providers','routing','defaults','conversation','install-app','runtime'],capabilities:['smart-tools','app-bundles','add-bundles','loaded-modules','registries','share-bundle'],maintenance:['ready-conversations','updates','diagnostics','history','recall','outputs','publishing','permissions','notifications','automation','repair','reset']};
export function settingsLocation(view={},sections=settingsSections){
 const pages=new Set(sections.flatMap(section=>section.pages.map(([page])=>page)));
 const page=view.panel==='appearance'?'appearance':view.settingsExpanded?.find?.(page=>pages.has(page))||({capabilities:'app-bundles',maintenance:'updates'})[view.settingsSection]||'overview';
 return {page,section:sections.find(section=>section.pages.some(([key])=>key===page))};
}
export function settingsPatch(page,sections=settingsSections){
 if(!sections.some(section=>section.pages.some(([id])=>id===page)))throw new Error('Unknown settings page');
 return {panel:'settings',settingsSection:Object.keys(legacySections).find(key=>legacySections[key].includes(page))||'setup',settingsExpanded:[page]};
}
export function settingsUnread(state,section){
 return section.pages.reduce((sum,[page])=>sum+(state.attention?.pages?.[page]||0),0);
}
