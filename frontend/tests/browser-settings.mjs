import {settingsSections} from '../src/settings-navigation.js';
export async function openSettingsPage(page,destination){
 if(!await page.locator('.a-settings-experience').isVisible())await page.getByRole('button',{name:'Settings',exact:true}).click();
 const section=settingsSections.find(section=>section.pages.some(([id])=>id===destination));
 if(!section)throw new Error('Unknown settings test destination: '+destination);
 await page.waitForFunction(()=>{const root=document.querySelector('.a-settings-experience');return root?.dataset.compact===String(root.closest('.a-overlay').clientWidth<960)});
 if(await page.locator('.a-settings-experience').getAttribute('data-compact')==='true'){
  for(let depth=0;depth<6&&await page.locator('.a-settings-experience').getAttribute('data-settings-index')!=='true';depth++){
   const before=await page.locator('.a-settings-experience').getAttribute('data-settings-route');
   await page.locator('.a-settings-mobile-head button').first().click();
   await page.waitForFunction(previous=>document.querySelector('.a-settings-experience')?.dataset.settingsRoute!==previous,before);
  }
 }
 await page.locator(`[data-settings-section="${section.id}"]`).click();
 if(section.pages.length>1){const picker=page.locator('.a-settings-section-picker select');if(await picker.isVisible())await picker.selectOption(destination);else await page.locator(`[data-settings-destination="${destination}"]`).click();}
 await page.locator(`.a-settings-experience[data-settings-page="${destination}"]`).waitFor();
}
