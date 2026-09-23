// Public destination IDs remain valid. Navigation never migrates configuration.
export const settingsSections=[
 {id:'ai',title:'AI connections',scope:'Across workspaces on this Amplifier host',pages:[['ai-connections','AI connections'],['overview','AI connections']]},
 {id:'smart-tools',title:'Smart Tools',scope:'This Amplifier host',pages:[['smart-tools','Smart Tools']]},
 {id:'appearance',title:'Appearance',scope:'This interface',pages:[['appearance','Appearance']]},
 {id:'voice',title:'Voice',scope:'Future voice connections',pages:[['voice','Voice']]},
 {id:'notifications',title:'Notifications',scope:'This app',pages:[['notifications','Notifications']]},
 {id:'privacy',title:'Privacy & files',scope:'Your work and access choices',pages:[['privacy','Privacy & files'],['workspaces','Files & folders'],['recall','Memory & past work'],['permissions','File access'],['desktop','Screen & computer access'],['diagnostics','App diagnostic capture'],['history','Saved work']]},
 {id:'updates',title:'Updates',scope:'This Amplifier host',pages:[['updates','Updates']]},
 {id:'advanced',title:'Advanced',scope:'Full configuration',pages:[['advanced','Advanced'],['providers','Provider configuration'],['routing','Model rules'],['app-bundles','Configured bundles'],['add-bundles','Discover bundles'],['defaults','Conversation defaults'],['loaded-modules','Conversation modules'],['share-bundle','Save & share a bundle'],['registries','Module & source registries'],['tool-connections','Tool connections'],['custom-appearance','Custom appearance'],['conversation','Current conversation'],['outputs','Outputs & review'],['publishing','Publishing'],['repair','Backup & repair'],['reset','Advanced recovery'],['ready-conversations','Ready conversations'],['automation','Terminal & automation'],['install-app','Install app'],['runtime','Session controls']]},
];
export const advancedGroups=[
 {title:'AI configuration',pages:['providers','routing']},
 {title:'Bundles & modules',pages:['app-bundles','add-bundles','defaults','loaded-modules','share-bundle','registries']},
 {title:'Tools & appearance',pages:['tool-connections','custom-appearance']},
 {title:'Conversation & work',pages:['conversation','outputs','publishing']},
 {title:'Performance & support',pages:['ready-conversations','runtime','repair','reset','automation','install-app']},
];
const legacySections={setup:['overview','ai-connections','advanced','appearance','voice','providers','routing','defaults','conversation','install-app','runtime','custom-appearance'],capabilities:['smart-tools','tool-connections','app-bundles','add-bundles','loaded-modules','registries','share-bundle'],maintenance:['privacy','ready-conversations','updates','diagnostics','history','recall','outputs','publishing','permissions','notifications','automation','repair','reset']};
export function settingsLocation(view={},sections=settingsSections){
 const pages=new Set(sections.flatMap(section=>section.pages.map(([page])=>page)));
 const page=view.panel==='appearance'?'appearance':view.settingsExpanded?.find?.(page=>pages.has(page))||({capabilities:'app-bundles',maintenance:'updates'})[view.settingsSection]||'ai-connections';
 return {page,section:sections.find(section=>section.pages.some(([key])=>key===page))};
}
export function settingsPatch(page,sections=settingsSections){
 if(!sections.some(section=>section.pages.some(([id])=>id===page)))throw new Error('Unknown settings page');
 return {panel:'settings',settingsSection:Object.keys(legacySections).find(key=>legacySections[key].includes(page))||'setup',settingsExpanded:[page]};
}
export function settingsUnread(state,section){return section.pages.reduce((sum,[page])=>sum+(state.attention?.pages?.[page]||0),0);}
export function settingsTitle(page,sections=settingsSections){return sections.flatMap(s=>s.pages).find(([id])=>id===page)?.[1]||'Settings';}
export function settingsParent(page,sections=settingsSections){const section=sections.find(s=>s.pages.some(([id])=>id===page));return ['privacy','advanced'].includes(section?.id)&&page!==section.id?section.id:null;}
