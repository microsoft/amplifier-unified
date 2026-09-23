import React from 'react';
import {ArrowRight,Layers,ShieldCheck,FolderOpen,Brain,Monitor,Archive,Activity} from 'lucide-react';
import {advancedGroups,settingsTitle} from './settings-navigation';
import './settings-everyday.css';

export function SettingsLink({title,description,onClick,Icon=ArrowRight,page,disabled=false}){
 return <button type="button" className="a-everyday-row" disabled={disabled} data-action="view.update" data-settings-destination={page} onClick={onClick}>{Icon!==ArrowRight&&<Icon aria-hidden="true"/>}<span><strong>{title}</strong>{description&&<small>{description}</small>}</span><ArrowRight aria-hidden="true"/></button>;
}
export function PrivacySettings({navigate}){
 const rows=[['recall','Memory & past work','Choose what is saved and reused. Review and correct memories.',Brain],['workspaces','Files & folders','Choose where new workspaces are created.',FolderOpen],['permissions','File access','Review additional allowed and blocked folders.',ShieldCheck],['desktop','Screen & computer access','Check the host, browser, and permission status.',Monitor],['history','Saved work','Import, export, and review cleanup.',Archive],['diagnostics','App diagnostic capture','Review recorded data and forwarding destinations.',Activity]];
 return <><p className="a-everyday-intro">Control your saved work and what Amplifier can access.</p><div className="a-everyday-list">{rows.map(([page,title,description,Icon])=><SettingsLink key={page} {...{page,title,description,Icon}} onClick={()=>navigate(page)}/>)}</div><div className="a-everyday-footer"><SettingsLink page="repair" title="Backups & recovery" description="Create a private backup or troubleshoot this app." onClick={()=>navigate('repair')}/></div></>;
}
export function AdvancedSettings({sections,navigate,state}){
 const contributed=sections.flatMap(section=>section.pages).filter(([id])=>id.startsWith('shell:'));
 return <><p className="a-everyday-intro">The full configuration, when you need it. Your existing settings stay in place.</p>{advancedGroups.map(group=><section className="a-everyday-group" key={group.title}><h4>{group.title}</h4><div className="a-everyday-list">{group.pages.map(page=><SettingsLink key={page} page={page} title={settingsTitle(page,sections)} description={state.attention?.pages?.[page]?'Needs attention':undefined} onClick={()=>navigate(page)}/>)}</div></section>)}{contributed.length>0&&<section className="a-everyday-group"><h4>Extension settings</h4><div className="a-everyday-list">{contributed.map(([page,title])=><SettingsLink key={page} page={page} title={title} Icon={Layers} onClick={()=>navigate(page)}/>)}</div></section>}<p className="a-caption">These controls use the same saved configuration and actions as the rest of Amplifier.</p></>;
}
