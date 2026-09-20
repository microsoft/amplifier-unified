import {settingsSections} from '../src/settings-navigation.js';
export async function openSettingsPage(page,destination){
 if(!await page.locator('.a-settings-experience').isVisible())await page.getByRole('button',{name:'Settings',exact:true}).click();
 const section=settingsSections.find(section=>section.pages.some(([id])=>id===destination));
 if(!section)throw new Error('Unknown settings test destination: '+destination);
 await page.locator(`[data-settings-section="${section.id}"]`).click();
 if(section.pages.length>1)await page.locator(`[data-settings-destination="${destination}"]`).click();
 await page.locator(`.a-settings-experience[data-settings-page="${destination}"]`).waitFor();
}
