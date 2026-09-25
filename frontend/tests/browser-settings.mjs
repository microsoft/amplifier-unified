import {settingsSections} from '../src/settings-navigation.js';
export async function openSettingsDialog(page){
 await page.waitForFunction(()=>window.amplifier?.getState());
 if(await page.evaluate(()=>['settings','appearance'].includes(window.amplifier.getState().view?.panel))){await page.locator('.a-settings-experience').waitFor({state:'visible'});return;}
 if(await page.locator('.a-settings-experience').isVisible())return;
 // The menu's attention badge adds its own accessible unread-item label.
 const settings=page.getByRole('button',{name:/^Settings\b/});
 if(await settings.isVisible()){await settings.click();return;}
 const options=page.getByRole('button',{name:'App options',exact:true});
 if(!await options.isVisible())await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await options.click();
 await page.getByRole('group',{name:'App options',exact:true}).getByRole('button',{name:/^Settings\b/}).click();
}
export async function openSettingsPage(page,destination){
 await openSettingsDialog(page);
 const section=settingsSections.find(section=>section.pages.some(([id])=>id===destination));
 if(!section)throw new Error('Unknown settings test destination: '+destination);
 await page.waitForFunction(()=>{const root=document.querySelector('.a-settings-experience');return !!root&&root.dataset.compact===String(root.closest('.a-overlay').clientWidth<960)});
 if(await page.locator('.a-settings-experience').getAttribute('data-compact')==='true'){
  for(let depth=0;depth<6&&await page.locator('.a-settings-experience').getAttribute('data-settings-index')!=='true';depth++){
   const before=await page.locator('.a-settings-experience').getAttribute('data-settings-route');
   await page.locator('.a-settings-mobile-head button').first().click();
   await page.waitForFunction(previous=>document.querySelector('.a-settings-experience')?.dataset.settingsRoute!==previous,before);
  }
 }
 await page.locator(`[data-settings-section="${section.id}"]`).click();
 if(section.pages[0][0]!==destination){
  const link=page.locator(`.a-settings-page-content:not([hidden]) [data-settings-destination="${destination}"]`);
  const group=link.locator('xpath=ancestor::details');
  if(await group.count()&&!await link.isVisible())await group.locator('summary').click();
  if(await link.isVisible())await link.click();
  else await page.evaluate(destination=>window.amplifier.dispatch('view.update',{patch:{panel:'settings',settingsExpanded:[destination]}}),destination);
 }

 await page.locator(`.a-settings-experience[data-settings-page="${destination}"]`).waitFor();
}
