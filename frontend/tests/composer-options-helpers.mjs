import {expect} from '@playwright/test';

// Keep the open dialog and its pending choices mounted while navigating through
// the same visible overview/Back controls a person uses.
export async function showComposerOption(page,section){
 const names={model:'Choose model',effort:'Choose reasoning effort',provider:'Choose provider',bundle:'Choose agent bundle'};
 if(!Object.hasOwn(names,section))throw Error('Unknown composer option: '+section);
 const dialog=page.getByRole('dialog',{name:'Chat options',exact:true});
 await expect(dialog).toBeVisible();
 const back=dialog.getByRole('button',{name:'Back to chat options',exact:true});
 if(await back.isVisible())await back.click();
 await dialog.getByRole('button',{name:names[section],exact:true}).click();
}